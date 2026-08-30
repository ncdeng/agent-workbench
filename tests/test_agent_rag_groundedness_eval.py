from __future__ import annotations

import pytest

from benchmarks import agent_rag_groundedness_eval as eval_module


def test_select_stratified_cases_takes_fixed_prefix_per_language():
    cases = [
        {"id": "zh1", "language": "zh"},
        {"id": "en1", "language": "en"},
        {"id": "zh2", "language": "zh"},
        {"id": "en2", "language": "en"},
        {"id": "zh3", "language": "zh"},
    ]

    selected = eval_module._select_stratified_cases(cases, per_language=2)

    assert [case["id"] for case in selected] == ["zh1", "en1", "zh2", "en2"]


def test_select_case_ids_preserves_requested_order_and_rejects_unknown_ids():
    cases = [{"id": "a"}, {"id": "b"}]

    assert [case["id"] for case in eval_module._select_case_ids(cases, ["b", "a"])] == [
        "b",
        "a",
    ]
    with pytest.raises(ValueError, match="unknown RAG groundedness case IDs"):
        eval_module._select_case_ids(cases, ["missing"])


def test_extract_citations_requires_source_and_accepts_chunk():
    citations = eval_module._extract_citations(
        "Fact [source=3D/help.htm, chunk=7]. Other [source=DES/plot.htm]."
    )

    assert citations == [
        {"source_path": "3D/help.htm", "chunk_idx": "7"},
        {"source_path": "DES/plot.htm", "chunk_idx": ""},
    ]


def test_source_match_accepts_absolute_hit_and_relative_citation():
    assert eval_module._source_matches(
        "D:/index/CST/3D/help.htm",
        "3D/help.htm",
    )


def test_canonical_evidence_preserves_zero_chunk_index():
    evidence = eval_module._canonical_evidence(
        [{"source_path": "help.htm", "chunk_idx": 0, "text": "first chunk"}]
    )

    assert evidence[0]["chunk_idx"] == "0"


def test_validate_judge_result_accepts_integer_zero_evidence_reference():
    payload = _judge_payload()
    payload["claims"][0]["evidence_refs"][0] = {
        "source_path": "help/solver.htm",
        "chunk_idx": 0,
    }
    evidence = [
        {"source_path": "help/solver.htm", "chunk_idx": "0", "text": "A port is required."}
    ]

    result = eval_module._validate_judge_result(payload, evidence_items=evidence)

    assert result["claims"][0]["evidence_refs"][0]["chunk_idx"] == "0"


def test_parse_json_object_accepts_code_fence():
    assert eval_module._parse_json_object('```json\n{"groundedness_score": 1}\n```') == {
        "groundedness_score": 1
    }


def _judge_payload():
    return {
        "claim_count": 2,
        "supported_claim_count": 1,
        "unsupported_claim_count": 1,
        "groundedness_score": 0.5,
        "answer_relevance_score": 0.8,
        "citation_entailment": True,
        "claims": [
            {
                "text": "The solver requires a port.",
                "supported": True,
                "evidence_refs": [{"source_path": "help/solver.htm", "chunk_idx": "7"}],
                "reason": "Directly stated.",
            },
            {
                "text": "The solver always converges.",
                "supported": False,
                "evidence_refs": [],
                "reason": "Not stated.",
            },
        ],
        "reason": "One of two claims is supported.",
    }


def test_validate_judge_result_binds_atomic_claims_to_supplied_evidence():
    result = eval_module._validate_judge_result(
        _judge_payload(),
        evidence_items=[
            {"source_path": "D:/CST/help/solver.htm", "chunk_idx": "7", "text": "A port is required."}
        ],
    )

    assert result["claim_count"] == 2
    assert result["groundedness_score"] == 0.5
    assert result["claims"][0]["claim_id"] == "claim-001"
    assert len(result["claims"][0]["claim_sha256"]) == 64
    assert result["claims"][0]["evidence_refs"] == [
        {"source_path": "D:/CST/help/solver.htm", "chunk_idx": "7"}
    ]
    assert result["unsupported_claims"] == ["The solver always converges."]


def test_validate_judge_result_fails_closed_on_count_and_evidence_drift():
    payload = _judge_payload()
    payload["claim_count"] = 3
    with pytest.raises(ValueError, match="claim_count"):
        eval_module._validate_judge_result(payload, evidence_items=[])

    payload = _judge_payload()
    payload["claims"][0]["evidence_refs"][0]["chunk_idx"] = "999"
    with pytest.raises(ValueError, match="outside the supplied Top-3"):
        eval_module._validate_judge_result(
            payload,
            evidence_items=[
                {"source_path": "help/solver.htm", "chunk_idx": "7", "text": "A port is required."}
            ],
        )


def test_judge_prompt_fingerprint_is_stable_for_same_material_input():
    evidence = [{"source_path": "help.htm", "chunk_idx": "1", "text": "Evidence"}]
    left = eval_module._build_judge_prompt("Q", "A", evidence)
    right = eval_module._build_judge_prompt("Q", "A", evidence)

    assert left == right
    assert eval_module._sha256_bytes(left.encode("utf-8")) == eval_module._sha256_bytes(
        right.encode("utf-8")
    )


def test_summary_separates_agent_completion_from_judge_validity():
    rows = [
        {
            "agent_ok": True,
            "judge": {
                "claim_count": 2,
                "unsupported_claim_count": 1,
                "groundedness_score": 0.5,
                "answer_relevance_score": 0.8,
            },
            "retrieval_relevant": True,
            "citation_present": True,
            "citation_precision": 1.0,
            "provenance_complete": True,
            "trace_rag_complete": True,
            "latency_ms": 10,
        },
        {
            "agent_ok": True,
            "retrieval_relevant": False,
            "citation_present": True,
            "citation_precision": 1.0,
            "provenance_complete": True,
            "trace_rag_complete": True,
            "latency_ms": 20,
        },
        {"agent_ok": False, "latency_ms": 30},
    ]

    summary = eval_module._summarize_rows(rows)

    assert summary["agent_completion_rate"] == pytest.approx(2 / 3)
    assert summary["judge_valid_rate"] == 0.5
    assert summary["evaluation_success_rate"] == pytest.approx(1 / 3)
    assert summary["judge_invalid_count"] == 1
    assert summary["groundedness_evaluated_responses"] == 1
    assert summary["groundedness_evaluated_claims"] == 2
    assert summary["retrieval_recall_at_3"] == 0.5
    assert summary["retrieval_evaluated_responses"] == 2
    assert summary["citation_presence_evaluated_responses"] == 2
    assert summary["provenance_evaluated_responses"] == 2
    assert summary["trace_evaluated_responses"] == 2
    assert summary["unsupported_claim_rate"] == 0.5
