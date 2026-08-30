"""Agent self-reflection — 每轮优化结束后调用 LLM 总结经验写入 memory。"""
from __future__ import annotations
import json
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def _get_attr_any(obj: Any, *names: str) -> Any:
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return None


def _get_optimization_state(agent: Any) -> Any:
    session = getattr(agent, "session", None)
    if session is not None and getattr(session, "optimization_state", None) is not None:
        return session.optimization_state
    opt_state = getattr(agent, "opt_state", None)
    if opt_state is not None:
        return opt_state
    return getattr(agent, "optimization_state", None)


def collect_recent_errors(agent: Any, limit: int = 3) -> list:
    """从 agent.tool_events 末尾收集最近 limit 个失败事件的 error_type 简要。

    给 reflection 的 LLM prompt 提供"最近发生了什么类型的错误"的上下文，让
    lesson 更具体（例如针对 VBA_EXECUTION 给端口/几何检查建议）。
    """
    events = getattr(agent, "tool_events", None) or []
    out = []
    for ev in reversed(events):
        if ev.get("success"):
            continue
        if not ev.get("error_type"):
            continue
        out.append(
            {
                "error_type": ev.get("error_type"),
                "phase": ev.get("phase", ""),
                "tool_name": ev.get("tool_name", ""),
                "message": (ev.get("message") or "")[:200],
            }
        )
        if len(out) >= limit:
            break
    return list(reversed(out))


def run_reflection_for_agent(agent: Any, *, min_confidence: float | None = None) -> dict[str, Any]:
    """优化轮次结束后调用 LLM 总结经验写入 memory。

    从 agent 对象提取 opt_state / session.memory / tool_events，构造 round_summary
    调用 reflect_on_round。opt_state.active=False 时跳过。返回空 dict（无返回值需求）。
    """
    opt_state = _get_optimization_state(agent)
    if opt_state is None or not getattr(opt_state, "active", False):
        return {}

    try:
        from cst_agent_workbench import config as app_config
        round_summary = {
            "round": _get_attr_any(opt_state, "current_round", "round") or 0,
            "best_value": _get_attr_any(opt_state, "best_value", "best_metric_value"),
            "recent_errors": collect_recent_errors(agent),
        }
        reflect_on_round(
            client=agent.client,
            model=agent.model,
            round_summary=round_summary,
            memory=agent.session.memory,
            min_confidence=min_confidence if min_confidence is not None else app_config.RAG_LESSON_MIN_CONFIDENCE,
        )
    except Exception as exc:
        logger.warning("reflection skipped: %s", exc)
    return {}

REFLECTION_PROMPT = """你是一个天线仿真优化 agent 的反思模块。
根据本轮优化数据，输出一条简短的经验总结（JSON，无其他内容）：
{"strategy": "<本轮调整了什么参数，方向，步长>", "outcome": "<结果：改善/无改善/退化>", "lesson": "<下轮应注意什么>", "failure_pattern": "<失败模式，没有则空字符串>", "effective_action": "<有效动作，没有则空字符串>", "avoid_next": "<下轮应避免什么>", "reuse_condition": "<什么条件下可复用这条经验>", "confidence": 0.0, "rule": {"param": "<该经验指向的参数名，如 patch_L/inset_depth/feed_W；不指向具体参数则空字符串>", "direction": "<increase|decrease|none>"}}

`rule` 字段是给程序读的：下轮调参时会按 rule.param / rule.direction 去校验 LLM 提案，
所以必须与 lesson 的文字含义一致；经验不指向某个具体参数时填 {"param": "", "direction": "none"}。

如果本轮数据里 recent_errors 非空，请在 lesson/failure_pattern/avoid_next 里针对具体的 error_type 给出建议：
- vba_execution: 检查相应阶段（initialize/geometry/port/farfield）的 VBA 拼装
- result_read: 检查 farfield monitor 是否在求解前创建、模板路径是否存在
- cst_timeout: 简化几何 / 降低网格密度 / 缩小频率范围
- physics_validation: 检查参数是否落在合理物理区间
- offline_mode: 信息态，不算错误
"""

_VALID_RULE_DIRECTIONS = {"increase": 1, "decrease": -1, "none": 0}


def _normalize_reflection_rule(data: Dict[str, Any]) -> Dict[str, Any]:
    """把 LLM 输出的 rule 归一化成 {param, delta_sign}。

    结构化 rule 让 memory 的可执行意图不再依赖对中文散文做关键词匹配
    （原来只有出现"feed_W"+"过小"这类字面量才生效，换个措辞就静默失效）。
    字段缺失/非法时返回空规则，调用方回退到旧的文本匹配。
    """
    rule = data.get("rule")
    if not isinstance(rule, dict):
        return {"param": "", "delta_sign": 0}
    param = str(rule.get("param", "") or "").strip()
    direction = str(rule.get("direction", "") or "").strip().lower()
    if direction not in _VALID_RULE_DIRECTIONS:
        direction = "none"
    return {"param": param, "delta_sign": _VALID_RULE_DIRECTIONS[direction]}



def should_write_reflection_memory(
    strategy_entry: Dict[str, Any],
    *,
    min_confidence: float | None = None,
) -> bool:
    """Gate reflection writes before they enter structured memory."""
    if not strategy_entry:
        return False
    if min_confidence is not None:
        try:
            confidence = float(strategy_entry.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        from cst_agent_workbench.agent.memory import _memory_confidence_floor

        # 门槛按 confidence_basis 分档：校准后的无改善/回滚经验打折只降信任，
        # 不等于直接丢弃（统一 0.6 门槛会拦掉 0.5/0.2 封顶的所有这两类经验）。
        if confidence < _memory_confidence_floor(strategy_entry, min_confidence):
            return False
    content_fields = ("lesson", "failure_pattern", "effective_action")
    return any(str(strategy_entry.get(field, "") or "").strip() for field in content_fields)


def reflect_on_round(
    client: Any,
    model: str,
    round_summary: Dict[str, Any],
    memory: Any,  # StructuredMemory
    timeout: int = 10,
    min_confidence: float | None = None,
) -> bool:
    """调用 LLM 总结本轮经验，写入 memory.decisions。返回是否成功。"""
    if client is None or memory is None:
        return False
    try:
        prompt = REFLECTION_PROMPT + "\n\n本轮数据：\n" + json.dumps(round_summary, ensure_ascii=False)
        # 8 个中文字段的 JSON 在 120 token 下几乎必然截断，导致反思静默丢失；
        # 400 token 覆盖最长的合规输出。
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
            temperature=0.1,
            timeout=timeout,
        )
        text = (resp.choices[0].message.content or "").strip()
        from cst_agent_workbench.agent.json_parse import extract_json_object
        data = extract_json_object(text)
        if data is None:
            logger.warning("reflection returned unparseable response, lesson lost: %s", text[:120])
            return False
        strategy_entry = {
            "round": round_summary.get("round", 0),
            "strategy": data.get("strategy", ""),
            "outcome": data.get("outcome", ""),
            "lesson": data.get("lesson", ""),
            "failure_pattern": data.get("failure_pattern", ""),
            "effective_action": data.get("effective_action", ""),
            "avoid_next": data.get("avoid_next", ""),
            "reuse_condition": data.get("reuse_condition", ""),
            "confidence": float(data.get("confidence", 0.0) or 0.0),
            "rule": _normalize_reflection_rule(data),
        }
        if "after_metric" in round_summary:
            strategy_entry["after_metric"] = round_summary.get("after_metric")
        if "improved" in round_summary:
            strategy_entry["improved"] = bool(round_summary.get("improved"))
        # confidence 原本完全等于「写这条经验的同一次 LLM 调用」给自己打的分，
        # 而本轮是否真的改善是已测量的事实。用实测结果给自评分打折：
        # 改善保持原值，无改善减半，回滚压到 0.2（原先只有回滚这一档）。
        # 写入门槛按 confidence_basis 分档（见 should_write_reflection_memory），
        # 否则打折后的这两类经验永远过不了统一 0.6 门槛。
        evidence_factor = None
        if bool(round_summary.get("rolled_back")):
            evidence_factor = None  # 回滚走下面的硬上限，不做乘法
        elif "improved" in round_summary:
            evidence_factor = 1.0 if round_summary.get("improved") else 0.5
        if evidence_factor is not None:
            strategy_entry["confidence"] = round(strategy_entry["confidence"] * evidence_factor, 4)
        strategy_entry["confidence_basis"] = (
            "rolled_back" if round_summary.get("rolled_back")
            else ("improved" if round_summary.get("improved") else "no_improvement")
            if "improved" in round_summary else "self_reported"
        )
        if "rolled_back" in round_summary:
            rolled_back = bool(round_summary.get("rolled_back"))
            strategy_entry["rolled_back"] = rolled_back
            if rolled_back:
                strategy_entry["confidence"] = min(strategy_entry["confidence"], 0.2)
        if not should_write_reflection_memory(strategy_entry, min_confidence=min_confidence):
            logger.debug("reflection skipped by memory write policy: %s", strategy_entry)
            return False
        from cst_agent_workbench.agent.memory import MemoryManager, project_scope_from_path
        from cst_agent_workbench.rag.knowledge_base import make_design_signature
        er_value = round_summary.get("epsilon_r", round_summary.get("er"))
        freq_value = (
            round_summary.get("target_freq_ghz")
            or round_summary.get("freq_ghz")
            or round_summary.get("freq_before")
        )
        feed_value = round_summary.get("feed_strategy", round_summary.get("feed_type"))
        design_signature = str(round_summary.get("design_signature") or "").strip()
        if not design_signature:
            design_signature = make_design_signature(er_value, freq_value, feed_value)
        project_scope = str(round_summary.get("project_scope") or "").strip()
        if not project_scope:
            project_scope = project_scope_from_path(str(round_summary.get("project_path") or ""))
        strategy_entry["storage"] = "structured_canonical"
        MemoryManager.update_decisions(
            memory,
            strategy_entry=strategy_entry,
            project_scope=project_scope,
            design_signature=design_signature,
        )
        logger.debug("reflection saved: %s", strategy_entry)
        return True
    except Exception as exc:
        logger.warning("reflection failed (non-critical): %s", exc)
        return False
