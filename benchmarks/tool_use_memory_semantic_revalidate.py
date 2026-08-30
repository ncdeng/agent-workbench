"""Recompute deterministic ToolUseMemory semantic endpoints without model calls."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchmarks import tool_use_memory_llm_pair_runner as pair_runner
from benchmarks import tool_use_memory_semantic_grader as semantic_grader


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_json(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return _sha256_bytes(encoded)


def _completion_evidence(sample: dict[str, Any]) -> tuple[bool | None, str]:
    explicit = sample.get("agent_completion_ok")
    if isinstance(explicit, bool):
        return explicit, "runtime_loop_result"
    if str(sample.get("final_response") or "").strip():
        return True, "legacy_nonempty_final_response_inference"
    return None, "missing"


def _confusion(machine: list[bool], reference: list[bool]) -> dict[str, Any]:
    tp = sum(left and right for left, right in zip(machine, reference))
    fp = sum(left and not right for left, right in zip(machine, reference))
    fn = sum(not left and right for left, right in zip(machine, reference))
    tn = len(machine) - tp - fp - fn
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall
        else None
    )
    return {
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "true_negative": tn,
        "agreement_rate": (tp + tn) / len(machine) if machine else None,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "denominator": len(machine),
    }


def _review_calibration(
    source_report: dict[str, Any],
    scored_samples: list[dict[str, Any]],
    review: dict[str, Any],
) -> dict[str, Any]:
    expected_identity = {
        "source_run_id": source_report["run"]["run_id"],
        "source_run_fingerprint": source_report["run"]["fingerprint"],
        "dataset_sha256": source_report["dataset"]["dataset_sha256"],
    }
    if review.get("schema_version") != "tool-use-memory-pair-semantic-review-v1":
        raise ValueError("unsupported ToolUseMemory semantic review schema")
    for field, expected in expected_identity.items():
        if review.get(field) != expected:
            raise ValueError(f"ToolUseMemory semantic review {field} mismatch")
    judgments = list(review.get("judgments") or [])
    by_sample = {str(item.get("sample_id") or ""): item for item in judgments}
    if len(by_sample) != len(judgments) or set(by_sample) != {
        str(sample["sample_id"]) for sample in scored_samples
    }:
        raise ValueError("ToolUseMemory semantic review sample identity mismatch")
    deterministic: list[bool] = []
    human: list[bool] = []
    disagreements: list[dict[str, Any]] = []
    source_samples = {str(item["sample_id"]): item for item in source_report["samples"]}
    for sample in scored_samples:
        sample_id = str(sample["sample_id"])
        judgment = by_sample[sample_id]
        source = source_samples[sample_id]
        if judgment.get("response_sha256") != source.get("final_response_sha256"):
            raise ValueError(f"response SHA mismatch for {sample_id}")
        semantic_task_success = judgment.get("semantic_task_success")
        if not isinstance(semantic_task_success, bool):
            raise ValueError(f"incomplete semantic judgment for {sample_id}")
        deterministic_value = bool(sample["deterministic_semantic"]["semantic_task_success"])
        deterministic.append(deterministic_value)
        human.append(semantic_task_success)
        if deterministic_value != semantic_task_success:
            disagreements.append(
                {
                    "sample_id": sample_id,
                    "deterministic_semantic_task_success": deterministic_value,
                    "review_semantic_task_success": semantic_task_success,
                    "review_notes": str(judgment.get("notes") or ""),
                }
            )
    return {
        "review_protocol": dict(review.get("review_protocol") or {}),
        "deterministic_vs_single_review": _confusion(deterministic, human),
        "disagreements": disagreements,
        "boundary": (
            "The available review is developer-visible and model-assisted, not an independent "
            "human gold standard. Outcome consistency is not calibrated as claim-level grounding."
        ),
    }


def build_revalidation(
    source_report: dict[str, Any],
    *,
    source_report_sha256: str,
    dataset: dict[str, Any],
    dataset_identity: dict[str, Any],
    semantic_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    for field in ("dataset_sha256", "manifest_sha256"):
        if source_report.get("dataset", {}).get(field) != dataset_identity[field]:
            raise ValueError(f"ToolUseMemory source report {field} mismatch")
    cases = {str(case["case_id"]): case for case in dataset["cases"]}
    source_samples = list(source_report.get("samples") or [])
    sample_ids = [str(sample.get("sample_id") or "") for sample in source_samples]
    if len(set(sample_ids)) != len(sample_ids):
        raise ValueError("ToolUseMemory source report has duplicate sample IDs")
    scored_samples: list[dict[str, Any]] = []
    for sample in source_samples:
        case_id = str(sample.get("case_id") or "")
        case = cases.get(case_id)
        if case is None:
            raise ValueError(f"ToolUseMemory source report references unknown case: {case_id}")
        completion_ok, completion_source = _completion_evidence(sample)
        score = semantic_grader.score_semantic_sample(
            sample,
            case,
            agent_completion_ok=completion_ok,
            completion_evidence_source=completion_source,
        )
        scored_samples.append(
            {
                "sample_id": sample["sample_id"],
                "case_id": case_id,
                "failure_family": sample["failure_family"],
                "repeat_index": sample["repeat_index"],
                "arm": sample["arm"],
                "final_response_sha256": sample["final_response_sha256"],
                "machine_exact_task_success": bool(sample["task_success"]),
                "deterministic_semantic": score,
            }
        )
    valid_scores = [
        sample
        for sample in scored_samples
        if sample["deterministic_semantic"]["score_valid"] is True
    ]
    machine = [bool(sample["machine_exact_task_success"]) for sample in valid_scores]
    semantic = [
        bool(sample["deterministic_semantic"]["semantic_task_success"])
        for sample in valid_scores
    ]
    portable_dataset_identity = {
        key: value for key, value in dataset_identity.items() if key != "manifest_path"
    }
    grader_contract = {
        "version": semantic_grader.GRADER_VERSION,
        "dataset_sha256": dataset_identity["dataset_sha256"],
        "manifest_sha256": dataset_identity["manifest_sha256"],
        "harmless_extra_tools": list(semantic_grader.DEFAULT_HARMLESS_EXTRA_TOOLS),
    }
    report = {
        "schema_version": "tool-use-memory-deterministic-semantic-revalidation-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_role": "post_hoc_deterministic_revalidation",
        "source": {
            "report_sha256": source_report_sha256,
            "run_id": source_report["run"]["run_id"],
            "run_fingerprint": source_report["run"]["fingerprint"],
            "source_report_schema_version": source_report.get("schema_version"),
        },
        "dataset": portable_dataset_identity,
        "grader": {
            **grader_contract,
            "contract_sha256": _sha256_json(grader_contract),
            "grader_sha256": _sha256_bytes(Path(semantic_grader.__file__).read_bytes()),
            "revalidator_sha256": _sha256_bytes(Path(__file__).read_bytes()),
            "harmless_extra_tools": list(semantic_grader.DEFAULT_HARMLESS_EXTRA_TOOLS),
        },
        "denominators": {
            "source_samples": len(scored_samples),
            "valid_semantic_scores": len(valid_scores),
            "invalid_semantic_scores": len(scored_samples) - len(valid_scores),
            "unique_cases": len({sample["case_id"] for sample in scored_samples}),
        },
        "arms": semantic_grader.semantic_arm_metrics(scored_samples),
        "case_majority": semantic_grader.semantic_case_endpoint(
            scored_samples, endpoint="majority"
        ),
        "case_all_repeats": semantic_grader.semantic_case_endpoint(
            scored_samples, endpoint="all_repeats"
        ),
        "machine_exact_vs_deterministic_semantic": _confusion(machine, semantic),
        "samples": scored_samples,
        "limitations": [
            "This revalidation makes no model calls and is bound to source-report and grader bytes.",
            "For legacy reports, non-empty final text is an explicit inference that the tool loop completed.",
            "The grader was added after these outputs and is therefore post-hoc, not a preregistered primary endpoint.",
            "Response-outcome consistency is not claim-level natural-language groundedness.",
            "The dataset is developer-visible and cannot support a blinded or sealed claim.",
        ],
    }
    if semantic_review is not None:
        report["single_review_calibration"] = _review_calibration(
            source_report,
            scored_samples,
            semantic_review,
        )
    return report


def revalidate_files(
    *,
    source_report_path: Path,
    dataset_path: Path,
    manifest_path: Path | None = None,
    semantic_review_path: Path | None = None,
) -> dict[str, Any]:
    source_bytes = source_report_path.read_bytes()
    source_report = json.loads(source_bytes.decode("utf-8"))
    dataset, dataset_identity = pair_runner._load_dataset(
        dataset_path,
        manifest_path=manifest_path,
    )
    review = (
        json.loads(semantic_review_path.read_text(encoding="utf-8"))
        if semantic_review_path is not None
        else None
    )
    return build_revalidation(
        source_report,
        source_report_sha256=_sha256_bytes(source_bytes),
        dataset=dataset,
        dataset_identity=dataset_identity,
        semantic_review=review,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-report", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--semantic-review", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = revalidate_files(
        source_report_path=args.source_report,
        dataset_path=args.dataset,
        manifest_path=args.manifest,
        semantic_review_path=args.semantic_review,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "denominators": report["denominators"],
                "arms": report["arms"],
                "case_majority": report["case_majority"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
