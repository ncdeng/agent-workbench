from __future__ import annotations

import pytest

from benchmarks.agent_e2e_semantic_audit import build_semantic_audit


def _report():
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
                        "case_id": "case-a",
                        "sample_id": "case-a::repeat_01",
                        "final_response_sha256": "1" * 64,
                        "task_success": False,
                        "strict_grounded_success": False,
                    },
                    {
                        "case_id": "case-a",
                        "sample_id": "case-a::repeat_02",
                        "final_response_sha256": "2" * 64,
                        "task_success": True,
                        "strict_grounded_success": False,
                    },
                ]
            }
        },
    }


def _manifest():
    return {
        "dataset_id": "data-1",
        "dataset_sha256": "a" * 64,
        "case_ids": ["case-a"],
    }


def _identity(sha256: str, name: str) -> dict[str, object]:
    return {"name": name, "sha256": sha256, "size_bytes": 123}


def _build(report=None, review=None, manifest=None):
    return build_semantic_audit(
        report or _report(),
        review or _review(),
        source_report_identity=_identity("c" * 64, "report.json"),
        semantic_review_identity=_identity("d" * 64, "review.json"),
        manifest=manifest or _manifest(),
        manifest_identity=_identity("b" * 64, "manifest.json"),
        auditor_identity=_identity("e" * 64, "agent_e2e_semantic_audit.py"),
    )


def _review():
    return {
        "schema_version": "agent-e2e-semantic-review-v1",
        "source_run_id": "run-1",
        "dataset_id": "data-1",
        "dataset_sha256": "a" * 64,
        "review_protocol": {"reviewer_id": "reviewer"},
        "judgments": [
            {
                "sample_id": "case-a::repeat_01",
                "response_sha256": "1" * 64,
                "semantic_execution_success": True,
                "grounded_final_response": True,
            },
            {
                "sample_id": "case-a::repeat_02",
                "response_sha256": "2" * 64,
                "semantic_execution_success": False,
                "grounded_final_response": False,
            },
        ],
    }


def test_semantic_audit_separates_execution_grounding_and_machine_disagreement():
    audit = _build()

    assert audit["schema_version"] == "agent-e2e-semantic-audit-v2"
    assert audit["provenance"]["source_report"]["sha256"] == "c" * 64
    assert audit["provenance"]["manifest_case_order_verified"] is True
    assert audit["metrics"]["semantic_execution_success_rate"] == 0.5
    assert audit["metrics"]["semantic_task_success_rate"] == 0.5
    assert audit["metrics"]["unstable_case_ids"] == ["case-a"]
    assert audit["metrics"]["machine_task_vs_semantic"] == {
        "true_positive": 0,
        "false_positive": 1,
        "false_negative": 1,
        "true_negative": 0,
        "agreement_rate": 0.0,
    }


def test_semantic_audit_rejects_response_drift_and_missing_samples():
    review = _review()
    review["judgments"][0]["response_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="response SHA mismatch"):
        _build(review=review)

    review = _review()
    review["judgments"].pop()
    with pytest.raises(ValueError, match="sample mismatch"):
        _build(review=review)


def test_semantic_audit_rejects_manifest_drift_and_unverified_source_report():
    report = _report()
    report["run"]["manifest_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="manifest SHA"):
        _build(report=report)

    report = _report()
    report["run"]["manifest_verified"] = False
    with pytest.raises(ValueError, match="did not verify"):
        _build(report=report)


def test_semantic_audit_rejects_manifest_case_order_drift():
    report = _report()
    report["groups"]["full"]["cases"].append(
        {
            "case_id": "case-b",
            "sample_id": "case-b::repeat_01",
            "final_response_sha256": "3" * 64,
            "task_success": True,
            "strict_grounded_success": True,
        }
    )
    review = _review()
    review["judgments"].append(
        {
            "sample_id": "case-b::repeat_01",
            "response_sha256": "3" * 64,
            "semantic_execution_success": True,
            "grounded_final_response": True,
        }
    )
    manifest = _manifest()
    manifest["case_ids"] = ["case-b", "case-a"]

    with pytest.raises(ValueError, match="manifest order"):
        _build(report=report, review=review, manifest=manifest)
