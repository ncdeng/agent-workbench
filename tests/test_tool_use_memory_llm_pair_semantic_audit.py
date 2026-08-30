import pytest

from benchmarks.tool_use_memory_llm_pair_semantic_audit import build_semantic_audit


def _report():
    return {
        "run": {"run_id": "run-1", "fingerprint": "f" * 64},
        "dataset": {"dataset_sha256": "d" * 64},
        "samples": [
            {
                "sample_id": "case-1::repeat_01::no_memory",
                "case_id": "case-1",
                "arm": "no_memory",
                "task_success": False,
                "final_response_sha256": "1" * 64,
                "presented_tool_order": ["failed_tool", "safe_tool"],
                "allowed_tools": ["failed_tool", "safe_tool"],
            },
            {
                "sample_id": "case-1::repeat_01::learned",
                "case_id": "case-1",
                "arm": "learned",
                "task_success": False,
                "final_response_sha256": "2" * 64,
                "presented_tool_order": ["safe_tool", "failed_tool"],
                "allowed_tools": ["failed_tool", "safe_tool"],
            },
        ],
    }


def _review():
    return {
        "schema_version": "tool-use-memory-pair-semantic-review-v1",
        "source_run_id": "run-1",
        "source_run_fingerprint": "f" * 64,
        "dataset_sha256": "d" * 64,
        "review_protocol": {"reviewer_id": "single-reviewer"},
        "judgments": [
            {
                "sample_id": "case-1::repeat_01::no_memory",
                "response_sha256": "1" * 64,
                "semantic_task_success": False,
                "grounded_final_response": False,
                "notes": "wrong argument",
            },
            {
                "sample_id": "case-1::repeat_01::learned",
                "response_sha256": "2" * 64,
                "semantic_task_success": True,
                "grounded_final_response": True,
                "notes": "extra safe call but task complete",
            },
        ],
    }


def test_memory_semantic_audit_separates_exact_sequence_from_semantic_success():
    audit = build_semantic_audit(_report(), _review())

    assert audit["arms"]["no_memory"]["semantic_task_success"]["rate"] == 0.0
    assert audit["arms"]["learned"]["semantic_task_success"]["rate"] == 1.0
    assert audit["arms"]["learned"]["memory_order_change_rate"] == 1.0
    assert audit["paired_case_majority"]["paired_test"]["treatment_wins"] == 1
    assert audit["machine_vs_semantic"]["false_negative"] == 1


def test_memory_semantic_audit_rejects_response_or_run_drift():
    review = _review()
    review["judgments"][0]["response_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="response SHA mismatch"):
        build_semantic_audit(_report(), review)

    review = _review()
    review["source_run_fingerprint"] = "0" * 64
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        build_semantic_audit(_report(), review)
