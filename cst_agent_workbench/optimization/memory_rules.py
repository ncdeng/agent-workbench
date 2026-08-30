"""Memory-rule matching, constraint generation, and violation checks.

Pure data transforms that turn recalled lessons + current params + S11 summary
into structured guidance for the LLM proposer (and into a fallback proposal
when the LLM ignores the constraint). No CST or solver state — safe to call
from any layer.

规则来源有两层，优先级从高到低：

1. **结构化 rule**（`reflection` 写入的 `entry.metadata["rule"] = {param, delta_sign}`）：
   经验自带机器可读意图，换措辞/换语言都不影响生效。
2. **散文关键词匹配**（下面的 `feed_W` + "过小" 之类字面量）：只对没有结构化
   rule 的历史条目兜底。这层是天线专用的，且换个说法就静默失效——所以新经验
   一律走第 1 层，这里只保留向后兼容。

两层都会经过同一组数值适用性检查（`feed_w < 2.3` 等），检查未通过时记入
`suppressed_rules`，避免"召回了但没生效"变成不可见的静默 no-op。
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _MemoryRule:
    name: str
    expected_param: str
    expected_delta_sign: int
    constraint: str
    priority: int
    enforceable: bool = True


def _param_float(params: dict, name: str, default: float = 0.0) -> float:
    try:
        return float(params.get(name, default))
    except (TypeError, ValueError):
        return default


def _any_text(texts: list[str], *needles: str) -> bool:
    return any(all(needle in text for needle in needles) for text in texts)


def _char_bigrams(value: str) -> set[str]:
    compact = "".join(str(value or "").lower().replace("/", "").replace("\\", "").split())
    if not compact:
        return set()
    if len(compact) == 1:
        return {compact}
    return {compact[index:index + 2] for index in range(len(compact) - 1)}


def _bigram_containment(a: str, b: str) -> float:
    set_a = _char_bigrams(a)
    set_b = _char_bigrams(b)
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / min(len(set_a), len(set_b))


def dedupe_rag_context_against_memory(
    rag_context: list[str],
    memory_texts: list[str],
    *,
    threshold: float = 0.8,
) -> list[str]:
    if not rag_context or not memory_texts:
        return list(rag_context or [])
    anchors = [str(text or "") for text in memory_texts if str(text or "").strip()]
    if not anchors:
        return list(rag_context or [])
    deduped: list[str] = []
    for item in rag_context:
        text = str(item or "")
        if any(_bigram_containment(text, anchor) >= threshold for anchor in anchors):
            continue
        deduped.append(item)
    return deduped


_STRUCTURED_RULE_PRIORITY = 120


def _structured_rules(recalled: list, params: dict) -> tuple[list[_MemoryRule], list[dict]]:
    """从召回条目的结构化 rule 生成规则。返回 (rules, suppressed)。

    只信任 reflection 写入的 {param, delta_sign}；数值适用性由
    `_rule_applicability` 判定，不适用的记入 suppressed 供观测。
    """
    rules: list[_MemoryRule] = []
    suppressed: list[dict] = []
    for entry in recalled:
        metadata = getattr(entry, "metadata", None) or {}
        rule = metadata.get("rule")
        if not isinstance(rule, dict):
            continue
        param = str(rule.get("param", "") or "").strip()
        try:
            delta_sign = int(rule.get("delta_sign", 0) or 0)
        except (TypeError, ValueError):
            delta_sign = 0
        if not param or delta_sign == 0:
            continue
        applicable, reason = _rule_applicability(param, delta_sign, params)
        entry_id = str(getattr(entry, "id", "") or "")
        if not applicable:
            suppressed.append({"source_id": entry_id, "param": param, "reason": reason})
            continue
        rules.append(
            _MemoryRule(
                name=f"structured:{param}",
                expected_param=param,
                expected_delta_sign=delta_sign,
                constraint=(
                    f"召回经验（{entry_id or 'memory'}）要求本轮"
                    f"{'增大' if delta_sign > 0 else '减小'} {param}；当前值 "
                    f"{_param_float(params, param):.3f}。"
                ),
                priority=_STRUCTURED_RULE_PRIORITY,
            )
        )
    return rules, suppressed


def _rule_applicability(param: str, delta_sign: int, params: dict) -> tuple[bool, str]:
    """数值适用性检查：经验说"增大 X"，但 X 已经偏大时不应继续增大。

    与散文层共用同一组物理边界，保证两条来源的行为一致。
    """
    value = _param_float(params, param)
    if param == "feed_W":
        if delta_sign > 0 and value >= 2.3:
            return False, f"feed_W={value:.3f} 已不算偏小，忽略'增大 feed_W'的经验"
        return True, ""
    if param == "inset_depth":
        patch_l = _param_float(params, "patch_L")
        if patch_l > 0:
            if delta_sign > 0 and value > patch_l * 0.48:
                return False, f"inset_depth={value:.3f} 已接近过深边界，忽略'增大'经验"
            if delta_sign < 0 and value < patch_l * 0.10:
                return False, f"inset_depth={value:.3f} 已接近过浅边界，忽略'减小'经验"
        return True, ""
    return True, ""


def _memory_guidance(recalled: list, params: dict, s11_summary: dict | None = None, target_freq_ghz: float = 0.0, target_db: float = -10.0) -> dict:
    texts = [str(getattr(entry, "text", "")) for entry in recalled]
    rules: list[_MemoryRule] = []
    structured, suppressed_rules = _structured_rules(recalled, params)
    rules.extend(structured)
    feed_w = _param_float(params, "feed_W")
    patch_l = _param_float(params, "patch_L")
    inset_depth = _param_float(params, "inset_depth")
    s11_summary = s11_summary or {}
    min_freq = _param_float(s11_summary, "min_freq_ghz")
    target_s11 = _param_float(s11_summary, "target_s11_db")

    feed_w_too_small = any(
        "feed_W" in text and any(marker in text for marker in ("过小", "偏小", "太小"))
        for text in texts
    )
    if feed_w_too_small and feed_w < 2.3:
        rules.append(
            _MemoryRule(
                name="feed_width_too_small",
                expected_param="feed_W",
                expected_delta_sign=1,
                constraint=f"当前 feed_W={feed_w:.3f} mm 偏小；召回记忆指出 feed_W 过小会导致匹配恶化，必须优先增大 feed_W。",
                priority=100,
            )
        )

    if target_freq_ghz > 0 and min_freq > 0:
        freq_error = (min_freq - target_freq_ghz) / target_freq_ghz
        mentions_frequency_low = any(
            any(token in text for token in ("谐振偏低", "频率偏低", "谐振频率偏低", "min_freq偏低", "min_freq 偏低"))
            for text in texts
        )
        mentions_frequency_high = any(
            any(token in text for token in ("谐振偏高", "频率偏高", "谐振频率偏高", "min_freq偏高", "min_freq 偏高"))
            for text in texts
        )
        mentions_patch_l_action = any("patch_L" in text for text in texts)
        if (mentions_frequency_low or (mentions_patch_l_action and any("减小" in text for text in texts))) and freq_error < -0.003:
            rules.append(
                _MemoryRule(
                    name="resonance_too_low_patch_length",
                    expected_param="patch_L",
                    expected_delta_sign=-1,
                    constraint=f"当前谐振点 {min_freq:.3f} GHz 低于目标 {target_freq_ghz:.3f} GHz；召回记忆要求优先减小 patch_L 以抬高谐振频率。",
                    priority=80,
                )
            )
        if (mentions_frequency_high or (mentions_patch_l_action and any("增大" in text for text in texts))) and freq_error > 0.003:
            rules.append(
                _MemoryRule(
                    name="resonance_too_high_patch_length",
                    expected_param="patch_L",
                    expected_delta_sign=1,
                    constraint=f"当前谐振点 {min_freq:.3f} GHz 高于目标 {target_freq_ghz:.3f} GHz；召回记忆要求优先增大 patch_L 以降低谐振频率。",
                    priority=80,
                )
            )

    match_is_poor = target_s11 > target_db if target_s11 else False
    inset_memory = any("inset_depth" in text and any(marker in text for marker in ("优先", "调整", "微调", "不合理")) for text in texts)
    if inset_memory and match_is_poor and patch_l > 0:
        if inset_depth < patch_l * 0.10:
            rules.append(
                _MemoryRule(
                    name="inset_depth_too_small",
                    expected_param="inset_depth",
                    expected_delta_sign=1,
                    constraint=f"当前 inset_depth={inset_depth:.3f} mm 接近过浅区域；召回记忆要求增大 inset_depth 改善匹配。",
                    priority=60,
                )
            )
        elif inset_depth > patch_l * 0.48:
            rules.append(
                _MemoryRule(
                    name="inset_depth_too_large",
                    expected_param="inset_depth",
                    expected_delta_sign=-1,
                    constraint=f"当前 inset_depth={inset_depth:.3f} mm 接近过深区域；召回记忆要求减小 inset_depth 改善匹配。",
                    priority=60,
                )
            )

    if _any_text(texts, "farfield") or _any_text(texts, "远场"):
        rules.append(
            _MemoryRule(
                name="farfield_monitor_required",
                expected_param="",
                expected_delta_sign=0,
                constraint="召回记忆指出远场结果缺失通常来自求解前未创建 farfield monitor；本轮若涉及远场，必须先确认 monitor 已创建。",
                priority=20,
                enforceable=False,
            )
        )
    if _any_text(texts, "端口") or _any_text(texts, "port"):
        rules.append(
            _MemoryRule(
                name="port_connection_required",
                expected_param="",
                expected_delta_sign=0,
                constraint="召回记忆指出端口异常通常来自端点/馈线物理连接问题；本轮若涉及端口，必须检查端口与馈线导体真实连接。",
                priority=20,
                enforceable=False,
            )
        )

    rules.sort(key=lambda rule: rule.priority, reverse=True)
    enforced_rule = next((rule for rule in rules if rule.enforceable and rule.expected_param), None)
    # "召回了经验但没有任何规则命中" 过去是完全静默的：measured memory_enforced_rate=0
    # 时无从判断是"没召回"还是"召回了但措辞对不上关键词"。这里显式记录。
    recalled_but_unmatched = bool(recalled) and enforced_rule is None
    if recalled_but_unmatched:
        logger.debug(
            "memory recalled %d entries but no enforceable rule matched (suppressed=%d)",
            len(recalled), len(suppressed_rules),
        )
    return {
        "expected_param": enforced_rule.expected_param if enforced_rule else "",
        "expected_delta_sign": enforced_rule.expected_delta_sign if enforced_rule else 0,
        "constraints": [rule.constraint for rule in rules],
        "rule_names": [rule.name for rule in rules],
        "enforced_rule": enforced_rule.name if enforced_rule else "",
        "suppressed_rules": suppressed_rules,
        "recalled_count": len(recalled),
        "recalled_but_unmatched": recalled_but_unmatched,
    }


def _memory_violation_reason(proposal: dict, guidance: dict) -> str:
    expected_param = str(guidance.get("expected_param") or "")
    if not expected_param:
        return ""
    actual_param = str((proposal or {}).get("param") or "")
    if actual_param != expected_param:
        return f"memory expected {expected_param}, got {actual_param or 'empty'}"
    expected_sign = int(guidance.get("expected_delta_sign") or 0)
    if expected_sign:
        try:
            delta = float((proposal or {}).get("delta_mm", 0.0))
        except (TypeError, ValueError):
            return f"memory expected {expected_param} delta to be numeric"
        if delta * expected_sign <= 0:
            direction = "positive" if expected_sign > 0 else "negative"
            return f"memory expected {expected_param} delta to be {direction}, got {delta:.4f}"
    return ""


def _with_memory_metadata(proposal: dict | None, recalled: list, guidance: dict) -> dict:
    enriched = dict(proposal or {})
    enriched["recalled_memory_ids"] = [getattr(entry, "id", "") for entry in recalled]
    enriched["recalled_lessons"] = [getattr(entry, "text", "") for entry in recalled]
    enriched["memory_expected_param"] = guidance.get("expected_param", "")
    enriched["memory_expected_delta_sign"] = guidance.get("expected_delta_sign", 0)
    enriched["memory_constraints"] = guidance.get("constraints", [])
    enriched["memory_rule_names"] = guidance.get("rule_names", [])
    enriched["memory_enforced_rule"] = guidance.get("enforced_rule", "")
    violation_reason = _memory_violation_reason(enriched, guidance)
    enriched["memory_complied"] = bool(guidance.get("expected_param") and not violation_reason)
    enriched["memory_violation"] = bool(violation_reason)
    enriched["memory_violation_reason"] = violation_reason
    return enriched


def _memory_constraint_fallback(proposal: dict) -> dict | None:
    expected_param = str(proposal.get("memory_expected_param") or "")
    if not expected_param:
        return None
    expected_sign = int(proposal.get("memory_expected_delta_sign") or 0)
    delta = 0.35 * (expected_sign or 1)
    return {
        "param": expected_param,
        "delta_mm": round(delta, 4),
        "reason": f"memory constraint fallback: {proposal.get('memory_violation_reason', '')}",
        "source": "memory_constraint",
        "recalled_memory_ids": proposal.get("recalled_memory_ids", []),
        "recalled_lessons": proposal.get("recalled_lessons", []),
        "memory_expected_param": expected_param,
        "memory_expected_delta_sign": expected_sign,
        "memory_constraints": proposal.get("memory_constraints", []),
        "memory_rule_names": proposal.get("memory_rule_names", []),
        "memory_enforced_rule": proposal.get("memory_enforced_rule", ""),
        "memory_complied": False,
        "memory_violation": bool(proposal.get("memory_violation", False)),
        "memory_violation_reason": proposal.get("memory_violation_reason", ""),
        "memory_enforced_by_validator": True,
    }
