"""Run an orthogonal retrieval ablation over the frozen CST official-doc set."""
from __future__ import annotations

import argparse
import json
import random
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchmarks import rag_official_eval as official_eval


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "benchmarks" / "reports" / "rag_official_ablation_matrix_v1.json"

VARIANTS: dict[str, dict[str, Any]] = {
    "dense_top3": {
        "retrieval_mode": "dense",
        "candidate_k": 3,
        "source_dedup": False,
        "rerank": {"enabled": False},
    },
    "dense_top20_no_dedup": {
        "retrieval_mode": "production",
        "candidate_k": 20,
        "source_dedup": False,
        "rerank": {"enabled": False, "candidate_k": 20},
    },
    "dense_top20_with_dedup": {
        "retrieval_mode": "production",
        "candidate_k": 20,
        "source_dedup": True,
        "rerank": {"enabled": False, "candidate_k": 20},
    },
    "minilm_top20_no_dedup": {
        "retrieval_mode": "production",
        "candidate_k": 20,
        "source_dedup": False,
        "rerank": {
            "enabled": True,
            "model": "cross-encoder/ms-marco-MiniLM-L6-v2",
            "candidate_k": 20,
            "batch_size": 16,
        },
    },
    "minilm_top20_with_dedup": {
        "retrieval_mode": "production",
        "candidate_k": 20,
        "source_dedup": True,
        "rerank": {
            "enabled": True,
            "model": "cross-encoder/ms-marco-MiniLM-L6-v2",
            "candidate_k": 20,
            "batch_size": 16,
        },
    },
}


def _paired_bootstrap_interval(
    deltas: list[float],
    *,
    iterations: int = 5000,
    seed: int = 20260810,
) -> dict[str, Any]:
    if not deltas:
        return {"delta": None, "low": None, "high": None, "method": "paired_bootstrap_95"}
    rng = random.Random(seed)
    count = len(deltas)
    samples = sorted(
        statistics.fmean(deltas[rng.randrange(count)] for _ in range(count))
        for _ in range(iterations)
    )
    low_index = max(0, int(iterations * 0.025) - 1)
    high_index = min(iterations - 1, int(iterations * 0.975))
    return {
        "delta": statistics.fmean(deltas),
        "low": samples[low_index],
        "high": samples[high_index],
        "iterations": iterations,
        "seed": seed,
        "method": "paired_bootstrap_95",
    }


def compare_reports(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    top_k: int,
) -> dict[str, Any]:
    baseline_rows = {str(row["id"]): row for row in baseline["results"]}
    candidate_rows = {str(row["id"]): row for row in candidate["results"]}
    if baseline_rows.keys() != candidate_rows.keys():
        raise ValueError("RAG ablation reports must contain identical case IDs")

    ordered_ids = list(baseline_rows)
    recall_deltas = [
        float(bool(candidate_rows[case_id]["hit"])) - float(bool(baseline_rows[case_id]["hit"]))
        for case_id in ordered_ids
    ]
    mrr_deltas = [
        float(candidate_rows[case_id]["reciprocal_rank"])
        - float(baseline_rows[case_id]["reciprocal_rank"])
        for case_id in ordered_ids
    ]
    ndcg_key = f"ndcg_at_{top_k}"
    ndcg_deltas = [
        float(candidate_rows[case_id][ndcg_key]) - float(baseline_rows[case_id][ndcg_key])
        for case_id in ordered_ids
    ]
    recall_wins = sum(delta > 0 for delta in recall_deltas)
    recall_losses = sum(delta < 0 for delta in recall_deltas)
    return {
        "case_count": len(ordered_ids),
        "recall": _paired_bootstrap_interval(recall_deltas),
        "mrr": _paired_bootstrap_interval(mrr_deltas),
        "ndcg": _paired_bootstrap_interval(ndcg_deltas),
        "recall_wins": recall_wins,
        "recall_losses": recall_losses,
        "recall_ties": len(ordered_ids) - recall_wins - recall_losses,
    }


def run_matrix(
    *,
    manifest_path: Path = official_eval.CANONICAL_MANIFEST,
    query_field: str = "reference_english_query",
    top_k: int = 3,
    warmup_query: str = "How do I configure a CST solver?",
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    canonical_preset = dict(manifest["presets"]["heldout_v1_dense_top3"])
    cases_path = official_eval._repo_path(canonical_preset["cases"])
    dataset_identity = official_eval._verify_dataset_identity(
        cases_path,
        manifest,
        canonical_preset,
    )
    payload = json.loads(cases_path.read_text(encoding="utf-8"))
    cases, dataset_role = official_eval._validate_cases(payload)

    reports: dict[str, dict[str, Any]] = {}
    for name, variant in VARIANTS.items():
        retrieval_config = {
            "query_field": query_field,
            "retrieval_mode": variant["retrieval_mode"],
            "top_k": top_k,
            "candidate_k": variant["candidate_k"],
            "warmup_query": warmup_query,
            "source_dedup": variant["source_dedup"],
            "rerank": dict(variant["rerank"]),
        }
        provenance = {
            "matrix_schema": "rag-official-ablation-matrix-v1",
            "variant": name,
            "manifest_path": str(manifest_path.resolve()),
            "manifest_sha256": official_eval._sha256_file(manifest_path),
            "dataset_path": dataset_identity["path"],
            "dataset_sha256": dataset_identity["sha256"],
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "code_revision": official_eval._git_revision(),
            "retrieval_config": retrieval_config,
        }
        with official_eval._retrieval_runtime_config(retrieval_config):
            report = official_eval.evaluate(
                cases,
                top_k=top_k,
                candidate_k=int(variant["candidate_k"]),
                source_dedup=bool(variant["source_dedup"]),
                query_field=query_field,
                dataset_role=dataset_role,
                retrieval_mode=str(variant["retrieval_mode"]),
                warmup_query=warmup_query,
                run_provenance=provenance,
            )
        official_eval._verify_index_contract(report["index"], dict(manifest["index_contract"]))
        reports[name] = report

    baseline = reports["dense_top3"]
    comparisons = {
        f"{name}_vs_dense_top3": compare_reports(baseline, report, top_k=top_k)
        for name, report in reports.items()
        if name != "dense_top3"
    }
    return {
        "schema_version": "rag-official-ablation-matrix-v1",
        "dataset": dataset_identity,
        "query_field": query_field,
        "top_k": top_k,
        "variant_order": list(VARIANTS),
        "variants": reports,
        "comparisons": comparisons,
        "limitations": [
            "The frozen 30-case dataset is developer-visible, not blinded.",
            "Reference English queries isolate retrieval ranking but do not measure production query translation.",
            "Qrels are graded but not exhaustive, and the set contains only one annotated hard-negative case.",
            "Latency is a same-machine observation, not a deployment SLA.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=official_eval.CANONICAL_MANIFEST)
    parser.add_argument("--query-field", default="reference_english_query")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--warmup-query", default="How do I configure a CST solver?")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    report = run_matrix(
        manifest_path=args.manifest,
        query_field=str(args.query_field),
        top_k=max(1, int(args.top_k)),
        warmup_query=str(args.warmup_query),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        name: {
            "recall_at_3": variant.get("recall_at_3"),
            "mrr": variant["mrr"],
            "ndcg_at_3": variant.get("ndcg_at_3"),
            "latency_p95_ms": variant["latency_ms"]["p95"],
        }
        for name, variant in report["variants"].items()
    }
    print(json.dumps({"variants": summary, "output": str(args.output.resolve())}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
