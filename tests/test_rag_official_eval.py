from __future__ import annotations

import hashlib
import json

import pytest

from benchmarks import rag_official_eval as eval_module


def _case(**overrides):
    case = {
        "id": "case-1",
        "query": "query",
        "language": "en",
        "qrels": [
            {
                "source_path": "docs/target.html",
                "relevance": 3,
                "doc_family": "target",
            }
        ],
    }
    case.update(overrides)
    return case


def _install_fake_store(monkeypatch, hits):
    import cst_agent_workbench.rag.chroma_store as store

    monkeypatch.setattr(store, "get_collection_stats", lambda: {"healthy": True})
    monkeypatch.setattr(store, "query_document_knowledge", lambda query, top_k: hits)


def test_validate_cases_accepts_legacy_and_held_out_shapes():
    cases = [_case()]

    assert eval_module._validate_cases(cases) == (cases, "development_pilot")
    assert eval_module._validate_cases({"dataset_role": "held_out_v1", "cases": cases}) == (
        cases,
        "held_out_v1",
    )


@pytest.mark.parametrize("payload", [{}, {"cases": {}}, {"cases": ["not-an-object"]}])
def test_validate_cases_rejects_malformed_payloads(payload):
    with pytest.raises((TypeError, ValueError)):
        eval_module._validate_cases(payload)


def test_ndcg_does_not_reward_repeated_chunks_or_hits_beyond_k(monkeypatch):
    hits = [
        {"source_path": "D:/index/docs/target.html", "source_type": "html", "chunk_idx": i, "score": 0.9}
        for i in range(4)
    ]
    _install_fake_store(monkeypatch, hits)

    report = eval_module.evaluate([_case()], top_k=3)

    assert report["ndcg_at_3"] == pytest.approx(1.0)
    assert report["repeated_source_slot_rate"] == pytest.approx(2 / 3)
    assert len(report["results"][0]["returned_sources"]) == 3


def test_hard_negative_rate_uses_annotated_cases_and_suffix_matching(monkeypatch):
    cases = [
        _case(
            id="with-hard-negative",
            qrels=[
                {"source_path": "docs/target.html", "relevance": 3},
                {
                    "source_path": "docs/wrong.html",
                    "relevance": 0,
                    "hard_negative": True,
                },
            ],
        ),
        _case(id="without-hard-negative"),
    ]
    _install_fake_store(
        monkeypatch,
        [
            {
                "source_path": "D:/index/docs/wrong.html",
                "source_type": "html",
                "chunk_idx": 0,
                "score": 0.8,
            }
        ],
    )

    report = eval_module.evaluate(cases, top_k=3)

    assert report["hard_negative_cases"] == 1
    assert report["hard_negative_top3_intrusion_rate"] == 1.0


def test_empty_qrel_path_never_matches():
    assert not eval_module._source_matches("docs/anything.html", [""])


def test_production_mode_uses_final_reranked_hits(monkeypatch):
    import cst_agent_workbench.rag.knowledge_base as kb

    _install_fake_store(monkeypatch, [])
    monkeypatch.setattr(
        kb,
        "retrieve_official_document_hits",
        lambda query, top_k, rewrite, candidate_k, deduplicate_sources: [
            {
                "source_path": "D:/index/docs/target.html",
                "source_type": "html",
                "chunk_idx": 2,
                "score": 0.7,
                "dense_score": 0.7,
                "rerank_score": 0.95,
                "rank_before": 4,
                "rank_after": 1,
                "rerank_applied": True,
            }
        ],
    )

    report = eval_module.evaluate(
        [_case()],
        top_k=3,
        candidate_k=20,
        source_dedup=False,
        retrieval_mode="production",
    )

    assert report["retrieval_mode"] == "production"
    assert report["candidate_k"] == 20
    assert report["source_dedup"] is False
    assert report["recall_at_3"] == 1.0
    assert report["rerank"] == {
        "applied_slot_rate": 1.0,
        "fallback_cases": 0,
        "changed_top1_rate": 1.0,
    }
    assert report["results"][0]["rerank_scores"] == [0.95]


def test_canonical_presets_pin_official_dataset_and_retrieval_contract():
    manifest_path = eval_module.CANONICAL_MANIFEST
    expected = {
        "heldout_v1_dense_top3": ("dense", False, None),
        "heldout_v1_minilm_top3": (
            "production",
            True,
            "cross-encoder/ms-marco-MiniLM-L6-v2",
        ),
        "heldout_v1_bge_reranker_base_top3": (
            "production",
            True,
            "BAAI/bge-reranker-base",
        ),
    }

    for preset_name, (mode, rerank_enabled, model) in expected.items():
        manifest, preset = eval_module._load_canonical_preset(manifest_path, preset_name)
        identity = eval_module._verify_dataset_identity(
            eval_module._repo_path(preset["cases"]), manifest, preset
        )

        assert identity["sha256"] == manifest["dataset"]["sha256"]
        assert identity["case_count"] == 30
        assert preset["query_field"] == "reference_english_query"
        assert preset["retrieval_mode"] == mode
        assert preset["top_k"] == 3
        assert preset["rerank"]["enabled"] is rerank_enabled
        assert preset["rerank"].get("model") == model


def test_canonical_dataset_verification_detects_exact_byte_drift(tmp_path):
    cases_path = tmp_path / "cases.json"
    payload = {"dataset_role": "held_out_v1", "cases": [_case(id="frozen-1")]}
    original = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    cases_path.write_bytes(original)
    expected_sha = hashlib.sha256(original).hexdigest()
    manifest = {
        "dataset": {
            "path": str(cases_path),
            "role": "held_out_v1",
            "sha256": expected_sha,
            "case_count": 1,
            "case_ids": ["frozen-1"],
        }
    }
    preset = {"cases": str(cases_path)}

    verified = eval_module._verify_dataset_identity(cases_path, manifest, preset)
    assert verified["sha256"] == expected_sha

    cases_path.write_bytes(original + b"\n")
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        eval_module._verify_dataset_identity(cases_path, manifest, preset)


def test_preset_controlled_values_reject_conflicting_overrides():
    assert eval_module._resolve_controlled_value("top-k", None, 3) == 3
    assert eval_module._resolve_controlled_value("top-k", 3, 3) == 3
    with pytest.raises(ValueError, match="conflicting value"):
        eval_module._resolve_controlled_value("top-k", 5, 3)


def test_report_preserves_run_provenance(monkeypatch):
    _install_fake_store(
        monkeypatch,
        [
            {
                "source_path": "D:/index/docs/target.html",
                "source_type": "html",
                "chunk_idx": 0,
                "score": 0.9,
            }
        ],
    )
    provenance = {
        "preset": "heldout_v1_dense_top3",
        "dataset_sha256": "a" * 64,
        "retrieval_config": {"retrieval_mode": "dense", "top_k": 3},
    }

    report = eval_module.evaluate([_case()], top_k=3, run_provenance=provenance)

    assert report["run"] == provenance


def test_canonical_reports_bind_dataset_manifest_preset_and_quality_metrics():
    manifest_path = eval_module.CANONICAL_MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_sha = eval_module._sha256_file(manifest_path)

    for preset_name, report_entry in manifest["reports"].items():
        report_path = eval_module._repo_path(report_entry["path"])
        report = json.loads(report_path.read_text(encoding="utf-8"))
        historical = report_entry["historical_metrics"]

        assert report["run"]["preset"] == preset_name
        assert report["run"]["manifest_sha256"] == manifest_sha
        assert report["run"]["dataset_sha256"] == manifest["dataset"]["sha256"]
        assert report["cases"] == manifest["dataset"]["case_count"]
        assert report["recall_at_3"] == historical["recall_at_3"]
        assert report["mrr"] == historical["mrr"]
        assert report["ndcg_at_3"] == historical["ndcg_at_3"]
        eval_module._verify_index_contract(report["index"], manifest["index_contract"])
