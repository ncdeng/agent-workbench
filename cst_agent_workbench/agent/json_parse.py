"""LLM 结构化输出的共享 JSON 解析工具。

背景：llm_planner / reflection 此前各有一套弱解析（裸 json.loads + 手工剥 fence），
analyzer 有一套最强的解析。这里收敛出一个通用的 dict 提取函数供前两者使用；
analyzer 的解析包含调参领域特定的候选顺序与字段归一化，保持独立。
"""
from __future__ import annotations

import ast
import json
import re

_THINK_RE = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


def _parse_candidate(candidate: str) -> dict | None:
    candidate = candidate.strip()
    if not candidate:
        return None
    parsers = (
        json.loads,
        ast.literal_eval,  # 容忍单引号/True/False 等 Python 字面量风格
        lambda s: json.loads(_TRAILING_COMMA_RE.sub(r"\1", s)),  # 容忍尾逗号
    )
    for parser in parsers:
        try:
            parsed = parser(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def extract_json_object(text: str) -> dict | None:
    """从 LLM 输出中尽力提取一个 JSON object（dict）。失败返回 None。

    依次尝试：原文 → 每个 ``` 代码块内容 → 首尾花括号截取；
    每个候选依次用 json / ast.literal_eval / 去尾逗号 json 解析。
    """
    text = _THINK_RE.sub("", str(text or "")).strip()
    if not text:
        return None
    candidates = [text]
    candidates.extend(match.group(1) for match in _FENCE_RE.finditer(text))
    brace_match = re.search(r"\{[\s\S]*\}", text)
    if brace_match:
        candidates.append(brace_match.group(0))
    for candidate in candidates:
        parsed = _parse_candidate(candidate)
        if parsed is not None:
            return parsed
    return None
