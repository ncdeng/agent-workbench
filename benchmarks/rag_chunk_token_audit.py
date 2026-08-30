"""Audit indexed CST chunks against the active embedding tokenizer limit."""
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any


def _percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    return int(ordered[index])


def audit() -> dict[str, Any]:
    from transformers import AutoTokenizer

    from cst_agent_workbench import config
    from cst_agent_workbench.rag.chroma_store import _get_collection, get_collection_stats

    collection = _get_collection()
    if collection is None:
        raise RuntimeError("active Chroma collection is unavailable")
    payload = collection.get(include=["documents"])
    documents = [str(text or "") for text in payload.get("documents") or []]
    tokenizer = AutoTokenizer.from_pretrained(config.EMBEDDING_LOCAL_MODEL)
    model_limit = int(getattr(tokenizer, "model_max_length", 512))

    token_lengths: list[int] = []
    for start in range(0, len(documents), 256):
        encoded = tokenizer(
            documents[start : start + 256],
            add_special_tokens=True,
            padding=False,
            truncation=False,
            return_length=True,
        )
        token_lengths.extend(int(value) for value in encoded["length"])

    character_lengths = [len(text) for text in documents]
    over_limit = sum(length > model_limit for length in token_lengths)
    return {
        "index": get_collection_stats(),
        "documents": len(documents),
        "tokenizer": config.EMBEDDING_LOCAL_MODEL,
        "model_token_limit": model_limit,
        "token_length": {
            "mean": round(statistics.fmean(token_lengths), 2) if token_lengths else 0.0,
            "p50": _percentile(token_lengths, 0.50),
            "p95": _percentile(token_lengths, 0.95),
            "p99": _percentile(token_lengths, 0.99),
            "max": max(token_lengths, default=0),
        },
        "character_length": {
            "mean": round(statistics.fmean(character_lengths), 2) if character_lengths else 0.0,
            "p50": _percentile(character_lengths, 0.50),
            "p95": _percentile(character_lengths, 0.95),
            "p99": _percentile(character_lengths, 0.99),
            "max": max(character_lengths, default=0),
        },
        "over_token_limit": over_limit,
        "over_token_limit_rate": over_limit / len(token_lengths) if token_lengths else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit()
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
