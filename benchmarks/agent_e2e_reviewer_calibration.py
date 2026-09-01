"""Calibrate Agent E2E machine graders against double review plus adjudication."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchmarks.agent_e2e_semantic_audit import score_semantic_review
from benchmarks.freeze_agent_e2e_dataset import recorded_sha_matches


REVIEW_SCHEMA = Path(__file__).with_name("agent_e2e_semantic_review.schema.json")
RUBRIC_PATH = Path(__file__).resolve().parents[1] / "docs/HUMAN_EVALUATION_RUBRIC.md"
PACK_ORDERING_VERSION = "sha256-pack-bound-permutation-v1"
VERDICT_BLINDING_SCOPE = (
    "machine_scores_and_experiment_fields_removed_natural_ids_visible"
)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _file_identity(path: Path, payload: bytes) -> dict[str, Any]:
    return {"name": path.name, "sha256": _sha256_bytes(payload), "size_bytes": len(payload)}


def _pack_id(*parts: str) -> str:
    payload = "\x1f".join(("agent-e2e-human-review-pack-v1", *parts)).encode("utf-8")
    return f"pk_{_sha256_bytes(payload)[:24]}"


def _pack_order_key(pack_id: str, sample_id: str) -> str:
    return _sha256_bytes(f"{pack_id}\x00{sample_id}".encode("utf-8"))


def _rubric_sha256() -> str:
    return _sha256_bytes(RUBRIC_PATH.read_bytes())


def _read_json_with_identity(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = path.read_bytes()
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain one JSON object")
    return value, _file_identity(path, payload)


def export_review_template(
    report: dict[str, Any],
    *,
    report_sha256: str,
    dataset: dict[str, Any],
    dataset_sha256: str,
    manifest: dict[str, Any],
    manifest_sha256: str,
    reviewer_id: str,
    dataset_bytes: bytes | None = None,
    manifest_bytes: bytes | None = None,
) -> dict[str, Any]:
    """Create a fill-in-place review file with machine verdicts and model identity removed."""
    run = report["run"]
    dataset_ok = run.get("dataset_sha256") == dataset_sha256
    if dataset_bytes is not None:
        dataset_ok = recorded_sha_matches(str(run.get("dataset_sha256") or ""), dataset_bytes) and (
            recorded_sha_matches(dataset_sha256, dataset_bytes)
        )
    if not dataset_ok:
        raise ValueError("dataset SHA does not match source report")
    manifest_ok = run.get("manifest_sha256") == manifest_sha256
    if manifest_bytes is not None:
        manifest_ok = recorded_sha_matches(str(run.get("manifest_sha256") or ""), manifest_bytes) and (
            recorded_sha_matches(manifest_sha256, manifest_bytes)
        )
    if not manifest_ok or run.get("manifest_verified") is not True:
        raise ValueError("manifest identity is not verified by source report")
    manifest_dataset_ok = manifest.get("dataset_sha256") == dataset_sha256
    if dataset_bytes is not None:
        manifest_dataset_ok = recorded_sha_matches(
            str(manifest.get("dataset_sha256") or ""), dataset_bytes
        ) and recorded_sha_matches(dataset_sha256, dataset_bytes)
    if not manifest_dataset_ok:
        raise ValueError("manifest dataset SHA does not match dataset bytes")
    if dataset.get("dataset_id") != run.get("dataset_id") or manifest.get("dataset_id") != run.get("dataset_id"):
        raise ValueError("dataset identity does not match source report")
    if not reviewer_id.strip():
        raise ValueError("reviewer_id must be non-empty")

    dataset_cases = {str(item["case_id"]): item for item in dataset.get("cases") or []}
    samples: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in report["groups"].values():
        for sample in group["cases"]:
            sample_id = str(sample.get("sample_id") or sample["case_id"])
            if sample_id in seen:
                raise ValueError("review export requires globally unique sample_id values")
            seen.add(sample_id)
            case = dataset_cases.get(str(sample["case_id"]))
            if case is None:
                raise ValueError(f"source dataset is missing case {sample['case_id']}")
            samples.append(
                {
                    "sample_id": sample_id,
                    "response_sha256": str(sample["final_response_sha256"]),
                    "semantic_execution_success": None,
                    "grounded_final_response": None,
                    "notes": "",
                    "review_context": {
                        "user_messages": [str(turn.get("content") or "") for turn in case.get("turns") or []],
                        "tool_events": [
                            {
                                "tool_name": str(event.get("tool_name") or ""),
                                "arguments": event.get("arguments") or {},
                                "success": bool(event.get("success")),
                                "message": str(event.get("message") or ""),
                            }
                            for event in sample.get("tool_events") or []
                        ],
                        "recovery_events": list(sample.get("recovery_events") or []),
                        "final_response": str(sample.get("final_response") or ""),
                    },
                }
            )

    rubric_sha256 = _rubric_sha256()
    pack_id = _pack_id(
        report_sha256,
        dataset_sha256,
        manifest_sha256,
        rubric_sha256,
    )
    samples.sort(key=lambda item: _pack_order_key(pack_id, item["sample_id"]))

    return {
        "schema_version": "agent-e2e-semantic-review-v2",
        "pack_id": pack_id,
        "source_run_id": str(run["run_id"]),
        "source_report_sha256": report_sha256,
        "dataset_id": str(run["dataset_id"]),
        "dataset_sha256": dataset_sha256,
        "review_protocol": {
            "reviewer_id": reviewer_id,
            "reviewer_type": "human",
            "review_role": "independent_reviewer",
            "blinded_to_group_and_model": True,
            "blinded_to_other_reviewer": True,
            "blinded_to_machine_grader": True,
            "verdict_blinding_scope": VERDICT_BLINDING_SCOPE,
            "ordering_version": PACK_ORDERING_VERSION,
            "rubric_version": "agent-semantic-rubric-v1",
            "rubric_sha256": rubric_sha256,
            "notes": (
                "Apply agent-semantic-rubric-v1 from the SHA-bound HUMAN_EVALUATION_RUBRIC.md. "
                "Fill both null verdicts using only user messages, tool/recovery evidence, and the final "
                "response in this file. Do not inspect machine scores or another review."
            ),
        },
        "judgments": samples,
    }


def _validate_review_v2(review: dict[str, Any]) -> None:
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:  # pragma: no cover - declared project dependency
        raise RuntimeError("jsonschema is required for reviewer calibration") from exc
    schema = json.loads(REVIEW_SCHEMA.read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(review), key=lambda item: list(item.path))
    if errors:
        detail = "; ".join(f"{list(item.path)}: {item.message}" for item in errors[:8])
        raise ValueError(f"invalid semantic review v2: {detail}")


def _as_audit(report: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    _validate_review_v2(review)
    compatible = dict(review)
    compatible["schema_version"] = "agent-e2e-semantic-review-v1"
    return score_semantic_review(report, compatible)


def export_adjudication_template(
    report: dict[str, Any],
    report_sha256: str,
    reviewer_a: dict[str, Any],
    reviewer_b: dict[str, Any],
    *,
    adjudicator_id: str,
) -> dict[str, Any]:
    """Prefill consensus and leave only reviewer disagreements for adjudication."""
    audits = [_as_audit(report, reviewer_a), _as_audit(report, reviewer_b)]
    reviews = [reviewer_a, reviewer_b]
    for review in reviews:
        if review.get("source_report_sha256") != report_sha256:
            raise ValueError("review source_report_sha256 does not match source report")
    reviewer_ids = [review["review_protocol"]["reviewer_id"] for review in reviews]
    if len(set(reviewer_ids)) != 2:
        raise ValueError("independent reviewer IDs must differ")
    if not adjudicator_id.strip() or adjudicator_id in reviewer_ids:
        raise ValueError("adjudicator ID must be non-empty and differ from reviewer IDs")
    pack_ids = {str(review.get("pack_id") or "") for review in reviews}
    if len(pack_ids) != 1 or "" in pack_ids:
        raise ValueError("independent reviews must bind the same non-empty pack_id")
    pack_id = pack_ids.pop()
    rubric_shas = {
        str(review["review_protocol"].get("rubric_sha256") or "")
        for review in reviews
    }
    if rubric_shas != {_rubric_sha256()}:
        raise ValueError("independent reviews must bind the current rubric SHA256")
    rubric_sha256 = rubric_shas.pop()

    rows = [
        {item["sample_id"]: item for item in review["judgments"]}
        for review in reviews
    ]
    contexts = {item["sample_id"]: item.get("review_context") for item in reviewer_a["judgments"]}
    judgments = []
    for sample in audits[0]["samples"]:
        sample_id = sample["sample_id"]
        left = rows[0][sample_id]
        right = rows[1][sample_id]
        execution = left["semantic_execution_success"] if left["semantic_execution_success"] == right["semantic_execution_success"] else None
        grounded = left["grounded_final_response"] if left["grounded_final_response"] == right["grounded_final_response"] else None
        judgments.append(
            {
                "sample_id": sample_id,
                "response_sha256": sample["response_sha256"],
                "semantic_execution_success": execution,
                "grounded_final_response": grounded,
                "notes": "CONSENSUS_LOCKED" if execution is not None and grounded is not None else "ADJUDICATE_DISAGREEMENT",
                **({"review_context": contexts[sample_id]} if contexts.get(sample_id) else {}),
            }
        )
    return {
        "schema_version": "agent-e2e-semantic-review-v2",
        "pack_id": pack_id,
        "source_run_id": report["run"]["run_id"],
        "source_report_sha256": report_sha256,
        "dataset_id": report["run"]["dataset_id"],
        "dataset_sha256": report["run"]["dataset_sha256"],
        "review_protocol": {
            "reviewer_id": adjudicator_id,
            "reviewer_type": "human",
            "review_role": "adjudicator",
            "blinded_to_group_and_model": True,
            "blinded_to_other_reviewer": False,
            "blinded_to_machine_grader": True,
            "verdict_blinding_scope": VERDICT_BLINDING_SCOPE,
            "ordering_version": PACK_ORDERING_VERSION,
            "rubric_version": reviewer_a["review_protocol"]["rubric_version"],
            "rubric_sha256": rubric_sha256,
            "adjudication_of": reviewer_ids,
            "notes": "Fill only null disagreement fields; consensus fields are verified as immutable.",
        },
        "judgments": judgments,
    }


def _cohen_kappa(left: list[bool], right: list[bool]) -> dict[str, Any]:
    if len(left) != len(right):
        raise ValueError("reviewer verdict counts differ")
    count = len(left)
    if count == 0:
        raise ValueError("reviewer calibration requires at least one sample")
    agreement = sum(a == b for a, b in zip(left, right)) / count
    left_positive = sum(left) / count
    right_positive = sum(right) / count
    expected = left_positive * right_positive + (1 - left_positive) * (1 - right_positive)
    kappa = None if expected == 1.0 else (agreement - expected) / (1 - expected)
    return {
        "sample_count": count,
        "agreement_rate": agreement,
        "expected_agreement": expected,
        "cohen_kappa": kappa,
        "method": "cohen_kappa_binary",
    }


def _classification(machine: list[bool], gold: list[bool]) -> dict[str, Any]:
    if len(machine) != len(gold):
        raise ValueError("machine and gold verdict counts differ")
    tp = sum(pred and truth for pred, truth in zip(machine, gold))
    fp = sum(pred and not truth for pred, truth in zip(machine, gold))
    fn = sum(not pred and truth for pred, truth in zip(machine, gold))
    tn = len(machine) - tp - fp - fn

    def ratio(numerator: int, denominator: int) -> float | None:
        return numerator / denominator if denominator else None

    precision = ratio(tp, tp + fp)
    recall = ratio(tp, tp + fn)
    f1 = None if precision is None or recall is None or precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return {
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "true_negative": tn,
        "accuracy": ratio(tp + tn, len(machine)),
        "precision": precision,
        "recall_sensitivity": recall,
        "specificity": ratio(tn, tn + fp),
        "f1": f1,
        "unit": "response_sha_bound_sample",
    }


def build_reviewer_calibration(
    report: dict[str, Any],
    report_sha256: str,
    reviewer_a: dict[str, Any],
    reviewer_b: dict[str, Any],
    adjudication: dict[str, Any],
) -> dict[str, Any]:
    for review in (reviewer_a, reviewer_b, adjudication):
        if review.get("source_report_sha256") != report_sha256:
            raise ValueError("review source_report_sha256 does not match source report")
    audits = {
        "reviewer_a": _as_audit(report, reviewer_a),
        "reviewer_b": _as_audit(report, reviewer_b),
        "adjudication": _as_audit(report, adjudication),
    }
    protocols = {
        name: review["review_protocol"]
        for name, review in (
            ("reviewer_a", reviewer_a),
            ("reviewer_b", reviewer_b),
            ("adjudication", adjudication),
        )
    }
    reviewer_ids = [protocols["reviewer_a"]["reviewer_id"], protocols["reviewer_b"]["reviewer_id"]]
    if len(set(reviewer_ids)) != 2:
        raise ValueError("independent reviewer IDs must differ")
    pack_ids = {
        str(review.get("pack_id") or "")
        for review in (reviewer_a, reviewer_b, adjudication)
    }
    if len(pack_ids) != 1 or "" in pack_ids:
        raise ValueError("reviewer and adjudication files must bind the same non-empty pack_id")
    pack_id = pack_ids.pop()
    for name in ("reviewer_a", "reviewer_b"):
        protocol = protocols[name]
        if protocol["review_role"] != "independent_reviewer":
            raise ValueError(f"{name} must have independent_reviewer role")
        for field in (
            "blinded_to_group_and_model",
            "blinded_to_other_reviewer",
            "blinded_to_machine_grader",
        ):
            if protocol[field] is not True:
                raise ValueError(f"{name} is not fully blinded: {field}")
        if protocol["verdict_blinding_scope"] != VERDICT_BLINDING_SCOPE:
            raise ValueError(f"{name} verdict_blinding_scope does not match exporter contract")
        if protocol["ordering_version"] != PACK_ORDERING_VERSION:
            raise ValueError(f"{name} ordering_version does not match exporter contract")
        if protocol["rubric_sha256"] != _rubric_sha256():
            raise ValueError(f"{name} rubric SHA does not match current rubric bytes")
    adjudication_protocol = protocols["adjudication"]
    if adjudication_protocol["review_role"] != "adjudicator":
        raise ValueError("adjudication review must have adjudicator role")
    if adjudication_protocol["reviewer_id"] in reviewer_ids:
        raise ValueError("adjudicator ID must differ from independent reviewer IDs")
    if set(adjudication_protocol["adjudication_of"]) != set(reviewer_ids):
        raise ValueError("adjudication_of does not match independent reviewer IDs")
    if adjudication_protocol["blinded_to_machine_grader"] is not True:
        raise ValueError("adjudicator must be blinded to machine grader outputs")
    if adjudication_protocol["verdict_blinding_scope"] != VERDICT_BLINDING_SCOPE:
        raise ValueError("adjudication verdict_blinding_scope does not match exporter contract")
    if adjudication_protocol["ordering_version"] != PACK_ORDERING_VERSION:
        raise ValueError("adjudication ordering_version does not match exporter contract")
    rubric_versions = {protocol["rubric_version"] for protocol in protocols.values()}
    if len(rubric_versions) != 1:
        raise ValueError("all reviewers must use the same rubric_version")
    rubric_shas = {protocol["rubric_sha256"] for protocol in protocols.values()}
    if rubric_shas != {_rubric_sha256()}:
        raise ValueError("all reviewers must bind the current rubric SHA256")

    rows = {
        name: {sample["sample_id"]: sample for sample in audit["samples"]}
        for name, audit in audits.items()
    }
    sample_ids = list(rows["adjudication"])
    if any(set(row) != set(sample_ids) for row in rows.values()):
        raise ValueError("review audit sample identities differ")
    report_samples = {
        str(item.get("sample_id") or item["case_id"]): item
        for group in report["groups"].values()
        for item in group["cases"]
    }

    def values(review_name: str, field: str) -> list[bool]:
        return [bool(rows[review_name][sample_id][field]) for sample_id in sample_ids]

    endpoints = {
        "semantic_execution_success": "semantic_execution_success",
        "grounded_final_response": "grounded_final_response",
        "semantic_task_success": "semantic_task_success",
    }
    agreement: dict[str, Any] = {}
    disagreements: dict[str, list[str]] = {}
    for label, field in endpoints.items():
        left = values("reviewer_a", field)
        right = values("reviewer_b", field)
        agreement[label] = _cohen_kappa(left, right)
        disagreements[label] = [
            sample_id for sample_id, a, b in zip(sample_ids, left, right) if a != b
        ]

    gold_execution = values("adjudication", "semantic_execution_success")
    gold_grounding = values("adjudication", "grounded_final_response")
    gold_task = values("adjudication", "semantic_task_success")
    for sample_id in sample_ids:
        for field in ("semantic_execution_success", "grounded_final_response"):
            left = rows["reviewer_a"][sample_id][field]
            right = rows["reviewer_b"][sample_id][field]
            resolved = rows["adjudication"][sample_id][field]
            if left == right and resolved != left:
                raise ValueError(f"adjudication changed reviewer consensus for {sample_id}/{field}")
    machine_execution = [
        bool(report_samples[sample_id]["execution_success"]) for sample_id in sample_ids
    ]
    machine_task = [bool(report_samples[sample_id]["task_success"]) for sample_id in sample_ids]
    machine_strict = [
        bool(report_samples[sample_id]["strict_grounded_success"]) for sample_id in sample_ids
    ]
    all_human = all(protocol["reviewer_type"] == "human" for protocol in protocols.values())
    return {
        "schema_version": "agent-e2e-reviewer-calibration-v1",
        "pack_id": pack_id,
        "source_run_id": report["run"]["run_id"],
        "source_report_sha256": report_sha256,
        "dataset_id": report["run"]["dataset_id"],
        "dataset_sha256": report["run"]["dataset_sha256"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sample_count": len(sample_ids),
        "calibration_status": "human_adjudicated_gold" if all_human else "model_review_not_human_gold",
        "review_protocols": protocols,
        "reviewer_agreement": agreement,
        "disagreement_sample_ids": disagreements,
        "machine_grader_vs_adjudicated_gold": {
            "execution_success_vs_semantic_execution": _classification(
                machine_execution, gold_execution
            ),
            "task_success_vs_semantic_task": _classification(machine_task, gold_task),
            "strict_grounded_success_vs_grounded_response": _classification(
                machine_strict, gold_grounding
            ),
        },
        "limitations": [
            "Calibration quality depends on the rubric, reviewer independence, and sample coverage.",
            "The reviewer pack is verdict-blind, not fully provenance-blind: natural sample IDs and source identities remain visible.",
            "A developer-visible calibration set can validate graders but cannot prove unseen Agent generalization.",
            "Kappa is undefined when both reviewers assign a single constant label.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    export_parser = subparsers.add_parser("export-template")
    export_parser.add_argument("--report", type=Path, required=True)
    export_parser.add_argument("--dataset", type=Path, required=True)
    export_parser.add_argument("--manifest", type=Path, required=True)
    export_parser.add_argument("--reviewer-id", required=True)
    export_parser.add_argument("--output", type=Path, required=True)
    adjudication_parser = subparsers.add_parser("export-adjudication")
    adjudication_parser.add_argument("--report", type=Path, required=True)
    adjudication_parser.add_argument("--reviewer-a", type=Path, required=True)
    adjudication_parser.add_argument("--reviewer-b", type=Path, required=True)
    adjudication_parser.add_argument("--adjudicator-id", required=True)
    adjudication_parser.add_argument("--output", type=Path, required=True)
    calibrate_parser = subparsers.add_parser("calibrate")
    calibrate_parser.add_argument("--report", type=Path, required=True)
    calibrate_parser.add_argument("--reviewer-a", type=Path, required=True)
    calibrate_parser.add_argument("--reviewer-b", type=Path, required=True)
    calibrate_parser.add_argument("--adjudication", type=Path, required=True)
    calibrate_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report, report_identity = _read_json_with_identity(args.report)
    if args.command == "export-template":
        dataset, dataset_identity = _read_json_with_identity(args.dataset)
        manifest, manifest_identity = _read_json_with_identity(args.manifest)
        output = export_review_template(
            report,
            report_sha256=report_identity["sha256"],
            dataset=dataset,
            dataset_sha256=dataset_identity["sha256"],
            manifest=manifest,
            manifest_sha256=manifest_identity["sha256"],
            reviewer_id=args.reviewer_id,
            dataset_bytes=args.dataset.read_bytes(),
            manifest_bytes=args.manifest.read_bytes(),
        )
    else:
        reviewer_a, reviewer_a_identity = _read_json_with_identity(args.reviewer_a)
        reviewer_b, reviewer_b_identity = _read_json_with_identity(args.reviewer_b)
        if args.command == "export-adjudication":
            output = export_adjudication_template(
                report,
                report_identity["sha256"],
                reviewer_a,
                reviewer_b,
                adjudicator_id=args.adjudicator_id,
            )
        else:
            adjudication, adjudication_identity = _read_json_with_identity(args.adjudication)
            output = build_reviewer_calibration(
                report,
                report_identity["sha256"],
                reviewer_a,
                reviewer_b,
                adjudication,
            )
            output["input_artifacts"] = {
                "source_report": report_identity,
                "reviewer_a": reviewer_a_identity,
                "reviewer_b": reviewer_b_identity,
                "adjudication": adjudication_identity,
                "calibrator": _file_identity(Path(__file__).resolve(), Path(__file__).resolve().read_bytes()),
                "human_evaluation_rubric": _file_identity(
                    RUBRIC_PATH,
                    RUBRIC_PATH.read_bytes(),
                ),
            }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in output.items() if key not in {"judgments", "machine_grader_vs_adjudicated_gold"}}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
