"""Compare proposal and CST-native optimizers under a real solver-call budget."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
import uuid
from typing import Any, Mapping

from cst_agent_workbench import config
from cst_agent_workbench.agent.analyzer import (
    propose_patch_optimization_with_llm,
    validate_llm_proposal,
)
from cst_agent_workbench.cst.controller import CSTController
from cst_agent_workbench.cst.primitives import store_parameter
from cst_agent_workbench.optimization.models import (
    OptimizationContext,
    OptimizationTarget,
    S11Summary,
)
from cst_agent_workbench.optimization.optimizer import PatchOptimizerMixin
from cst_agent_workbench.optimization.strategy import HeuristicPatchOptimizationStrategy

from benchmarks.cst_native_optimizer_runner import (
    _read_s11,
    clone_cst_project,
    run_native_optimizer_experiment,
    validate_native_optimizer_request,
)


ARMS = ("heuristic", "llm", "cst_native", "hybrid")


def _load_parameters(project_path: Path) -> dict[str, float]:
    payload = json.loads(
        (project_path.with_suffix("") / "Model" / "Parameters.json").read_text(
            encoding="utf-8"
        )
    )
    return {
        str(item["name"]): float(item["value"])
        for item in payload.get("parameters") or []
    }


def _proposal_history(rounds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    history = []
    for item in rounds:
        proposal = item.get("proposal") or {}
        history.append(
            {
                "param_name": proposal.get("param"),
                "delta_mm": proposal.get("delta_mm"),
                "improved": item.get("improved", False),
                "metric_value": item.get("after", {}).get("target_s11_db"),
                "param_snapshot": item.get("parameters_after", {}),
            }
        )
    return history


def _heuristic_proposal(
    params: dict[str, float],
    summary: dict[str, Any],
    rounds: list[dict[str, Any]],
    target_freq_ghz: float,
    target_db: float,
    allowed_parameters: set[str],
) -> dict[str, Any]:
    history = _proposal_history(rounds)
    strategy = HeuristicPatchOptimizationStrategy(
        PatchOptimizerMixin._infer_param_direction_from_history
    )
    proposal = strategy.propose_next_step(
        OptimizationContext(
            target=OptimizationTarget(
                mode="at_f0",
                target_freq_ghz=target_freq_ghz,
                target_db=target_db,
            ),
            parameters=params,
            s11_summary=S11Summary.from_mapping(summary),
            history=history,
        )
    )
    update = next(
        (item for item in proposal.updates if item.name in allowed_parameters),
        None,
    )
    if update is None:
        return {
            "success": False,
            "source": "heuristic",
            "reason": proposal.reason or proposal.message,
            "error": "heuristic produced no update in the frozen search space",
        }
    return {
        "success": True,
        "source": "heuristic",
        "param": update.name,
        "delta_mm": update.new - update.old,
        "new_value": update.new,
        "reason": proposal.reason,
    }


def _llm_proposal(
    *,
    client: Any,
    model: str,
    params: dict[str, float],
    summary: dict[str, Any],
    rounds: list[dict[str, Any]],
    target_freq_ghz: float,
    allowed_parameters: set[str],
    parameter_bounds: Mapping[str, tuple[float, float]],
) -> dict[str, Any]:
    history = _proposal_history(rounds)
    reject_reason = ""
    attempts = []
    for _ in range(2):
        proposal, usage = propose_patch_optimization_with_llm(
            client,
            model,
            {
                "min_s11_db": summary.get("min_s11_db"),
                "min_freq_ghz": summary.get("min_freq_ghz"),
                "at_f0_s11_db": summary.get("target_s11_db"),
            },
            target_freq_ghz,
            params,
            history=history,
            timeout=60,
            reject_reason=reject_reason,
            memory_constraints=[
                "This paired experiment freezes the search space: change one of "
                + ", ".join(sorted(allowed_parameters))
                + " only. Absolute bounds: "
                + ", ".join(
                    f"{name}=[{parameter_bounds[name][0]}, {parameter_bounds[name][1]}]"
                    for name in sorted(allowed_parameters)
                )
                + ". The resulting old+delta value must stay inside its bound."
            ],
        )
        attempts.append({"proposal": proposal, "usage": usage})
        ok, reason = validate_llm_proposal(proposal, history)
        param = proposal.get("param") if isinstance(proposal, dict) else None
        proposed_value = (
            params[param] + float(proposal["delta_mm"])
            if ok and param in allowed_parameters
            else None
        )
        if (
            ok
            and param in allowed_parameters
            and parameter_bounds[param][0] <= proposed_value <= parameter_bounds[param][1]
        ):
            return {
                "success": True,
                "source": "llm",
                "model": model,
                "param": proposal["param"],
                "delta_mm": float(proposal["delta_mm"]),
                "new_value": proposed_value,
                "reason": proposal.get("reason", ""),
                "attempts": attempts,
            }
        if ok and param in allowed_parameters and proposed_value is not None:
            lower, upper = parameter_bounds[param]
            reject_reason = (
                f"resulting {param}={proposed_value:.6g} is outside [{lower}, {upper}]"
            )
        else:
            reject_reason = reason or (
                "paired experiment permits only " + ", ".join(sorted(allowed_parameters))
            )
    return {
        "success": False,
        "source": "llm",
        "model": model,
        "error": reject_reason or "no valid LLM proposal",
        "attempts": attempts,
    }


def _connect_pinned(project_path: Path) -> tuple[CSTController, dict[str, Any]]:
    original = config.CST_DEFAULT_PROJECT
    config.CST_DEFAULT_PROJECT = str(project_path)
    try:
        controller = CSTController()
        result = controller.connect()
    finally:
        config.CST_DEFAULT_PROJECT = original
    controller.project_path = str(project_path)
    controller._query_best_effort_project_path = lambda: str(project_path)  # type: ignore[method-assign]
    return controller, result


def run_proposal_arm(
    *,
    arm: str,
    source_project: Path,
    run_root: Path,
    solver_budget: int,
    target_freq_ghz: float,
    target_db: float,
    llm_model: str,
    llm_client: Any = None,
    parameter_bounds: Mapping[str, tuple[float, float]] | None = None,
) -> dict[str, Any]:
    allowed_parameters = set((parameter_bounds or {}).keys())
    project = clone_cst_project(source_project, run_root)
    temp_root = run_root / "tmp"
    temp_root.mkdir()
    before = _read_s11(project, target_freq_ghz)
    best = dict(before)
    rounds: list[dict[str, Any]] = []
    solver_count = 0
    successful_solver_count = 0
    failed_solver_count = 0
    started = time.perf_counter()
    old_temp = os.environ.get("TEMP"), os.environ.get("TMP")
    controller: CSTController | None = None
    connection: dict[str, Any] = {}
    try:
        os.environ["TEMP"] = str(temp_root)
        os.environ["TMP"] = str(temp_root)
        controller, connection = _connect_pinned(project)
        while connection.get("success") and solver_count < solver_budget:
            current = _read_s11(project, target_freq_ghz)
            if current.get("target_s11_db") is not None and current["target_s11_db"] <= target_db:
                break
            params = _load_parameters(project)
            if arm == "llm":
                proposal = _llm_proposal(
                    client=llm_client,
                    model=llm_model,
                    params=params,
                    summary=current,
                    rounds=rounds,
                    target_freq_ghz=target_freq_ghz,
                    allowed_parameters=allowed_parameters,
                    parameter_bounds=parameter_bounds or {},
                )
            else:
                proposal = _heuristic_proposal(
                    params,
                    current,
                    rounds,
                    target_freq_ghz,
                    target_db,
                    allowed_parameters,
                )
            if not proposal.get("success"):
                rounds.append({"proposal": proposal, "stopped": "proposal_failed"})
                break
            param_name = str(proposal["param"])
            bounds = (parameter_bounds or {}).get(param_name)
            if bounds is None or not bounds[0] <= float(proposal["new_value"]) <= bounds[1]:
                rounds.append(
                    {
                        "proposal": proposal,
                        "stopped": "proposal_outside_frozen_bounds",
                        "bounds": bounds,
                    }
                )
                break
            _, vba = store_parameter(param_name, format(proposal["new_value"], ".15g"))
            update = controller.execute_vba(
                vba, label=f"eval_{arm}_{param_name}", timeout=120
            )
            if not update.get("success"):
                rounds.append({"proposal": proposal, "update": update})
                break
            solve = controller.run_solver(timeout=600)
            solver_count += 1
            after = _read_s11(project, target_freq_ghz)
            solver_succeeded = bool(solve.get("success") and after.get("success"))
            if solver_succeeded:
                successful_solver_count += 1
            else:
                failed_solver_count += 1
            improved = bool(
                current.get("target_s11_db") is not None
                and after.get("target_s11_db") is not None
                and after["target_s11_db"] < current["target_s11_db"] - 1e-9
            )
            if solver_succeeded and (
                after.get("target_s11_db") is not None
                and (
                    best.get("target_s11_db") is None
                    or after["target_s11_db"] < best["target_s11_db"]
                )
            ):
                best = dict(after)
            rounds.append(
                {
                    "proposal": proposal,
                    "update": update,
                    "solver": solve,
                    "before": current,
                    "after": after,
                    "improved": improved,
                    "parameters_after": _load_parameters(project),
                }
            )
            if not solve.get("success"):
                break
    finally:
        if controller is not None and connection.get("success"):
            controller.close_project(str(project), timeout=60)
        for name, value in zip(("TEMP", "TMP"), old_temp):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    target_met = bool(
        best.get("target_s11_db") is not None and best["target_s11_db"] <= target_db
    )
    return {
        "arm": arm,
        "project": str(project),
        "connection": connection,
        "solver_budget": solver_budget,
        "physical_solver_evaluations": solver_count,
        "successful_solver_evaluations": successful_solver_count,
        "failed_solver_evaluations": failed_solver_count,
        "protocol_violation": solver_count > solver_budget,
        "before": before,
        "best": best,
        "rounds": rounds,
        "target_met": target_met,
        "improvement_db": (
            before["target_s11_db"] - best["target_s11_db"]
            if before.get("target_s11_db") is not None
            and best.get("target_s11_db") is not None
            else None
        ),
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        "success": bool(
            connection.get("success")
            and successful_solver_count >= 1
            and not solver_count > solver_budget
        ),
    }


def run_comparison(
    *,
    source_project: Path,
    artifact_root: Path,
    solver_budget: int,
    target_freq_ghz: float,
    target_db: float,
    minimum: float,
    maximum: float,
    inset_minimum: float,
    inset_maximum: float,
    llm_model: str,
    search_dimensions: tuple[str, ...] = ("patch_L", "inset_depth"),
    arms: tuple[str, ...] = ARMS,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    source, artifacts = validate_native_optimizer_request(
        source_project=source_project,
        artifact_root=artifact_root,
        environment=environment,
    )
    if solver_budget < 2:
        raise ValueError("solver_budget must be at least 2")
    unknown = set(arms) - set(ARMS)
    if unknown:
        raise ValueError(f"unknown arms: {sorted(unknown)}")
    root = artifacts / uuid.uuid4().hex[:12]
    root.mkdir(parents=True, exist_ok=False)
    all_parameter_bounds = {
        "patch_L": (minimum, maximum),
        "inset_depth": (inset_minimum, inset_maximum),
    }
    if not search_dimensions or not set(search_dimensions) <= set(all_parameter_bounds):
        raise ValueError("search_dimensions must contain patch_L and/or inset_depth")
    parameter_bounds = {
        name: all_parameter_bounds[name] for name in search_dimensions
    }
    native_primary = search_dimensions[0]
    native_additional = tuple(
        (name, *parameter_bounds[name]) for name in search_dimensions[1:]
    )
    results: dict[str, Any] = {}
    llm_client = None
    if any(arm in {"llm", "hybrid"} for arm in arms):
        from openai import OpenAI

        kwargs = {"api_key": config.OPENAI_API_KEY}
        if config.OPENAI_BASE_URL:
            kwargs["base_url"] = config.OPENAI_BASE_URL
        llm_client = OpenAI(**kwargs)
    for arm in arms:
        if arm in {"heuristic", "llm"}:
            results[arm] = run_proposal_arm(
                arm=arm,
                source_project=source,
                run_root=root / arm,
                solver_budget=solver_budget,
                target_freq_ghz=target_freq_ghz,
                target_db=target_db,
                llm_model=llm_model,
                llm_client=llm_client,
                parameter_bounds=parameter_bounds,
            )
        elif arm == "cst_native":
            native = run_native_optimizer_experiment(
                source_project=source,
                artifact_root=root / "cst_native",
                parameter_name=native_primary,
                minimum=parameter_bounds[native_primary][0],
                maximum=parameter_bounds[native_primary][1],
                additional_parameters=native_additional,
                target_freq_ghz=target_freq_ghz,
                target_db=target_db,
                max_evaluations=solver_budget + 1,
                timeout_seconds=1800,
                environment=environment,
            )
            count = native["status"].get("solver_evaluation_count")
            results[arm] = {
                "arm": arm,
                "solver_budget": solver_budget,
                "physical_solver_evaluations": count,
                "protocol_violation": not isinstance(count, int) or count > solver_budget,
                "target_met": native["comparison"]["target_met"],
                "improvement_db": native["comparison"]["physical_improvement_db"],
                "native_report": native,
                "success": bool(native["success"] and isinstance(count, int) and count <= solver_budget),
            }
        else:
            warm = run_proposal_arm(
                arm="llm",
                source_project=source,
                run_root=root / "hybrid_warm",
                solver_budget=1,
                target_freq_ghz=target_freq_ghz,
                target_db=target_db,
                llm_model=llm_model,
                llm_client=llm_client,
                parameter_bounds=parameter_bounds,
            )
            remaining = solver_budget - warm["physical_solver_evaluations"]
            if (
                not warm["success"]
                or warm["successful_solver_evaluations"] < 1
                or remaining < 2
            ):
                results[arm] = {
                    "arm": arm,
                    "success": False,
                    "warm_start": warm,
                    "reason": "warm-start failed or left fewer than two native evaluations",
                }
            else:
                hybrid_native = run_native_optimizer_experiment(
                    source_project=Path(warm["project"]),
                    artifact_root=root / "hybrid_native",
                    parameter_name=native_primary,
                    minimum=parameter_bounds[native_primary][0],
                    maximum=parameter_bounds[native_primary][1],
                    additional_parameters=native_additional,
                    target_freq_ghz=target_freq_ghz,
                    target_db=target_db,
                    max_evaluations=remaining + 1,
                    timeout_seconds=1800,
                    environment=environment,
                )
                native_count = hybrid_native["status"].get("solver_evaluation_count")
                total = (
                    warm["physical_solver_evaluations"] + native_count
                    if isinstance(native_count, int)
                    else None
                )
                warm_best = warm["best"].get("target_s11_db")
                native_best = hybrid_native["after"].get("target_s11_db")
                best_s11 = min(
                    value for value in (warm_best, native_best) if value is not None
                )
                baseline_s11 = warm["before"].get("target_s11_db")
                results[arm] = {
                    "arm": arm,
                    "solver_budget": solver_budget,
                    "physical_solver_evaluations": total,
                    "protocol_violation": total is None or total > solver_budget,
                    "warm_start": warm,
                    "native_report": hybrid_native,
                    "target_met": best_s11 <= target_db,
                    "improvement_db": baseline_s11 - best_s11,
                    "success": bool(
                        total is not None
                        and total <= solver_budget
                        and hybrid_native["success"]
                    ),
                }
    report = {
        "schema_version": "cst-optimization-comparison-v1",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "source_project": str(source),
        "protocol": {
            "target": f"S11@{target_freq_ghz}GHz <= {target_db}dB",
            "search_space": {
                name: list(bounds) for name, bounds in parameter_bounds.items()
            },
            "solver_budget_upper_bound": solver_budget,
            "baseline_readback_cost": 0,
            "selection": "best observed physically solved point",
            "llm_model": llm_model,
        },
        "arms": results,
    }
    (root / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-project", type=Path, required=True)
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=Path("D:/cst_agent_rag_data/agent_eval/optimization_comparison"),
    )
    parser.add_argument("--solver-budget", type=int, default=2)
    parser.add_argument("--target-freq-ghz", type=float, default=9.4)
    parser.add_argument("--target-db", type=float, default=-18.0)
    parser.add_argument("--minimum", type=float, required=True)
    parser.add_argument("--maximum", type=float, required=True)
    parser.add_argument("--inset-minimum", type=float, default=3.2)
    parser.add_argument("--inset-maximum", type=float, default=4.5)
    parser.add_argument("--llm-model", default="gpt-5.6-terra")
    parser.add_argument(
        "--search-dimensions",
        nargs="+",
        choices=("patch_L", "inset_depth"),
        default=["patch_L", "inset_depth"],
    )
    parser.add_argument("--arms", nargs="+", choices=ARMS, default=list(ARMS))
    args = parser.parse_args()
    report = run_comparison(
        source_project=args.source_project,
        artifact_root=args.artifact_root,
        solver_budget=args.solver_budget,
        target_freq_ghz=args.target_freq_ghz,
        target_db=args.target_db,
        minimum=args.minimum,
        maximum=args.maximum,
        inset_minimum=args.inset_minimum,
        inset_maximum=args.inset_maximum,
        llm_model=args.llm_model,
        search_dimensions=tuple(args.search_dimensions),
        arms=tuple(args.arms),
    )
    print(
        json.dumps(
            {
                name: {
                    "success": item.get("success"),
                    "solver_evaluations": item.get("physical_solver_evaluations"),
                    "target_met": item.get("target_met"),
                    "improvement_db": item.get("improvement_db"),
                }
                for name, item in report["arms"].items()
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
