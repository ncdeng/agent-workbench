from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from cst_agent_workbench.cst.rectangular_patch_fast import (
    RectangularPatchRequest,
    build_rectangular_patch_vba_artifact,
    format_mm,
    synthesize_rectangular_patch,
)
from cst_agent_workbench.optimization.report import (
    build_patch_l_sweep_markdown,
    build_patch_optimization_markdown,
    build_patch_optimization_report,
    build_round_record,
    build_s11_check,
)


def _default_vba_path() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path("runs") / timestamp / "generated.vba"


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _best_effort_git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            check=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return ""
    return result.stdout.strip()


def _patch_request_from_args(args: argparse.Namespace) -> RectangularPatchRequest:
    return RectangularPatchRequest(
        f0_ghz=args.f0,
        substrate_name=args.material,
        epsilon_r=args.er,
        loss_tangent=args.loss,
        substrate_thickness_mm=args.h,
        conductor_name=args.conductor,
        conductor_thickness_mm=args.copper_t,
        feed_strategy=args.feed,
    )


def _patch_request_to_dict(request: RectangularPatchRequest) -> dict[str, Any]:
    return {
        "f0_ghz": request.f0_ghz,
        "substrate_name": request.substrate_name,
        "epsilon_r": request.epsilon_r,
        "loss_tangent": request.loss_tangent,
        "substrate_thickness_mm": request.substrate_thickness_mm,
        "conductor_name": request.conductor_name,
        "conductor_thickness_mm": request.conductor_thickness_mm,
        "feed_strategy": request.feed_strategy,
    }


PATCH_MATRIX_CASES: list[dict[str, Any]] = [
    {
        "name": "rogers5880_9p4_microstrip",
        "description": "9.4 GHz Rogers5880 inset microstrip baseline and closed-loop optimization",
        "f0": 9.4,
        "er": 2.2,
        "h": 1.6,
        "loss": 0.0009,
        "material": "Rogers5880",
        "feed": "microstrip",
    },
    {
        "name": "rogers5880_5p8_microstrip",
        "description": "5.8 GHz Rogers5880 inset microstrip baseline and closed-loop optimization",
        "f0": 5.8,
        "er": 2.2,
        "h": 1.6,
        "loss": 0.0009,
        "material": "Rogers5880",
        "feed": "microstrip",
    },
    {
        "name": "fr4_2p45_microstrip",
        "description": "2.45 GHz FR4 inset microstrip baseline and closed-loop optimization",
        "f0": 2.45,
        "er": 4.4,
        "h": 1.6,
        "loss": 0.02,
        "material": "FR4",
        "feed": "microstrip",
    },
    {
        "name": "rogers5880_9p4_probe",
        "description": "9.4 GHz Rogers5880 probe-fed baseline evidence",
        "f0": 9.4,
        "er": 2.2,
        "h": 1.6,
        "loss": 0.0009,
        "material": "Rogers5880",
        "feed": "probe",
        "max_rounds": 0,
    },
    {
        "name": "fr4_2p45_probe",
        "description": "2.45 GHz FR4 probe-fed baseline evidence",
        "f0": 2.45,
        "er": 4.4,
        "h": 1.6,
        "loss": 0.02,
        "material": "FR4",
        "feed": "probe",
        "max_rounds": 0,
    },
]


def _matrix_case_request(case: dict[str, Any]) -> RectangularPatchRequest:
    return RectangularPatchRequest(
        f0_ghz=float(case["f0"]),
        substrate_name=str(case["material"]),
        epsilon_r=float(case["er"]),
        loss_tangent=float(case.get("loss", 0.0009)),
        substrate_thickness_mm=float(case["h"]),
        conductor_name=str(case.get("conductor", "Copper (annealed)")),
        conductor_thickness_mm=float(case.get("copper_t", 0.035)),
        feed_strategy=str(case.get("feed", "microstrip")),
    )


def _build_patch(args: argparse.Namespace) -> int:
    if not args.dry_run:
        raise SystemExit("build-patch 第一版仅支持 --dry-run，不会连接或执行 CST")

    request = _patch_request_from_args(args)
    artifact = build_rectangular_patch_vba_artifact(request)
    export_path = Path(args.export_vba) if args.export_vba else _default_vba_path()
    _write_text(export_path, artifact["vba_code"])

    dims = artifact["dims"]
    print("[dry-run] 矩形贴片 VBA 已生成，未连接或执行 CST。")
    print(f"VBA: {export_path}")
    if request.feed_strategy == "probe":
        probe = artifact.get("probe") or {}
        print(
            "尺寸摘要: "
            f"patch_W={dims['patch_w']:.4f} mm, "
            f"patch_L={dims['patch_l']:.4f} mm, "
            f"probe_R={float(probe.get('probe_radius_mm') or 0):.4f} mm, "
            f"probe_Y={float(probe.get('probe_y_offset') or 0):.4f} mm, "
            f"freq_range={dims['fmin']:.4f}-{dims['fmax']:.4f} GHz"
        )
    else:
        print(
            "尺寸摘要: "
            f"patch_W={dims['patch_w']:.4f} mm, "
            f"patch_L={dims['patch_l']:.4f} mm, "
            f"feed_W={dims['feed_w']:.4f} mm, "
            f"inset_depth={dims['inset_depth']:.4f} mm, "
            f"freq_range={dims['fmin']:.4f}-{dims['fmax']:.4f} GHz"
        )
    return 0


def _execute_patch_case(
    *,
    cst,
    request: RectangularPatchRequest,
    connect_result: dict[str, Any],
    target_mode: str,
    target_freq: float,
    target_db: float,
    max_rounds: int,
    algorithm: str,
    allow_build_failure: bool,
    execution_mode: str,
    command_name: str = "optimize-patch-report",
    case_name: str = "",
) -> dict[str, Any]:
    from cst_agent_workbench.agent.agent import CSTAgent
    from cst_agent_workbench.cst.primitives import get_parameters

    started_at = time.perf_counter()
    agent = CSTAgent(cst)
    build_message = agent._run_rectangular_patch_fast_path_from_request(request, execution_mode=execution_mode, allow_solver=True)
    build_success = bool(agent.last_chat_status.get("ok")) and not bool(agent.last_chat_status.get("had_tool_failure"))
    if not build_success and not allow_build_failure:
        raise SystemExit(f"贴片建模/首次求解失败: {agent.last_chat_status.get('error', '') or build_message}")

    effective_target_freq = target_freq if target_mode == "at_f0" and target_freq > 0 else request.f0_ghz
    executable_rounds = max_rounds if request.feed_strategy == "microstrip" else 0
    target = {
        "mode": target_mode,
        "target_db": target_db,
        "configured_target_freq_ghz": target_freq,
        "effective_target_freq_ghz": effective_target_freq,
        "max_rounds": max_rounds,
        "executed_max_rounds": executable_rounds,
        "optimization_supported": request.feed_strategy == "microstrip",
    }
    baseline_raw = dict(agent.last_results or {})
    baseline_check = build_s11_check(
        baseline_raw,
        mode=target_mode,
        target_db=target_db,
        effective_target_freq=effective_target_freq,
        configured_target_freq=target_freq,
    )
    baseline_params = get_parameters()
    agent.opt_state.reset()
    agent.opt_state.active = True
    agent.opt_state.target_mode = target_mode
    agent.opt_state.target_freq = effective_target_freq
    agent.opt_state.target_db = target_db
    agent.opt_state.record_baseline(baseline_check, baseline_params)

    rounds: list[dict[str, Any]] = []
    final_check = baseline_check
    for round_index in range(1, executable_rounds + 1):
        if final_check.get("met"):
            break
        before_event_count = len(agent.tool_events)
        result = agent.run_programmatic_patch_optimization_round(
            target_mode=target_mode,
            target_freq_ghz=effective_target_freq,
            target_db=target_db,
            preferred_algorithm=algorithm,
        )
        after_raw = dict((result.get("results_raw") or agent.last_results or {}))
        final_check = build_s11_check(
            after_raw,
            mode=target_mode,
            target_db=target_db,
            effective_target_freq=effective_target_freq,
            configured_target_freq=target_freq,
        )
        round_params = get_parameters()
        agent.opt_state.record_round(
            final_check,
            round_params,
            strategy=result.get("strategy", ""),
            proposal_reason=result.get("proposal_reason", ""),
            optimizer_result=result,
        )
        rounds.append(
            build_round_record(
                round_index=round_index,
                optimizer_result=result,
                check=final_check,
                param_snapshot=round_params,
                memory_recall=agent.session.metadata.get("optimizer_memory_recall", []),
                memory_impact=agent.session.metadata.get("optimizer_memory_impact", {}),
                tool_events=agent.tool_events[before_event_count:],
            )
        )
        if not result.get("success"):
            break

    runtime_sec = round(time.perf_counter() - started_at, 3)
    return build_patch_optimization_report(
        request=_patch_request_to_dict(request),
        target=target,
        connect_result=connect_result,
        project_path=cst.project_path,
        build_message=build_message,
        build_success=build_success,
        baseline_check=baseline_check,
        baseline_params=baseline_params,
        rounds=rounds,
        final_check=final_check,
        token_stats=agent.token_stats,
        benchmark={
            "command": command_name,
            "case_name": case_name,
            "execution_mode": execution_mode,
            "git_sha": _best_effort_git_sha(),
            "cst_mode": connect_result.get("mode", ""),
            "runtime_sec": runtime_sec,
            "optimization_supported": bool(target.get("optimization_supported")),
            "executed_rounds": len(rounds),
        },
    )


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _patch_param_sweep_values(
    param_name: str,
    center_mm: float,
    *,
    steps: int,
    span_pct: float,
    explicit_values: str = "",
) -> list[float]:
    values: list[float] = []
    if explicit_values.strip():
        for token in explicit_values.split(","):
            text = token.strip()
            if text:
                values.append(float(text))
    else:
        if steps < 2:
            raise SystemExit("patch parameter sweep --steps 必须 >= 2")
        if span_pct <= 0:
            raise SystemExit("patch parameter sweep --span-pct 必须 > 0")
        half_span = center_mm * span_pct / 100.0
        values = [center_mm - half_span + (2 * half_span * index / (steps - 1)) for index in range(steps)]
        values.append(center_mm)

    deduped: list[float] = []
    seen: set[float] = set()
    for value in values:
        if value <= 0:
            raise SystemExit(f"{param_name} 必须 > 0，当前 {value}")
        rounded = round(float(value), 4)
        if rounded not in seen:
            deduped.append(rounded)
            seen.add(rounded)
    return sorted(deduped)


def _patch_l_sweep_values(
    center_mm: float,
    *,
    steps: int,
    span_pct: float,
    explicit_values: str = "",
) -> list[float]:
    return _patch_param_sweep_values(
        "patch_L",
        center_mm,
        steps=steps,
        span_pct=span_pct,
        explicit_values=explicit_values,
    )


def _nearest_sweep_resonance(check: dict[str, Any], target_freq_ghz: float) -> dict[str, Any]:
    candidates = []
    for resonance in check.get("resonances") or []:
        freq = _coerce_float(resonance.get("freq_ghz"))
        depth = _coerce_float(resonance.get("depth_db"))
        if freq is not None and depth is not None:
            candidates.append((freq, depth, resonance))
    if not candidates:
        return {}
    if target_freq_ghz > 0:
        freq, depth, resonance = min(candidates, key=lambda item: abs(item[0] - target_freq_ghz))
        frequency_error = freq - target_freq_ghz
    else:
        freq, depth, resonance = min(candidates, key=lambda item: item[1])
        frequency_error = None
    return {
        "freq_ghz": freq,
        "depth_db": depth,
        "frequency_error_ghz": frequency_error,
        "bandwidth_ghz": resonance.get("bandwidth_ghz"),
        "bandwidth_pct": resonance.get("bandwidth_pct"),
    }


def _compact_s11_check(check: dict[str, Any]) -> dict[str, Any]:
    compact = dict(check or {})
    compact.pop("plot_data", None)
    return compact


def _compact_patch_build_message(message: str) -> str:
    return str(message or "").split("\n\n**AI 分析：**", 1)[0]


def _patch_l_sweep_entry(
    *,
    index: int,
    patch_l_mm: float,
    baseline_patch_l_mm: float,
    target_freq_ghz: float,
    check: dict[str, Any],
    update_result: dict[str, Any],
    solver_result: dict[str, Any],
    param_name: str = "patch_L",
) -> dict[str, Any]:
    delta_mm = patch_l_mm - baseline_patch_l_mm
    delta_pct = 100.0 * delta_mm / baseline_patch_l_mm if baseline_patch_l_mm else 0.0
    entry = {
        "index": index,
        "parameter": param_name,
        "value_mm": patch_l_mm,
        "center_value_mm": baseline_patch_l_mm,
        "delta_mm": round(delta_mm, 4),
        "delta_pct": round(delta_pct, 4),
        "update_success": bool(update_result.get("success")),
        "solver_success": bool(solver_result.get("success")),
        "met": bool(check.get("met")),
        "status_text": check.get("status_text", "No data available"),
        "target_s11_db": check.get("at_f0_s11"),
        "min_s11_db": check.get("min_s11"),
        "min_freq_ghz": check.get("min_freq"),
        "bandwidth_ghz": check.get("bandwidth_ghz"),
        "bandwidth_pct": check.get("bandwidth_pct"),
        "resonances": list(check.get("resonances") or []),
        "nearest_resonance": _nearest_sweep_resonance(check, target_freq_ghz),
        "update_message": update_result.get("message", ""),
        "solver_message": solver_result.get("message", ""),
    }
    if param_name == "patch_L":
        entry["patch_l_mm"] = patch_l_mm
    return entry


def _best_patch_l_sweep_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    target_candidates = [entry for entry in entries if entry.get("target_s11_db") is not None]
    resonance_candidates = [
        entry for entry in entries
        if (entry.get("nearest_resonance") or {}).get("frequency_error_ghz") is not None
    ]
    best_target = min(target_candidates, key=lambda entry: float(entry["target_s11_db"])) if target_candidates else {}
    best_resonance = min(
        resonance_candidates,
        key=lambda entry: (
            abs(float((entry.get("nearest_resonance") or {})["frequency_error_ghz"])),
            float(entry.get("target_s11_db")) if entry.get("target_s11_db") is not None else float("inf"),
        ),
    ) if resonance_candidates else {}
    return {
        "by_target_s11": dict(best_target),
        "by_nearest_resonance": dict(best_resonance),
    }


def _patch_param_update_values(param_name: str, value_mm: float, baseline_params: dict[str, Any]) -> dict[str, float]:
    updates = {param_name: value_mm}
    if param_name == "feed_W":
        inset_gap = _coerce_float(baseline_params.get("inset_gap")) or 0.0
        updates["notch_W"] = value_mm + 2 * inset_gap
    return updates


def _execute_patch_l_sweep(
    *,
    cst,
    request: RectangularPatchRequest,
    connect_result: dict[str, Any],
    target_mode: str,
    target_freq: float,
    target_db: float,
    steps: int,
    span_pct: float,
    values: str,
    allow_build_failure: bool,
    execution_mode: str,
    sweep_param: str = "patch_L",
) -> dict[str, Any]:
    from cst_agent_workbench.agent.agent import CSTAgent
    from cst_agent_workbench.cst.primitives import get_parameters, register_parameter, store_parameter

    if request.feed_strategy != "microstrip":
        raise SystemExit("patch-l-sweep 当前只支持 microstrip feed")

    agent = CSTAgent(cst)
    build_message = agent._run_rectangular_patch_fast_path_from_request(request, execution_mode=execution_mode, allow_solver=True)
    build_success = bool(agent.last_chat_status.get("ok")) and not bool(agent.last_chat_status.get("had_tool_failure"))
    if not build_success and not allow_build_failure:
        raise SystemExit(f"贴片建模/首次求解失败: {agent.last_chat_status.get('error', '') or build_message}")

    effective_target_freq = target_freq if target_mode == "at_f0" and target_freq > 0 else request.f0_ghz
    target = {
        "mode": target_mode,
        "target_db": target_db,
        "configured_target_freq_ghz": target_freq,
        "effective_target_freq_ghz": effective_target_freq,
    }
    baseline_raw = dict(agent.last_results or {})
    baseline_check = build_s11_check(
        baseline_raw,
        mode=target_mode,
        target_db=target_db,
        effective_target_freq=effective_target_freq,
        configured_target_freq=target_freq,
    )
    baseline_params = get_parameters()
    baseline_value = _coerce_float(baseline_params.get(sweep_param))
    if baseline_value is None:
        synthesized = synthesize_rectangular_patch(request)
        synth_key = {
            "patch_L": "patch_l",
            "inset_depth": "inset_depth",
            "feed_W": "feed_w",
        }.get(sweep_param)
        baseline_value = synthesized[synth_key] if synth_key else None
    if baseline_value is None:
        raise SystemExit(f"当前模型缺少可扫描参数: {sweep_param}")

    entries: list[dict[str, Any]] = []
    if build_success:
        sweep_values = _patch_param_sweep_values(
            sweep_param,
            baseline_value,
            steps=steps,
            span_pct=span_pct,
            explicit_values=values,
        )
        for index, patch_l in enumerate(sweep_values, start=1):
            if abs(patch_l - baseline_value) < 0.00005:
                check = baseline_check
                update_result = {"success": True, "message": "baseline cached", "executed": False}
                solver_result = {"success": True, "message": "baseline cached"}
            else:
                update_lines = []
                for update_name, update_value in _patch_param_update_values(sweep_param, patch_l, baseline_params).items():
                    _, update_line = store_parameter(update_name, format_mm(update_value))
                    update_lines.append(update_line)
                update_result = cst.execute_vba(
                    "\n".join(update_lines),
                    label=f"patch_param_sweep_{sweep_param}_{index}",
                    timeout=120,
                )
                check = build_s11_check(
                    {},
                    mode=target_mode,
                    target_db=target_db,
                    effective_target_freq=effective_target_freq,
                    configured_target_freq=target_freq,
                )
                solver_result = {"success": False, "message": "parameter update failed"}
                if update_result.get("success"):
                    if update_result.get("executed", False):
                        for update_name, update_value in _patch_param_update_values(sweep_param, patch_l, baseline_params).items():
                            register_parameter(update_name, format_mm(update_value))
                    solver_result = cst.run_solver(timeout=360)
                    if solver_result.get("success"):
                        summary = agent._collect_s11_summary(effective_target_freq)
                        if summary.get("success") and summary.get("raw"):
                            agent.last_results = summary["raw"]
                        check = build_s11_check(
                            summary.get("raw") or {},
                            mode=target_mode,
                            target_db=target_db,
                            effective_target_freq=effective_target_freq,
                            configured_target_freq=target_freq,
                        )
            entries.append(
                _patch_l_sweep_entry(
                    index=index,
                    patch_l_mm=patch_l,
                    baseline_patch_l_mm=baseline_value,
                    target_freq_ghz=effective_target_freq,
                    check=check,
                    update_result=update_result,
                    solver_result=solver_result,
                    param_name=sweep_param,
                )
            )

    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "request": _patch_request_to_dict(request),
        "target": target,
        "connect": dict(connect_result or {}),
        "project_path": getattr(cst, "project_path", ""),
        "build": {
            "success": build_success,
            "message": _compact_patch_build_message(build_message),
        },
        "baseline": {
            "patch_l_mm": _coerce_float(baseline_params.get("patch_L")),
            "parameter": sweep_param,
            "value_mm": baseline_value,
            "check": _compact_s11_check(baseline_check),
            "param_snapshot": baseline_params,
        },
        "sweep": {
            "parameter": sweep_param,
            "center_patch_l_mm": _coerce_float(baseline_params.get("patch_L")),
            "center_value_mm": baseline_value,
            "span_pct": span_pct,
            "steps": steps,
            "explicit_values": values,
            "entries": entries,
        },
        "best": _best_patch_l_sweep_entries(entries),
        "token_stats": agent.token_stats,
    }


def _run_patch_l_sweep(args: argparse.Namespace) -> int:
    from cst_agent_workbench.cst.controller import CSTController

    request = _patch_request_from_args(args)
    cst = CSTController()
    connect_result = cst.connect()
    if not connect_result.get("success"):
        raise SystemExit(f"CST 连接失败，无法执行 patch_L sweep: {connect_result.get('message', '')}")

    sweep_param = getattr(args, "param", "patch_L")
    report = _execute_patch_l_sweep(
        cst=cst,
        request=request,
        connect_result=connect_result,
        target_mode=args.target_mode,
        target_freq=args.target_freq,
        target_db=args.target_db,
        steps=args.steps,
        span_pct=args.span_pct,
        values=args.values,
        allow_build_failure=args.allow_build_failure,
        execution_mode=f"cli_patch_param_sweep_{sweep_param}",
        sweep_param=sweep_param,
    )
    output_path = Path(args.output)
    json_output_path = Path(args.json_output) if args.json_output else None
    _write_text(output_path, build_patch_l_sweep_markdown(report))
    if json_output_path is not None:
        _write_text(json_output_path, json.dumps(report, ensure_ascii=False, indent=2))
    print(f"{sweep_param} sweep report written: {output_path}")
    if json_output_path is not None:
        print(f"{sweep_param} sweep JSON written: {json_output_path}")
    best = (report.get("best") or {}).get("by_target_s11") or {}
    print(f"Best target S11: {best.get('status_text', '-')}")
    return 0


def _run_patch_optimization_report(args: argparse.Namespace) -> int:
    from cst_agent_workbench.cst.controller import CSTController

    request = _patch_request_from_args(args)
    cst = CSTController()
    connect_result = cst.connect()
    if not connect_result.get("success"):
        raise SystemExit(f"CST 连接失败，无法生成真实闭环报告: {connect_result.get('message', '')}")

    report = _execute_patch_case(
        cst=cst,
        request=request,
        connect_result=connect_result,
        target_mode=args.target_mode,
        target_freq=args.target_freq,
        target_db=args.target_db,
        max_rounds=args.max_rounds,
        algorithm=args.algorithm,
        allow_build_failure=args.allow_build_failure,
        execution_mode="cli_patch_optimization_report",
    )
    output_path = Path(args.output)
    json_output_path = Path(args.json_output) if args.json_output else None
    _write_text(output_path, build_patch_optimization_markdown(report))
    if json_output_path is not None:
        _write_text(json_output_path, json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Closed-loop optimization report written: {output_path}")
    if json_output_path is not None:
        print(f"Closed-loop optimization JSON written: {json_output_path}")
    print(f"Final target status: {(report.get('final') or {}).get('check', {}).get('status_text', '-')}")
    return 0


def _case_metric(check: dict[str, Any], mode: str) -> float | None:
    value = check.get("at_f0_s11") if mode == "at_f0" else check.get("min_s11")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _case_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _check_data_success(check: dict[str, Any]) -> bool:
    if "data_success" in check:
        return bool(check.get("data_success"))
    return check.get("min_s11") is not None or check.get("at_f0_s11") is not None or bool(check.get("plot_point_count"))


def _matrix_case_summary(
    case: dict[str, Any],
    report_path: Path,
    report: dict[str, Any],
    report_json_path: Path | None = None,
) -> dict[str, Any]:
    baseline = report.get("baseline") or {}
    final = report.get("final") or {}
    baseline_check = baseline.get("check") or {}
    final_check = final.get("check") or {}
    target = report.get("target") or {}
    connect = report.get("connect") or {}
    build = report.get("build") or {}
    benchmark = report.get("benchmark") or {}
    mode = target.get("mode", "at_f0")
    baseline_metric = _case_metric(baseline_check, mode)
    final_metric = _case_metric(final_check, mode)
    rounds = report.get("rounds") or []
    target_met = bool(final.get("success"))
    return {
        "schema_version": report.get("schema_version", ""),
        "name": case["name"],
        "description": case.get("description", ""),
        "feed_strategy": (report.get("request") or {}).get("feed_strategy", ""),
        "f0_ghz": (report.get("request") or {}).get("f0_ghz"),
        "substrate_name": (report.get("request") or {}).get("substrate_name", ""),
        "epsilon_r": (report.get("request") or {}).get("epsilon_r"),
        "connect_success": bool(connect.get("success")),
        "connect_mode": connect.get("mode", "") or benchmark.get("cst_mode", ""),
        "build_success": bool(build.get("success")),
        "baseline_s11_success": _check_data_success(baseline_check),
        "final_s11_success": _check_data_success(final_check),
        "optimization_supported": bool(target.get("optimization_supported")),
        "optimization_attempted": bool(rounds),
        "target_met": target_met,
        "runtime_sec": benchmark.get("runtime_sec"),
        "git_sha": benchmark.get("git_sha", ""),
        "rounds": len(rounds),
        "rolled_back_rounds": sum(1 for item in rounds if item.get("rolled_back")),
        "llm_parse_errors": sum(1 for item in rounds if (item.get("memory_impact") or {}).get("llm_parse_error")),
        "baseline_metric_db": baseline_metric,
        "final_metric_db": final_metric,
        "improvement_db": round(baseline_metric - final_metric, 4) if baseline_metric is not None and final_metric is not None else None,
        "baseline_target_s11_db": _case_float(baseline_check.get("at_f0_s11")),
        "final_target_s11_db": _case_float(final_check.get("at_f0_s11")),
        "baseline_min_s11_db": _case_float(baseline_check.get("min_s11")),
        "final_min_s11_db": _case_float(final_check.get("min_s11")),
        "baseline_min_freq_ghz": _case_float(baseline_check.get("min_freq")),
        "final_min_freq_ghz": _case_float(final_check.get("min_freq")),
        "baseline_bandwidth_ghz": _case_float(baseline_check.get("bandwidth_ghz")),
        "final_bandwidth_ghz": _case_float(final_check.get("bandwidth_ghz")),
        "baseline_bandwidth_pct": _case_float(baseline_check.get("bandwidth_pct")),
        "final_bandwidth_pct": _case_float(final_check.get("bandwidth_pct")),
        "baseline_resonance_count": int(baseline_check.get("resonance_count", 0) or 0),
        "final_resonance_count": int(final_check.get("resonance_count", 0) or 0),
        "baseline_diagnosis": dict(baseline.get("diagnosis") or {}),
        "final_diagnosis": dict(final.get("diagnosis") or {}),
        "success": target_met,
        "report_path": str(report_path),
        "report_json_path": str(report_json_path or ""),
        "project_path": report.get("project_path", ""),
        "status_text": final_check.get("status_text", ""),
    }


def _matrix_status(value: Any) -> str:
    return "PASS" if value else "FAIL"


def _matrix_opt_status(case: dict[str, Any]) -> str:
    if not case.get("optimization_supported"):
        return "NA"
    return "YES" if case.get("optimization_attempted") else "NO"


def _matrix_db(value: Any) -> str:
    number = _case_float(value)
    return "-" if number is None else f"{number:.2f}"


def _matrix_freq(value: Any) -> str:
    number = _case_float(value)
    return "-" if number is None else f"{number:.3f}"


def _matrix_bandwidth(ghz: Any, pct: Any) -> str:
    ghz_number = _case_float(ghz)
    pct_number = _case_float(pct)
    if ghz_number is None or pct_number is None:
        return "bw=-"
    return f"bw={ghz_number:.4f}GHz/{pct_number:.2f}%"


def _matrix_rf_summary(case: dict[str, Any], prefix: str) -> str:
    return ", ".join(
        [
            f"t={_matrix_db(case.get(f'{prefix}_target_s11_db'))}",
            f"min={_matrix_db(case.get(f'{prefix}_min_s11_db'))}@{_matrix_freq(case.get(f'{prefix}_min_freq_ghz'))}",
            _matrix_bandwidth(case.get(f"{prefix}_bandwidth_ghz"), case.get(f"{prefix}_bandwidth_pct")),
        ]
    )


def _matrix_diagnosis_text(case: dict[str, Any]) -> str:
    diagnosis = case.get("final_diagnosis") or case.get("baseline_diagnosis") or {}
    if not diagnosis:
        return "-"
    shift = diagnosis.get("resonance_shift") or "-"
    family = diagnosis.get("recommended_parameter_family") or "-"
    return f"{shift} -> {family}"


def build_patch_matrix_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Real CST Patch Evaluation Matrix",
        "",
        f"- Timestamp UTC: {summary.get('timestamp_utc', '')}",
        f"- Case count: {len(summary.get('cases') or [])}",
        f"- Target: {summary.get('target_mode', 'at_f0')} <= {summary.get('target_db', -10.0)} dB",
        "",
        "| Case | Feed | f0 GHz | Material | Build | S11 data | Opt | Target | R/RB/PE | Baseline | Final | Diagnosis | Report |",
        "|---|---|---:|---|---|---|---|---|---:|---|---|---|---|",
    ]
    for case in summary.get("cases") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(case.get("name", "")),
                    str(case.get("feed_strategy", "")),
                    f"{float(case.get('f0_ghz') or 0):.3f}",
                    str(case.get("substrate_name", "")),
                    _matrix_status(case.get("build_success")),
                    f"{_matrix_status(case.get('baseline_s11_success'))}/{_matrix_status(case.get('final_s11_success'))}",
                    _matrix_opt_status(case),
                    _matrix_status(case.get("target_met")),
                    f"{case.get('rounds', 0)}/{case.get('rolled_back_rounds', 0)}/{case.get('llm_parse_errors', 0)}",
                    _matrix_rf_summary(case, "baseline"),
                    _matrix_rf_summary(case, "final"),
                    _matrix_diagnosis_text(case),
                    f"[{Path(case.get('report_path', '')).name}]({case.get('report_path', '')})",
                ]
            )
            + " |"
        )
    failures = [case for case in summary.get("cases") or [] if not case.get("target_met")]
    lines.extend(["", "## Failure cases", ""])
    if not failures:
        lines.append("All cases met the target.")
    else:
        for case in failures:
            stages = (
                f"build={_matrix_status(case.get('build_success'))}, "
                f"s11={_matrix_status(case.get('baseline_s11_success'))}/{_matrix_status(case.get('final_s11_success'))}, "
                f"target={_matrix_status(case.get('target_met'))}"
            )
            lines.append(f"- `{case['name']}` ({stages}): {case.get('status_text', 'unknown')}")
    return "\n".join(lines).rstrip() + "\n"


def _select_matrix_cases(case_names: str) -> list[dict[str, Any]]:
    if not case_names:
        return list(PATCH_MATRIX_CASES)
    requested = {name.strip() for name in case_names.split(",") if name.strip()}
    selected = [case for case in PATCH_MATRIX_CASES if case["name"] in requested]
    missing = requested - {case["name"] for case in selected}
    if missing:
        raise SystemExit(f"未知 matrix case: {', '.join(sorted(missing))}")
    return selected


def _run_patch_matrix(args: argparse.Namespace) -> int:
    from cst_agent_workbench.cst.controller import CSTController

    cases = _select_matrix_cases(args.cases)
    output_dir = Path(args.output_dir)
    cst = CSTController()
    connect_result = cst.connect()
    if not connect_result.get("success"):
        raise SystemExit(f"CST 连接失败，无法生成真实矩阵报告: {connect_result.get('message', '')}")

    summaries = []
    for case in cases:
        request = _matrix_case_request(case)
        case_rounds = int(case.get("max_rounds", args.microstrip_rounds if request.feed_strategy == "microstrip" else 0))
        report = _execute_patch_case(
            cst=cst,
            request=request,
            connect_result=connect_result,
            target_mode=args.target_mode,
            target_freq=args.target_freq,
            target_db=args.target_db,
            max_rounds=case_rounds,
            algorithm=args.algorithm,
            allow_build_failure=args.allow_build_failure,
            execution_mode=f"cli_patch_matrix_{case['name']}",
            command_name="patch-matrix",
            case_name=case["name"],
        )
        report_path = output_dir / f"{case['name']}.md"
        report_json_path = output_dir / f"{case['name']}.json"
        _write_text(report_path, build_patch_optimization_markdown(report))
        _write_text(report_json_path, json.dumps(report, ensure_ascii=False, indent=2))
        summaries.append(_matrix_case_summary(case, report_path, report, report_json_path))
        print(f"[{case['name']}] {summaries[-1]['status_text']} -> {report_path}")

    summary = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "target_mode": args.target_mode,
        "target_db": args.target_db,
        "cases": summaries,
    }
    summary_path = Path(args.summary)
    _write_text(summary_path, build_patch_matrix_markdown(summary))
    if args.json_output:
        _write_text(Path(args.json_output), json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Patch matrix summary written: {summary_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cst-agent-workbench")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_patch = subparsers.add_parser(
        "build-patch",
        help="生成 microstrip 矩形贴片 fast-path VBA artifact",
    )
    build_patch.add_argument("--f0", type=float, required=True, help="中心频率，单位 GHz")
    build_patch.add_argument("--er", type=float, required=True, help="基板介电常数")
    build_patch.add_argument("--h", type=float, required=True, help="基板厚度，单位 mm")
    build_patch.add_argument("--loss", type=float, default=0.0009, help="损耗角正切")
    build_patch.add_argument("--material", default="CustomSubstrate", help="基板材料名")
    build_patch.add_argument("--conductor", default="Copper (annealed)", help="导体材料名")
    build_patch.add_argument("--copper-t", type=float, default=0.035, help="导体厚度，单位 mm")
    build_patch.add_argument("--feed", choices=["microstrip", "probe"], default="microstrip", help="馈电方式")
    build_patch.add_argument("--dry-run", action="store_true", help="只生成 VBA，不连接或执行 CST")
    build_patch.add_argument("--export-vba", help="VBA 导出路径；默认写入 runs/<timestamp>/generated.vba")
    build_patch.set_defaults(func=_build_patch)

    optimize_report = subparsers.add_parser(
        "optimize-patch-report",
        help="运行真实 CST 矩形贴片建模+优化闭环并输出 Markdown 报告",
    )
    optimize_report.add_argument("--f0", type=float, required=True, help="中心频率，单位 GHz")
    optimize_report.add_argument("--er", type=float, required=True, help="基板介电常数")
    optimize_report.add_argument("--h", type=float, required=True, help="基板厚度，单位 mm")
    optimize_report.add_argument("--loss", type=float, default=0.0009, help="损耗角正切")
    optimize_report.add_argument("--material", default="CustomSubstrate", help="基板材料名")
    optimize_report.add_argument("--conductor", default="Copper (annealed)", help="导体材料名")
    optimize_report.add_argument("--copper-t", type=float, default=0.035, help="导体厚度，单位 mm")
    optimize_report.add_argument("--feed", choices=["microstrip", "probe"], default="microstrip", help="馈电方式")
    optimize_report.add_argument("--target-mode", choices=["at_f0", "min_s11"], default="at_f0", help="优化停止判据")
    optimize_report.add_argument("--target-freq", type=float, default=0.0, help="目标频率，默认使用模型 f0")
    optimize_report.add_argument("--target-db", type=float, default=-10.0, help="S11 达标阈值 dB")
    optimize_report.add_argument("--max-rounds", type=int, default=3, help="最多优化轮数")
    optimize_report.add_argument("--algorithm", default="auto", help="优化算法偏好，默认 auto")
    optimize_report.add_argument("--output", default=str(Path("runs") / "patch_optimization_report.md"), help="Markdown 报告输出路径")
    optimize_report.add_argument("--json-output", default="", help="可选 JSON 报告输出路径")
    optimize_report.add_argument("--allow-build-failure", action="store_true", help="即使首次建模/求解失败也写出报告")
    optimize_report.set_defaults(func=_run_patch_optimization_report)

    patch_l_sweep = subparsers.add_parser(
        "patch-l-sweep",
        help="运行真实 CST microstrip patch_L 单参数扫描并输出诊断报告",
    )
    patch_l_sweep.add_argument("--f0", type=float, required=True, help="中心频率，单位 GHz")
    patch_l_sweep.add_argument("--er", type=float, required=True, help="基板介电常数")
    patch_l_sweep.add_argument("--h", type=float, required=True, help="基板厚度，单位 mm")
    patch_l_sweep.add_argument("--loss", type=float, default=0.0009, help="损耗角正切")
    patch_l_sweep.add_argument("--material", default="CustomSubstrate", help="基板材料名")
    patch_l_sweep.add_argument("--conductor", default="Copper (annealed)", help="导体材料名")
    patch_l_sweep.add_argument("--copper-t", type=float, default=0.035, help="导体厚度，单位 mm")
    patch_l_sweep.add_argument("--feed", choices=["microstrip"], default="microstrip", help="馈电方式")
    patch_l_sweep.add_argument("--target-mode", choices=["at_f0", "min_s11"], default="at_f0", help="评估判据")
    patch_l_sweep.add_argument("--target-freq", type=float, default=0.0, help="目标频率，默认使用模型 f0")
    patch_l_sweep.add_argument("--target-db", type=float, default=-10.0, help="S11 达标阈值 dB")
    patch_l_sweep.add_argument("--steps", type=int, default=9, help="百分比扫描步数，默认 9")
    patch_l_sweep.add_argument("--span-pct", type=float, default=6.0, help="相对基线 patch_L 的半跨度百分比")
    patch_l_sweep.add_argument("--values", default="", help="可选，逗号分隔的 patch_L mm 显式扫描值")
    patch_l_sweep.add_argument("--output", default=str(Path("runs") / "patch_l_sweep_report.md"), help="Markdown 报告输出路径")
    patch_l_sweep.add_argument("--json-output", default=str(Path("runs") / "patch_l_sweep_report.json"), help="JSON 报告输出路径；传空字符串可关闭")
    patch_l_sweep.add_argument("--allow-build-failure", action="store_true", help="即使首次建模/求解失败也写出报告")
    patch_l_sweep.set_defaults(func=_run_patch_l_sweep, param="patch_L")

    patch_param_sweep = subparsers.add_parser(
        "patch-param-sweep",
        help="运行真实 CST microstrip 单参数扫描，可扫 patch_L/inset_depth/feed_W",
    )
    patch_param_sweep.add_argument("--param", choices=["patch_L", "inset_depth", "feed_W"], required=True, help="要扫描的参数")
    patch_param_sweep.add_argument("--f0", type=float, required=True, help="中心频率，单位 GHz")
    patch_param_sweep.add_argument("--er", type=float, required=True, help="基板介电常数")
    patch_param_sweep.add_argument("--h", type=float, required=True, help="基板厚度，单位 mm")
    patch_param_sweep.add_argument("--loss", type=float, default=0.0009, help="损耗角正切")
    patch_param_sweep.add_argument("--material", default="CustomSubstrate", help="基板材料名")
    patch_param_sweep.add_argument("--conductor", default="Copper (annealed)", help="导体材料名")
    patch_param_sweep.add_argument("--copper-t", type=float, default=0.035, help="导体厚度，单位 mm")
    patch_param_sweep.add_argument("--feed", choices=["microstrip"], default="microstrip", help="馈电方式")
    patch_param_sweep.add_argument("--target-mode", choices=["at_f0", "min_s11"], default="at_f0", help="评估判据")
    patch_param_sweep.add_argument("--target-freq", type=float, default=0.0, help="目标频率，默认使用模型 f0")
    patch_param_sweep.add_argument("--target-db", type=float, default=-10.0, help="S11 达标阈值 dB")
    patch_param_sweep.add_argument("--steps", type=int, default=7, help="百分比扫描步数，默认 7")
    patch_param_sweep.add_argument("--span-pct", type=float, default=30.0, help="相对基线参数的半跨度百分比")
    patch_param_sweep.add_argument("--values", default="", help="可选，逗号分隔的参数 mm 显式扫描值")
    patch_param_sweep.add_argument("--output", default=str(Path("runs") / "patch_param_sweep_report.md"), help="Markdown 报告输出路径")
    patch_param_sweep.add_argument("--json-output", default=str(Path("runs") / "patch_param_sweep_report.json"), help="JSON 报告输出路径；传空字符串可关闭")
    patch_param_sweep.add_argument("--allow-build-failure", action="store_true", help="即使首次建模/求解失败也写出报告")
    patch_param_sweep.set_defaults(func=_run_patch_l_sweep)

    patch_matrix = subparsers.add_parser(
        "patch-matrix",
        help="运行真实 CST 矩形贴片评测矩阵并输出汇总报告",
    )
    patch_matrix.add_argument("--cases", default="", help="逗号分隔 case 名；默认运行全部内置 case")
    patch_matrix.add_argument("--output-dir", default=str(Path("runs") / "patch_matrix"), help="单 case Markdown 报告输出目录")
    patch_matrix.add_argument("--summary", default=str(Path("runs") / "patch_matrix" / "summary.md"), help="矩阵 Markdown 汇总输出路径")
    patch_matrix.add_argument("--json-output", default="", help="可选 JSON 汇总输出路径")
    patch_matrix.add_argument("--microstrip-rounds", type=int, default=3, help="microstrip case 默认优化轮数；probe case 只跑基线")
    patch_matrix.add_argument("--target-mode", choices=["at_f0", "min_s11"], default="at_f0", help="优化停止判据")
    patch_matrix.add_argument("--target-freq", type=float, default=0.0, help="目标频率，默认使用每个 case 的 f0")
    patch_matrix.add_argument("--target-db", type=float, default=-10.0, help="S11 达标阈值 dB")
    patch_matrix.add_argument("--algorithm", default="auto", help="优化算法偏好，默认 auto")
    patch_matrix.add_argument("--allow-build-failure", action="store_true", help="即使某个 case 首次建模/求解失败也继续写出报告")
    patch_matrix.set_defaults(func=_run_patch_matrix)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
