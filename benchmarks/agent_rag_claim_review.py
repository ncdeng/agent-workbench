"""Export SHA-bound RAG claim reviews and calibrate the machine judge.

The review unit is one atomic claim already segmented by the machine judge.
Independent reviewers receive answer/evidence/claim text but not the machine
support verdict. Calibration therefore measures support-label quality; it does
not validate whether the machine judge omitted or over-split claims.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REVIEW_SCHEMA = Path(__file__).with_name("agent_rag_claim_review.schema.json")
RUBRIC_PATH = Path(__file__).resolve().parents[1] / "docs/HUMAN_EVALUATION_RUBRIC.md"
PACK_ORDERING_VERSION = "sha256-pack-bound-permutation-v1"
VERDICT_BLINDING_SCOPE = (
    "machine_support_and_experiment_fields_removed_natural_ids_visible"
)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _pack_id(*parts: str) -> str:
    payload = "\x1f".join(("agent-rag-human-review-pack-v1", *parts)).encode("utf-8")
    return f"pk_{_sha256_bytes(payload)[:24]}"


def _pack_order_key(pack_id: str, claim_id: str) -> str:
    return _sha256_bytes(f"{pack_id}\x00{claim_id}".encode("utf-8"))


def _rubric_sha256() -> str:
    return _sha256_bytes(RUBRIC_PATH.read_bytes())


def _validate_review(review: dict[str, Any]) -> None:
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:  # pragma: no cover - declared test dependency
        raise RuntimeError("jsonschema is required for RAG claim review") from exc
    schema = json.loads(REVIEW_SCHEMA.read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(review), key=lambda item: list(item.path))
    if errors:
        detail = "; ".join(f"{list(item.path)}: {item.message}" for item in errors[:8])
        raise ValueError(f"invalid RAG claim review: {detail}")


def _report_identity(report: dict[str, Any], report_sha256: str) -> dict[str, str]:
    dataset_sha = str((report.get("dataset") or {}).get("sha256") or "")
    prompt_version = str((report.get("execution_contract") or {}).get("judge_prompt_version") or "")
    if len(report_sha256) != 64:
        raise ValueError("source report SHA256 must be a 64-character digest")
    if len(dataset_sha) != 64:
        raise ValueError("groundedness report is missing a dataset SHA256")
    if not prompt_version:
        raise ValueError("groundedness report is missing judge_prompt_version")
    run_id = str(report.get("run_id") or "")
    if not run_id:
        raise ValueError("groundedness report is missing run_id")
    return {
        "source_run_id": run_id,
        "source_report_sha256": report_sha256,
        "dataset_sha256": dataset_sha,
        "judge_prompt_version": prompt_version,
    }


def _report_claims(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    claims: dict[str, dict[str, Any]] = {}
    for row in report.get("results") or []:
        if not row.get("judge"):
            continue
        case_id = str(row.get("id") or "")
        answer_sha = str(row.get("answer_sha256") or "")
        evidence_sha = str(row.get("evidence_sha256") or "")
        evidence = row.get("evidence") or []
        known_refs = {
            (str(item.get("source_path") or ""), str(item.get("chunk_idx") or ""))
            for item in evidence
            if isinstance(item, dict)
        }
        for claim in row["judge"].get("claims") or []:
            claim_id = str(claim.get("claim_id") or "")
            if not claim_id or claim_id in claims:
                raise ValueError(f"groundedness report has missing or duplicate claim_id: {claim_id!r}")
            claims[claim_id] = {
                "case_id": case_id,
                "query": str(row.get("query") or ""),
                "answer": str(row.get("answer") or ""),
                "answer_sha256": answer_sha,
                "evidence": evidence,
                "evidence_sha256": evidence_sha,
                "known_refs": known_refs,
                "claim_id": claim_id,
                "claim_text": str(claim.get("text") or ""),
                "claim_sha256": str(claim.get("claim_sha256") or ""),
                "machine_supported": bool(claim.get("supported")),
                "machine_evidence_refs": list(claim.get("evidence_refs") or []),
            }
    if not claims:
        raise ValueError("groundedness report contains no claim-level judgments")
    return claims


def export_review_template(
    report: dict[str, Any], report_sha256: str, *, reviewer_id: str
) -> dict[str, Any]:
    """Return a fill-in-place blind review; machine support labels are absent."""
    identity = _report_identity(report, report_sha256)
    claims = _report_claims(report)
    if not reviewer_id.strip():
        raise ValueError("reviewer_id must be non-empty")
    rubric_sha256 = _rubric_sha256()
    pack_id = _pack_id(
        report_sha256,
        identity["dataset_sha256"],
        identity["judge_prompt_version"],
        rubric_sha256,
    )
    ordered_claims = sorted(
        claims.values(),
        key=lambda item: _pack_order_key(pack_id, item["claim_id"]),
    )
    return {
        "schema_version": "agent-rag-claim-review-v1",
        "pack_id": pack_id,
        **identity,
        "review_protocol": {
            "reviewer_id": reviewer_id,
            "reviewer_type": "human",
            "review_role": "independent_reviewer",
            "blinded_to_machine_judge": True,
            "blinded_to_other_reviewer": True,
            "verdict_blinding_scope": VERDICT_BLINDING_SCOPE,
            "ordering_version": PACK_ORDERING_VERSION,
            "rubric_version": "rag-claim-support-rubric-v1",
            "rubric_sha256": rubric_sha256,
            "notes": (
                "Apply rag-claim-support-rubric-v1 from the SHA-bound HUMAN_EVALUATION_RUBRIC.md. "
                "Fill every null supported verdict using only the claim and supplied evidence in this file. "
                "Supported claims require exact source/chunk refs. Do not inspect machine verdicts or another review."
            ),
        },
        "judgments": [
            {
                "case_id": item["case_id"],
                "claim_id": item["claim_id"],
                "claim_sha256": item["claim_sha256"],
                "answer_sha256": item["answer_sha256"],
                "evidence_sha256": item["evidence_sha256"],
                "supported": None,
                "evidence_refs": [],
                "notes": "",
                "review_context": {
                    "query": item["query"],
                    "answer": item["answer"],
                    "evidence": item["evidence"],
                    "claim_text": item["claim_text"],
                },
            }
            for item in ordered_claims
        ],
    }


def _cohen_kappa(left: list[bool], right: list[bool]) -> dict[str, Any]:
    if len(left) != len(right) or not left:
        raise ValueError("reviewer verdicts must be non-empty and aligned")
    count = len(left)
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
        "method": "cohen_kappa_binary_claim_level",
    }


def _classification(machine: list[bool], gold: list[bool]) -> dict[str, Any]:
    if len(machine) != len(gold) or not machine:
        raise ValueError("machine and gold verdicts must be non-empty and aligned")
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
        "recall": recall,
        "specificity": ratio(tn, tn + fp),
        "f1": f1,
        "unit": "answer_and_evidence_sha_bound_atomic_claim",
    }


def _validated_judgments(
    review: dict[str, Any],
    *,
    identity: dict[str, str],
    report_claims: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    _validate_review(review)
    for field, expected in identity.items():
        if review[field] != expected:
            raise ValueError(f"review {field} does not match groundedness report")
    rows: dict[str, dict[str, Any]] = {}
    for judgment in review["judgments"]:
        claim_id = judgment["claim_id"]
        if claim_id in rows:
            raise ValueError(f"duplicate review claim_id: {claim_id}")
        expected = report_claims.get(claim_id)
        if expected is None:
            raise ValueError(f"review contains unknown claim_id: {claim_id}")
        for field in ("case_id", "claim_sha256", "answer_sha256", "evidence_sha256"):
            if judgment[field] != expected[field]:
                raise ValueError(f"review {field} mismatch for {claim_id}")
        refs = {
            (str(ref["source_path"]), str(ref["chunk_idx"]))
            for ref in judgment["evidence_refs"]
        }
        if not refs.issubset(expected["known_refs"]):
            raise ValueError(f"review evidence reference mismatch for {claim_id}")
        if not isinstance(judgment["supported"], bool):
            raise ValueError(f"review verdict is incomplete for {claim_id}")
        if judgment["supported"] and not refs:
            raise ValueError(f"supported review claim lacks evidence reference: {claim_id}")
        if not judgment["supported"] and refs:
            raise ValueError(f"unsupported review claim must not cite supporting evidence: {claim_id}")
        rows[claim_id] = judgment
    if set(rows) != set(report_claims):
        missing = sorted(set(report_claims) - set(rows))
        raise ValueError(f"review does not cover every report claim: {missing[:5]}")
    return rows


def export_adjudication_template(
    report: dict[str, Any],
    report_sha256: str,
    reviewer_a: dict[str, Any],
    reviewer_b: dict[str, Any],
    *,
    adjudicator_id: str,
) -> dict[str, Any]:
    """Prefill reviewer consensus and expose only disagreement verdicts as null."""
    identity = _report_identity(report, report_sha256)
    claims = _report_claims(report)
    rows = [
        _validated_judgments(review, identity=identity, report_claims=claims)
        for review in (reviewer_a, reviewer_b)
    ]
    reviewer_ids = [reviewer_a["review_protocol"]["reviewer_id"], reviewer_b["review_protocol"]["reviewer_id"]]
    if len(set(reviewer_ids)) != 2:
        raise ValueError("independent reviewer IDs must differ")
    if not adjudicator_id.strip() or adjudicator_id in reviewer_ids:
        raise ValueError("adjudicator ID must be non-empty and differ from reviewer IDs")
    pack_ids = {
        str(review.get("pack_id") or "") for review in (reviewer_a, reviewer_b)
    }
    if len(pack_ids) != 1 or "" in pack_ids:
        raise ValueError("independent reviews must bind the same non-empty pack_id")
    pack_id = pack_ids.pop()
    rubric_shas = {
        str(review["review_protocol"].get("rubric_sha256") or "")
        for review in (reviewer_a, reviewer_b)
    }
    if rubric_shas != {_rubric_sha256()}:
        raise ValueError("independent reviews must bind the current rubric SHA256")
    rubric_sha256 = rubric_shas.pop()

    claim_order = [item["claim_id"] for item in reviewer_a["judgments"]]
    contexts = {
        item["claim_id"]: item.get("review_context")
        for item in reviewer_a["judgments"]
    }
    judgments = []
    for claim_id in claim_order:
        claim = claims[claim_id]
        left = rows[0][claim_id]
        right = rows[1][claim_id]
        supported = left["supported"] if left["supported"] == right["supported"] else None
        refs = left["evidence_refs"] if supported is True else []
        context = contexts.get(claim_id)
        judgments.append(
            {
                "case_id": claim["case_id"],
                "claim_id": claim_id,
                "claim_sha256": claim["claim_sha256"],
                "answer_sha256": claim["answer_sha256"],
                "evidence_sha256": claim["evidence_sha256"],
                "supported": supported,
                "evidence_refs": refs,
                "notes": "CONSENSUS_LOCKED" if supported is not None else "ADJUDICATE_DISAGREEMENT",
                **({"review_context": context} if context else {}),
            }
        )
    return {
        "schema_version": "agent-rag-claim-review-v1",
        "pack_id": pack_id,
        **identity,
        "review_protocol": {
            "reviewer_id": adjudicator_id,
            "reviewer_type": "human",
            "review_role": "adjudicator",
            "blinded_to_machine_judge": True,
            "blinded_to_other_reviewer": False,
            "verdict_blinding_scope": VERDICT_BLINDING_SCOPE,
            "ordering_version": PACK_ORDERING_VERSION,
            "rubric_version": reviewer_a["review_protocol"]["rubric_version"],
            "rubric_sha256": rubric_sha256,
            "adjudication_of": reviewer_ids,
            "notes": "Fill only null disagreement verdicts; consensus verdicts are verified as immutable.",
        },
        "judgments": judgments,
    }


def build_claim_calibration(
    report: dict[str, Any],
    report_sha256: str,
    reviewer_a: dict[str, Any],
    reviewer_b: dict[str, Any],
    adjudication: dict[str, Any],
) -> dict[str, Any]:
    identity = _report_identity(report, report_sha256)
    report_claims = _report_claims(report)
    reviews = {
        "reviewer_a": reviewer_a,
        "reviewer_b": reviewer_b,
        "adjudication": adjudication,
    }
    rows = {
        name: _validated_judgments(review, identity=identity, report_claims=report_claims)
        for name, review in reviews.items()
    }
    protocols = {name: review["review_protocol"] for name, review in reviews.items()}
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
        if protocol["blinded_to_machine_judge"] is not True or protocol["blinded_to_other_reviewer"] is not True:
            raise ValueError(f"{name} is not fully blinded")
        if protocol["verdict_blinding_scope"] != VERDICT_BLINDING_SCOPE:
            raise ValueError(f"{name} verdict_blinding_scope does not match exporter contract")
        if protocol["ordering_version"] != PACK_ORDERING_VERSION:
            raise ValueError(f"{name} ordering_version does not match exporter contract")
        if protocol["rubric_sha256"] != _rubric_sha256():
            raise ValueError(f"{name} rubric SHA does not match current rubric bytes")
    adjudicator = protocols["adjudication"]
    if adjudicator["review_role"] != "adjudicator":
        raise ValueError("adjudication review must have adjudicator role")
    if adjudicator["reviewer_id"] in reviewer_ids:
        raise ValueError("adjudicator ID must differ from independent reviewer IDs")
    if set(adjudicator.get("adjudication_of") or []) != set(reviewer_ids):
        raise ValueError("adjudication_of does not match independent reviewer IDs")
    if adjudicator["blinded_to_machine_judge"] is not True:
        raise ValueError("adjudicator must be blinded to machine judge outputs")
    if adjudicator["verdict_blinding_scope"] != VERDICT_BLINDING_SCOPE:
        raise ValueError("adjudication verdict_blinding_scope does not match exporter contract")
    if adjudicator["ordering_version"] != PACK_ORDERING_VERSION:
        raise ValueError("adjudication ordering_version does not match exporter contract")
    rubric_versions = {protocol["rubric_version"] for protocol in protocols.values()}
    if len(rubric_versions) != 1:
        raise ValueError("all reviewers must use the same rubric_version")
    rubric_shas = {protocol["rubric_sha256"] for protocol in protocols.values()}
    if rubric_shas != {_rubric_sha256()}:
        raise ValueError("all reviewers must bind the current rubric SHA256")

    claim_ids = list(report_claims)
    left = [bool(rows["reviewer_a"][claim_id]["supported"]) for claim_id in claim_ids]
    right = [bool(rows["reviewer_b"][claim_id]["supported"]) for claim_id in claim_ids]
    gold = [bool(rows["adjudication"][claim_id]["supported"]) for claim_id in claim_ids]
    machine = [bool(report_claims[claim_id]["machine_supported"]) for claim_id in claim_ids]
    disagreement_ids = [claim_id for claim_id, a, b in zip(claim_ids, left, right) if a != b]
    for claim_id in claim_ids:
        left_value = rows["reviewer_a"][claim_id]["supported"]
        right_value = rows["reviewer_b"][claim_id]["supported"]
        resolved = rows["adjudication"][claim_id]["supported"]
        if left_value == right_value and resolved != left_value:
            raise ValueError(f"adjudication changed reviewer consensus for {claim_id}")

    per_case: dict[str, dict[str, list[bool]]] = {}
    for claim_id, machine_value, gold_value in zip(claim_ids, machine, gold):
        case_id = report_claims[claim_id]["case_id"]
        bucket = per_case.setdefault(case_id, {"machine": [], "gold": []})
        bucket["machine"].append(machine_value)
        bucket["gold"].append(gold_value)
    response_rows = []
    for case_id, values in per_case.items():
        machine_score = sum(values["machine"]) / len(values["machine"])
        gold_score = sum(values["gold"]) / len(values["gold"])
        response_rows.append(
            {
                "case_id": case_id,
                "claim_count": len(values["gold"]),
                "machine_groundedness": machine_score,
                "adjudicated_groundedness": gold_score,
                "absolute_error": abs(machine_score - gold_score),
            }
        )

    all_human = all(protocol["reviewer_type"] == "human" for protocol in protocols.values())
    return {
        "schema_version": "agent-rag-claim-calibration-v1",
        "pack_id": pack_id,
        **identity,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "claim_count": len(claim_ids),
        "response_count": len(response_rows),
        "calibration_status": "human_adjudicated_gold" if all_human else "model_review_not_human_gold",
        "review_protocols": protocols,
        "reviewer_agreement": _cohen_kappa(left, right),
        "disagreement_claim_ids": disagreement_ids,
        "machine_judge_vs_adjudicated_gold": _classification(machine, gold),
        "adjudicated_supported_claim_rate": sum(gold) / len(gold),
        "response_groundedness": {
            "machine_macro_mean": statistics.fmean(row["machine_groundedness"] for row in response_rows),
            "adjudicated_macro_mean": statistics.fmean(row["adjudicated_groundedness"] for row in response_rows),
            "mean_absolute_error": statistics.fmean(row["absolute_error"] for row in response_rows),
            "rows": response_rows,
        },
        "limitations": [
            "Calibration validates support labels for machine-segmented claims; it does not validate claim coverage.",
            "The reviewer pack is verdict-blind, not fully provenance-blind: natural case/claim IDs and source identities remain visible.",
            "Human-gold status depends on reviewer independence and protocol metadata being true in practice.",
            "Developer-visible calibration estimates grader error but does not prove unseen RAG generalization.",
            "Kappa is undefined when both independent reviewers assign a single constant label.",
        ],
    }


def _read_json_with_sha(path: Path) -> tuple[dict[str, Any], str]:
    payload = path.read_bytes()
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain one JSON object")
    return value, _sha256_bytes(payload)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    export_parser = subparsers.add_parser("export-template")
    export_parser.add_argument("--report", type=Path, required=True)
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

    report, report_sha = _read_json_with_sha(args.report)
    if args.command == "export-template":
        output = export_review_template(report, report_sha, reviewer_id=args.reviewer_id)
    elif args.command == "export-adjudication":
        reviewer_a, _ = _read_json_with_sha(args.reviewer_a)
        reviewer_b, _ = _read_json_with_sha(args.reviewer_b)
        output = export_adjudication_template(
            report,
            report_sha,
            reviewer_a,
            reviewer_b,
            adjudicator_id=args.adjudicator_id,
        )
    else:
        reviewer_a, reviewer_a_sha = _read_json_with_sha(args.reviewer_a)
        reviewer_b, reviewer_b_sha = _read_json_with_sha(args.reviewer_b)
        adjudication, adjudication_sha = _read_json_with_sha(args.adjudication)
        output = build_claim_calibration(
            report,
            report_sha,
            reviewer_a,
            reviewer_b,
            adjudication,
        )
        output["input_artifacts"] = {
            "reviewer_a_sha256": reviewer_a_sha,
            "reviewer_b_sha256": reviewer_b_sha,
            "adjudication_sha256": adjudication_sha,
            "calibrator_sha256": _sha256_bytes(Path(__file__).resolve().read_bytes()),
            "human_evaluation_rubric_sha256": _rubric_sha256(),
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                key: value
                for key, value in output.items()
                if key not in {"judgments", "response_groundedness"}
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
