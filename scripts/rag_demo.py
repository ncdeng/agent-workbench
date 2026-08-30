"""RAG retrieval demo — side-by-side comparison of confidence weighting and query rewrite.

Designed for demos: shows what the RAG pipeline actually returns, with scores.
Runs offline using the local sentence-transformers embedding (no API keys needed).

Run:
    python scripts/rag_demo.py
    python scripts/rag_demo.py --query "S11 不够深怎么办"
    python scripts/rag_demo.py --rewrite  # exercises query-rewrite path (needs OPENAI_API_KEY)
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


DEFAULT_QUERIES = [
    "谐振频率偏高如何调整",
    "S11 深度不足匹配差",
    "带宽如何提升",
    "毫米波贴片仿真要注意什么",
    "圆极化怎么实现",
]


def _print_results(label: str, results: list) -> None:
    print(f"\n  [{label}]")
    if not results:
        print("    (no results)")
        return
    for i, item in enumerate(results, 1):
        if isinstance(item, tuple):
            text, score = item
            score_str = f"{score:.3f}" if isinstance(score, (int, float)) else "—"
            print(f"    {i}. ({score_str}) {text[:100]}")
        else:
            print(f"    {i}.         {str(item)[:100]}")


def run_demo(query: str, client, rewrite: bool) -> None:
    from cst_agent_workbench.rag.knowledge_base import retrieve_antenna_rules

    print(f"\n────────── query: {query!r} ──────────")

    # Path A: plain retrieval, no scores
    plain = retrieve_antenna_rules(query, client, top_k=3, rewrite=False)
    _print_results("plain (top_k=3)", plain)

    # Path B: with scores — shows confidence-weighted ranking
    scored = retrieve_antenna_rules(query, client, top_k=3, rewrite=False, with_scores=True)
    _print_results("with_scores=True (confidence-weighted)", scored)

    # Path C: query rewrite + scores (opt-in)
    if rewrite:
        scored_rw = retrieve_antenna_rules(query, client, top_k=3, rewrite=True, with_scores=True)
        _print_results("with rewrite=True (multi-query union)", scored_rw)


def _make_client():
    """Try to construct an OpenAI client for chat (rewrite needs it).
    Falls back to None when no key is configured — embedding still works (uses local model)."""
    try:
        from cst_agent_workbench import config
        if not config.OPENAI_API_KEY:
            return None
        from openai import OpenAI
        return OpenAI(api_key=config.OPENAI_API_KEY, base_url=config.OPENAI_BASE_URL or None)
    except Exception as exc:
        print(f"[warn] OpenAI client init failed: {exc}")
        return None


def main():
    # Windows 控制台默认 GBK，部分知识条目含 • / ε 等字符会炸；强制 utf-8。
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", default=None, help="single query to test (default: run preset suite)")
    parser.add_argument("--rewrite", action="store_true", help="enable query-rewrite path (requires chat LLM)")
    args = parser.parse_args()

    client = _make_client()
    if args.rewrite and client is None:
        print("[warn] --rewrite requested but no OPENAI_API_KEY; skipping rewrite path.")
        args.rewrite = False

    queries = [args.query] if args.query else DEFAULT_QUERIES
    print(f"RAG demo  |  client={'OpenAI' if client else 'None (embedding-only)'}  |  rewrite={'on' if args.rewrite else 'off'}")
    for q in queries:
        run_demo(q, client, args.rewrite)
    print("\ndone.")


if __name__ == "__main__":
    main()
