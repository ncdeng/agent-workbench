"""Build a SHA-bound three-arm comparison from saved real-CST reports."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any

try:
    from benchmarks.cst_solver_evidence import (
        build_source_bindings,
        repository_commit,
        sha256_path,
        verify_source_bindings,
    )
except ModuleNotFoundError:
    from cst_solver_evidence import (
        build_source_bindings,
        repository_commit,
        sha256_path,
        verify_source_bindings,
    )


BASELINE_SCHEMA = "cst-dipole-solver-smoke-v2"
TUNING_SCHEMA = "cst-dipole-tuning-smoke-v1"
NATIVE_SCHEMA = "cst-native-optimizer-run-v3"
SOURCE_PATHS = {
    "comparison": "benchmarks/cst_dipole_optimization_comparison.py",
    "evidence": "benchmarks/cst_solver_evidence.py",
}


def _load(path: Path) -> tuple[Path, dict[str, Any]]:
    resolved = path.resolve()
    if resolved.drive.upper() != "D:":
        raise ValueError(f"source report must be on D: {resolved}")
    return resolved, json.loads(resolved.read_text(encoding="utf-8"))


def _report_binding(path: Path) -> dict[str, Any]:
    return {"path": str(path), "sha256": sha256_path(path)}


def _resonance_error(summary: dict[str, Any], target_ghz: float) -> float | None:
    frequency = summary.get("min_freq_ghz")
    return abs(float(frequency) - target_ghz) if frequency is not None else None


def _target_met(
    *,
    execution_success: bool,
    summary: dict[str, Any],
    target_ghz: float,
    target_db: float,
    tolerance_ghz: float,
) -> bool:
    error = _resonance_error(summary, target_ghz)
    s11 = summary.get("target_s11_db")
    return bool(
        execution_success
        and error is not None
        and error <= tolerance_ghz
        and s11 is not None
        and float(s11) <= target_db
    )


def build_comparison(
    *,
    baseline_report: Path,
    physics_guided_report: Path,
    native_report: Path,
    agent_configured_native_report: Path,
    output_path: Path,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    baseline_path, baseline = _load(baseline_report)
    tuning_path, tuning = _load(physics_guided_report)
    native_path, native = _load(native_report)
    configured_path, configured = _load(agent_configured_native_report)
    output = output_path.resolve()
    if output.drive.upper() != "D:":
        raise ValueError("comparison output must be on D:")
    if output.exists():
        raise FileExistsError(f"refusing to overwrite comparison: {output}")
    root = repo_root or Path(__file__).resolve().parents[1]

    if baseline.get("schema_version") != BASELINE_SCHEMA or not baseline.get("success"):
        raise ValueError("invalid baseline report")
    inputs = baseline.get("input") or {}
    target_ghz = float(inputs.get("f0_ghz"))
    if inputs.get("wire_or_plate") != "plate" or not math.isclose(target_ghz, 5.8):
        raise ValueError("comparison requires the frozen 5.8 GHz plate dipole baseline")
    verify_source_bindings(
        root,
        commit=str(baseline.get("repository_commit") or ""),
        bindings=baseline.get("source_bindings") or {},
    )
    project = Path(str(baseline.get("project_file") or ""))
    curve = Path(str(baseline.get("s11_curve_file") or ""))
    if (
        not project.is_file()
        or sha256_path(project) != baseline.get("project_sha256")
        or not curve.is_file()
        or sha256_path(curve) != baseline.get("s11_curve_sha256")
    ):
        raise ValueError("baseline project or curve binding mismatch")

    target_db = -10.0
    tolerance_ghz = 0.05
    if tuning.get("schema_version") != TUNING_SCHEMA:
        raise ValueError("invalid physics-guided report schema")
    contract = tuning.get("target_contract") or {}
    if (
        not math.isclose(float(contract.get("frequency_ghz")), target_ghz)
        or not math.isclose(float(contract.get("s11_at_target_threshold_db")), target_db)
        or not math.isclose(float(contract.get("resonance_tolerance_ghz")), tolerance_ghz)
    ):
        raise ValueError("physics-guided target contract is not paired")
    verify_source_bindings(
        root,
        commit=str(tuning.get("repository_commit") or ""),
        bindings=tuning.get("source_bindings") or {},
    )

    baseline_binding = _report_binding(baseline_path)
    native_arms = {
        "cst_native_plain": (native_path, native),
        "agent_configured_cst_native": (configured_path, configured),
    }
    for arm, (path, payload) in native_arms.items():
        if payload.get("schema_version") != NATIVE_SCHEMA:
            raise ValueError(f"{arm}: invalid native report schema")
        if not payload.get("execution_success"):
            raise ValueError(f"{arm}: native execution did not succeed")
        if payload.get("run", {}).get("baseline_report", {}).get("sha256") != baseline_binding["sha256"]:
            raise ValueError(f"{arm}: baseline report binding mismatch")
        verify_source_bindings(
            root,
            commit=str(payload.get("repository_commit") or ""),
            bindings=payload.get("source_bindings") or {},
        )

    tuning_before = tuning.get("before") or {}
    if not (
        math.isclose(float(tuning_before.get("min_freq_ghz")), float(baseline["s11"]["min_freq_ghz"]), abs_tol=1e-9)
        and math.isclose(float(tuning_before.get("target_s11_db")), float(baseline["s11"]["target_s11_db"]), abs_tol=1e-9)
    ):
        raise ValueError("physics-guided arm does not share the frozen baseline")

    tuning_final = tuning.get("attempted_after") or {}
    tuning_tools = [stage.get("tool_name") for stage in tuning.get("stages") or []]
    tuning_solver_count = sum(name in {"build_dipole_fast", "run_solver"} for name in tuning_tools)
    arms: dict[str, Any] = {
        "physics_guided": {
            "source_report": _report_binding(tuning_path),
            "role_split": "Agent diagnoses resonance and proposes one parameter; CST validates baseline and candidate",
            "execution_success": bool(tuning.get("execution_success")),
            "physical_solver_evaluations_including_baseline": tuning_solver_count,
            "new_candidate_solver_evaluations": max(0, tuning_solver_count - 1),
            "optimizer_function_evaluations": None,
            "elapsed_sec": tuning.get("elapsed_sec"),
            "final_parameter_mm": (tuning.get("proposal") or {}).get("new_value_mm"),
            "s11_at_target_db": tuning_final.get("target_s11_db"),
            "resonance_ghz": tuning_final.get("min_freq_ghz"),
            "resonance_error_ghz": _resonance_error(tuning_final, target_ghz),
            "joint_target_met": _target_met(
                execution_success=bool(tuning.get("execution_success")),
                summary=tuning_final,
                target_ghz=target_ghz,
                target_db=target_db,
                tolerance_ghz=tolerance_ghz,
            ),
        }
    }
    for name, (path, payload) in native_arms.items():
        comparison = payload.get("comparison") or {}
        evidence = payload.get("optimizer_evidence") or {}
        after = payload.get("after") or {}
        arms[name] = {
            "source_report": _report_binding(path),
            "role_split": (
                "Agent passes the user S11 threshold directly; CST owns search"
                if name == "cst_native_plain"
                else "Agent configures a stricter developer-informed surrogate S11 goal; CST owns search; Agent applies the original joint acceptance endpoint"
            ),
            "execution_success": bool(payload.get("execution_success")),
            "physical_solver_evaluations_including_baseline": (
                int(evidence.get("solver_evaluation_count")) + int(evidence.get("reloaded_evaluation_count"))
            ),
            "new_candidate_solver_evaluations": evidence.get("solver_evaluation_count"),
            "optimizer_function_evaluations": evidence.get("optimizer_evaluation_count"),
            "elapsed_sec": round(float(payload["run"]["duration_ms"]) / 1000, 3),
            "final_parameter_mm": comparison.get("persisted_parameter_after"),
            "optimizer_goal_db": comparison.get("optimizer_goal_db", target_db),
            "s11_at_target_db": after.get("target_s11_db"),
            "resonance_ghz": after.get("min_freq_ghz"),
            "resonance_error_ghz": _resonance_error(after, target_ghz),
            "joint_target_met": _target_met(
                execution_success=bool(payload.get("execution_success")),
                summary=after,
                target_ghz=target_ghz,
                target_db=target_db,
                tolerance_ghz=tolerance_ghz,
            ),
            "termination_status": evidence.get("termination_status"),
            "termination_reason": evidence.get("termination_reason"),
        }

    source_bindings = build_source_bindings(
        root,
        SOURCE_PATHS,
        snapshot_root=output.with_name(f"{output.stem}_source_snapshot"),
    )
    report = {
        "schema_version": "cst-dipole-optimization-comparison-v2",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "repository_commit": repository_commit(root),
        "source_bindings": source_bindings,
        "baseline": {
            **baseline_binding,
            "project_sha256": baseline["project_sha256"],
            "curve_sha256": baseline["s11_curve_sha256"],
            "s11_at_target_db": baseline["s11"]["target_s11_db"],
            "resonance_ghz": baseline["s11"]["min_freq_ghz"],
            "resonance_error_ghz": abs(float(baseline["s11"]["min_freq_ghz"]) - target_ghz),
        },
        "frozen_acceptance_contract": {
            "target_frequency_ghz": target_ghz,
            "s11_at_target_threshold_db": target_db,
            "resonance_tolerance_ghz": tolerance_ghz,
        },
        "arms": arms,
        "conclusion": {
            "execution_success_count": sum(bool(arm["execution_success"]) for arm in arms.values()),
            "joint_target_met_count": sum(bool(arm["joint_target_met"]) for arm in arms.values()),
            "physics_guided_passed": bool(arms["physics_guided"]["joint_target_met"]),
            "plain_native_passed": bool(arms["cst_native_plain"]["joint_target_met"]),
            "agent_configured_native_passed": bool(arms["agent_configured_cst_native"]["joint_target_met"]),
        },
        "honest_boundary": [
            "This is one developer-visible 5.8 GHz plate-dipole failure case, not a benchmark of general optimizer quality.",
            "The -15.5 dB surrogate was selected after observing development results and is not blinded.",
            "The physics-guided arm spends one baseline solve plus one candidate solve; native arms reuse the bound baseline as a reload and report new solver runs separately.",
            "Wall time is descriptive because arms were run sequentially and CST session state was not randomized.",
            "The result supports role separation: the Agent defines and audits physical intent, while CST remains the numerical search and physics source of truth.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-report", type=Path, required=True)
    parser.add_argument("--physics-guided-report", type=Path, required=True)
    parser.add_argument("--native-report", type=Path, required=True)
    parser.add_argument("--agent-configured-native-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_comparison(
        baseline_report=args.baseline_report,
        physics_guided_report=args.physics_guided_report,
        native_report=args.native_report,
        agent_configured_native_report=args.agent_configured_native_report,
        output_path=args.output,
    )
    print(json.dumps(report["conclusion"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
