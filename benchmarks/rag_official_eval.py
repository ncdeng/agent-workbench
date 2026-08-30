"""Evaluate CST official-document retrieval with provenance-aware metrics."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = Path(__file__).with_name("rag_official_docs_cases.json")
CANONICAL_MANIFEST = Path(__file__).with_name("rag_official_canonical.json")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _repo_path(value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def _load_canonical_preset(
    manifest_path: Path,
    preset_name: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    presets = manifest.get("presets") or {}
    if preset_name not in presets:
        raise ValueError(
            f"unknown canonical RAG preset {preset_name!r}; "
            f"available: {', '.join(sorted(presets))}"
        )
    return manifest, dict(presets[preset_name])


def _verify_dataset_identity(
    cases_path: Path,
    manifest: dict[str, Any],
    preset: dict[str, Any],
) -> dict[str, Any]:
    dataset = dict(manifest.get("dataset") or {})
    expected_path = _repo_path(str(dataset.get("path") or ""))
    preset_path = _repo_path(str(preset.get("cases") or ""))
    actual_path = cases_path.resolve()
    if actual_path != expected_path or preset_path != expected_path:
        raise ValueError(
            "canonical preset must use the frozen dataset path "
            f"{expected_path}; got {actual_path}"
        )
    expected_sha = str(dataset.get("sha256") or "").lower()
    actual_sha = _sha256_file(actual_path)
    if actual_sha != expected_sha:
        raise ValueError(
            "canonical RAG dataset SHA256 mismatch: "
            f"expected {expected_sha}, got {actual_sha}"
        )
    payload = json.loads(actual_path.read_text(encoding="utf-8"))
    cases, role = _validate_cases(payload)
    case_ids = [str(case.get("id") or "") for case in cases]
    if role != str(dataset.get("role") or ""):
        raise ValueError(f"canonical dataset role mismatch: expected {dataset.get('role')}, got {role}")
    if len(cases) != int(dataset.get("case_count") or -1):
        raise ValueError("canonical RAG dataset case count does not match manifest")
    if case_ids != [str(value) for value in dataset.get("case_ids") or []]:
        raise ValueError("canonical RAG dataset case IDs do not match manifest")
    return {
        "path": str(actual_path),
        "sha256": actual_sha,
        "role": role,
        "case_count": len(cases),
    }


def _verify_index_contract(index: dict[str, Any], contract: dict[str, Any]) -> None:
    field_map = {
        "chunk_count": "count",
        "collection": "collection",
        "schema_version": "schema_version",
        "embedding_provider": "embedding_provider",
        "embedding_model": "embedding_model",
        "embedding_query_instruction": "embedding_query_instruction",
        "document_language": "document_language",
        "query_expansion": "query_expansion",
        "multi_query_fusion": "multi_query_fusion",
    }
    mismatches = []
    for expected_name, actual_name in field_map.items():
        expected = contract.get(expected_name)
        actual = index.get(actual_name)
        if isinstance(expected, str):
            matches = str(actual or "").strip() == expected.strip()
        else:
            matches = actual == expected
        if not matches:
            mismatches.append(f"{expected_name}: expected {expected!r}, got {actual!r}")
    if mismatches:
        raise ValueError("canonical RAG index contract mismatch: " + "; ".join(mismatches))


def _git_revision() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


@contextmanager
def _retrieval_runtime_config(retrieval_config: dict[str, Any]):
    from cst_agent_workbench import config

    reranker = dict(retrieval_config.get("rerank") or {})
    updates = {
        "RAG_RERANK_ENABLED": bool(reranker.get("enabled")),
        "RAG_RERANK_MODEL": str(reranker.get("model") or config.RAG_RERANK_MODEL),
        "RAG_RERANK_CANDIDATE_K": int(
            reranker.get("candidate_k") or config.RAG_RERANK_CANDIDATE_K
        ),
        "RAG_RERANK_BATCH_SIZE": int(
            reranker.get("batch_size") or config.RAG_RERANK_BATCH_SIZE
        ),
    }
    previous = {name: getattr(config, name) for name in updates}
    try:
        for name, value in updates.items():
            setattr(config, name, value)
        yield
    finally:
        for name, value in previous.items():
            setattr(config, name, value)


def _resolve_controlled_value(name: str, supplied: Any, expected: Any) -> Any:
    if supplied is not None and supplied != expected:
        raise ValueError(
            f"--preset controls {name}; expected {expected!r}, got conflicting value {supplied!r}"
        )
    return expected


def _normalize_source(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip().lower()


def _source_matches(actual: str, expected_sources: list[str]) -> bool:
    normalized = _normalize_source(actual)
    expected = [_normalize_source(value) for value in expected_sources]
    return bool(normalized) and any(value and normalized.endswith(value) for value in expected)


def _matching_qrels(actual: str, case: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        qrel
        for qrel in case.get("qrels") or []
        if _source_matches(actual, [str(qrel.get("source_path") or "")])
    ]


def _source_relevance(actual: str, case: dict[str, Any]) -> int:
    qrels = case.get("qrels") or []
    if qrels:
        return max(
            (int(qrel.get("relevance", 0)) for qrel in _matching_qrels(actual, case)),
            default=0,
        )
    return 3 if _source_matches(actual, case.get("expected_sources") or []) else 0


def _dcg(relevances: list[int]) -> float:
    return sum(
        ((2 ** int(relevance)) - 1) / math.log2(rank + 1)
        for rank, relevance in enumerate(relevances, start=1)
    )


def _validate_cases(payload: Any) -> tuple[list[dict[str, Any]], str]:
    if isinstance(payload, dict):
        if "cases" not in payload:
            raise ValueError("object-form evaluation dataset must contain a 'cases' list")
        cases = payload["cases"]
        dataset_role = str(payload.get("dataset_role") or "held_out")
    else:
        cases = payload
        dataset_role = "development_pilot"
    if not isinstance(cases, list) or not all(isinstance(case, dict) for case in cases):
        raise TypeError("evaluation cases must be a list of JSON objects")
    return cases, dataset_role


def evaluate(
    cases: list[dict[str, Any]],
    *,
    top_k: int,
    candidate_k: int | None = None,
    source_dedup: bool = True,
    query_field: str = "query",
    dataset_role: str = "development_pilot",
    retrieval_mode: str = "dense",
    warmup_query: str = "",
    run_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from cst_agent_workbench.rag.chroma_store import (
        get_collection_stats,
        query_document_knowledge,
    )
    from cst_agent_workbench.rag.knowledge_base import retrieve_official_document_hits

    index = get_collection_stats()
    if not index.get("healthy"):
        raise RuntimeError(f"RAG index is not healthy: {index.get('error') or index}")

    warmup_ms = 0.0
    if warmup_query:
        warmup_started = time.perf_counter()
        if retrieval_mode == "production":
            retrieve_official_document_hits(
                warmup_query,
                top_k=top_k,
                rewrite=False,
                candidate_k=candidate_k,
                deduplicate_sources=source_dedup,
            )
        elif retrieval_mode == "dense":
            query_document_knowledge(warmup_query, top_k=top_k)
        else:
            raise ValueError(f"unsupported retrieval_mode: {retrieval_mode}")
        warmup_ms = (time.perf_counter() - warmup_started) * 1000.0

    rows = []
    reciprocal_ranks = []
    latencies_ms = []
    provenance_fields = ("source_path", "source_type", "chunk_idx", "score")
    provenance_total = 0
    provenance_complete = 0
    repeated_source_slots = 0
    repeated_family_slots = 0
    returned_slots = 0
    ndcg_scores = []
    hard_negative_intrusions = 0
    hard_negative_cases = 0
    rerank_applied_slots = 0
    rerank_fallback_cases = 0
    rerank_changed_top1_cases = 0

    for case in cases:
        retrieval_query = str(case.get(query_field) or case["query"])
        started = time.perf_counter()
        if retrieval_mode == "production":
            hits = retrieve_official_document_hits(
                retrieval_query,
                top_k=top_k,
                rewrite=False,
                candidate_k=candidate_k,
                deduplicate_sources=source_dedup,
            )[:top_k]
        elif retrieval_mode == "dense":
            hits = query_document_knowledge(retrieval_query, top_k=top_k)[:top_k]
        else:
            raise ValueError(f"unsupported retrieval_mode: {retrieval_mode}")
        latency_ms = (time.perf_counter() - started) * 1000.0
        latencies_ms.append(latency_ms)
        rerank_applied_slots += sum(bool(hit.get("rerank_applied")) for hit in hits)
        if retrieval_mode == "production" and hits:
            rerank_fallback_cases += int(not any(hit.get("rerank_applied") for hit in hits))
            rerank_changed_top1_cases += int(int(hits[0].get("rank_before") or 1) != 1)

        rank = 0
        seen_sources: set[str] = set()
        seen_families: set[str] = set()
        returned_relevances: list[int] = []
        hard_negative_paths = [
            str(qrel.get("source_path") or "")
            for qrel in case.get("qrels") or []
            if qrel.get("hard_negative") and qrel.get("source_path")
        ]
        hard_negative_cases += int(bool(hard_negative_paths))
        case_hard_negative_intrusion = False
        for index_in_hits, hit in enumerate(hits, start=1):
            source_path = str(hit.get("source_path") or "")
            relevance = _source_relevance(source_path, case)
            normalized_source = _normalize_source(source_path)
            is_repeated_source = bool(normalized_source and normalized_source in seen_sources)
            # A source earns gain once. Repeated chunks consume a result slot but
            # must not inflate nDCG beyond 1.0.
            returned_relevances.append(0 if is_repeated_source else relevance)
            if not rank and relevance >= 2:
                rank = index_in_hits
            provenance_total += 1
            if all(hit.get(field) not in (None, "") for field in provenance_fields):
                provenance_complete += 1
            if is_repeated_source:
                repeated_source_slots += 1
            if normalized_source:
                seen_sources.add(normalized_source)
            matching_qrels = _matching_qrels(source_path, case)
            family = _normalize_source(
                next(
                    (
                        qrel.get("doc_family") or qrel.get("source_path")
                        for qrel in matching_qrels
                    ),
                    normalized_source,
                )
            )
            if family and family in seen_families:
                repeated_family_slots += 1
            if family:
                seen_families.add(family)
            if index_in_hits <= 3 and _source_matches(source_path, hard_negative_paths):
                case_hard_negative_intrusion = True
            returned_slots += 1

        ideal_by_source: dict[str, int] = {}
        for qrel in case.get("qrels") or []:
            source = _normalize_source(qrel.get("source_path"))
            if source:
                ideal_by_source[source] = max(
                    ideal_by_source.get(source, 0),
                    int(qrel.get("relevance", 0)),
                )
        ideal_relevances = sorted(ideal_by_source.values(), reverse=True)[:top_k]
        if not ideal_relevances:
            ideal_relevances = [3] if case.get("expected_sources") else []
        ideal_dcg = _dcg(ideal_relevances)
        ndcg = _dcg(returned_relevances) / ideal_dcg if ideal_dcg else 0.0
        ndcg_scores.append(ndcg)
        hard_negative_intrusions += int(case_hard_negative_intrusion)

        reciprocal_rank = 1.0 / rank if rank else 0.0
        reciprocal_ranks.append(reciprocal_rank)
        rows.append(
            {
                **case,
                "retrieval_query": retrieval_query,
                "hit": bool(rank),
                "rank": rank or None,
                "reciprocal_rank": reciprocal_rank,
                f"ndcg_at_{top_k}": ndcg,
                "latency_ms": round(latency_ms, 2),
                "returned_sources": [hit.get("source_path", "") for hit in hits],
                "scores": [hit.get("score") for hit in hits],
                "dense_scores": [hit.get("dense_score", hit.get("score")) for hit in hits],
                "rerank_scores": [hit.get("rerank_score") for hit in hits],
                "rank_before": [hit.get("rank_before") for hit in hits],
                "rank_after": [hit.get("rank_after") for hit in hits],
                "rerank_applied": [bool(hit.get("rerank_applied")) for hit in hits],
            }
        )

    count = len(cases)
    sorted_latencies = sorted(latencies_ms)
    p95_index = max(0, min(len(sorted_latencies) - 1, int(round(0.95 * len(sorted_latencies) + 0.5)) - 1))
    by_language: dict[str, dict[str, float]] = {}
    for language in sorted({str(case.get("language") or "unknown") for case in cases}):
        subset = [row for row in rows if row.get("language") == language]
        by_language[language] = {
            "cases": len(subset),
            f"recall_at_{top_k}": sum(bool(row["hit"]) for row in subset) / len(subset),
            "mrr": sum(float(row["reciprocal_rank"]) for row in subset) / len(subset),
            f"ndcg_at_{top_k}": sum(float(row[f"ndcg_at_{top_k}"]) for row in subset) / len(subset),
        }

    return {
        "run": dict(run_provenance) if run_provenance is not None else None,
        "index": index,
        "dataset_role": dataset_role,
        "query_field": query_field,
        "retrieval_mode": retrieval_mode,
        "warmup_ms": round(warmup_ms, 2),
        "cases": count,
        "top_k": top_k,
        "candidate_k": candidate_k if candidate_k is not None else top_k,
        "source_dedup": source_dedup,
        f"recall_at_{top_k}": sum(bool(row["hit"]) for row in rows) / count if count else 0.0,
        "mrr": statistics.fmean(reciprocal_ranks) if reciprocal_ranks else 0.0,
        f"ndcg_at_{top_k}": statistics.fmean(ndcg_scores) if ndcg_scores else 0.0,
        "provenance_completeness": provenance_complete / provenance_total if provenance_total else 0.0,
        "repeated_source_slot_rate": repeated_source_slots / returned_slots if returned_slots else 0.0,
        "repeated_family_slot_rate": repeated_family_slots / returned_slots if returned_slots else 0.0,
        "hard_negative_cases": hard_negative_cases,
        "hard_negative_top3_intrusion_rate": (
            hard_negative_intrusions / hard_negative_cases if hard_negative_cases else 0.0
        ),
        "rerank": {
            "applied_slot_rate": rerank_applied_slots / returned_slots if returned_slots else 0.0,
            "fallback_cases": rerank_fallback_cases,
            "changed_top1_rate": rerank_changed_top1_cases / count if count else 0.0,
        },
        "latency_ms": {
            "mean": round(statistics.fmean(latencies_ms), 2) if latencies_ms else 0.0,
            "p50": round(statistics.median(latencies_ms), 2) if latencies_ms else 0.0,
            "p95": round(sorted_latencies[p95_index], 2) if sorted_latencies else 0.0,
        },
        "by_language": by_language,
        "results": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=None)
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument(
        "--candidate-k",
        type=int,
        default=None,
        help="Dense candidate pool size before optional reranking and source deduplication",
    )
    parser.add_argument(
        "--source-dedup",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Keep at most one returned chunk per source after ranking",
    )
    parser.add_argument(
        "--query-field",
        default=None,
        help="Use original user queries or controlled English retrieval queries",
    )
    parser.add_argument(
        "--retrieval-mode",
        choices=("dense", "production"),
        default=None,
        help="Evaluate raw dense retrieval or the production dense-plus-rerank pipeline",
    )
    parser.add_argument(
        "--warmup-query",
        default=None,
        help="Optional unscored query used to separate model cold start from measured latency",
    )
    parser.add_argument(
        "--preset",
        default=None,
        help="Canonical manifest preset; controls dataset, query field, mode, K and reranker",
    )
    parser.add_argument("--manifest", type=Path, default=CANONICAL_MANIFEST)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    manifest: dict[str, Any] | None = None
    preset: dict[str, Any] | None = None
    dataset_identity: dict[str, Any] | None = None
    if args.preset:
        manifest, preset = _load_canonical_preset(args.manifest, args.preset)
        expected_cases = _repo_path(str(preset["cases"]))
        cases_path = Path(
            _resolve_controlled_value(
                "cases",
                args.cases.resolve() if args.cases is not None else None,
                expected_cases,
            )
        )
        top_k = int(_resolve_controlled_value("top-k", args.top_k, int(preset["top_k"])))
        query_field = str(
            _resolve_controlled_value("query-field", args.query_field, preset["query_field"])
        )
        retrieval_mode = str(
            _resolve_controlled_value(
                "retrieval-mode", args.retrieval_mode, preset["retrieval_mode"]
            )
        )
        preset_rerank = dict(preset.get("rerank") or {})
        candidate_k = int(
            _resolve_controlled_value(
                "candidate-k",
                args.candidate_k,
                int(preset_rerank.get("candidate_k") or top_k),
            )
        )
        source_dedup = bool(
            _resolve_controlled_value(
                "source-dedup",
                args.source_dedup,
                bool(preset.get("source_dedup", retrieval_mode == "production")),
            )
        )
        warmup_query = str(
            _resolve_controlled_value(
                "warmup-query", args.warmup_query, str(preset.get("warmup_query") or "")
            )
        )
        dataset_identity = _verify_dataset_identity(cases_path, manifest, preset)
        retrieval_config = {
            "query_field": query_field,
            "retrieval_mode": retrieval_mode,
            "top_k": top_k,
            "candidate_k": candidate_k,
            "warmup_query": warmup_query,
            "rerank": preset_rerank,
            "source_dedup": source_dedup,
        }
        run_provenance = {
            "preset": args.preset,
            "manifest_path": str(args.manifest.resolve()),
            "manifest_sha256": _sha256_file(args.manifest),
            "dataset_path": dataset_identity["path"],
            "dataset_sha256": dataset_identity["sha256"],
            "dataset_case_count": dataset_identity["case_count"],
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "code_revision": _git_revision(),
            "retrieval_config": retrieval_config,
        }
    else:
        cases_path = args.cases or DEFAULT_CASES
        top_k = max(1, int(args.top_k or 3))
        query_field = str(args.query_field or "query")
        retrieval_mode = str(args.retrieval_mode or "dense")
        candidate_k = max(top_k, int(args.candidate_k or top_k))
        source_dedup = bool(
            args.source_dedup
            if args.source_dedup is not None
            else retrieval_mode == "production"
        )
        warmup_query = str(args.warmup_query or "")
        retrieval_config = {
            "query_field": query_field,
            "retrieval_mode": retrieval_mode,
            "top_k": top_k,
            "candidate_k": candidate_k,
            "warmup_query": warmup_query,
            "rerank": {},
            "source_dedup": retrieval_mode == "production",
        }
        run_provenance = {
            "preset": None,
            "dataset_path": str(cases_path.resolve()),
            "dataset_sha256": _sha256_file(cases_path),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "code_revision": _git_revision(),
            "retrieval_config": retrieval_config,
        }

    payload = json.loads(cases_path.read_text(encoding="utf-8"))
    cases, dataset_role = _validate_cases(payload)
    with _retrieval_runtime_config(retrieval_config):
        report = evaluate(
            cases,
            top_k=top_k,
            candidate_k=candidate_k,
            source_dedup=source_dedup,
            query_field=query_field,
            dataset_role=dataset_role,
            retrieval_mode=retrieval_mode,
            warmup_query=warmup_query,
            run_provenance=run_provenance,
        )
    if manifest is not None:
        _verify_index_contract(report["index"], dict(manifest.get("index_contract") or {}))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(
            json.dumps(
                {
                    "preset": report["run"].get("preset") if report.get("run") else None,
                    "cases": report["cases"],
                    f"recall_at_{top_k}": report[f"recall_at_{top_k}"],
                    "mrr": report["mrr"],
                    f"ndcg_at_{top_k}": report[f"ndcg_at_{top_k}"],
                    "latency_p95_ms": report["latency_ms"]["p95"],
                    "output": str(args.output.resolve()),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(rendered)


if __name__ == "__main__":
    main()
