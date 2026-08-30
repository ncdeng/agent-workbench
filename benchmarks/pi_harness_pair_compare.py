"""Build a SHA-bound Native/Pi paired Harness comparison.

The comparison is deterministic and performs no model or CST calls. Repeats
are treated as correlated observations: inferential endpoints are reduced to
one majority/all-repeats outcome per unique case before pairing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.agent_e2e_outcomes import summarize_outcomes
from benchmarks.eval_statistics import exact_paired_binary_test

SCHEMA_VERSION = "pi-harness-paired-comparison-v1"
SOURCE_SCHEMA_VERSION = "agent-e2e-report-v1"
ALIGNMENT_FIELDS = (
    "provider",
    "model",
    "dataset_id",
    "dataset_split",
    "dataset_role",
    "dataset_sha256",
    "manifest_sha256",
    "manifest_verified",
    "evaluation_scope",
    "code_revision",
    "case_count",
    "sample_count",
    "repeat_count",
    "filters",
)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_identity(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": _sha256_bytes(path.read_bytes())}


def _load_report(path: Path, expected_brain: str) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    report = json.loads(raw.decode("utf-8"))
    if not isinstance(report, dict):
        raise ValueError(f"{expected_brain} report must be a JSON object")
    if report.get("schema_version") != SOURCE_SCHEMA_VERSION:
        raise ValueError(
            f"{expected_brain} report schema_version must be {SOURCE_SCHEMA_VERSION!r}"
        )
    run = report.get("run")
    groups = report.get("groups")
    if not isinstance(run, dict) or not isinstance(groups, dict):
        raise ValueError(f"{expected_brain} report must contain run and groups objects")
    if run.get("agent_brain") != expected_brain:
        raise ValueError(f"expected {expected_brain} report, got {run.get('agent_brain')!r}")
    if run.get("manifest_verified") is not True:
        raise ValueError(f"{expected_brain} report must have manifest_verified=true")
    if "full" not in (run.get("groups") or []):
        raise ValueError(f"{expected_brain} report run.groups must include 'full'")
    full = groups.get("full")
    if not isinstance(full, dict) or not isinstance(full.get("cases"), list):
        raise ValueError(f"{expected_brain} report must contain groups.full.cases list")
    harness = run.get("harness_identity")
    if not isinstance(harness, dict) or harness.get("agent_brain") != expected_brain:
        raise ValueError(f"{expected_brain} harness identity does not match its arm")
    return report, raw


def _index_cases(
    report: Mapping[str, Any], arm: str
) -> tuple[dict[str, Mapping[str, Any]], dict[str, list[Mapping[str, Any]]], list[str]]:
    run = report["run"]
    cases = report["groups"]["full"]["cases"]
    by_sample: dict[str, Mapping[str, Any]] = {}
    by_case: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    case_order: list[str] = []
    structural_keys: set[tuple[str, int]] = set()

    for position, sample in enumerate(cases):
        if not isinstance(sample, Mapping):
            raise ValueError(f"{arm} sample at position {position} must be an object")
        sample_id = str(sample.get("sample_id") or "").strip()
        case_id = str(sample.get("case_id") or "").strip()
        run_index = sample.get("run_index")
        if not sample_id or not case_id or not isinstance(run_index, int) or run_index < 1:
            raise ValueError(f"{arm} sample at position {position} has invalid identity fields")
        if sample.get("valid") is not True:
            raise ValueError(f"{arm} sample {sample_id!r} is not score-valid")
        if sample_id in by_sample:
            raise ValueError(f"{arm} report contains duplicate sample_id {sample_id!r}")
        structural_key = (case_id, run_index)
        if structural_key in structural_keys:
            raise ValueError(f"{arm} report contains duplicate case/repeat {structural_key!r}")
        if case_id not in by_case:
            case_order.append(case_id)
        by_sample[sample_id] = sample
        by_case[case_id].append(sample)
        structural_keys.add(structural_key)

    declared_case_count = run.get("case_count")
    declared_sample_count = run.get("sample_count")
    repeat_count = run.get("repeat_count")
    if declared_case_count != len(by_case):
        raise ValueError(f"{arm} case_count does not match groups.full.cases")
    if declared_sample_count != len(by_sample):
        raise ValueError(f"{arm} sample_count does not match groups.full.cases")
    if not isinstance(repeat_count, int) or repeat_count < 1:
        raise ValueError(f"{arm} repeat_count must be a positive integer")
    expected_repeats = set(range(1, repeat_count + 1))
    for case_id, repeats in by_case.items():
        actual_repeats = {int(sample["run_index"]) for sample in repeats}
        if actual_repeats != expected_repeats:
            raise ValueError(
                f"{arm} case {case_id!r} repeat indexes {sorted(actual_repeats)!r} "
                f"do not match {sorted(expected_repeats)!r}"
            )
    declared_case_ids = (run.get("filters") or {}).get("case_ids")
    if declared_case_ids != case_order:
        raise ValueError(f"{arm} filters.case_ids must match report case order")
    return by_sample, by_case, case_order


def _validate_alignment(native: Mapping[str, Any], pi: Mapping[str, Any]) -> None:
    native_run = native["run"]
    pi_run = pi["run"]
    for field in ALIGNMENT_FIELDS:
        if native_run.get(field) != pi_run.get(field):
            raise ValueError(f"Native/Pi alignment mismatch for run.{field}")


def _endpoint(values: list[bool], reduction: str) -> bool:
    if not values:
        raise ValueError("cannot reduce an empty repeat set")
    if reduction == "majority":
        return sum(values) > len(values) / 2
    if reduction == "all_repeats":
        return all(values)
    raise ValueError(f"unsupported endpoint reduction: {reduction}")


def _paired_endpoint(
    native_by_case: Mapping[str, list[Mapping[str, Any]]],
    pi_by_case: Mapping[str, list[Mapping[str, Any]]],
    case_order: list[str],
    *,
    metric: str,
    reduction: str,
) -> dict[str, Any]:
    per_case: list[dict[str, Any]] = []
    pairs: list[tuple[bool, bool]] = []
    cells = {"both_pass": 0, "native_only": 0, "pi_only": 0, "both_fail": 0}
    for case_id in case_order:
        native_values = [
            bool(sample.get(metric))
            for sample in sorted(native_by_case[case_id], key=lambda item: item["run_index"])
        ]
        pi_values = [
            bool(sample.get(metric))
            for sample in sorted(pi_by_case[case_id], key=lambda item: item["run_index"])
        ]
        native_pass = _endpoint(native_values, reduction)
        pi_pass = _endpoint(pi_values, reduction)
        pairs.append((native_pass, pi_pass))
        if native_pass and pi_pass:
            cell = "both_pass"
        elif native_pass:
            cell = "native_only"
        elif pi_pass:
            cell = "pi_only"
        else:
            cell = "both_fail"
        cells[cell] += 1
        per_case.append(
            {
                "case_id": case_id,
                "native_repeats": native_values,
                "pi_repeats": pi_values,
                "native_pass": native_pass,
                "pi_pass": pi_pass,
                "cell": cell,
            }
        )

    case_count = len(case_order)
    test = exact_paired_binary_test(
        pairs, baseline_label="native", treatment_label="pi"
    )
    return {
        "metric": metric,
        "reduction": reduction,
        "unit": "independent_case",
        "unique_case_count": case_count,
        **cells,
        "native_successes": cells["both_pass"] + cells["native_only"],
        "pi_successes": cells["both_pass"] + cells["pi_only"],
        "paired_delta": (cells["pi_only"] - cells["native_only"]) / case_count,
        "paired_test": test,
        "per_case": per_case,
    }


def _source_identity(path: Path, raw: bytes, report: Mapping[str, Any]) -> dict[str, Any]:
    run = report["run"]
    return {
        "path": str(path.resolve()),
        "sha256": _sha256_bytes(raw),
        "schema_version": report.get("schema_version"),
        "run_id": run.get("run_id"),
        "agent_brain": run.get("agent_brain"),
        "provider": run.get("provider"),
        "model": run.get("model"),
        "code_revision": run.get("code_revision"),
        "harness_sha256": (run.get("harness_identity") or {}).get("combined_sha256"),
    }


def build_comparison(native_path: Path, pi_path: Path) -> dict[str, Any]:
    if native_path.resolve() == pi_path.resolve():
        raise ValueError("Native and Pi reports must be different files")
    native, native_raw = _load_report(native_path, "native")
    pi, pi_raw = _load_report(pi_path, "pi")
    _validate_alignment(native, pi)
    native_samples, native_cases, case_order = _index_cases(native, "native")
    pi_samples, pi_cases, pi_case_order = _index_cases(pi, "pi")
    if case_order != pi_case_order:
        raise ValueError("Native/Pi case order does not match")
    if set(native_samples) != set(pi_samples):
        raise ValueError("Native/Pi sample_id sets do not match")
    for sample_id in sorted(native_samples):
        left = native_samples[sample_id]
        right = pi_samples[sample_id]
        for field in ("case_id", "run_index", "seed"):
            if left.get(field) != right.get(field):
                raise ValueError(f"Native/Pi sample {sample_id!r} mismatch for {field}")

    endpoints = {
        f"{metric}_{reduction}": _paired_endpoint(
            native_cases,
            pi_cases,
            case_order,
            metric=metric,
            reduction=reduction,
        )
        for metric in ("task_success", "strict_grounded_success")
        for reduction in ("majority", "all_repeats")
    }
    comparator_path = Path(__file__)
    statistics_path = comparator_path.with_name("eval_statistics.py")
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "execution": {
            "new_model_calls": 0,
            "new_cst_calls": 0,
            "method": "deterministic_sha_bound_paired_comparison",
        },
        "sources": {
            "native": _source_identity(native_path, native_raw, native),
            "pi": _source_identity(pi_path, pi_raw, pi),
        },
        "alignment": {
            "provider": native["run"].get("provider"),
            "model": native["run"].get("model"),
            "dataset_id": native["run"].get("dataset_id"),
            "dataset_sha256": native["run"].get("dataset_sha256"),
            "manifest_sha256": native["run"].get("manifest_sha256"),
            "evaluation_scope": native["run"].get("evaluation_scope"),
            "code_revision": native["run"].get("code_revision"),
            "case_count": native["run"].get("case_count"),
            "sample_count": native["run"].get("sample_count"),
            "repeat_count": native["run"].get("repeat_count"),
            "case_ids": case_order,
        },
        "provider_outcomes": {
            "native": summarize_outcomes(native["groups"]["full"]["cases"]),
            "pi": summarize_outcomes(pi["groups"]["full"]["cases"]),
        },
        "endpoints": endpoints,
        "implementation": {
            "comparator": _file_identity(comparator_path),
            "statistics": _file_identity(statistics_path),
        },
        "limitations": [
            "Primary inferential units are unique cases; repeated model runs are correlated observations, not independent tasks.",
            "The source dataset is developer-visible and post-audit, not blinded or sealed.",
            "Provider sampling was not seeded, so matching local seeds do not make model generations deterministic.",
            "This comparison supports attribution to the recorded source reports only; it does not establish general Native/Pi superiority.",
        ],
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    alignment = report["alignment"]
    sources = report["sources"]
    lines = [
        "# Native vs Pi Harness Paired Comparison",
        "",
        f"- Model: `{alignment['model']}`",
        f"- Dataset: `{alignment['dataset_id']}`",
        f"- Unique cases / repeats: `{alignment['case_count']}` / `{alignment['repeat_count']}`",
        f"- Native source SHA-256: `{sources['native']['sha256']}`",
        f"- Pi source SHA-256: `{sources['pi']['sha256']}`",
        "- New model calls: `0`",
        "",
        "| Endpoint | Native | Pi | Both pass | Native only | Pi only | Both fail | Delta | p |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for endpoint in report["endpoints"].values():
        test = endpoint["paired_test"]
        lines.append(
            "| {metric} ({reduction}) | {native}/{total} | {pi}/{total} | {both} | "
            "{native_only} | {pi_only} | {both_fail} | {delta:+.3f} | {p:.3f} |".format(
                metric=endpoint["metric"],
                reduction=endpoint["reduction"],
                native=endpoint["native_successes"],
                pi=endpoint["pi_successes"],
                total=endpoint["unique_case_count"],
                both=endpoint["both_pass"],
                native_only=endpoint["native_only"],
                pi_only=endpoint["pi_only"],
                both_fail=endpoint["both_fail"],
                delta=endpoint["paired_delta"],
                p=test["p_value"],
            )
        )
    lines.extend(["", "## Provider outcomes", ""])
    for arm in ("native", "pi"):
        metrics = report["provider_outcomes"][arm]
        lines.append(
            f"- {arm}: provider failures `{metrics['provider_failure_count']}` / "
            f"`{metrics['total_sample_count']}`; degraded-but-eligible "
            f"`{metrics['provider_degraded_eligible_count']}`."
        )
    lines.extend(["", "## Interpretation limits", ""])
    lines.extend(f"- {item}" for item in report["limitations"])
    lines.append("")
    return "\n".join(lines)


def _write_new(path: Path, content: str) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", required=True, type=Path)
    parser.add_argument("--pi", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--summary-md", type=Path)
    args = parser.parse_args()

    report = build_comparison(args.native, args.pi)
    _write_new(args.output, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if args.summary_md:
        _write_new(args.summary_md, render_markdown(report))
    print(json.dumps(report["endpoints"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
