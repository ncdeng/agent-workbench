from __future__ import annotations

from benchmarks.agent_rag_groundedness_revalidate import revalidate_report


def _claim_payload(*, count_is_valid: bool):
    return {
        "claim_count": 1,
        "supported_claim_count": 1 if count_is_valid else 0,
        "unsupported_claim_count": 0 if count_is_valid else 1,
        "groundedness_score": 1.0 if count_is_valid else 0.0,
        "answer_relevance_score": 1.0,
        "citation_entailment": True,
        "claims": [
            {
                "text": "Supported fact.",
                "supported": True,
                "evidence_refs": [{"source_path": "help.htm", "chunk_idx": 0}],
                "reason": "Direct evidence.",
            }
        ],
        "reason": "reviewed",
    }


def _failure_row(case_id: str, *, count_is_valid: bool):
    return {
        "id": case_id,
        "agent_ok": True,
        "evaluation_ok": False,
        "ok": False,
        "evidence": [{"source_path": "help.htm", "chunk_idx": "0", "text": "Supported fact."}],
        "evidence_sha256": "e" * 64,
        "answer_sha256": "a" * 64,
        "retrieval_relevant": True,
        "citations": [{"source_path": "help.htm", "chunk_idx": "0"}],
        "citation_present": True,
        "citation_precision": 1.0,
        "provenance_complete": True,
        "trace_rag_complete": True,
        "latency_ms": 10,
        "error_stage": "judge_validation",
        "error": "JudgeResponseValidationError: invalid",
        "judge_validation_failure": {
            "prompt_version": "rag-claim-judge-v2",
            "prompt_sha256": "p" * 64,
            "raw_response_sha256": "r" * 64,
            "parsed_response": _claim_payload(count_is_valid=count_is_valid),
        },
    }


def test_revalidation_recovers_only_saved_outputs_that_pass_current_validator():
    report = {
        "schema_version": "agent-rag-groundedness-report-v2",
        "run_id": "source-run",
        "execution_contract_sha256": "c" * 64,
        "results": [
            _failure_row("recoverable", count_is_valid=True),
            _failure_row("still-invalid", count_is_valid=False),
        ],
    }

    derived = revalidate_report(report, source_report_sha256="s" * 64)

    assert derived["agent_completion_rate"] == 1.0
    assert derived["judge_valid_rate"] == 0.5
    assert derived["judge_invalid_count"] == 1
    assert derived["groundedness_evaluated_responses"] == 1
    assert derived["citation_presence_rate"] == 1.0
    assert derived["citation_precision"] == 1.0
    assert derived["revalidation"]["recovered_case_ids"] == ["recoverable"]
    assert derived["revalidation"]["still_invalid_case_ids"] == ["still-invalid"]
    assert derived["revalidation"]["new_model_calls"] == 0
    recovered = derived["results"][0]
    assert recovered["judge"]["claims"][0]["claim_id"] == "recoverable:claim-001"
    assert recovered["original_validation_failure"]["error_stage"] == "judge_validation"


def test_revalidation_migrates_pre_denominator_split_agent_status():
    row = _failure_row("legacy-row", count_is_valid=True)
    row.pop("agent_ok")
    row.pop("evaluation_ok")
    report = {
        "schema_version": "agent-rag-groundedness-report-v2",
        "run_id": "legacy-source-run",
        "execution_contract_sha256": "c" * 64,
        "results": [row],
    }

    derived = revalidate_report(report, source_report_sha256="s" * 64)

    assert derived["agent_completion_rate"] == 1.0
    assert derived["judge_valid_rate"] == 1.0
    assert derived["evaluation_success_rate"] == 1.0
