from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from benchmarks.agent_rag_claim_review import (
    RUBRIC_PATH,
    build_claim_calibration,
    export_adjudication_template,
    export_review_template,
)


REPORT_SHA = "f" * 64
DATASET_SHA = "d" * 64
PACK_ID = "pk_" + "2" * 24
ROOT = Path(__file__).resolve().parents[1]
RUBRIC_SHA = hashlib.sha256(RUBRIC_PATH.read_bytes()).hexdigest()


def _report():
    evidence = [{"source_path": "help/a.htm", "chunk_idx": "1", "text": "Evidence A"}]
    rows = []
    for case_id, verdicts in (("case-1", [True, False]), ("case-2", [True])):
        claims = []
        for index, verdict in enumerate(verdicts, start=1):
            claim_id = f"{case_id}:claim-{index:03d}"
            claims.append(
                {
                    "claim_id": claim_id,
                    "text": f"Claim {claim_id}",
                    "claim_sha256": str(len(rows) + index) * 64,
                    "supported": verdict,
                    "evidence_refs": (
                        [{"source_path": "help/a.htm", "chunk_idx": "1"}] if verdict else []
                    ),
                    "reason": "machine reason",
                }
            )
        rows.append(
            {
                "id": case_id,
                "query": f"Question {case_id}",
                "answer": f"Answer {case_id}",
                "answer_sha256": ("a" if case_id == "case-1" else "b") * 64,
                "evidence": evidence,
                "evidence_sha256": ("1" if case_id == "case-1" else "2") * 64,
                "judge": {"claims": claims},
            }
        )
    return {
        "run_id": "rag-run-1",
        "dataset": {"sha256": DATASET_SHA},
        "execution_contract": {"judge_prompt_version": "rag-claim-judge-v2"},
        "results": rows,
    }


def _review(reviewer_id: str, verdicts: list[bool], *, role: str = "independent_reviewer"):
    report = _report()
    report_claims = [claim for row in report["results"] for claim in row["judge"]["claims"]]
    rows_by_case = {row["id"]: row for row in report["results"]}
    protocol = {
        "reviewer_id": reviewer_id,
        "reviewer_type": "human",
        "review_role": role,
        "blinded_to_machine_judge": True,
        "blinded_to_other_reviewer": role == "independent_reviewer",
        "verdict_blinding_scope": (
            "machine_support_and_experiment_fields_removed_natural_ids_visible"
        ),
        "ordering_version": "sha256-pack-bound-permutation-v1",
        "rubric_version": "rag-claim-support-rubric-v1",
        "rubric_sha256": RUBRIC_SHA,
    }
    if role == "adjudicator":
        protocol["adjudication_of"] = ["reviewer-a", "reviewer-b"]
    return {
        "schema_version": "agent-rag-claim-review-v1",
        "pack_id": PACK_ID,
        "source_run_id": "rag-run-1",
        "source_report_sha256": REPORT_SHA,
        "dataset_sha256": DATASET_SHA,
        "judge_prompt_version": "rag-claim-judge-v2",
        "review_protocol": protocol,
        "judgments": [
            {
                "case_id": claim["claim_id"].split(":", 1)[0],
                "claim_id": claim["claim_id"],
                "claim_sha256": claim["claim_sha256"],
                "answer_sha256": rows_by_case[claim["claim_id"].split(":", 1)[0]][
                    "answer_sha256"
                ],
                "evidence_sha256": rows_by_case[claim["claim_id"].split(":", 1)[0]][
                    "evidence_sha256"
                ],
                "supported": verdict,
                "evidence_refs": (
                    [{"source_path": "help/a.htm", "chunk_idx": "1"}] if verdict else []
                ),
                "notes": "reviewed against supplied evidence",
            }
            for claim, verdict in zip(report_claims, verdicts)
        ],
    }


def test_export_review_template_hides_machine_support_labels():
    template = export_review_template(_report(), REPORT_SHA, reviewer_id="reviewer-a")

    assert template["schema_version"] == "agent-rag-claim-review-v1"
    assert len(template["judgments"]) == 3
    assert all(item["supported"] is None for item in template["judgments"])
    assert all("machine_supported" not in item for item in template["judgments"])
    assert template["source_report_sha256"] == REPORT_SHA
    assert template["pack_id"].startswith("pk_")
    assert "machine reason" not in str(template)
    other = export_review_template(_report(), REPORT_SHA, reviewer_id="reviewer-b")
    assert other["pack_id"] == template["pack_id"]
    assert [item["claim_id"] for item in other["judgments"]] == [
        item["claim_id"] for item in template["judgments"]
    ]

    with pytest.raises(ValueError, match="verdict is incomplete"):
        build_claim_calibration(
            _report(),
            REPORT_SHA,
            template,
            _review("reviewer-b", [True, False, False]),
            _review("adjudicator", [True, True, False], role="adjudicator"),
        )


def test_claim_calibration_reports_kappa_confusion_and_response_error():
    calibration = build_claim_calibration(
        _report(),
        REPORT_SHA,
        _review("reviewer-a", [True, True, False]),
        _review("reviewer-b", [True, False, False]),
        _review("adjudicator", [True, True, False], role="adjudicator"),
    )

    assert calibration["calibration_status"] == "human_adjudicated_gold"
    assert calibration["claim_count"] == 3
    assert calibration["disagreement_claim_ids"] == ["case-1:claim-002"]
    machine = calibration["machine_judge_vs_adjudicated_gold"]
    assert machine["true_positive"] == 1
    assert machine["false_positive"] == 1
    assert machine["false_negative"] == 1
    assert machine["precision"] == machine["recall"] == machine["f1"] == 0.5
    assert calibration["response_groundedness"]["mean_absolute_error"] == 0.75


def test_claim_calibration_fails_closed_on_sha_coverage_and_protocol_drift():
    reviewer_a = _review("reviewer-a", [True, True, False])
    reviewer_b = _review("reviewer-b", [True, False, False])
    adjudication = _review("adjudicator", [True, True, False], role="adjudicator")

    reviewer_a["judgments"][0]["answer_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="answer_sha256 mismatch"):
        build_claim_calibration(_report(), REPORT_SHA, reviewer_a, reviewer_b, adjudication)

    reviewer_a = _review("reviewer-a", [True, True, False])
    reviewer_a["judgments"].pop()
    with pytest.raises(ValueError, match="does not cover every report claim"):
        build_claim_calibration(_report(), REPORT_SHA, reviewer_a, reviewer_b, adjudication)

    reviewer_a = _review("reviewer-a", [True, True, False])
    bad_adjudication = deepcopy(adjudication)
    bad_adjudication["review_protocol"]["reviewer_id"] = "reviewer-a"
    with pytest.raises(ValueError, match="adjudicator ID"):
        build_claim_calibration(_report(), REPORT_SHA, reviewer_a, reviewer_b, bad_adjudication)

    bad_adjudication = deepcopy(adjudication)
    bad_adjudication["review_protocol"]["rubric_version"] = "other-rubric"
    with pytest.raises(ValueError, match="same rubric_version"):
        build_claim_calibration(_report(), REPORT_SHA, reviewer_a, reviewer_b, bad_adjudication)

    bad_adjudication = deepcopy(adjudication)
    bad_adjudication["pack_id"] = "pk_" + "9" * 24
    with pytest.raises(ValueError, match="same non-empty pack_id"):
        build_claim_calibration(
            _report(), REPORT_SHA, reviewer_a, reviewer_b, bad_adjudication
        )

    reviewer_a = _review("reviewer-a", [True, True, False])
    reviewer_a["review_protocol"]["rubric_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="rubric SHA"):
        build_claim_calibration(
            _report(), REPORT_SHA, reviewer_a, reviewer_b, adjudication
        )


def test_rag_adjudication_template_leaves_disagreements_and_locks_consensus():
    reviewer_a = _review("reviewer-a", [True, True, False])
    reviewer_b = _review("reviewer-b", [True, False, False])
    adjudication = export_adjudication_template(
        _report(),
        REPORT_SHA,
        reviewer_a,
        reviewer_b,
        adjudicator_id="adjudicator",
    )

    rows = {row["claim_id"]: row for row in adjudication["judgments"]}
    assert rows["case-1:claim-001"]["supported"] is True
    assert rows["case-1:claim-002"]["supported"] is None
    assert rows["case-2:claim-001"]["supported"] is False
    rows["case-1:claim-002"]["supported"] = True
    rows["case-1:claim-002"]["evidence_refs"] = [
        {"source_path": "help/a.htm", "chunk_idx": "1"}
    ]

    calibration = build_claim_calibration(
        _report(), REPORT_SHA, reviewer_a, reviewer_b, adjudication
    )
    assert calibration["disagreement_claim_ids"] == ["case-1:claim-002"]

    rows["case-1:claim-001"]["supported"] = False
    rows["case-1:claim-001"]["evidence_refs"] = []
    with pytest.raises(ValueError, match="changed reviewer consensus"):
        build_claim_calibration(
            _report(), REPORT_SHA, reviewer_a, reviewer_b, adjudication
        )


def test_canonical_revalidated_export_has_55_blank_verdict_blind_claims():
    report_path = (
        ROOT
        / "benchmarks/reports/agent_rag_groundedness_terra_v2_6case_revalidated.json"
    )
    report_bytes = report_path.read_bytes()
    template = export_review_template(
        json.loads(report_bytes),
        hashlib.sha256(report_bytes).hexdigest(),
        reviewer_id="assigned-reviewer",
    )

    assert len(template["judgments"]) == 55
    assert len({item["case_id"] for item in template["judgments"]}) == 5
    assert all(item["supported"] is None for item in template["judgments"])
    assert all("machine_supported" not in item for item in template["judgments"])
    assert template["review_protocol"]["verdict_blinding_scope"].endswith(
        "natural_ids_visible"
    )
    assert template["review_protocol"]["rubric_sha256"] == RUBRIC_SHA
