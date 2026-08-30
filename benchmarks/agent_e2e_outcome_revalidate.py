"""Revalidate Agent E2E reports with infrastructure-aware outcome metrics.

This command performs no model or CST calls.  It preserves the raw runner
report and writes a SHA-bound derived artifact that separates Agent capability
from provider availability.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.agent_e2e_outcomes import classify_sample, summarize_outcomes

SCHEMA_VERSION = "agent-e2e-outcome-revalidation-v1"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_identity(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": _sha256_bytes(path.read_bytes())}


def _case_summary(case: Mapping[str, Any]) -> dict[str, Any]:
    classified = classify_sample(case)
    return {
        "case_id": str(case.get("case_id") or ""),
        "sample_id": str(case.get("sample_id") or ""),
        "raw_task_success": bool(case.get("task_success")),
        "raw_strict_grounded_success": bool(case.get("strict_grounded_success")),
        "outcome": classified.outcome.value,
        "eligible_for_agent_metrics": classified.eligible_for_agent_metrics,
        "provider_degraded": classified.provider_degraded,
        "partial_execution": classified.partial_execution,
        "provider_errors": [error.to_dict() for error in classified.provider_errors],
    }


def build_revalidation(source_path: Path) -> dict[str, Any]:
    source_bytes = source_path.read_bytes()
    source = json.loads(source_bytes.decode("utf-8"))
    if not isinstance(source, dict) or not isinstance(source.get("groups"), dict):
        raise ValueError("source report must contain a groups object")

    groups: dict[str, Any] = {}
    for group_name, group in source["groups"].items():
        if not isinstance(group, dict):
            continue
        cases = group.get("cases") or []
        if not isinstance(cases, list):
            raise ValueError(f"group {group_name!r} cases must be a list")
        groups[str(group_name)] = {
            "metrics": summarize_outcomes(cases),
            "cases": [_case_summary(case) for case in cases if isinstance(case, Mapping)],
        }

    classifier_path = Path(__file__).with_name("agent_e2e_outcomes.py")
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "execution": {
            "new_model_calls": 0,
            "new_cst_calls": 0,
            "method": "deterministic_post_hoc_revalidation",
        },
        "source_report": {
            "path": str(source_path.resolve()),
            "sha256": _sha256_bytes(source_bytes),
            "schema_version": source.get("schema_version"),
            "run_id": (source.get("run") or {}).get("run_id"),
            "agent_brain": (source.get("run") or {}).get("agent_brain"),
            "provider": (source.get("run") or {}).get("provider"),
            "model": (source.get("run") or {}).get("model"),
        },
        "implementation": {
            "revalidator": _file_identity(Path(__file__)),
            "classifier": _file_identity(classifier_path),
        },
        "groups": groups,
        "interpretation": {
            "eligible_task_success_rate": "Agent capability over samples not terminated by provider failure.",
            "provider_availability_rate": "Fraction of samples not terminated by provider failure.",
            "end_to_end_task_success_rate": "Raw user-visible task success over all attempted samples.",
            "provider_degraded_eligible_count": "Eligible samples that observed a provider error but still completed.",
        },
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    source = report["source_report"]
    lines = [
        "# Agent E2E Infrastructure-Aware Revalidation",
        "",
        f"- Source run: `{source.get('run_id') or 'unknown'}`",
        f"- Agent brain: `{source.get('agent_brain') or 'unknown'}`",
        f"- Provider/model: `{source.get('provider') or 'unknown'}` / `{source.get('model') or 'unknown'}`",
        f"- Source SHA-256: `{source['sha256']}`",
        "- New model calls: `0`",
        "",
        "| Group | Eligible | Agent success | Agent failure | Provider failure | Eligible success | Provider availability | E2E success |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for group_name, group in report["groups"].items():
        metrics = group["metrics"]

        def percent(value: float | None) -> str:
            return "n/a" if value is None else f"{value:.1%}"

        lines.append(
            "| {group} | {eligible}/{total} | {success} | {failure} | {provider_failure} | "
            "{eligible_rate} | {availability} | {e2e} |".format(
                group=group_name,
                eligible=metrics["eligible_sample_count"],
                total=metrics["total_sample_count"],
                success=metrics["agent_success_count"],
                failure=metrics["agent_failure_count"],
                provider_failure=metrics["provider_failure_count"],
                eligible_rate=percent(metrics["eligible_task_success_rate"]),
                availability=percent(metrics["provider_availability_rate"]),
                e2e=percent(metrics["end_to_end_task_success_rate"]),
            )
        )
    lines.extend(
        [
            "",
            "> Provider failures are excluded only from Agent-capability denominators. They remain visible in provider availability and end-to-end success.",
            "",
        ]
    )
    return "\n".join(lines)


def _write_new(path: Path, content: str) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--summary-md", type=Path)
    args = parser.parse_args()

    report = build_revalidation(args.source)
    _write_new(args.output, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if args.summary_md:
        _write_new(args.summary_md, render_markdown(report))
    print(json.dumps({name: value["metrics"] for name, value in report["groups"].items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
