from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmarks.fake_cst_simulator import FakeCSTCase, FakeCSTSimulator, make_benchmark_cases
from cst_agent_workbench.agent.analyzer import validate_llm_proposal
from cst_agent_workbench.agent.memory import MemoryManager, StructuredMemory, recall_memory
from cst_agent_workbench.optimization.memory_rules import (
    _memory_constraint_fallback,
    _memory_guidance,
    _with_memory_metadata,
)
from cst_agent_workbench.optimization.models import OptimizationContext, OptimizationTarget, S11Summary
from cst_agent_workbench.optimization.strategy import HeuristicPatchOptimizationStrategy


@dataclass(frozen=True)
class AblationGroup:
    name: str
    use_llm: bool = False
    use_algorithm: bool = False
    use_memory: bool = False
    use_reflection: bool = False


ABLATION_GROUPS = [
    AblationGroup("heuristic_only"),
    AblationGroup("algorithm_baseline", use_algorithm=True),
    AblationGroup("llm_no_memory", use_llm=True),
    AblationGroup("llm_with_memory", use_llm=True, use_memory=True),
    AblationGroup("llm_memory_reflection", use_llm=True, use_memory=True, use_reflection=True),
]


class ProposalProvider(Protocol):
    name: str

    def propose(
        self,
        *,
        params: dict[str, float],
        summary: dict[str, Any],
        case: FakeCSTCase,
        memory: StructuredMemory | None,
        history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        ...


def _direction_inferer(history: list[dict[str, Any]], param_name: str, default_sign: int) -> int:
    if not history:
        return default_sign
    last = history[-1]
    if last.get("param") != param_name or last.get("improved", True):
        return default_sign
    try:
        return -1 if float(last.get("delta_mm", 0.0)) > 0 else 1
    except (TypeError, ValueError):
        return default_sign


def _make_memory(robust_to_misleading_lessons: bool = False) -> StructuredMemory:
    """构造 ablation 用 memory。

    注入的 lesson 是通用微带天线物理经验（方向性因果，不绑定具体 case 的 ideal_params），
    而非 fake case 的标准答案。当 robust_to_misleading_lessons=True 时，额外注入一条与
    当前 case 状态可能矛盾的 lesson（"feed_W 过大需继续增大"），用于验证 memory 的状态
    守卫（_memory_guidance 的 feed_w < 2.3 判断）能挡住错误 lesson，不会把 LLM 带向更差方向。
    """
    memory = StructuredMemory()
    MemoryManager.update_decisions(
        memory,
        strategy_entry={
            "round": 0,
            "lesson": "S11 匹配差且 feed_W 过小时，优先增大 feed_W，而不是反复调整 inset_depth。",
            "failure_pattern": "feed_W 过小会导致匹配持续恶化",
            "effective_action": "增大 feed_W 靠近 50 欧姆馈线宽度",
            "avoid_next": "避免在 feed_W 明显过小时只调 inset_depth",
            "reuse_condition": "目标频率附近 S11 未达标且 feed_W 偏小时",
            "confidence": 0.8,
        },
    )
    MemoryManager.update_decisions(
        memory,
        strategy_entry={
            "round": 0,
            "lesson": "谐振频率偏高时优先增大 patch_L 降低频率，谐振频率偏低时优先减小 patch_L 抬高频率。",
            "failure_pattern": "频率偏移时先调匹配参数会导致 S11 在目标频点继续不达标",
            "effective_action": "根据 min_freq 与目标频率差异调整 patch_L",
            "avoid_next": "避免频率未对准时先调 inset_depth 或 feed_W",
            "reuse_condition": "min_freq 明显偏离目标频率",
            "confidence": 0.8,
        },
    )
    MemoryManager.update_decisions(
        memory,
        strategy_entry={
            "round": 0,
            "lesson": "S11 匹配差且 inset_depth 不合理时，优先调整 inset_depth。",
            "failure_pattern": "inset_depth 过浅或过深会导致目标频点 S11 匹配差",
            "effective_action": "inset_depth 过浅时增大，过深时减小",
            "avoid_next": "避免 inset_depth 明显偏离时只调 patch_L",
            "reuse_condition": "谐振已接近目标但 S11 未达标",
            "confidence": 0.75,
        },
    )
    if robust_to_misleading_lessons:
        # 故意注入一条与多数 case 状态矛盾的 lesson：声称 feed_W 过大需要继续增大。
        # _memory_guidance 的 feed_w < 2.3 状态守卫应阻止这条 lesson 触发 enforce，
        # 验证 memory 机制不会被错误 lesson 误导到"继续增大已经过大的 feed_W"。
        MemoryManager.update_decisions(
            memory,
            strategy_entry={
                "round": 0,
                "lesson": "feed_W 过大导致匹配恶化时，仍应继续增大 feed_W 以达到馈线宽度。",
                "failure_pattern": "feed_W 过大时匹配变差（误导性 lesson）",
                "effective_action": "继续增大 feed_W",
                "avoid_next": "避免减小 feed_W",
                "reuse_condition": "feed_W 已经偏大时（误导性）",
                "confidence": 0.5,
            },
        )
    MemoryManager.update_decisions(memory, failure_reason="历史失败：feed_W 过小导致匹配恶化")
    MemoryManager.update_decisions(memory, failure_reason="历史失败：频率偏移时必须先校正 patch_L")
    MemoryManager.update_decisions(memory, failure_reason="历史失败：inset_depth 不合理导致匹配恶化")
    return memory


# memory 强制逻辑直接复用生产实现（optimization/memory_rules.py），不再在这里复制一份。
# 此前 benchmark 维护着一份近乎逐字的副本，已经与生产漂移（缺 farfield/port 两条规则），
# 于是 CI 的 --assert-thresholds 门禁验证的是副本，生产 _memory_guidance 反而没有基准覆盖。
# memory_rules 是纯数据变换、不依赖 CST/solver 状态，从 benchmark 直接 import 是安全的。
def _with_changed_by_memory(proposal: dict[str, Any], recalled: list[Any], guidance: dict[str, Any]) -> dict[str, Any]:
    """生产 _with_memory_metadata + benchmark 自己需要的统计字段。

    `changed_by_memory` 与 `provider` 都只服务于 benchmark 报表（生产 optimizer 用
    session.metadata 记录同类信息），所以留在 harness 这一侧，不回灌生产结构。
    """
    enriched = _with_memory_metadata(proposal, recalled, guidance)
    enriched["changed_by_memory"] = bool(guidance.get("expected_param") and not enriched.get("memory_violation_reason"))
    if proposal.get("provider"):
        enriched.setdefault("provider", proposal["provider"])
    return enriched


def _memory_fallback_for_benchmark(proposal: dict[str, Any]) -> dict[str, Any] | None:
    """生产 _memory_constraint_fallback + 保留 benchmark 报表用的 provider 字段。"""
    fallback = _memory_constraint_fallback(proposal)
    if fallback is None:
        return None
    if proposal.get("provider"):
        fallback["provider"] = proposal["provider"]
    fallback["changed_by_memory"] = False
    return fallback


def _heuristic_proposal(params: dict[str, float], summary: dict[str, Any], history: list[dict[str, Any]], case: FakeCSTCase) -> dict[str, Any]:
    strategy = HeuristicPatchOptimizationStrategy(_direction_inferer)
    proposal = strategy.propose_next_step(
        OptimizationContext(
            target=OptimizationTarget(mode="at_f0", target_freq_ghz=case.target_freq_ghz, target_db=case.target_db),
            parameters=params,
            s11_summary=S11Summary.from_mapping(summary),
            history=history,
        )
    )
    if not proposal.has_updates:
        return {"param": "", "delta_mm": 0.0, "reason": proposal.message or proposal.reason, "source": proposal.strategy}
    update = proposal.updates[0]
    return {
        "param": update.name,
        "delta_mm": round(update.new - update.old, 4),
        "reason": proposal.reason,
        "source": proposal.strategy,
    }


def _algorithm_proposal(sim: FakeCSTSimulator, params: dict[str, float], summary: dict[str, Any], case: FakeCSTCase) -> dict[str, Any]:
    single_candidates = [
        [("patch_L", 0.5)],
        [("patch_L", -0.5)],
        [("inset_depth", 0.25)],
        [("inset_depth", -0.25)],
        [("feed_W", 0.25)],
        [("feed_W", -0.25)],
    ]
    joint_candidates = [
        [("inset_depth", inset_delta), ("feed_W", feed_delta)]
        for inset_delta in (0.25, -0.25)
        for feed_delta in (0.25, -0.25)
    ]
    freq_delta = 0.0
    if abs(float(summary["min_freq_ghz"]) - case.target_freq_ghz) > 0.03:
        freq_delta = 0.5 if summary["min_freq_ghz"] > case.target_freq_ghz else -0.5
        single_candidates = [[("patch_L", freq_delta)]] + single_candidates
        joint_candidates.extend(
            [("patch_L", freq_delta), ("inset_depth", inset_delta), ("feed_W", feed_delta)]
            for inset_delta in (0.25, -0.25)
            for feed_delta in (0.25, -0.25)
        )
    candidates = single_candidates + joint_candidates
    baseline = float(summary["target_s11_db"])
    best_updates: list[tuple[str, float]] = []
    best_metric = baseline
    for updates in candidates:
        trial = dict(params)
        for param, delta in updates:
            trial[param] = trial[param] + delta
        metric = float(sim.evaluate(trial)["target_s11_db"])
        if metric < best_metric:
            best_updates = list(updates)
            best_metric = metric
    if not best_updates:
        return {"param": "", "delta_mm": 0.0, "reason": "algorithm found no improving local candidate", "source": "algorithm"}
    if len(best_updates) == 1:
        param, delta = best_updates[0]
        return {
            "param": param,
            "delta_mm": delta,
            "reason": f"local candidate search improved target S11 from {baseline:.2f} to {best_metric:.2f} dB",
            "source": "algorithm",
            "predicted_target_s11_db": round(best_metric, 3),
        }
    return {
        "param": "+".join(param for param, _ in best_updates),
        "delta_mm": 0.0,
        "updates": [{"param": param, "delta_mm": delta} for param, delta in best_updates],
        "reason": f"joint candidate search improved target S11 from {baseline:.2f} to {best_metric:.2f} dB",
        "source": "algorithm",
        "predicted_target_s11_db": round(best_metric, 3),
    }


class DeterministicProposalProvider:
    name = "deterministic_proxy"

    def propose(
        self,
        *,
        params: dict[str, float],
        summary: dict[str, Any],
        case: FakeCSTCase,
        memory: StructuredMemory | None,
        history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        freq_error = float(summary["min_freq_ghz"]) - case.target_freq_ghz
        if abs(freq_error) > 0.03:
            return {
                "param": "patch_L",
                "delta_mm": 0.45 if freq_error > 0 else -0.45,
                "reason": "LLM proxy: 根据谐振频率偏差先调 patch_L。",
                "source": "llm",
                "provider": self.name,
            }

        recalled = []
        if memory is not None:
            query = (
                f"{case.description} S11 匹配差 频率偏移 patch_L feed_W inset_depth "
                f"feed_W={params.get('feed_W'):.3f} inset_depth={params.get('inset_depth'):.3f} "
                f"min_freq={summary['min_freq_ghz']:.3f} target_freq={case.target_freq_ghz:.3f}"
            )
            recalled = recall_memory(memory, query, scopes=["project", "session"], k=6, entry_types=["lesson", "failure"])
        guidance = _memory_guidance(recalled, params, summary, case.target_freq_ghz, case.target_db)
        if guidance["expected_param"] == "feed_W":
            return _with_changed_by_memory(
                {
                    "param": "feed_W",
                    "delta_mm": 0.35,
                    "reason": "LLM proxy: 召回 feed_W 过小失败模式后优先增大 feed_W。",
                    "source": "llm",
                    "provider": self.name,
                },
                recalled,
                guidance,
            )
        if guidance["expected_param"] == "inset_depth":
            return _with_changed_by_memory(
                {
                    "param": "inset_depth",
                    "delta_mm": 0.3 * (guidance["expected_delta_sign"] or 1),
                    "reason": "LLM proxy: 召回 inset_depth 匹配经验后优先修正 inset 深度。",
                    "source": "llm",
                    "provider": self.name,
                },
                recalled,
                guidance,
            )
        return _with_changed_by_memory(
            _heuristic_proposal(params, summary, history, case)
            | {"source": "llm", "provider": self.name, "reason": "LLM proxy: 使用物理启发式策略避免固定步长振荡。"},
            recalled,
            guidance,
        )


class OpenAICompatibleProposalProvider:
    name = "openai_compatible"

    def __init__(self, model: str | None = None, timeout: int | None = None):
        from openai import OpenAI

        self.client = OpenAI()
        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        self.timeout = timeout or int(os.environ.get("OPENAI_TIMEOUT", "20"))

    def propose(
        self,
        *,
        params: dict[str, float],
        summary: dict[str, Any],
        case: FakeCSTCase,
        memory: StructuredMemory | None,
        history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        from cst_agent_workbench.agent.analyzer import propose_patch_optimization_with_llm

        recalled = []
        if memory is not None:
            query = (
                f"{case.description} S11 匹配差 频率偏移 patch_L feed_W inset_depth "
                f"feed_W={params.get('feed_W'):.3f} inset_depth={params.get('inset_depth'):.3f} "
                f"min_freq={summary.get('min_freq_ghz'):.3f} target_freq={case.target_freq_ghz:.3f}"
            )
            recalled = recall_memory(memory, query, scopes=["project", "session"], k=6, entry_types=["lesson", "failure"])
        guidance = _memory_guidance(recalled, params, summary, case.target_freq_ghz, case.target_db)
        proposal, usage = propose_patch_optimization_with_llm(
            self.client,
            self.model,
            {
                "min_s11_db": summary.get("min_s11_db"),
                "min_freq_ghz": summary.get("min_freq_ghz"),
                "bandwidth_ghz": summary.get("bandwidth_ghz"),
                "at_f0_s11_db": summary.get("target_s11_db"),
            },
            case.target_freq_ghz,
            params,
            history=history,
            timeout=self.timeout,
            failure_reasons=[entry.text for entry in recalled if entry.entry_type == "failure"],
            memory_constraints=guidance["constraints"],
        )
        if not proposal:
            return _with_changed_by_memory(
                {
                    "param": "",
                    "delta_mm": 0.0,
                    "reason": "OpenAI-compatible provider returned no valid proposal",
                    "source": "llm",
                    "provider": self.name,
                    "usage": usage,
                },
                recalled,
                guidance,
            )
        proposal = dict(proposal)
        proposal.update({"source": "llm", "provider": self.name, "usage": usage})
        return _with_changed_by_memory(proposal, recalled, guidance)


def make_proposal_provider(name: str, llm_timeout: int | None = None) -> ProposalProvider:
    if name == "deterministic_proxy":
        return DeterministicProposalProvider()
    if name == "openai_compatible":
        return OpenAICompatibleProposalProvider(timeout=llm_timeout)
    raise ValueError(f"Unknown proposal provider: {name}")


def _token_cost(group: AblationGroup, recalled_count: int, proposal: dict[str, Any] | None = None) -> dict[str, int]:
    if not group.use_llm:
        return {"prompt": 0, "completion": 0, "total": 0}
    usage = (proposal or {}).get("usage") or {}
    if usage:
        prompt = int(usage.get("prompt") or usage.get("prompt_tokens") or 0)
        completion = int(usage.get("completion") or usage.get("completion_tokens") or 0)
        return {"prompt": prompt, "completion": completion, "total": prompt + completion}
    prompt = 140 + 25 * recalled_count
    completion = 45
    if group.use_reflection:
        completion += 20
    return {"prompt": prompt, "completion": completion, "total": prompt + completion}


def _compact_summary(summary: dict[str, Any]) -> dict[str, Any]:
    compact = {key: value for key, value in summary.items() if key != "plot_data"}
    compact["plot_data_points"] = len(summary.get("plot_data") or [])
    return compact


def _proposal_updates(proposal: dict[str, Any]) -> list[tuple[str, float]]:
    updates = proposal.get("updates") or []
    if updates:
        parsed = []
        for update in updates:
            try:
                parsed.append((str(update["param"]), float(update["delta_mm"])))
            except (KeyError, TypeError, ValueError):
                return []
        return parsed
    if not proposal.get("param"):
        return []
    try:
        return [(str(proposal["param"]), float(proposal.get("delta_mm", 0.0)))]
    except (TypeError, ValueError):
        return []


def _trial_target_s11(sim: FakeCSTSimulator, params: dict[str, float], proposal: dict[str, Any]) -> float:
    trial = dict(params)
    for param, delta in _proposal_updates(proposal):
        if param in trial:
            trial[param] = trial[param] + delta
    return float(sim.evaluate(trial)["target_s11_db"])


def _same_direction_repeat(history: list[dict[str, Any]], proposal: dict[str, Any]) -> bool:
    updates = _proposal_updates(proposal)
    if not history or len(updates) != 1:
        return False
    last = history[-1]
    param, delta = updates[0]
    if last.get("improved", True) or last.get("param") != param:
        return False
    try:
        return float(last.get("delta_mm", 0.0)) * delta > 0
    except (TypeError, ValueError):
        return False


def _provider_error_proposal(provider_name: str, error: Exception) -> dict[str, Any]:
    return {
        "param": "",
        "delta_mm": 0.0,
        "reason": f"provider error: {type(error).__name__}",
        "source": "provider_error",
        "provider": provider_name,
        "provider_error": {"type": type(error).__name__, "message": str(error)},
    }


def _emit_progress(enabled: bool, message: str) -> None:
    if enabled:
        print(message, file=sys.stderr, flush=True)


def _write_trace_event(trace_jsonl: Path | None, event: dict[str, Any]) -> None:
    if trace_jsonl is None:
        return
    trace_jsonl.parent.mkdir(parents=True, exist_ok=True)
    payload = {"timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), **event}
    with trace_jsonl.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _run_case(
    group: AblationGroup,
    case: FakeCSTCase,
    max_rounds: int,
    proposal_provider: ProposalProvider,
    *,
    progress: bool = False,
    trace_jsonl: Path | None = None,
    memory_factory=None,
) -> dict[str, Any]:
    sim = FakeCSTSimulator(case)
    if group.use_memory:
        memory = (memory_factory or _make_memory)()
    else:
        memory = None
    history: list[dict[str, Any]] = []
    rounds = []
    tokens = {"prompt": 0, "completion": 0, "total": 0}
    proposal_rejects = 0
    fallbacks = 0
    repeated_errors = 0
    memory_hits = 0
    memory_rounds = 0
    memory_changed = 0
    memory_guided_rounds = 0
    memory_complied = 0
    memory_violations = 0
    memory_enforced = 0
    provider_errors: list[dict[str, Any]] = []

    initial_summary = sim.evaluate()
    _emit_progress(progress, f"[case-start] group={group.name} case={case.name} initial_s11={initial_summary['target_s11_db']}")
    if initial_summary["met"]:
        case_result = {
            "case": case.name,
            "success": True,
            "rounds_to_success": 0,
            "initial_summary": _compact_summary(initial_summary),
            "final_summary": _compact_summary(initial_summary),
            "rounds": rounds,
            "tokens": tokens,
            "proposal_rejects": proposal_rejects,
            "fallbacks": fallbacks,
            "repeated_errors": repeated_errors,
            "memory_hits": memory_hits,
            "memory_rounds": memory_rounds,
            "memory_changed": memory_changed,
            "memory_guided_rounds": memory_guided_rounds,
            "memory_complied": memory_complied,
            "memory_violations": memory_violations,
            "memory_enforced": memory_enforced,
            "provider_errors": provider_errors,
        }
        _write_trace_event(trace_jsonl, {"event": "case_complete", "group": group.name, "case": case.name, "result": case_result})
        _emit_progress(progress, f"[case-done] group={group.name} case={case.name} success=True rounds=0")
        return case_result

    for round_index in range(1, max_rounds + 1):
        params = sim.snapshot()
        before = sim.evaluate()
        proposal_before_validation: dict[str, Any] | None = None
        _emit_progress(progress, f"[round-start] group={group.name} case={case.name} round={round_index}")
        if group.use_algorithm:
            proposal = _algorithm_proposal(sim, params, before, case)
        elif group.use_llm:
            try:
                proposal = proposal_provider.propose(
                    params=params,
                    summary=before,
                    case=case,
                    memory=memory,
                    history=history,
                )
            except Exception as exc:
                error_payload = {"round": round_index, "type": type(exc).__name__, "message": str(exc)}
                provider_errors.append(error_payload)
                _emit_progress(progress, f"[provider-error] group={group.name} case={case.name} round={round_index} error={type(exc).__name__}")
                _write_trace_event(trace_jsonl, {"event": "provider_error", "group": group.name, "case": case.name, **error_payload})
                proposal = _provider_error_proposal(proposal_provider.name, exc)
            proposal_before_validation = dict(proposal)
            recalled_count = len(proposal.get("recalled_memory_ids") or [])
            if group.use_memory:
                memory_rounds += 1
                if recalled_count:
                    memory_hits += 1
                if proposal.get("memory_expected_param"):
                    memory_guided_rounds += 1
                if proposal.get("memory_complied"):
                    memory_complied += 1
                if proposal.get("memory_violation"):
                    memory_violations += 1
                if proposal.get("changed_by_memory"):
                    memory_changed += 1
            usage = _token_cost(group, recalled_count, proposal)
            for key, value in usage.items():
                tokens[key] += value
            ok, reject_reason = validate_llm_proposal(
                {"param": proposal.get("param"), "delta_mm": proposal.get("delta_mm"), "reason": proposal.get("reason", "")},
                history,
            )
            memory_fallback = _memory_fallback_for_benchmark(proposal) if proposal.get("memory_violation") else None
            if memory_fallback is not None:
                proposal_rejects += 1
                fallbacks += 1
                memory_enforced += 1
                memory_fallback["fallback_reason"] = proposal.get("memory_violation_reason", "")
                proposal = memory_fallback
            elif not ok and not proposal.get("provider_error"):
                proposal_rejects += 1
                fallbacks += 1
                fallback = _heuristic_proposal(params, before, history, case)
                fallback["fallback_reason"] = reject_reason
                proposal = fallback
        else:
            proposal = _heuristic_proposal(params, before, history, case)

        if group.use_llm and len(history) >= 2 and not before.get("met"):
            recent = history[-2:]
            same_param_loop = len({item.get("param") for item in recent if item.get("param")}) == 1
            no_recent_target = all(float(item.get("metric_value", 0.0)) > case.target_db for item in recent)
            if same_param_loop and no_recent_target:
                fallback = _algorithm_proposal(sim, params, before, case)
                if fallback.get("param"):
                    fallbacks += 1
                    fallback["source"] = "validator_algorithm_fallback"
                    fallback["provider"] = proposal.get("provider", "")
                    fallback["fallback_reason"] = "LLM proposal looped without meeting target; local candidate search selected a better step"
                    proposal = fallback

        if group.use_llm and not proposal.get("provider_error") and not before.get("met") and float(before["min_s11_db"]) > case.target_db + 1.0:
            fallback = _algorithm_proposal(sim, params, before, case)
            if fallback.get("updates") and _trial_target_s11(sim, params, fallback) < _trial_target_s11(sim, params, proposal) - 1e-9:
                fallbacks += 1
                fallback["source"] = "validator_joint_fallback"
                fallback["provider"] = proposal.get("provider", "")
                fallback["fallback_reason"] = "mixed frequency and matching error; joint candidate search selected a better step"
                proposal = fallback

        if _same_direction_repeat(history, proposal):
            repeated_errors += 1
        updates = _proposal_updates(proposal)
        if not updates:
            fallbacks += 1
            after = before
            improved = False
        else:
            for param, delta in updates:
                sim.apply_delta(param, delta)
            after = sim.evaluate()
            improved = float(after["target_s11_db"]) < float(before["target_s11_db"])

        record = {
            "round": round_index,
            "param": proposal.get("param", ""),
            "delta_mm": proposal.get("delta_mm", 0.0),
            "updates": [{"param": param, "delta_mm": delta} for param, delta in updates],
            "source": proposal.get("source", group.name),
            "provider": proposal.get("provider", ""),
            "reason": proposal.get("reason", ""),
            "target_s11_before": before["target_s11_db"],
            "target_s11_after": after["target_s11_db"],
            "min_freq_after": after["min_freq_ghz"],
            "improved": improved,
            "met": after["met"],
            "memory_impact": {
                "used_memory": bool(proposal.get("recalled_memory_ids")),
                "recalled_memory_ids": proposal.get("recalled_memory_ids", []),
                "recalled_lessons": proposal.get("recalled_lessons", []),
                "proposal_before_validation": {
                    "param": (proposal_before_validation or proposal).get("param", ""),
                    "delta_mm": (proposal_before_validation or proposal).get("delta_mm", 0.0),
                },
                "proposal_after_validation": {
                    "param": proposal.get("param", ""),
                    "delta_mm": proposal.get("delta_mm", 0.0),
                    "updates": [{"param": param, "delta_mm": delta} for param, delta in updates],
                },
                "fallback_reason": proposal.get("fallback_reason", ""),
                "changed_by_memory": bool(proposal.get("changed_by_memory", False)),
                "memory_expected_param": (proposal_before_validation or proposal).get("memory_expected_param", ""),
                "memory_expected_delta_sign": (proposal_before_validation or proposal).get("memory_expected_delta_sign", 0),
                "memory_constraints": (proposal_before_validation or proposal).get("memory_constraints", []),
                "memory_rule_names": (proposal_before_validation or proposal).get("memory_rule_names", []),
                "memory_enforced_rule": (proposal_before_validation or proposal).get("memory_enforced_rule", ""),
                "memory_complied": bool((proposal_before_validation or proposal).get("memory_complied", False)),
                "memory_violation": bool((proposal_before_validation or proposal).get("memory_violation", False)),
                "memory_violation_reason": (proposal_before_validation or proposal).get("memory_violation_reason", ""),
                "memory_enforced_by_validator": bool(proposal.get("memory_enforced_by_validator", False)),
            },
        }
        if proposal.get("provider_error"):
            record["provider_error"] = proposal["provider_error"]
        rounds.append(record)
        _write_trace_event(trace_jsonl, {"event": "round_complete", "group": group.name, "case": case.name, "round": round_index, "record": record})
        _emit_progress(progress, f"[round-done] group={group.name} case={case.name} round={round_index} param={record['param']} s11={record['target_s11_after']} met={record['met']}")
        history.append(
            {
                "round": round_index,
                "param": proposal.get("param", ""),
                "delta_mm": proposal.get("delta_mm", 0.0),
                "updates": [{"param": param, "delta_mm": delta} for param, delta in updates],
                "improved": improved,
                "param_snapshot": sim.snapshot(),
                "metric_value": after["target_s11_db"],
            }
        )
        if memory is not None and group.use_reflection and not improved and proposal.get("param"):
            MemoryManager.update_decisions(memory, failure_reason=f"{proposal['param']} 同方向调整未改善")
            MemoryManager.update_decisions(
                memory,
                strategy_entry={
                    "round": round_index,
                    "lesson": f"避免重复 {proposal['param']} 同方向调整，改查匹配参数。",
                    "failure_pattern": f"{proposal['param']} same-direction no improvement",
                    "avoid_next": f"不要继续同方向调整 {proposal['param']}",
                    "reuse_condition": "上一轮未改善时",
                    "confidence": 0.7,
                },
            )
        if after["met"]:
            break

    final_summary = sim.evaluate()
    case_result = {
        "case": case.name,
        "success": bool(final_summary["met"]),
        "rounds_to_success": len(rounds) if final_summary["met"] else None,
        "initial_summary": _compact_summary(initial_summary),
        "final_summary": _compact_summary(final_summary),
        "rounds": rounds,
        "tokens": tokens,
        "proposal_rejects": proposal_rejects,
        "fallbacks": fallbacks,
        "repeated_errors": repeated_errors,
        "memory_hits": memory_hits,
        "memory_rounds": memory_rounds,
        "memory_changed": memory_changed,
        "memory_guided_rounds": memory_guided_rounds,
        "memory_complied": memory_complied,
        "memory_violations": memory_violations,
        "memory_enforced": memory_enforced,
        "provider_errors": provider_errors,
    }
    _write_trace_event(trace_jsonl, {"event": "case_complete", "group": group.name, "case": case.name, "result": case_result})
    _emit_progress(progress, f"[case-done] group={group.name} case={case.name} success={case_result['success']} rounds={len(rounds)}")
    return case_result


def _avg(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def _summarize_group(group: AblationGroup, cases: list[dict[str, Any]]) -> dict[str, Any]:
    successes = [case for case in cases if case["success"]]
    total_rounds = sum(len(case["rounds"]) for case in cases)
    total_tokens = sum(case["tokens"]["total"] for case in cases)
    memory_rounds = sum(case["memory_rounds"] for case in cases)
    memory_guided_rounds = sum(case.get("memory_guided_rounds", 0) for case in cases)
    return {
        "group": group.name,
        "case_count": len(cases),
        "success_rate": round(len(successes) / len(cases), 4) if cases else 0.0,
        "avg_rounds_to_success": _avg([case["rounds_to_success"] for case in successes if case["rounds_to_success"] is not None]),
        "avg_tokens": round(total_tokens / len(cases), 2) if cases else 0.0,
        "proposal_reject_rate": round(sum(case["proposal_rejects"] for case in cases) / total_rounds, 4) if total_rounds else 0.0,
        "fallback_rate": round(sum(case["fallbacks"] for case in cases) / total_rounds, 4) if total_rounds else 0.0,
        "repeat_error_rate": round(sum(case["repeated_errors"] for case in cases) / total_rounds, 4) if total_rounds else 0.0,
        "memory_recall_hit_rate": round(sum(case["memory_hits"] for case in cases) / memory_rounds, 4) if memory_rounds else 0.0,
        "memory_changed_proposal_rate": round(sum(case["memory_changed"] for case in cases) / memory_rounds, 4) if memory_rounds else 0.0,
        "memory_compliance_rate": round(sum(case.get("memory_complied", 0) for case in cases) / memory_guided_rounds, 4) if memory_guided_rounds else 0.0,
        "memory_violation_rate": round(sum(case.get("memory_violations", 0) for case in cases) / memory_guided_rounds, 4) if memory_guided_rounds else 0.0,
        "memory_enforced_rate": round(sum(case.get("memory_enforced", 0) for case in cases) / memory_guided_rounds, 4) if memory_guided_rounds else 0.0,
        "provider_error_count": sum(len(case.get("provider_errors", [])) for case in cases),
        "failure_cases": [
            {
                "case": case["case"],
                "final_target_s11_db": case["final_summary"]["target_s11_db"],
                "rounds": len(case["rounds"]),
            }
            for case in cases
            if not case["success"]
        ],
        "cases": cases,
    }


def build_run_metadata(provider: ProposalProvider, cases: list[FakeCSTCase]) -> dict[str, Any]:
    seeds = [case.seed for case in cases]
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "proposal_provider": provider.name,
        "provider_model": getattr(provider, "model", os.environ.get("OPENAI_MODEL", "")),
        "openai_api_key_configured": bool(os.environ.get("OPENAI_API_KEY")),
        "openai_base_url_configured": bool(os.environ.get("OPENAI_BASE_URL")),
        "case_seed_min": min(seeds) if seeds else None,
        "case_seed_max": max(seeds) if seeds else None,
        "case_names": [case.name for case in cases],
    }


def run_ablation(
    count: int = 20,
    max_rounds: int = 6,
    group_names: list[str] | None = None,
    proposal_provider: ProposalProvider | None = None,
    *,
    progress: bool = False,
    trace_jsonl: Path | None = None,
    memory_factory=None,
) -> dict[str, Any]:
    provider = proposal_provider or DeterministicProposalProvider()
    cases = make_benchmark_cases(count)
    selected = [group for group in ABLATION_GROUPS if group_names is None or group.name in set(group_names)]
    groups = {}
    if trace_jsonl is not None:
        trace_jsonl.parent.mkdir(parents=True, exist_ok=True)
        trace_jsonl.write_text("", encoding="utf-8")
    for group in selected:
        _emit_progress(progress, f"[group-start] group={group.name} cases={len(cases)}")
        group_cases = [
            _run_case(
                group,
                case,
                max_rounds=max_rounds,
                proposal_provider=provider,
                progress=progress,
                trace_jsonl=trace_jsonl,
                memory_factory=memory_factory,
            )
            for case in cases
        ]
        groups[group.name] = _summarize_group(group, group_cases)
        _write_trace_event(trace_jsonl, {"event": "group_complete", "group": group.name, "summary": groups[group.name]})
        _emit_progress(progress, f"[group-done] group={group.name} success_rate={groups[group.name]['success_rate']}")
    return {
        "config": {
            "case_count": count,
            "max_rounds": max_rounds,
            "groups": [group.name for group in selected],
            "proposal_provider": provider.name,
        },
        "metadata": build_run_metadata(provider, cases),
        "groups": groups,
    }


def build_stdout_summary(report: dict[str, Any], output_path: Path | None = None) -> dict[str, Any]:
    summary = {
        "config": report.get("config", {}),
        "groups": {
            name: {
                key: group.get(key)
                for key in (
                    "success_rate",
                    "avg_rounds_to_success",
                    "avg_tokens",
                    "proposal_reject_rate",
                    "fallback_rate",
                    "repeat_error_rate",
                    "memory_recall_hit_rate",
                    "memory_changed_proposal_rate",
                    "memory_compliance_rate",
                    "memory_violation_rate",
                    "memory_enforced_rate",
                    "provider_error_count",
                    "failure_cases",
                )
            }
            for name, group in (report.get("groups") or {}).items()
        },
    }
    if output_path is not None:
        summary["report_path"] = str(output_path)
    return summary


def validate_quality_thresholds(report: dict[str, Any]) -> list[str]:
    groups = report.get("groups") or {}
    failures: list[str] = []
    no_memory = groups.get("llm_no_memory")
    with_memory = groups.get("llm_with_memory")
    if no_memory and with_memory:
        if float(with_memory.get("success_rate", 0.0)) < float(no_memory.get("success_rate", 0.0)):
            failures.append("llm_with_memory success_rate is lower than llm_no_memory")
        if float(with_memory.get("memory_recall_hit_rate", 0.0)) <= 0.0:
            failures.append("llm_with_memory did not recall memory in any round")
        if float(with_memory.get("memory_changed_proposal_rate", 0.0)) <= 0.0:
            failures.append("llm_with_memory did not change any proposal after memory recall")
    algorithm = groups.get("algorithm_baseline")
    if algorithm and float(algorithm.get("success_rate", 0.0)) <= 0.0:
        failures.append("algorithm_baseline solved no cases")
    return failures


def build_markdown_summary(report: dict[str, Any]) -> str:
    config = report.get("config", {})
    groups = report.get("groups") or {}
    metadata = report.get("metadata") or {}
    lines = [
        "# Fake CST Agent Ablation Summary",
        "",
        f"- Cases: {config.get('case_count', 0)}",
        f"- Max rounds: {config.get('max_rounds', 0)}",
        f"- Proposal provider: {config.get('proposal_provider', '')}",
        f"- Timestamp UTC: {metadata.get('timestamp_utc', '')}",
        f"- Provider model: {metadata.get('provider_model', '')}",
        f"- Case seed range: {metadata.get('case_seed_min', '')}–{metadata.get('case_seed_max', '')}",
        "",
        "| Group | Success | Avg rounds | Avg tokens | Reject rate | Fallback rate | Repeat error | Memory hit | Memory changed | Memory compliance | Memory violation | Enforced | Provider errors |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, group in groups.items():
        lines.append(
            "| "
            + " | ".join(
                [
                    name,
                    f"{float(group.get('success_rate', 0.0)):.2f}",
                    f"{float(group.get('avg_rounds_to_success', 0.0)):.2f}",
                    f"{float(group.get('avg_tokens', 0.0)):.1f}",
                    f"{float(group.get('proposal_reject_rate', 0.0)):.2f}",
                    f"{float(group.get('fallback_rate', 0.0)):.2f}",
                    f"{float(group.get('repeat_error_rate', 0.0)):.2f}",
                    f"{float(group.get('memory_recall_hit_rate', 0.0)):.2f}",
                    f"{float(group.get('memory_changed_proposal_rate', 0.0)):.2f}",
                    f"{float(group.get('memory_compliance_rate', 0.0)):.2f}",
                    f"{float(group.get('memory_violation_rate', 0.0)):.2f}",
                    f"{float(group.get('memory_enforced_rate', 0.0)):.2f}",
                    str(int(group.get("provider_error_count", 0) or 0)),
                ]
            )
            + " |"
        )

    no_memory = groups.get("llm_no_memory")
    with_memory = groups.get("llm_with_memory")
    if no_memory and with_memory:
        delta_success = float(with_memory.get("success_rate", 0.0)) - float(no_memory.get("success_rate", 0.0))
        delta_rounds = float(with_memory.get("avg_rounds_to_success", 0.0)) - float(no_memory.get("avg_rounds_to_success", 0.0))
        lines.extend(
            [
                "",
                "## Memory impact",
                "",
                f"- Success-rate delta (with memory - no memory): {delta_success:+.2f}",
                f"- Avg-round delta (with memory - no memory): {delta_rounds:+.2f}",
                f"- Memory recall hit rate: {float(with_memory.get('memory_recall_hit_rate', 0.0)):.2f}",
                f"- Memory changed proposal rate: {float(with_memory.get('memory_changed_proposal_rate', 0.0)):.2f}",
                f"- Memory compliance rate: {float(with_memory.get('memory_compliance_rate', 0.0)):.2f}",
                f"- Memory violation rate: {float(with_memory.get('memory_violation_rate', 0.0)):.2f}",
                f"- Memory enforced rate: {float(with_memory.get('memory_enforced_rate', 0.0)):.2f}",
            ]
        )

    lines.extend(["", "## Failure cases", ""])
    for name, group in groups.items():
        failures = group.get("failure_cases") or []
        if not failures:
            lines.append(f"- {name}: none")
            continue
        preview = ", ".join(f"{case['case']} ({case['final_target_s11_db']} dB)" for case in failures[:5])
        suffix = "" if len(failures) <= 5 else f", +{len(failures) - 5} more"
        lines.append(f"- {name}: {preview}{suffix}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run deterministic fake-CST agent ablation benchmark.")
    parser.add_argument("--cases", type=int, default=20)
    parser.add_argument("--max-rounds", type=int, default=6)
    parser.add_argument("--group", action="append", dest="groups", help="Group name to run; repeat for multiple groups.")
    parser.add_argument(
        "--provider",
        default="deterministic_proxy",
        choices=["deterministic_proxy", "openai_compatible"],
        help="LLM proposal provider for LLM ablation groups.",
    )
    parser.add_argument("--output", default=str(PROJECT_ROOT / "benchmarks" / "reports" / "agent_ablation_fake_cst.json"))
    parser.add_argument("--summary-md", default="", help="Optional path to write a Markdown summary report.")
    parser.add_argument("--trace-jsonl", default="", help="Optional path for incremental JSONL trace events.")
    parser.add_argument("--progress", action="store_true", help="Print group/case/round progress to stderr.")
    parser.add_argument("--llm-timeout", type=int, default=20, help="Per-request timeout in seconds for openai_compatible provider.")
    parser.add_argument("--assert-thresholds", action="store_true", help="Fail if deterministic quality thresholds are not met.")
    args = parser.parse_args(argv)

    report = run_ablation(
        count=args.cases,
        max_rounds=args.max_rounds,
        group_names=args.groups,
        proposal_provider=make_proposal_provider(args.provider, llm_timeout=args.llm_timeout),
        progress=args.progress,
        trace_jsonl=Path(args.trace_jsonl) if args.trace_jsonl else None,
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.summary_md:
        summary_path = Path(args.summary_md)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(build_markdown_summary(report), encoding="utf-8")
    threshold_failures = validate_quality_thresholds(report) if args.assert_thresholds else []
    stdout_summary = build_stdout_summary(report, output_path)
    if threshold_failures:
        stdout_summary["threshold_failures"] = threshold_failures
    print(json.dumps(stdout_summary, ensure_ascii=False, indent=2))
    return 1 if threshold_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
