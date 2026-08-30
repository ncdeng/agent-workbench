"""Apply response-hash-bound semantic judgments to a ToolUseMemory LLM pair report."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchmarks.eval_statistics import exact_paired_binary_test, wilson_interval


def _confusion(machine: list[bool], semantic: list[bool]) -> dict[str, Any]:
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


def _case_endpoint(samples: list[dict[str, Any]], endpoint: str) -> dict[str, Any]:
    by_case: dict[str, dict[str, list[bool]]] = {}
    for sample in samples:
        by_case.setdefault(str(sample["case_id"]), {}).setdefault(str(sample["arm"]), []).append(
            bool(sample["semantic_task_success"])
        )

    def reduce(values: list[bool]) -> bool:
        return all(values) if endpoint == "all_repeats" else sum(values) > len(values) / 2

    paired = [
        (reduce(arms["no_memory"]), reduce(arms["learned"]))
        for _, arms in sorted(by_case.items())
    ]
    cold = sum(left for left, _ in paired)
    learned = sum(right for _, right in paired)
    return {
        "endpoint": endpoint,
        "no_memory": wilson_interval(cold, len(paired)),
        "learned": wilson_interval(learned, len(paired)),
        "rate_delta": (learned - cold) / len(paired),
        "paired_test": exact_paired_binary_test(
            paired,
            baseline_label="no_memory",
            treatment_label="learned",
        ),
    }


def build_semantic_audit(report: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    if review.get("schema_version") != "tool-use-memory-pair-semantic-review-v1":
        raise ValueError("unsupported ToolUseMemory semantic review schema")
    identities = {
        "source_run_id": report["run"]["run_id"],
        "source_run_fingerprint": report["run"]["fingerprint"],
        "dataset_sha256": report["dataset"]["dataset_sha256"],
    }
    for field, expected in identities.items():
        if review.get(field) != expected:
            raise ValueError(f"ToolUseMemory semantic review {field} mismatch")
    report_samples = {str(sample["sample_id"]): sample for sample in report["samples"]}
    judgments = list(review.get("judgments") or [])
    by_sample = {str(item.get("sample_id") or ""): item for item in judgments}
    if len(by_sample) != len(judgments):
        raise ValueError("ToolUseMemory semantic review has duplicate sample IDs")
    if set(by_sample) != set(report_samples):
        raise ValueError("ToolUseMemory semantic review sample identity mismatch")

    audited: list[dict[str, Any]] = []
    machine: list[bool] = []
    semantic: list[bool] = []
    for sample_id, sample in report_samples.items():
        judgment = by_sample[sample_id]
        if judgment.get("response_sha256") != sample.get("final_response_sha256"):
            raise ValueError(f"response SHA mismatch for {sample_id}")
        task_success = judgment.get("semantic_task_success")
        grounded = judgment.get("grounded_final_response")
        if not isinstance(task_success, bool) or not isinstance(grounded, bool):
            raise ValueError(f"incomplete semantic judgment for {sample_id}")
        audited.append(
            {
                "sample_id": sample_id,
                "case_id": sample["case_id"],
                "arm": sample["arm"],
                "response_sha256": judgment["response_sha256"],
                "machine_task_success": bool(sample["task_success"]),
                "semantic_task_success": task_success,
                "grounded_final_response": grounded,
                "memory_order_changed": list(sample.get("presented_tool_order") or [])
                != list(sample.get("allowed_tools") or []),
                "notes": str(judgment.get("notes") or ""),
            }
        )
        machine.append(bool(sample["task_success"]))
        semantic.append(task_success)

    arms: dict[str, Any] = {}
    for arm in ("no_memory", "learned"):
        rows = [item for item in audited if item["arm"] == arm]
        successes = sum(item["semantic_task_success"] for item in rows)
        arms[arm] = {
            "samples": len(rows),
            "semantic_task_success": wilson_interval(successes, len(rows)),
            "grounded_final_response_rate": sum(item["grounded_final_response"] for item in rows)
            / len(rows),
            "memory_order_change_rate": sum(item["memory_order_changed"] for item in rows)
            / len(rows),
        }
    return {
        "schema_version": "tool-use-memory-pair-semantic-audit-v1",
        **identities,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "review_protocol": dict(review.get("review_protocol") or {}),
        "arms": arms,
        "paired_case_majority": _case_endpoint(audited, "majority"),
        "paired_case_all_repeats": _case_endpoint(audited, "all_repeats"),
        "machine_vs_semantic": _confusion(machine, semantic),
        "samples": audited,
        "limitations": [
            "This is a response-SHA-bound semantic audit, not a blinded or powered Memory benchmark.",
            "The bundled review is single-reviewer and developer-visible.",
            "A one-case semantic difference at repeat=1 is directional and must not be called a Memory gain.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit = build_semantic_audit(
        json.loads(args.report.read_text(encoding="utf-8")),
        json.loads(args.review.read_text(encoding="utf-8")),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"arms": audit["arms"], "paired": audit["paired_case_majority"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
