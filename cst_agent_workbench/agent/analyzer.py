"""LLM-based S11 result analyzer.

调用 LLM 对仿真结果做物理层面的解读，给出中文自然语言分析和改进建议。
这是 LLM 在项目中承担「物理推理」角色的核心模块。
"""
import ast
import json
import logging
import re

logger = logging.getLogger(__name__)


def _is_unsupported_response_format_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "response_format" in text and any(
        marker in text
        for marker in ("unsupported", "not support", "unknown", "unexpected", "invalid parameter")
    )

_ANALYSIS_PROMPT = """你是一位经验丰富的微波天线工程师，正在分析 CST 仿真结果。

请根据以下 S11 仿真数据，给出简洁的物理分析（不超过 150 字）：
1. 谐振情况（频率、深度）
2. 与目标频率的偏差及可能的物理原因
3. 一条最有价值的改进建议（具体参数方向）

不要重复数据，直接给出工程判断。"""


def build_s11_analysis_prompt(s11_summary: dict, target_freq_ghz: float, params: dict) -> str:
    min_s11 = s11_summary.get("min_s11_db")
    min_freq = s11_summary.get("min_freq_ghz")
    bw = s11_summary.get("bandwidth_ghz")
    at_f0 = s11_summary.get("at_f0_s11_db")

    lines = []
    if min_freq is not None and min_s11 is not None:
        lines.append(f"- 谐振频率: {min_freq:.3f} GHz，S11 最小值: {min_s11:.2f} dB")
    if target_freq_ghz:
        lines.append(f"- 目标频率: {target_freq_ghz:.3f} GHz")
        if at_f0 is not None:
            lines.append(f"- 目标频率处 S11: {at_f0:.2f} dB")
        if min_freq is not None:
            delta = min_freq - target_freq_ghz
            lines.append(f"- 频率偏差: {delta:+.3f} GHz ({delta/target_freq_ghz*100:+.1f}%)")
    if bw is not None:
        lines.append(f"- -10dB 带宽: {bw:.3f} GHz")
    if params:
        important = ["patch_L", "patch_W", "inset_depth", "feed_W", "feed_L", "substrate_h"]
        param_lines = [f"  {k}={params[k]}" for k in important if k in params]
        if param_lines:
            lines.append("- 当前关键参数:")
            lines.extend(param_lines)

    data_block = "\n".join(lines)
    return f"{_ANALYSIS_PROMPT}\n\n仿真数据:\n{data_block}"


def analyze_s11_with_llm(
    client,
    model: str,
    s11_summary: dict,
    target_freq_ghz: float,
    params: dict,
    timeout: int = 30,
) -> tuple:
    """调用 LLM 分析 S11 结果，返回 (text, usage_dict)。"""
    if client is None:
        return None, {}
    try:
        prompt = build_s11_analysis_prompt(s11_summary, target_freq_ghz, params)
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.3,
            timeout=timeout,
        )
        text = response.choices[0].message.content or ""
        usage = {}
        if response.usage:
            usage = {
                "prompt": response.usage.prompt_tokens,
                "completion": response.usage.completion_tokens,
            }
            cached_tokens = getattr(response.usage, "cached_tokens", 0)
            cache_write_tokens = getattr(response.usage, "cache_write_tokens", 0)
            if isinstance(cached_tokens, (int, float)) and cached_tokens:
                usage["cached"] = cached_tokens
            if isinstance(cache_write_tokens, (int, float)) and cache_write_tokens:
                usage["cache_write"] = cache_write_tokens
        return text.strip(), usage
    except Exception as exc:
        logger.warning("S11 LLM analysis failed: %s", exc)
        return None, {"error_type": type(exc).__name__, "error": str(exc)[:500]}


_OPTIMIZATION_SYSTEM_PROMPT = """你是微波天线工程师。只输出一个 JSON 对象，不要输出 <think>、推理过程、Markdown 或解释性前后缀。"""

_OPTIMIZATION_PROMPT = """根据 S11 数据决定下一步优化。
输出格式：{"param": "<名>", "delta_mm": <数值>, "reason": "<中文>"}
可选参数：patch_L（增大降频）、inset_depth（影响匹配）、feed_W（馈线阻抗）。
delta_mm 范围 -2.0~2.0，精度 0.1。"""


ALLOWED_PATCH_PARAMS = {"patch_L", "patch_W", "inset_depth", "feed_W", "feed_L", "substrate_h"}
_DELTA_MIN = 0.05
_DELTA_MAX = 2.0
_PARAM_KEYS = ("param", "parameter", "param_name", "name")
_DELTA_KEYS = ("delta_mm", "delta", "change_mm", "deltaMm", "step_mm")
_REASON_KEYS = ("reason", "rationale", "explanation")


def _strip_think_blocks(text: str) -> str:
    text = re.sub(r"<think>[\s\S]*?</think>", "", text or "", flags=re.IGNORECASE)
    return re.sub(r"<think>[\s\S]*$", "", text, flags=re.IGNORECASE).strip()


def _proposal_debug_excerpt(text: str) -> str:
    stripped = _strip_think_blocks(text)
    if stripped:
        return stripped[:300]
    if "<think" in (text or "").lower():
        return "[model output only contained reasoning tags; no parseable proposal was emitted]"
    return (text or "")[:300]


def _clean_json_candidate(candidate: str) -> str:
    return re.sub(r",\s*([}\]])", r"\1", candidate.strip())


def _parse_dict_candidate(candidate: str) -> dict | None:
    candidate = _clean_json_candidate(candidate)
    for parser in (json.loads, ast.literal_eval):
        try:
            parsed = parser(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _coerce_delta_mm(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"[-+]?\d+(?:\.\d+)?", value)
        if match:
            return float(match.group(0))
    return None


def parse_optimization_proposal_text(text: str) -> dict | None:
    text = _strip_think_blocks(text)
    if not text:
        return None
    candidates = [text]
    candidates.extend(match.group(1) for match in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE))
    object_match = re.search(r"\{[\s\S]*\}", text)
    if object_match:
        candidates.append(object_match.group(0))
    for candidate in candidates:
        parsed = _parse_dict_candidate(candidate)
        if not isinstance(parsed, dict):
            continue
        param = next((parsed[key] for key in _PARAM_KEYS if key in parsed), "")
        delta = next((parsed[key] for key in _DELTA_KEYS if key in parsed), None)
        delta_mm = _coerce_delta_mm(delta)
        if not param or delta_mm is None:
            continue
        reason = next((parsed[key] for key in _REASON_KEYS if key in parsed), "")
        normalized = dict(parsed)
        normalized["param"] = str(param).strip().strip("` ")
        normalized["delta_mm"] = delta_mm
        normalized["reason"] = str(reason) if reason is not None else ""
        return normalized
    return None


def validate_llm_proposal(proposal: dict, history: list | None) -> tuple[bool, str]:
    """校验 LLM 返回的调参 proposal 是否满足物理约束。

    返回 (ok, reason)。reason 在 ok=False 时说明拒绝原因。
    """
    if not isinstance(proposal, dict):
        return False, "proposal 不是 dict"
    param = proposal.get("param", "")
    if param not in ALLOWED_PATCH_PARAMS:
        return False, f"param '{param}' 不在允许列表"
    try:
        delta = float(proposal["delta_mm"])
    except (KeyError, TypeError, ValueError):
        return False, "delta_mm 缺失或非数值"
    if abs(delta) < _DELTA_MIN:
        return False, f"|delta_mm|={abs(delta):.3f} 小于最小步长 {_DELTA_MIN}"
    if abs(delta) > _DELTA_MAX:
        return False, f"|delta_mm|={abs(delta):.3f} 超过最大步长 {_DELTA_MAX}"
    # 检查历史：若同参数、同方向上轮刚失败，则拒绝
    if history and len(history) >= 1:
        last = history[-1]
        last_param = last.get("param_name") or last.get("param")
        last_delta = last.get("delta_mm") or last.get("param_delta")
        last_improved = last.get("improved", True)
        if (
            last_param == param
            and not last_improved
            and last_delta is not None
        ):
            try:
                last_sign = 1 if float(last_delta) > 0 else -1
                cur_sign = 1 if delta > 0 else -1
                if last_sign == cur_sign:
                    return False, f"上轮 {param} 同方向调整未改善，拒绝重复"
            except (TypeError, ValueError):
                pass
    return True, ""


def propose_patch_optimization_with_llm(
    client,
    model: str,
    s11_summary: dict,
    target_freq_ghz: float,
    current_params: dict,
    history: list = None,
    timeout: int = 20,
    rag_context: list = None,
    reject_reason: str = "",  # 上次 validate 失败原因
    failure_reasons: list = None,
    memory_lessons: list = None,
    memory_constraints: list = None,
) -> tuple:
    """调用 LLM 生成优化 proposal，返回 (proposal_dict, usage_dict)。"""
    if client is None:
        return None, {}
    try:
        lines = []
        min_s11 = s11_summary.get("min_s11_db")
        min_freq = s11_summary.get("min_freq_ghz")
        at_f0 = s11_summary.get("at_f0_s11_db")
        if min_freq is not None:
            lines.append(f"谐振: {min_freq:.3f} GHz, S11: {min_s11:.2f} dB")
        if target_freq_ghz:
            lines.append(f"目标: {target_freq_ghz:.3f} GHz")
            if at_f0 is not None:
                lines.append(f"目标处S11: {at_f0:.2f} dB")
        keys = ["patch_L", "inset_depth", "feed_W", "substrate_h"]
        ps = ", ".join(f"{k}={current_params[k]}" for k in keys if k in current_params)
        if ps:
            lines.append(f"参数: {ps}")
        if history:
            from cst_agent_workbench.optimization.state import OptimizationState
            hist_text = OptimizationState.format_history_for_llm(history, n=3)
            if hist_text:
                lines.append(hist_text)
                lines.append("请参考历史趋势，避免重复上轮失败的调整方向。")
        if reject_reason:
            lines.append(f"\n[上次方案被拒绝，原因：{reject_reason}]请避免重复同样错误。")
        if failure_reasons:
            recent_failures = failure_reasons[-3:]
            lines.append("\n[历史失败记录 — 避免重复]")
            for f in recent_failures:
                lines.append(f"- {f}")
        if memory_lessons:
            lines.append("\n[历史经验 — 结构化记忆]")
            for lesson in memory_lessons[-3:]:
                lines.append(f"- {lesson}")
        if memory_constraints:
            lines.append("\n[记忆约束 — 必须优先满足]")
            for constraint in memory_constraints[-3:]:
                lines.append(f"- {constraint}")
            lines.append("如果输出不满足记忆约束，必须在 reason 中给出明确反证。")
        if rag_context:
            lines.append("\n[参考知识库]")
            for i, rule in enumerate(rag_context, 1):
                lines.append(f"{i}. {rule}")
        prompt = _OPTIMIZATION_PROMPT + "\n\n数据:\n" + "\n".join(lines)
        request = {
            "model": model,
            "messages": [
                {"role": "system", "content": _OPTIMIZATION_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 120,
            "temperature": 0.2,
            "timeout": timeout,
        }
        try:
            response = client.chat.completions.create(**request, response_format={"type": "json_object"})
        except Exception as exc:
            if not _is_unsupported_response_format_error(exc):
                raise
            logger.info("provider does not support response_format; retrying without JSON mode: %s", exc)
            response = client.chat.completions.create(**request)
        text = (response.choices[0].message.content or "").strip()
        usage = {}
        if response.usage:
            usage = {
                "prompt": response.usage.prompt_tokens,
                "completion": response.usage.completion_tokens,
            }
            cached_tokens = getattr(response.usage, "cached_tokens", 0)
            cache_write_tokens = getattr(response.usage, "cache_write_tokens", 0)
            if isinstance(cached_tokens, (int, float)) and cached_tokens:
                usage["cached"] = cached_tokens
            if isinstance(cache_write_tokens, (int, float)) and cache_write_tokens:
                usage["cache_write"] = cache_write_tokens

        parsed = parse_optimization_proposal_text(text)
        if not isinstance(parsed, dict):
            usage["parse_error"] = "no_parseable_optimization_proposal"
            usage["raw_text_excerpt"] = _proposal_debug_excerpt(text)
            return None, usage
        return parsed, usage
    except Exception as exc:
        logger.warning("optimization proposal LLM call failed: %s", exc)
        return None, {"error_type": type(exc).__name__, "error": str(exc)[:500]}
