"""Validate and aggregate response-hash-bound semantic Agent E2E judgments."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


AUDIT_SCHEMA_VERSION = "agent-e2e-semantic-audit-v2"


def _file_identity(path: Path, payload: bytes) -> dict[str, Any]:
    return {
        "name": path.name,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }


def _validate_identity(identity: dict[str, Any], *, label: str) -> None:
    sha256 = identity.get("sha256")
    size_bytes = identity.get("size_bytes")
    if not isinstance(sha256, str) or len(sha256) != 64:
        raise ValueError(f"{label} identity has invalid sha256")
    if not isinstance(size_bytes, int) or size_bytes < 0:
        raise ValueError(f"{label} identity has invalid size_bytes")


def _validate_manifest_binding(
    report: dict[str, Any],
    manifest: dict[str, Any],
    manifest_identity: dict[str, Any],
) -> None:
    run = report["run"]
    if run.get("manifest_verified") is not True:
        raise ValueError("source report did not verify its manifest")
    if manifest_identity["sha256"] != run.get("manifest_sha256"):
        raise ValueError("manifest SHA does not match source report")
    for field in ("dataset_id", "dataset_sha256"):
        if manifest.get(field) != run.get(field):
            raise ValueError(f"manifest {field} does not match source report")

    report_case_order = list(
        dict.fromkeys(
            str(item["case_id"])
            for group in report["groups"].values()
            for item in group["cases"]
        )
    )
    report_case_set = set(report_case_order)
    manifest_case_order = [
        str(case_id)
        for case_id in manifest.get("case_ids") or []
        if str(case_id) in report_case_set
    ]
    if manifest_case_order != report_case_order:
        raise ValueError("source report cases do not preserve manifest order")


def _confusion(machine: list[bool], semantic: list[bool]) -> dict[str, Any]:
    if len(machine) != len(semantic):
        raise ValueError("machine and semantic verdict counts differ")
    tp = sum(left and right for left, right in zip(machine, semantic))
    fp = sum(left and not right for left, right in zip(machine, semantic))
    fn = sum(not left and right for left, right in zip(machine, semantic))
    tn = len(machine) - tp - fp - fn
    return {
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "true_negative": tn,
        "agreement_rate": (tp + tn) / len(machine) if machine else None,
    }


def score_semantic_review(report: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    """Score one response-bound review without making file-provenance claims."""
    if review.get("schema_version") != "agent-e2e-semantic-review-v1":
        raise ValueError("unsupported semantic review schema")
    for review_field, report_field in (
        ("source_run_id", "run_id"),
        ("dataset_id", "dataset_id"),
        ("dataset_sha256", "dataset_sha256"),
    ):
        if review.get(review_field) != report["run"].get(report_field):
            raise ValueError(f"semantic review {review_field} does not match report")

    report_samples = {
        str(item.get("sample_id") or item["case_id"]): item
        for group in report["groups"].values()
        for item in group["cases"]
    }
    judgments = list(review.get("judgments") or [])
    by_sample = {str(item.get("sample_id") or ""): item for item in judgments}
    if len(by_sample) != len(judgments):
        raise ValueError("semantic review contains duplicate sample_id values")
    if by_sample.keys() != report_samples.keys():
        missing = sorted(report_samples.keys() - by_sample.keys())
        extra = sorted(by_sample.keys() - report_samples.keys())
        raise ValueError(f"semantic review sample mismatch: missing={missing}, extra={extra}")

    audited_samples: list[dict[str, Any]] = []
    per_case: dict[str, list[bool]] = defaultdict(list)
    machine_task: list[bool] = []
    machine_strict: list[bool] = []
    semantic_task: list[bool] = []
    for sample_id, sample in report_samples.items():
        judgment = by_sample[sample_id]
        if judgment.get("response_sha256") != sample.get("final_response_sha256"):
            raise ValueError(f"response SHA mismatch for {sample_id}")
        execution = judgment.get("semantic_execution_success")
        grounded = judgment.get("grounded_final_response")
        if not isinstance(execution, bool) or not isinstance(grounded, bool):
            raise ValueError(f"semantic verdict is incomplete for {sample_id}")
        overall = execution and grounded
        audited_samples.append(
            {
                "sample_id": sample_id,
                "case_id": sample["case_id"],
                "response_sha256": judgment["response_sha256"],
                "semantic_execution_success": execution,
                "grounded_final_response": grounded,
                "semantic_task_success": overall,
                "notes": str(judgment.get("notes") or ""),
            }
        )
        per_case[str(sample["case_id"])].append(overall)
        machine_task.append(bool(sample["task_success"]))
        machine_strict.append(bool(sample["strict_grounded_success"]))
        semantic_task.append(overall)

    case_rows = []
    for case_id, verdicts in sorted(per_case.items()):
        successes = sum(verdicts)
        case_rows.append(
            {
                "case_id": case_id,
                "successes": successes,
                "samples": len(verdicts),
                "success_rate": successes / len(verdicts),
                "all_repeats_success": successes == len(verdicts),
                "majority_success": successes > len(verdicts) / 2,
                "unstable": 0 < successes < len(verdicts),
            }
        )

    return {
        "schema_version": "agent-e2e-semantic-score-v1",
        "source_run_id": report["run"]["run_id"],
        "dataset_id": report["run"]["dataset_id"],
        "dataset_sha256": report["run"]["dataset_sha256"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "review_protocol": review.get("review_protocol") or {},
        "metrics": {
            "sample_count": len(audited_samples),
            "semantic_execution_success_rate": sum(
                item["semantic_execution_success"] for item in audited_samples
            ) / len(audited_samples),
            "grounded_final_response_rate": sum(
                item["grounded_final_response"] for item in audited_samples
            ) / len(audited_samples),
            "semantic_task_success_rate": sum(semantic_task) / len(semantic_task),
            "case_all_repeats_success_rate": sum(
                item["all_repeats_success"] for item in case_rows
            ) / len(case_rows),
            "case_majority_success_rate": sum(item["majority_success"] for item in case_rows)
            / len(case_rows),
            "unstable_case_ids": [item["case_id"] for item in case_rows if item["unstable"]],
            "machine_task_vs_semantic": _confusion(machine_task, semantic_task),
            "machine_strict_vs_semantic": _confusion(machine_strict, semantic_task),
        },
        "per_case": case_rows,
        "samples": audited_samples,
        "limitations": [
            "This is a single-reviewer semantic audit, not a double-blind annotation study.",
            "The evaluated task set is developer-visible and contains only seven unique cases.",
            "Semantic review can expose grader error but does not turn the set into unseen generalization evidence.",
        ],
    }


def build_semantic_audit(
    report: dict[str, Any],
    review: dict[str, Any],
    *,
    source_report_identity: dict[str, Any],
    semantic_review_identity: dict[str, Any],
    manifest: dict[str, Any],
    manifest_identity: dict[str, Any],
    auditor_identity: dict[str, Any],
) -> dict[str, Any]:
    """Wrap semantic scores in a byte-bound, manifest-verified audit artifact."""
    for label, identity in (
        ("source report", source_report_identity),
        ("semantic review", semantic_review_identity),
        ("manifest", manifest_identity),
        ("auditor", auditor_identity),
    ):
        _validate_identity(identity, label=label)
    _validate_manifest_binding(report, manifest, manifest_identity)

    audit = score_semantic_review(report, review)
    audit["schema_version"] = AUDIT_SCHEMA_VERSION
    audit["provenance"] = {
        "source_report": source_report_identity,
        "semantic_review": semantic_review_identity,
        "manifest": manifest_identity,
        "auditor": auditor_identity,
        "manifest_case_order_verified": True,
    }
    return audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True, help="Source Agent E2E report JSON.")
    parser.add_argument("--review", type=Path, required=True, help="Response-SHA-bound review JSON.")
    parser.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help="Dataset manifest whose byte SHA must match the source report.",
    )
    parser.add_argument("--output", type=Path, required=True, help="Output semantic audit JSON.")
    args = parser.parse_args()
    report_bytes = args.report.read_bytes()
    review_bytes = args.review.read_bytes()
    manifest_bytes = args.manifest.read_bytes()
    auditor_path = Path(__file__).resolve()
    auditor_bytes = auditor_path.read_bytes()
    report = json.loads(report_bytes.decode("utf-8"))
    review = json.loads(review_bytes.decode("utf-8"))
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    audit = build_semantic_audit(
        report,
        review,
        source_report_identity=_file_identity(args.report, report_bytes),
        semantic_review_identity=_file_identity(args.review, review_bytes),
        manifest=manifest,
        manifest_identity=_file_identity(args.manifest, manifest_bytes),
        auditor_identity=_file_identity(auditor_path, auditor_bytes),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(audit["metrics"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
