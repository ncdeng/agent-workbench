from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from benchmarks.agent_e2e_reviewer_calibration import (
    RUBRIC_PATH,
    build_reviewer_calibration,
    export_adjudication_template,
    export_review_template,
)


REPORT_SHA = "f" * 64
PACK_ID = "pk_" + "1" * 24
ROOT = Path(__file__).resolve().parents[1]
RUBRIC_SHA = hashlib.sha256(RUBRIC_PATH.read_bytes()).hexdigest()


def _report():
    rows = [
        ("s1", True, True),
        ("s2", False, False),
        ("s3", False, False),
        ("s4", True, False),
    ]
    return {
        "run": {
            "run_id": "run-1",
            "dataset_id": "data-1",
            "dataset_sha256": "a" * 64,
            "manifest_sha256": "b" * 64,
            "manifest_verified": True,
        },
        "groups": {
            "full": {
                "cases": [
                    {
                        "case_id": sample_id,
                        "sample_id": sample_id,
                        "final_response_sha256": str(index) * 64,
                        "execution_success": task,
                        "task_success": task,
                        "strict_grounded_success": strict,
                    }
                    for index, (sample_id, task, strict) in enumerate(rows, start=1)
                ]
            }
        },
    }


def _review(reviewer_id: str, verdicts: list[bool], *, role: str = "independent_reviewer"):
    protocol = {
        "reviewer_id": reviewer_id,
        "reviewer_type": "human",
        "review_role": role,
        "blinded_to_group_and_model": True,
        "blinded_to_other_reviewer": True,
        "blinded_to_machine_grader": True,
        "verdict_blinding_scope": (
            "machine_scores_and_experiment_fields_removed_natural_ids_visible"
        ),
        "ordering_version": "sha256-pack-bound-permutation-v1",
        "rubric_version": "agent-semantic-rubric-v1",
        "rubric_sha256": RUBRIC_SHA,
    }
    if role == "adjudicator":
        protocol["adjudication_of"] = ["reviewer-a", "reviewer-b"]
    return {
        "schema_version": "agent-e2e-semantic-review-v2",
        "pack_id": PACK_ID,
        "source_run_id": "run-1",
        "source_report_sha256": REPORT_SHA,
        "dataset_id": "data-1",
        "dataset_sha256": "a" * 64,
        "review_protocol": protocol,
        "judgments": [
            {
                "sample_id": f"s{index}",
                "response_sha256": str(index) * 64,
                "semantic_execution_success": verdict,
                "grounded_final_response": verdict,
                "notes": "reviewed against tool evidence",
            }
            for index, verdict in enumerate(verdicts, start=1)
        ],
    }


def test_reviewer_calibration_reports_kappa_disagreements_and_machine_confusion():
    calibration = build_reviewer_calibration(
        _report(),
        REPORT_SHA,
        _review("reviewer-a", [True, True, False, False]),
        _review("reviewer-b", [True, False, False, False]),
        _review("adjudicator", [True, True, False, False], role="adjudicator"),
    )

    task_agreement = calibration["reviewer_agreement"]["semantic_task_success"]
    assert task_agreement["agreement_rate"] == 0.75
    assert task_agreement["cohen_kappa"] == 0.5
    assert calibration["disagreement_sample_ids"]["semantic_task_success"] == ["s2"]
    machine = calibration["machine_grader_vs_adjudicated_gold"][
        "task_success_vs_semantic_task"
    ]
    assert machine["true_positive"] == 1
    assert machine["false_positive"] == 1
    assert machine["false_negative"] == 1
    assert machine["true_negative"] == 1
    assert machine["precision"] == machine["recall_sensitivity"] == machine["f1"] == 0.5
    assert calibration["calibration_status"] == "human_adjudicated_gold"


def test_reviewer_calibration_fails_closed_on_identity_and_blinding_drift():
    reviewer_a = _review("reviewer-a", [True, True, False, False])
    reviewer_b = _review("reviewer-b", [True, False, False, False])
    adjudication = _review("adjudicator", [True, True, False, False], role="adjudicator")

    reviewer_a["judgments"][0]["response_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="response SHA mismatch"):
        build_reviewer_calibration(_report(), REPORT_SHA, reviewer_a, reviewer_b, adjudication)

    reviewer_a = _review("reviewer-a", [True, True, False, False])
    reviewer_a["review_protocol"]["blinded_to_machine_grader"] = False
    with pytest.raises(ValueError, match="not fully blinded"):
        build_reviewer_calibration(_report(), REPORT_SHA, reviewer_a, reviewer_b, adjudication)

    reviewer_a = _review("reviewer-a", [True, True, False, False])
    bad_adjudication = deepcopy(adjudication)
    bad_adjudication["review_protocol"]["adjudication_of"] = ["reviewer-a", "other"]
    with pytest.raises(ValueError, match="adjudication_of"):
        build_reviewer_calibration(_report(), REPORT_SHA, reviewer_a, reviewer_b, bad_adjudication)

    bad_adjudication = deepcopy(adjudication)
    bad_adjudication["review_protocol"]["reviewer_id"] = "reviewer-a"
    with pytest.raises(ValueError, match="adjudicator ID"):
        build_reviewer_calibration(_report(), REPORT_SHA, reviewer_a, reviewer_b, bad_adjudication)

    bad_adjudication = deepcopy(adjudication)
    bad_adjudication["review_protocol"]["rubric_version"] = "other-rubric"
    with pytest.raises(ValueError, match="same rubric_version"):
        build_reviewer_calibration(_report(), REPORT_SHA, reviewer_a, reviewer_b, bad_adjudication)

    bad_adjudication = deepcopy(adjudication)
    bad_adjudication["pack_id"] = "pk_" + "9" * 24
    with pytest.raises(ValueError, match="same non-empty pack_id"):
        build_reviewer_calibration(
            _report(), REPORT_SHA, reviewer_a, reviewer_b, bad_adjudication
        )

    reviewer_a = _review("reviewer-a", [True, True, False, False])
    reviewer_a["review_protocol"]["rubric_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="rubric SHA"):
        build_reviewer_calibration(
            _report(), REPORT_SHA, reviewer_a, reviewer_b, adjudication
        )


def test_export_review_template_is_fillable_and_hides_machine_labels():
    dataset = {
        "dataset_id": "data-1",
        "cases": [
            {"case_id": f"s{index}", "turns": [{"content": f"request {index}"}]}
            for index in range(1, 5)
        ],
    }
    manifest = {"dataset_id": "data-1", "dataset_sha256": "a" * 64}

    template = export_review_template(
        _report(),
        report_sha256=REPORT_SHA,
        dataset=dataset,
        dataset_sha256="a" * 64,
        manifest=manifest,
        manifest_sha256="b" * 64,
        reviewer_id="reviewer-a",
    )

    assert template["source_report_sha256"] == REPORT_SHA
    assert template["pack_id"].startswith("pk_")
    assert all(item["semantic_execution_success"] is None for item in template["judgments"])
    serialized = str(template)
    assert "task_success" not in serialized
    assert "strict_grounded_success" not in serialized
    assert "provider" not in serialized
    assert "groups" not in serialized
    other = export_review_template(
        _report(),
        report_sha256=REPORT_SHA,
        dataset=dataset,
        dataset_sha256="a" * 64,
        manifest=manifest,
        manifest_sha256="b" * 64,
        reviewer_id="reviewer-b",
    )
    assert other["pack_id"] == template["pack_id"]
    assert [item["sample_id"] for item in other["judgments"]] == [
        item["sample_id"] for item in template["judgments"]
    ]
    with pytest.raises(ValueError, match="semantic verdict is incomplete"):
        build_reviewer_calibration(
            _report(),
            REPORT_SHA,
            template,
            _review("reviewer-b", [True, False, False, False]),
            _review("adjudicator", [True, True, False, False], role="adjudicator"),
        )


def test_adjudication_template_only_leaves_disagreements_and_locks_consensus():
    reviewer_a = _review("reviewer-a", [True, True, False, False])
    reviewer_b = _review("reviewer-b", [True, False, False, False])
    adjudication = export_adjudication_template(
        _report(),
        REPORT_SHA,
        reviewer_a,
        reviewer_b,
        adjudicator_id="adjudicator",
    )

    rows = {row["sample_id"]: row for row in adjudication["judgments"]}
    assert rows["s1"]["semantic_execution_success"] is True
    assert rows["s2"]["semantic_execution_success"] is None
    assert rows["s3"]["grounded_final_response"] is False
    rows["s2"]["semantic_execution_success"] = True
    rows["s2"]["grounded_final_response"] = True

    calibration = build_reviewer_calibration(
        _report(), REPORT_SHA, reviewer_a, reviewer_b, adjudication
    )
    assert calibration["disagreement_sample_ids"]["semantic_task_success"] == ["s2"]

    rows["s1"]["semantic_execution_success"] = False
    with pytest.raises(ValueError, match="changed reviewer consensus"):
        build_reviewer_calibration(
            _report(), REPORT_SHA, reviewer_a, reviewer_b, adjudication
        )


def test_canonical_repeat3_export_has_21_blank_verdict_blind_samples():
    report_path = ROOT / "benchmarks/reports/agent_e2e_frozen_dev_v1_terra_representative_repeat3_v1.json"
    dataset_path = ROOT / "benchmarks/agent_e2e_frozen_dev_v1.json"
    manifest_path = ROOT / "benchmarks/agent_e2e_frozen_dev_v1.manifest.json"
    report_bytes = report_path.read_bytes()
    dataset_bytes = dataset_path.read_bytes()
    manifest_bytes = manifest_path.read_bytes()
    template = export_review_template(
        json.loads(report_bytes),
        report_sha256=hashlib.sha256(report_bytes).hexdigest(),
        dataset=json.loads(dataset_bytes),
        dataset_sha256=hashlib.sha256(dataset_bytes).hexdigest(),
        manifest=json.loads(manifest_bytes),
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        reviewer_id="assigned-reviewer",
        dataset_bytes=dataset_bytes,
        manifest_bytes=manifest_bytes,
    )

    assert len(template["judgments"]) == 21
    assert all(item["semantic_execution_success"] is None for item in template["judgments"])
    assert all(item["grounded_final_response"] is None for item in template["judgments"])
    assert template["review_protocol"]["verdict_blinding_scope"].endswith(
        "natural_ids_visible"
    )
    assert template["review_protocol"]["rubric_sha256"] == RUBRIC_SHA
