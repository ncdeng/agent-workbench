from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from cst_agent_workbench.optimization.diagnosis import diagnose_s11
from cst_agent_workbench.results.summary import evaluate_optimization_target


PATCH_BENCHMARK_SCHEMA_VERSION = "patch_benchmark_v2"


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_s11_check(
    s11_result: dict[str, Any],
    *,
    mode: str,
    target_db: float,
    effective_target_freq: float,
    configured_target_freq: float = 0.0,
) -> dict[str, Any]:
    evaluation = evaluate_optimization_target(
        s11_result or {},
        mode=mode,
        target_db=target_db,
        effective_target_freq=effective_target_freq,
        configured_target_freq=configured_target_freq,
    )
    summary = evaluation.summary.to_dict()
    check = evaluation.to_dict()
    check["data_success"] = bool(evaluation.summary.success)
    check["plot_data"] = list(summary.get("plot_data") or [])
    check["plot_point_count"] = len(check["plot_data"])
    check["target_freq_ghz"] = summary.get("target_freq_ghz")
    check["bandwidth_ghz"] = summary.get("bandwidth_ghz")
    check["bandwidth_pct"] = summary.get("bandwidth_pct")
    check["primary_band"] = summary.get("primary_band")
    check["resonances"] = list(summary.get("resonances") or [])
    check["resonance_count"] = len(check["resonances"])
    return check


def build_round_record(
    *,
    round_index: int,
    optimizer_result: dict[str, Any],
    check: dict[str, Any],
    param_snapshot: dict[str, Any],
    memory_recall: list[dict[str, Any]],
    memory_impact: dict[str, Any],
    tool_events: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "round": round_index,
        "success": bool(optimizer_result.get("success")),
        "strategy": optimizer_result.get("strategy", ""),
        "proposal_reason": optimizer_result.get("proposal_reason", ""),
        "diagnosis": dict(optimizer_result.get("diagnosis") or {}),
        "attempted_check": dict(optimizer_result.get("attempted_check") or {}),
        "message": optimizer_result.get("message", ""),
        "changed_params": dict(optimizer_result.get("changed_params") or {}),
        "rolled_back": bool(optimizer_result.get("rolled_back")),
        "rollback_reason": optimizer_result.get("rollback_reason", ""),
        "check": dict(check or {}),
        "param_snapshot": dict(param_snapshot or {}),
        "memory_recall": list(memory_recall or []),
        "memory_impact": dict(memory_impact or {}),
        "tool_events": list(tool_events or []),
    }


def build_patch_optimization_report(
    *,
    request: dict[str, Any],
    target: dict[str, Any],
    connect_result: dict[str, Any],
    project_path: str,
    build_message: str,
    build_success: bool,
    baseline_check: dict[str, Any],
    baseline_params: dict[str, Any],
    rounds: list[dict[str, Any]],
    final_check: dict[str, Any],
    token_stats: dict[str, Any] | None = None,
    timestamp_utc: str | None = None,
    benchmark: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": PATCH_BENCHMARK_SCHEMA_VERSION,
        "timestamp_utc": timestamp_utc or utc_timestamp(),
        "benchmark": dict(benchmark or {}),
        "request": dict(request),
        "target": dict(target),
        "connect": dict(connect_result or {}),
        "project_path": project_path,
        "build": {
            "success": bool(build_success),
            "message": build_message,
        },
        "baseline": {
            "check": dict(baseline_check or {}),
            "param_snapshot": dict(baseline_params or {}),
            "diagnosis": _diagnosis_dict(baseline_check, target),
        },
        "rounds": list(rounds or []),
        "final": {
            "check": dict(final_check or {}),
            "diagnosis": _diagnosis_dict(final_check, target),
            "success": bool((final_check or {}).get("met")),
        },
        "token_stats": dict(token_stats or {}),
    }


def build_patch_optimization_markdown(report: dict[str, Any]) -> str:
    target = report.get("target") or {}
    request = report.get("request") or {}
    connect = report.get("connect") or {}
    build = report.get("build") or {}
    baseline = report.get("baseline") or {}
    final = report.get("final") or {}
    rounds = report.get("rounds") or []
    benchmark = report.get("benchmark") or {}

    lines = [
        "# CST Patch Optimization Closed-Loop Report",
        "",
        f"- Timestamp UTC: {report.get('timestamp_utc', '')}",
        f"- Project: `{report.get('project_path', '') or 'unknown'}`",
        f"- Target: {_target_summary(target)}",
        f"- CST connection: {_status(connect.get('success'))} — {connect.get('message', '')}",
        f"- Build: {_status(build.get('success'))}",
    ]
    if report.get("schema_version") or benchmark:
        lines.extend(
            [
                "",
                "## Benchmark evidence",
                "",
                f"- Schema: `{report.get('schema_version', '')}`",
                f"- Command: `{benchmark.get('command', '')}`",
                f"- Case: `{benchmark.get('case_name', '')}`",
                f"- Execution mode: `{benchmark.get('execution_mode', '')}`",
                f"- Git SHA: `{benchmark.get('git_sha', '')}`",
                f"- CST mode: `{benchmark.get('cst_mode', '')}`",
                f"- Runtime: {benchmark.get('runtime_sec', '-')} s",
                f"- Executed rounds: {benchmark.get('executed_rounds', 0)}",
                f"- Optimization supported: {'yes' if benchmark.get('optimization_supported') else 'no'}",
            ]
        )
    lines.extend([
        "",
        "## Patch request",
        "",
        "| Field | Value |",
        "|---|---:|",
    ])
    for key in [
        "f0_ghz",
        "substrate_name",
        "epsilon_r",
        "loss_tangent",
        "substrate_thickness_mm",
        "conductor_name",
        "conductor_thickness_mm",
        "feed_strategy",
    ]:
        lines.append(f"| {key} | {_cell(request.get(key, ''))} |")

    lines.extend(
        [
            "",
            "## Baseline",
            "",
            _check_bullet_list(baseline.get("check") or {}),
            "",
            _diagnosis_bullet_list(baseline.get("diagnosis") or {}),
            "",
            "### Baseline parameters",
            "",
            _params_table(baseline.get("param_snapshot") or {}),
            "",
        ]
    )

    if build.get("message"):
        lines.extend(["## Build log excerpt", "", _quote_block(str(build.get("message", "")), max_lines=12), ""])

    lines.extend(["## Optimization rounds", ""])
    if not rounds:
        lines.append("No optimization round was executed.")
    for round_record in rounds:
        lines.extend(_round_markdown(round_record))

    lines.extend(
        [
            "",
            "## Final result",
            "",
            _check_bullet_list((final.get("check") or {})),
            "",
            "## Token usage",
            "",
            _token_summary(report.get("token_stats") or {}),
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def build_patch_l_sweep_markdown(report: dict[str, Any]) -> str:
    target = report.get("target") or {}
    request = report.get("request") or {}
    connect = report.get("connect") or {}
    build = report.get("build") or {}
    baseline = report.get("baseline") or {}
    sweep = report.get("sweep") or {}
    entries = sweep.get("entries") or []
    best = report.get("best") or {}
    param_name = sweep.get("parameter") or "patch_L"

    lines = [
        f"# CST {param_name} Sweep Report",
        "",
        f"- Timestamp UTC: {report.get('timestamp_utc', '')}",
        f"- Project: `{report.get('project_path', '') or 'unknown'}`",
        f"- Target: {_target_summary(target)}",
        f"- CST connection: {_status(connect.get('success'))} — {connect.get('message', '')}",
        f"- Build: {_status(build.get('success'))}",
        f"- Sweep parameter: `{param_name}`",
        f"- Sweep center: {_mm(sweep.get('center_value_mm') if sweep.get('center_value_mm') is not None else sweep.get('center_patch_l_mm'))}",
        f"- Sweep span: {_percent(sweep.get('span_pct'))}",
        f"- Sweep values: {len(entries)}",
        "",
        "## Patch request",
        "",
        "| Field | Value |",
        "|---|---:|",
    ]
    for key in [
        "f0_ghz",
        "substrate_name",
        "epsilon_r",
        "loss_tangent",
        "substrate_thickness_mm",
        "conductor_name",
        "conductor_thickness_mm",
        "feed_strategy",
    ]:
        lines.append(f"| {key} | {_cell(request.get(key, ''))} |")

    lines.extend(
        [
            "",
            "## Baseline",
            "",
            f"- Baseline `{param_name}`: {_mm(baseline.get('value_mm') if baseline.get('value_mm') is not None else baseline.get('patch_l_mm'))}",
            _check_bullet_list(baseline.get("check") or {}),
            "",
        ]
    )
    if build.get("message"):
        lines.extend(["## Build log excerpt", "", _quote_block(str(build.get("message", "")), max_lines=10), ""])

    lines.extend(
        [
            "## Sweep results",
            "",
            f"| # | {param_name} mm | Δ mm | Δ % | S11@target | Min S11 | Nearest resonance | Status |",
            "|---:|---:|---:|---:|---:|---:|---|---|",
        ]
    )
    for entry in entries:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(entry.get("index", "")),
                    _number(entry.get("value_mm") if entry.get("value_mm") is not None else entry.get("patch_l_mm")),
                    _signed_number(entry.get("delta_mm")),
                    _signed_percent(entry.get("delta_pct")),
                    _db(entry.get("target_s11_db")),
                    f"{_db(entry.get('min_s11_db'))} @ {_freq(entry.get('min_freq_ghz'))}",
                    _nearest_resonance_text(entry.get("nearest_resonance") or {}),
                    _cell(entry.get("status_text", "-")),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Best candidates",
            "",
            _best_sweep_line("Best target-frequency S11", best.get("by_target_s11") or {}),
            _best_sweep_line("Closest target-neighborhood resonance", best.get("by_nearest_resonance") or {}),
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _round_markdown(round_record: dict[str, Any]) -> list[str]:
    check = round_record.get("check") or {}
    impact = round_record.get("memory_impact") or {}
    recall = round_record.get("memory_recall") or []
    before = impact.get("proposal_before_validation") or {}
    after = impact.get("proposal_after_validation") or {}
    events = round_record.get("tool_events") or []
    diagnosis = round_record.get("diagnosis") or {}
    diagnosis_text = _inline_diagnosis(diagnosis)
    attempted_check = round_record.get("attempted_check") or {}
    attempted_text = _inline_check(attempted_check) if attempted_check else "-"
    lines = [
        f"### Round {round_record.get('round', '')}",
        "",
        f"- Optimizer success: {_status(round_record.get('success'))}",
        f"- Strategy: `{round_record.get('strategy', '') or 'unknown'}`",
        f"- Changed params: {_changed_params(round_record.get('changed_params') or {})}",
        f"- Proposal reason: {round_record.get('proposal_reason', '') or '-'}",
        f"- Diagnosis: {diagnosis_text}",
        f"- Attempted result before rollback: {attempted_text}",
        f"- Result: {_inline_check(check)}",
        f"- Rolled back: {'yes' if round_record.get('rolled_back') else 'no'}{_reason_suffix(round_record.get('rollback_reason'))}",
        f"- Memory recalled: {'yes' if recall else 'no'}",
        f"- Memory constraints: {_list_inline(impact.get('memory_constraints') or [])}",
        f"- Proposal before validation: {_proposal_summary(before)}",
        f"- Proposal after validation: {_proposal_summary(after)}",
        f"- LLM parse error: {impact.get('llm_parse_error') or 'none'}",
        f"- Validator enforced memory: {'yes' if impact.get('memory_enforced_by_validator') else 'no'}",
        f"- Memory violation: {'yes' if impact.get('memory_violation') else 'no'}{_reason_suffix(impact.get('memory_violation_reason'))}",
    ]
    if impact.get("llm_raw_excerpt"):
        lines.extend(["", "LLM raw excerpt:", _quote_block(str(impact.get("llm_raw_excerpt", "")), max_lines=6)])
    if recall:
        lines.extend(["", "Recalled memory:"])
        for entry in recall[:5]:
            text = entry.get("text") if isinstance(entry, dict) else str(entry)
            lines.append(f"- {text}")
    if events:
        lines.extend(["", "Tool events:", "", "| Phase | Tool | Status | Description |", "|---|---|---|---|"])
        for event in events[-8:]:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _cell(event.get("phase", "")),
                        _cell(event.get("tool_name", "")),
                        _status(event.get("success")),
                        _cell(event.get("description", "")),
                    ]
                )
                + " |"
            )
    lines.append("")
    return lines


def _target_summary(target: dict[str, Any]) -> str:
    mode = target.get("mode", "")
    target_db = target.get("target_db", "")
    freq = target.get("effective_target_freq_ghz", "")
    if mode == "at_f0":
        return f"S11@{freq} GHz <= {target_db} dB"
    return f"min S11 <= {target_db} dB"


def _diagnosis_dict(check: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    if not check:
        return {}
    summary = {
        "min_freq": check.get("min_freq"),
        "min_s11": check.get("min_s11"),
        "at_f0_s11": check.get("at_f0_s11"),
        "resonances": list(check.get("resonances") or []),
    }
    return diagnose_s11(summary, target).to_dict()


def _diagnosis_bullet_list(diagnosis: dict[str, Any]) -> str:
    if not diagnosis:
        return "- Diagnosis: -"
    return "\n".join(
        [
            f"- Diagnosis: {diagnosis.get('resonance_shift', '-')} / {diagnosis.get('matching_quality', '-')}",
            f"- Recommended parameter family: {diagnosis.get('recommended_parameter_family', '-')}",
            f"- Diagnosis reason: {diagnosis.get('reason', '-')}",
        ]
    )


def _inline_diagnosis(diagnosis: dict[str, Any]) -> str:
    if not diagnosis:
        return "-"
    family = diagnosis.get("recommended_parameter_family", "-")
    shift = diagnosis.get("resonance_shift", "-")
    reason = diagnosis.get("reason", "-")
    return f"{shift} -> {family}; {reason}"


def _check_bullet_list(check: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"- Criteria: {check.get('criteria_text', '-')}",
            f"- Status: {check.get('status_text', '-')}",
            f"- Minimum S11: {_db(check.get('min_s11'))} @ {_freq(check.get('min_freq'))}",
            f"- Target-frequency S11: {_db(check.get('at_f0_s11'))}",
            f"- Resonances: {_resonance_summary(check)}",
            f"- -10 dB bandwidth: {_bandwidth(check)}",
            f"- Plot points: {check.get('plot_point_count', 0)}",
        ]
    )


def _inline_check(check: dict[str, Any]) -> str:
    status = check.get("status_text") or "-"
    min_s11 = _db(check.get("min_s11"))
    min_freq = _freq(check.get("min_freq"))
    at_f0 = _db(check.get("at_f0_s11"))
    resonances = _resonance_summary(check)
    return f"{status}; min={min_s11}@{min_freq}; target={at_f0}; resonances={resonances}"


def _resonance_summary(check: dict[str, Any]) -> str:
    resonances = check.get("resonances") or []
    if not resonances:
        return "-"
    parts = []
    for resonance in resonances[:4]:
        freq = resonance.get("freq_ghz")
        depth = resonance.get("depth_db")
        if freq is not None and depth is not None:
            parts.append(f"{float(freq):.4f} GHz/{float(depth):.2f} dB")
    return "; ".join(parts) if parts else "-"


def _params_table(params: dict[str, Any]) -> str:
    if not params:
        return "No parameter snapshot available."
    lines = ["| Parameter | Value |", "|---|---:|"]
    for key in sorted(params):
        lines.append(f"| {key} | {_cell(params[key])} |")
    return "\n".join(lines)


def _changed_params(changed: dict[str, Any]) -> str:
    if not changed:
        return "none"
    parts = []
    for name, meta in changed.items():
        if isinstance(meta, dict):
            parts.append(f"`{name}` {meta.get('old', '?')} → {meta.get('new', '?')}")
        else:
            parts.append(f"`{name}` {meta}")
    return ", ".join(parts)


def _proposal_summary(proposal: dict[str, Any]) -> str:
    if not proposal:
        return "-"
    param = proposal.get("param", "")
    delta = proposal.get("delta_mm", "")
    reason = proposal.get("reason", "")
    source = proposal.get("source", "")
    prefix = f"`{param}` {delta:+.4f} mm" if isinstance(delta, (int, float)) else f"`{param}` {delta} mm"
    suffix = f" ({source})" if source else ""
    return f"{prefix}{suffix}: {reason or '-'}"


def _token_summary(token_stats: dict[str, Any]) -> str:
    if not token_stats:
        return "No token usage recorded."
    return "\n".join(
        [
            f"- Prompt tokens: {token_stats.get('prompt', 0)}",
            f"- Completion tokens: {token_stats.get('completion', 0)}",
            f"- Calls: {token_stats.get('calls', 0)}",
        ]
    )


def _quote_block(text: str, *, max_lines: int) -> str:
    lines = text.splitlines()[:max_lines]
    if len(text.splitlines()) > max_lines:
        lines.append("...")
    return "\n".join(f"> {line}" for line in lines)


def _bandwidth(check: dict[str, Any]) -> str:
    bw = check.get("bandwidth_ghz")
    pct = check.get("bandwidth_pct")
    if bw is None:
        return "-"
    return f"{bw:.4f} GHz ({pct:.2f}%)" if pct is not None else f"{bw:.4f} GHz"


def _mm(value: Any) -> str:
    return f"{float(value):.4f} mm" if value is not None else "-"


def _number(value: Any) -> str:
    return f"{float(value):.4f}" if value is not None else "-"


def _signed_number(value: Any) -> str:
    return f"{float(value):+.4f}" if value is not None else "-"


def _percent(value: Any) -> str:
    return f"{float(value):.2f}%" if value is not None else "-"


def _signed_percent(value: Any) -> str:
    return f"{float(value):+.2f}%" if value is not None else "-"


def _nearest_resonance_text(resonance: dict[str, Any]) -> str:
    if not resonance:
        return "-"
    freq = resonance.get("freq_ghz")
    depth = resonance.get("depth_db")
    error = resonance.get("frequency_error_ghz")
    if freq is None or depth is None:
        return "-"
    suffix = f", err={float(error):+.4f} GHz" if error is not None else ""
    return f"{float(freq):.4f} GHz/{float(depth):.2f} dB{suffix}"


def _best_sweep_line(label: str, entry: dict[str, Any]) -> str:
    if not entry:
        return f"- {label}: -"
    param_name = entry.get("parameter") or "patch_L"
    value = entry.get("value_mm") if entry.get("value_mm") is not None else entry.get("patch_l_mm")
    return (
        f"- {label}: {param_name}={_mm(value)}, "
        f"S11@target={_db(entry.get('target_s11_db'))}, "
        f"nearest={_nearest_resonance_text(entry.get('nearest_resonance') or {})}, "
        f"status={entry.get('status_text', '-')}"
    )


def _list_inline(items: list[Any]) -> str:
    if not items:
        return "none"
    return "; ".join(str(item) for item in items[:3])


def _reason_suffix(reason: Any) -> str:
    return f" — {reason}" if reason else ""


def _status(value: Any) -> str:
    return "PASS" if bool(value) else "FAIL"


def _db(value: Any) -> str:
    return f"{float(value):.2f} dB" if value is not None else "-"


def _freq(value: Any) -> str:
    return f"{float(value):.4f} GHz" if value is not None else "-"


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", "<br>")
