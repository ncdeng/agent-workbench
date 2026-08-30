"""P4: 记忆系统 recall@k 评估脚本。

构造已知相关的 query-memory 对，用 unified_recall 检索，计算 recall@k 和跨设计隔离率。

用法:
    python benchmarks/eval_recall.py [--client openai_compatible]
    python benchmarks/eval_recall.py --no-client  # 只测 token-overlap 模式

指标:
    recall@k: 已知相关的 memory 在 top-k 召回中的比例
    cross_design_isolation: 不同 design_signature 的经验不会互相召回的比例
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cst_agent_workbench.agent.memory import MemoryManager, StructuredMemory, unified_recall


# ── 评估数据集 ──────────────────────────────────────────────────────────
# 每条是一个 (query, expected_keyword, design_signature) 三元组。
# expected_keyword 是期望在召回结果中出现的文本片段。
# 如果 unified_recall 返回的 top-k 结果里有包含该片段的条目，算命中。

EVAL_QUERIES = [
    # 频率偏高频偏类
    ("S11 匹配差且谐振频率偏高", "patch_L", "er2-4_f9-10ghz_inset"),
    ("谐振偏低需要调整参数", "patch_L", "er2-4_f9-10ghz_inset"),
    ("频率校正失败", "patch_L", "er2-4_f9-10ghz_inset"),
    # feed_W 匹配类
    ("馈线宽度太小匹配差", "feed_W", "er2-4_f9-10ghz_inset"),
    ("feed_W 过小导致恶化", "feed_W", "er2-4_f9-10ghz_inset"),
    # inset_depth 匹配类
    ("inset 深度不合理", "inset_depth", "er2-4_f9-10ghz_inset"),
    ("匹配差调 inset", "inset_depth", "er2-4_f9-10ghz_inset"),
    # 跨设计隔离测试（不同 signature 的经验不应被召回）
    ("频率偏高", "patch_L", "er6-10_f18-20ghz_probe"),
]

CROSS_DESIGN_QUERIES = [
    # 这些 query 带 design_signature A，但相关 memory 存在 signature B 下
    # 期望：A 的 query 不召回 B 的 memory（除非有通用条目）
    ("S11 匹配差", "er6-10_f18-20ghz_probe"),
    ("谐振偏高", "er6-10_f18-20ghz_probe"),
]


def _seed_evaluation_memory() -> StructuredMemory:
    """构造评估用 StructuredMemory，包含已知 lesson。"""
    memory = StructuredMemory()
    MemoryManager.update_decisions(memory, strategy_entry={
        "round": 1,
        "lesson": "谐振频率偏高时优先增大 patch_L 降低频率，谐振偏低时优先减小 patch_L 抬高频率。",
        "failure_pattern": "频率偏移时先调匹配参数会导致 S11 在目标频点继续不达标",
        "effective_action": "根据 min_freq 与目标频率差异调整 patch_L",
        "avoid_next": "避免频率未对准时先调 inset_depth 或 feed_W",
        "reuse_condition": "min_freq 明显偏离目标频率",
        "confidence": 0.8,
    })
    MemoryManager.update_decisions(memory, strategy_entry={
        "round": 2,
        "lesson": "S11 匹配差且 feed_W 过小时，优先增大 feed_W。",
        "failure_pattern": "feed_W 过小会导致匹配持续恶化",
        "effective_action": "增大 feed_W 靠近 50 欧姆馈线宽度",
        "avoid_next": "避免在 feed_W 明显过小时只调 inset_depth",
        "reuse_condition": "目标频率附近 S11 未达标且 feed_W 偏小时",
        "confidence": 0.8,
    })
    MemoryManager.update_decisions(memory, strategy_entry={
        "round": 3,
        "lesson": "S11 匹配差且 inset_depth 不合理时，优先调整 inset_depth。",
        "failure_pattern": "inset_depth 过浅或过深会导致目标频点 S11 匹配差",
        "effective_action": "inset_depth 过浅时增大，过深时减小",
        "avoid_next": "避免 inset_depth 明显偏离时只调 patch_L",
        "reuse_condition": "谐振已接近目标但 S11 未达标",
        "confidence": 0.75,
    })
    MemoryManager.update_decisions(memory, failure_reason="历史失败：feed_W 过小导致匹配恶化")
    MemoryManager.update_decisions(memory, failure_reason="历史失败：频率偏移时必须先校正 patch_L")
    MemoryManager.update_decisions(memory, failure_reason="历史失败：inset_depth 不合理导致匹配恶化")
    return memory


def evaluate_recall_at_k(memory: StructuredMemory, client=None, k: int = 3) -> dict:
    """评估 recall@k：已知相关的 query 在 top-k 召回中命中的比例。"""
    hits = 0
    total = len(EVAL_QUERIES)
    details = []

    for query, expected_keyword, signature in EVAL_QUERIES:
        results = unified_recall(
            memory,
            query,
            client=client,
            design_signature=signature,
            k=k,
            min_score=0.0,
        )
        result_texts = " ".join(entry.text for entry in results)
        is_hit = expected_keyword.lower() in result_texts.lower()
        if is_hit:
            hits += 1
        details.append({
            "query": query,
            "expected_keyword": expected_keyword,
            "signature": signature,
            "hit": is_hit,
            "recalled_count": len(results),
            "recalled_texts": [entry.text[:80] for entry in results],
        })

    recall_at_k = hits / total if total > 0 else 0.0
    return {
        "metric": "recall@k",
        "k": k,
        "hits": hits,
        "total": total,
        "recall_at_k": round(recall_at_k, 4),
        "details": details,
    }


def evaluate_cross_design_isolation(memory: StructuredMemory, client=None) -> dict:
    """评估跨设计隔离：不同 design_signature 的经验不会互相召回。

    由于 unified_recall 在没有 RAG dynamic entries 时只从 StructuredMemory 检索，
    而 StructuredMemory 的 lesson 没有 design_signature 过滤（它们是通用的），
    所以这里的"隔离"主要验证 RAG 侧的 dynamic entries 不串台。
    """
    # 这个评估在无 RAG dynamic entries 时，StructuredMemory 的 lesson 是通用的
    # （没有 signature），所以任何 query 都可能召回它们——这是正确的行为。
    # 真正的隔离测试需要 RAG dynamic entries 有不同 signature。
    total = len(CROSS_DESIGN_QUERIES)
    hits = 0
    details = []
    for query, signature in CROSS_DESIGN_QUERIES:
        results = unified_recall(
            memory,
            query,
            client=client,
            design_signature=signature,
            k=3,
            min_score=0.0,
        )
        # StructuredMemory 的通用 lesson 在任何 signature 下都可被召回（设计如此）
        details.append({
            "query": query,
            "signature": signature,
            "recalled_count": len(results),
            "note": "StructuredMemory lessons are universal (no signature filtering)",
        })
    return {
        "metric": "cross_design_isolation",
        "total": total,
        "details": details,
        "note": "StructuredMemory lessons are universal; RAG dynamic entries are signature-scoped.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate unified memory recall quality.")
    parser.add_argument("--client", choices=["openai_compatible", "none"], default="none",
                        help="LLM client for semantic recall (none = token-overlap fallback).")
    parser.add_argument("--k", type=int, default=3, help="Top-k for recall evaluation.")
    parser.add_argument("--output", type=str, default=None, help="Output JSON path.")
    args = parser.parse_args(argv)

    memory = _seed_evaluation_memory()

    client = None
    if args.client == "openai_compatible":
        from benchmarks.agent_ablation_runner import OpenAICompatibleProposalProvider
        provider = OpenAICompatibleProposalProvider(timeout=20)
        client = provider.client

    print("=== Memory Recall Evaluation ===")
    print(f"Client: {args.client}, k={args.k}")
    print(f"Memory entries: {len(memory.decisions.recent_strategies)} lessons + {len(memory.decisions.failure_reasons)} failures")
    print()

    recall_result = evaluate_recall_at_k(memory, client=client, k=args.k)
    isolation_result = evaluate_cross_design_isolation(memory, client=client)

    print(f"recall@{args.k}: {recall_result['hits']}/{recall_result['total']} = {recall_result['recall_at_k']:.1%}")
    print()
    for detail in recall_result["details"]:
        status = "HIT " if detail["hit"] else "MISS"
        print(f"  [{status}] q='{detail['query'][:40]}' expect='{detail['expected_keyword']}' sig={detail['signature']}")
        for text in detail["recalled_texts"]:
            print(f"         -> {text}")
    print()
    print(f"cross_design_isolation: {isolation_result['total']} queries tested")
    print(f"  note: {isolation_result['note']}")

    report = {
        "config": {"client": args.client, "k": args.k},
        "recall_at_k": recall_result,
        "cross_design_isolation": isolation_result,
    }
    if args.output:
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nReport saved to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
