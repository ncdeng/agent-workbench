"""Module-level helper functions extracted from agent.py."""

import json
from typing import Dict

from cst_agent_workbench.results.summary import summarize_s11_result


def coerce_success(value: object) -> bool:
    """Interpret heterogeneous success flags without treating "false" as truthy."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "ok", "success", "succeeded"}
    return False


def _truncate_tool_result(msg: Dict, max_len: int = 800, optimization_mode: bool = False) -> Dict:
    """截断过长的工具返回内容，保留关键结构化数据。"""
    if msg.get("role") != "tool":
        return msg
    content = msg.get("content", "")
    if len(content) <= max_len and not optimization_mode:
        return msg
    try:
        data = json.loads(content)
        if "plot_data" in data and isinstance(data["plot_data"], list):
            plot_summary = summarize_s11_result(data, 0.0)
            data["plot_summary"] = {
                "min_s11_db": plot_summary.min_s11_db,
                "min_freq_ghz": plot_summary.min_freq_ghz,
                "bandwidth_ghz": plot_summary.bandwidth_ghz,
                "bandwidth_pct": plot_summary.bandwidth_pct,
            }
            if optimization_mode:
                data.pop("plot_data", None)
            else:
                pd_list = data["plot_data"]
                if len(pd_list) > 10:
                    data["plot_data"] = pd_list[:5] + pd_list[-5:]
                    data["_plot_data_truncated"] = f"原 {len(pd_list)} 点已截断为 10 点"
            truncated = json.dumps(data, ensure_ascii=False)
            if len(truncated) <= max_len:
                return {**msg, "content": truncated}
        data.pop("plot_data", None)
        data.pop("vba_code", None)
        truncated = json.dumps(data, ensure_ascii=False)
        return {**msg, "content": truncated[:max_len]}
    except (json.JSONDecodeError, TypeError):
        return {**msg, "content": content[:max_len] + "...(截断)"}


_OPTIMIZATION_INTENT_KEYWORDS = [
    "优化", "自动优化", "调参", "调参数", "优化一轮", "执行一轮优化",
    "连续优化", "继续优化", "参数扫描优化",
]


def is_optimization_request(text: str) -> bool:
    """判断用户文本是否明确表达了优化意图。只用于优化入口分流。"""
    if not text:
        return False
    lowered = text.lower()
    return any(keyword in text or keyword in lowered for keyword in _OPTIMIZATION_INTENT_KEYWORDS)


_META_LLM_QUERY_KEYWORDS = [
    "大模型", "llm", "fast path", "fast_path", "快速路径", "通用大模型",
    "模型思考", "经过大模型", "调用大模型",
]

_META_LLM_QUERY_REFERENCES = [
    "刚才", "刚刚", "上一条", "前面", "这一条", "这条", "你这些", "你这一步",
]


def is_meta_llm_query(text: str) -> bool:
    """Detect follow-up questions about whether the previous reply used the LLM."""
    if not text:
        return False
    lowered = text.lower()
    mentions_llm = any(keyword in text or keyword in lowered for keyword in _META_LLM_QUERY_KEYWORDS)
    references_previous = any(keyword in text or keyword in lowered for keyword in _META_LLM_QUERY_REFERENCES)
    explicit_mode_query = any(
        keyword in text or keyword in lowered
        for keyword in [
            "调用大模型",
            "经过大模型",
            "fast path",
            "fast_path",
            "快速路径",
            "通用大模型",
            "模型思考",
        ]
    )
    asks_mode = any(
        token in text or token in lowered
        for token in ["有没有", "是不是", "是否", "吗", "?", "？"]
    )
    return mentions_llm and asks_mode and (references_previous or explicit_mode_query)


def _format_mm(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text or "0"
