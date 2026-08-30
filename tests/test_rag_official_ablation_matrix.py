from __future__ import annotations

import pytest

from benchmarks.rag_official_ablation_matrix import VARIANTS, compare_reports


def _report(rows):
    return {"results": rows}


def test_matrix_is_orthogonal_over_candidate_pool_rerank_and_dedup():
    assert list(VARIANTS) == [
        "dense_top3",
        "dense_top20_no_dedup",
        "dense_top20_with_dedup",
        "minilm_top20_no_dedup",
        "minilm_top20_with_dedup",
    ]
    assert VARIANTS["dense_top20_no_dedup"]["rerank"]["enabled"] is False
    assert VARIANTS["dense_top20_with_dedup"]["source_dedup"] is True
    assert VARIANTS["minilm_top20_no_dedup"]["source_dedup"] is False
    assert VARIANTS["minilm_top20_with_dedup"]["rerank"]["enabled"] is True


def test_paired_comparison_reports_effect_size_interval_and_wins():
    baseline = _report(
        [
            {"id": "a", "hit": False, "reciprocal_rank": 0.0, "ndcg_at_3": 0.0},
            {"id": "b", "hit": True, "reciprocal_rank": 0.5, "ndcg_at_3": 0.4},
        ]
    )
    candidate = _report(
        [
            {"id": "a", "hit": True, "reciprocal_rank": 1.0, "ndcg_at_3": 1.0},
            {"id": "b", "hit": True, "reciprocal_rank": 1.0, "ndcg_at_3": 0.8},
        ]
    )

    comparison = compare_reports(baseline, candidate, top_k=3)

    assert comparison["recall"]["delta"] == 0.5
    assert comparison["mrr"]["delta"] == pytest.approx(0.75)
    assert comparison["ndcg"]["delta"] == pytest.approx(0.7)
    assert comparison["recall_wins"] == 1
    assert comparison["recall_losses"] == 0
    assert comparison["recall_ties"] == 1
    assert comparison["recall"]["low"] <= comparison["recall"]["delta"]
    assert comparison["recall"]["high"] >= comparison["recall"]["delta"]


def test_paired_comparison_rejects_different_case_sets():
    baseline = _report([{"id": "a", "hit": True, "reciprocal_rank": 1.0, "ndcg_at_3": 1.0}])
    candidate = _report([{"id": "b", "hit": True, "reciprocal_rank": 1.0, "ndcg_at_3": 1.0}])

    with pytest.raises(ValueError, match="identical case IDs"):
        compare_reports(baseline, candidate, top_k=3)
