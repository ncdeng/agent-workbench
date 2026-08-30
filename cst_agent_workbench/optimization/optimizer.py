import json
import logging
from dataclasses import dataclass

from cst_agent_workbench import config
from cst_agent_workbench.agent.runtime_state import (
    append_trace_turn,
    finish_tool_call_trace,
    finish_trace_run,
    record_observability_degradation,
    start_optimizer_trace_run,
    start_tool_call_trace,
)
from cst_agent_workbench.optimization.diagnosis import diagnose_s11
from cst_agent_workbench.optimization.memory_rules import (
    dedupe_rag_context_against_memory,
    _memory_constraint_fallback,
    _memory_guidance,
    _with_memory_metadata,
)
from cst_agent_workbench.optimization.models import OptimizationContext, OptimizationTarget, S11Summary
from cst_agent_workbench.optimization.strategy import HeuristicPatchOptimizationStrategy

logger = logging.getLogger(__name__)
from cst_agent_workbench.results.summary import evaluate_optimization_target


@dataclass
class _LLMDecisionResult:
    """Output from the LLM decision layer."""
    proposal: object  # OptimizationProposal (avoid circular import at module level)
    llm_source: str = "[heuristic]"
    llm_failure_reason: str = ""
    llm_proposal: dict = None  # raw LLM proposal dict
    execution_path: str = "llm"


def _coerce_float(value):
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _reference_resonance_freq(summary: dict, target_freq_ghz: float) -> float | None:
    candidates = []
    for resonance in summary.get("resonances") or []:
        freq = _coerce_float(resonance.get("freq_ghz"))
        depth = _coerce_float(resonance.get("depth_db"))
        if freq is not None and depth is not None:
            candidates.append((freq, depth))
    if candidates and target_freq_ghz > 0:
        return min(candidates, key=lambda item: abs(item[0] - target_freq_ghz))[0]
    if candidates:
        return min(candidates, key=lambda item: item[1])[0]
    return _coerce_float(summary.get("min_freq_ghz"))


class PatchOptimizerMixin:
    def can_use_programmatic_patch_optimizer(self) -> bool:
        from cst_agent_workbench.cst.primitives import get_parameters

        if not self.cst.is_connected():
            return False
        params = get_parameters()
        required = {"f0", "patch_L", "feed_W", "inset_depth", "substrate_h", "copper_t"}
        return required.issubset(params.keys())

    @staticmethod
    def _attempted_param_direction(record: dict, param_name: str) -> int:
        meta = (record.get("attempted_changed_params") or {}).get(param_name)
        if not isinstance(meta, dict):
            return 0
        try:
            old = float(meta.get("old"))
            new = float(meta.get("new"))
        except (TypeError, ValueError):
            return 0
        if abs(new - old) < 1e-12:
            return 0
        return 1 if new > old else -1

    @classmethod
    def _recent_rollback_avoidance_direction(cls, history: list[dict], param_name: str) -> int:
        for record in reversed(history[-4:]):
            if not record.get("rolled_back"):
                continue
            direction = cls._attempted_param_direction(record, param_name)
            if direction:
                return -direction
        return 0

    @classmethod
    def _infer_param_direction_from_history(cls, history: list[dict], param_name: str, default_sign: int) -> int:
        rollback_direction = cls._recent_rollback_avoidance_direction(history, param_name)
        if len(history) < 2:
            return rollback_direction or default_sign
        prev = history[-2]
        last = history[-1]
        prev_snapshot = prev.get("param_snapshot", {})
        last_snapshot = last.get("param_snapshot", {})
        try:
            prev_value = float(prev_snapshot.get(param_name))
            last_value = float(last_snapshot.get(param_name))
        except (TypeError, ValueError):
            return default_sign
        if abs(last_value - prev_value) < 1e-12:
            return rollback_direction or default_sign
        last_direction = 1 if last_value > prev_value else -1
        prev_metric = prev.get("metric_value")
        last_metric = last.get("metric_value")
        try:
            return last_direction if float(last_metric) < float(prev_metric) else -last_direction
        except (TypeError, ValueError):
            return last_direction if last.get("improved") else -last_direction

    def _infer_param_direction(self, param_name: str, default_sign: int) -> int:
        return self._infer_param_direction_from_history(self.opt_state.history, param_name, default_sign)

    def _build_programmatic_patch_strategy(self):
        return HeuristicPatchOptimizationStrategy(self._infer_param_direction_from_history)

    def _attempt_rollback(
        self,
        proposal,
        baseline: dict,
        after: dict,
        effective_target_freq: float,
        target_mode: str,
        gen_store_parameter,
        register_parameter,
        diagnosis: dict | None = None,
    ) -> tuple:
        """Attempt rollback if metric degraded. Returns (after, rolled_back, rollback_failed, rollback_reason)."""
        rolled_back = False
        rollback_failed = False
        rollback_reason = ""
        if not after.get("success"):
            return after, rolled_back, rollback_failed, rollback_reason

        if (diagnosis or {}).get("recommended_parameter_family") == "patch_L":
            before_freq = _reference_resonance_freq(baseline, effective_target_freq)
            after_freq = _reference_resonance_freq(after, effective_target_freq)
            if before_freq is not None and after_freq is not None and effective_target_freq > 0:
                before_error = abs(float(before_freq) - effective_target_freq)
                after_error = abs(float(after_freq) - effective_target_freq)
                if after_error <= before_error + 1e-9:
                    return after, rolled_back, rollback_failed, rollback_reason
                rollback_reason = (
                    f"本轮频率校正使谐振偏差从 {before_error:.3f} GHz 退化到 {after_error:.3f} GHz，自动回滚。"
                )
            else:
                rollback_reason = "本轮频率校正后缺少可比较的谐振频率，自动回滚。"
        else:
            before_metric = baseline["target_s11_db"] if target_mode == "at_f0" else baseline["min_s11_db"]
            after_metric = after["target_s11_db"] if target_mode == "at_f0" else after["min_s11_db"]
            if after_metric is None or before_metric is None or after_metric <= before_metric + 1e-9:
                return after, rolled_back, rollback_failed, rollback_reason
            rollback_reason = f"本轮调参使目标指标从 {before_metric:.2f} dB 退化到 {after_metric:.2f} dB，自动回滚。"
        rollback_lines = []
        rollback_params = {}
        for update in proposal.updates:
            _, rollback_line = gen_store_parameter(update.name, self._format_mm(update.old))
            rollback_lines.append(rollback_line)
            rollback_params[update.name] = {
                "old": self._format_mm(update.new),
                "new": self._format_mm(update.old),
            }
        rollback_result = self.cst.execute_vba("\n".join(rollback_lines), label="programmatic_patch_opt_rollback", timeout=120)
        self._record_runtime_event(
            "1_环境与材料",
            "programmatic_patch_rollback",
            bool(rollback_result.get("success")),
            rollback_result.get("message", ""),
            f"程序化优化回滚参数: {json.dumps(rollback_params, ensure_ascii=False)}",
        )
        if not rollback_result.get("success"):
            return after, True, True, f"{rollback_reason} 回滚参数写回失败：{rollback_result.get('message', '')}"

        if rollback_result.get("executed", False):
            for update in proposal.updates:
                register_parameter(update.name, self._format_mm(update.old))
        rollback_solver = self.cst.run_solver(timeout=300)
        self._record_runtime_event(
            "4_运行仿真",
            "programmatic_patch_rollback_run_solver",
            bool(rollback_solver.get("success")),
            rollback_solver.get("message", ""),
            "程序化贴片优化: 回滚后重新求解",
        )
        if rollback_solver.get("success"):
            rollback_after = self._collect_s11_summary(effective_target_freq)
            if rollback_after.get("success"):
                return rollback_after, True, False, rollback_reason
            return baseline, True, True, f"{rollback_reason} 回滚后结果读取失败：{rollback_after.get('message', '')}"
        return baseline, True, True, f"{rollback_reason} 回滚参数已写回，但回滚后重新求解失败：{rollback_solver.get('message', '')}"

    def _run_reflection(
        self,
        proposal,
        baseline: dict,
        execution_path: str,
        effective_target_freq: float | None = None,
        after_metric: float | None = None,
        improved: bool = False,
        rolled_back: bool = False,
    ):
        """Run post-round reflection to write lessons into memory."""
        try:
            from cst_agent_workbench.agent.reflection import collect_recent_errors, reflect_on_round
            from cst_agent_workbench.rag.knowledge_base import make_design_signature
            update = proposal.updates[0] if proposal.updates else None
            session = getattr(self, "session", None)
            artifacts = getattr(session, "artifacts", None) if session is not None else None
            request = getattr(artifacts, "last_patch_request", None)
            target_freq = (
                effective_target_freq
                if effective_target_freq is not None
                else getattr(request, "f0_ghz", None)
            )
            round_summary = {
                "round": getattr(self.opt_state, "round", 0),
                "param": update.name if update else "",
                "delta": (update.new - update.old) if update else 0,
                "execution_path": execution_path,
                "s11_before": baseline.get("min_s11_db"),
                "freq_before": baseline.get("min_freq_ghz"),
                "after_metric": after_metric,
                "improved": bool(improved),
                "rolled_back": bool(rolled_back),
                "epsilon_r": getattr(request, "epsilon_r", None),
                "target_freq_ghz": target_freq,
                "feed_strategy": getattr(request, "feed_strategy", ""),
                "design_signature": make_design_signature(
                    getattr(request, "epsilon_r", None),
                    target_freq,
                    getattr(request, "feed_strategy", ""),
                ),
                "project_path": str(getattr(getattr(self, "cst", None), "project_path", "") or ""),
                "recent_errors": collect_recent_errors(self),
            }
            reflect_on_round(
                getattr(self, 'client', None),
                getattr(self, 'model', ''),
                round_summary,
                getattr(getattr(self, 'session', None), 'memory', None),
                min_confidence=config.RAG_LESSON_MIN_CONFIDENCE,
            )
        except Exception as exc:
            logger.warning("reflection hook failed: %s", exc)

    def _run_llm_decision_layer(
        self,
        proposal,
        baseline: dict,
        params: dict,
        effective_target_freq: float,
        target_db: float,
        preferred_algorithm: str,
    ) -> _LLMDecisionResult:
        """Run LLM decision layer: RAG retrieval, memory recall, LLM proposal, validation, retry, compliance."""
        result = _LLMDecisionResult(proposal=proposal)
        skip_llm = preferred_algorithm != "auto"
        if skip_llm or getattr(self, "client", None) is None or not proposal.handled or not proposal.has_updates:
            return result

        try:
            from cst_agent_workbench.agent.analyzer import propose_patch_optimization_with_llm
            summary_dict = {
                "min_s11_db": baseline.get("min_s11_db"),
                "min_freq_ghz": baseline.get("min_freq_ghz"),
                "bandwidth_ghz": baseline.get("bandwidth_ghz"),
                "at_f0_s11_db": baseline.get("target_s11_db"),
            }
            history = list(self.opt_state.rounds[-3:]) if hasattr(self.opt_state, "rounds") else None

            # RAG retrieval
            rag_context = self._retrieve_rag_context(baseline, effective_target_freq)

            # Memory recall
            failure_reasons, recalled_memories, memory_guidance_info = self._recall_optimization_memories(
                params, baseline, effective_target_freq, target_db, rag_context,
            )
            memory_lessons = [
                str((getattr(entry, "metadata", {}) or {}).get("lesson") or getattr(entry, "text", ""))
                for entry in recalled_memories
                if getattr(entry, "entry_type", "") == "lesson"
            ]
            rag_context = dedupe_rag_context_against_memory(
                rag_context,
                [*memory_lessons, *failure_reasons, *memory_guidance_info.get("constraints", [])],
            )

            # First LLM call
            llm_proposal, usage = propose_patch_optimization_with_llm(
                self.client, self.model, summary_dict,
                effective_target_freq, dict(params), history=history,
                rag_context=rag_context,
                failure_reasons=failure_reasons,
                memory_lessons=memory_lessons,
                memory_constraints=memory_guidance_info.get("constraints", []),
            )
            llm_parse_error, llm_raw_excerpt = self._account_llm_usage(usage)

            # Validation + retry
            proposal_before_validation = {}
            proposal_after_validation = {}
            memory_enforced_by_validator = False
            if llm_proposal:
                from cst_agent_workbench.agent.analyzer import validate_llm_proposal
                proposal_before_validation = dict(llm_proposal)
                valid, reject_reason = validate_llm_proposal(llm_proposal, history)
                if not valid:
                    llm_proposal2, usage2 = propose_patch_optimization_with_llm(
                        self.client, self.model, summary_dict,
                        effective_target_freq, dict(params), history=history,
                        rag_context=rag_context,
                        reject_reason=reject_reason,
                        failure_reasons=failure_reasons,
                        memory_lessons=memory_lessons,
                        memory_constraints=memory_guidance_info.get("constraints", []),
                    )
                    pe2, re2 = self._account_llm_usage(usage2)
                    llm_parse_error = pe2 or llm_parse_error
                    llm_raw_excerpt = re2 or llm_raw_excerpt
                    valid2, reject_reason2 = validate_llm_proposal(llm_proposal2, history) if llm_proposal2 else (False, "重试无结果")
                    if valid2:
                        llm_proposal = llm_proposal2
                        proposal_before_validation = dict(llm_proposal2 or {})
                        result.execution_path = "llm_after_retry"
                    else:
                        result.llm_failure_reason = f"LLM proposal 自检失败（初次: {reject_reason}; 重试: {reject_reason2}），回退 heuristic"
                        result.execution_path = "heuristic_fallback"
                        llm_proposal = None
                else:
                    result.execution_path = "llm"

                # Memory compliance
                if llm_proposal:
                    llm_proposal = _with_memory_metadata(llm_proposal, recalled_memories, memory_guidance_info)
                    proposal_before_validation = dict(llm_proposal)
                    if llm_proposal.get("memory_violation"):
                        llm_proposal, memory_enforced_by_validator = self._handle_memory_violation(
                            llm_proposal, history, result,
                        )
                proposal_after_validation = dict(llm_proposal or {})

            # Record memory impact
            self._record_memory_impact_metadata(
                recalled_memories, memory_guidance_info,
                proposal_before_validation, proposal_after_validation,
                memory_enforced_by_validator, llm_parse_error, llm_raw_excerpt,
                result.llm_failure_reason,
            )

            # Convert to OptimizationProposal
            if llm_proposal:
                result.llm_proposal = llm_proposal
                param = llm_proposal["param"]
                delta = float(llm_proposal["delta_mm"])
                reason = llm_proposal.get("reason", "")
                if param in params:
                    from cst_agent_workbench.optimization.models import OptimizationProposal, ParameterUpdate
                    old_val = float(params[param])
                    new_val = round(old_val + delta, 4)
                    logger.debug("optimizer execution_path=%s", result.execution_path)
                    result.proposal = OptimizationProposal(
                        handled=True,
                        updates=[ParameterUpdate(name=param, old=old_val, new=new_val)],
                        strategy="llm",
                        reason=f"[LLM] {reason}" if reason else "[LLM] 使用 LLM 生成调参建议。",
                        message=proposal.message,
                    )
                    result.llm_source = "[LLM]"
                else:
                    result.llm_failure_reason = f"LLM 返回了未知参数: {param}"
            else:
                result.execution_path = "heuristic_fallback"
                result.llm_failure_reason = result.llm_failure_reason or "LLM 未返回可解析 proposal"
        except Exception as exc:
            result.llm_failure_reason = f"LLM proposal 异常: {exc}"

        return result

    def _retrieve_rag_context(self, baseline: dict, effective_target_freq: float) -> list:
        """Retrieve RAG antenna design rules for optimization context."""
        rag_context = []
        design_signature = ""
        try:
            from cst_agent_workbench.rag.knowledge_base import make_design_signature, retrieve_antenna_rules
            session = getattr(self, "session", None)
            artifacts = getattr(session, "artifacts", None) if session is not None else None
            request = getattr(artifacts, "last_patch_request", None)
            design_signature = make_design_signature(
                getattr(request, "epsilon_r", None),
                effective_target_freq,
                getattr(request, "feed_strategy", ""),
            )
            rag_query = (
                f"谐振频率 {baseline.get('min_freq_ghz', '?')} GHz "
                f"目标 {effective_target_freq} GHz "
                f"S11 {baseline.get('min_s11_db', '?')} dB 如何调参改善"
            )
            rag_context = retrieve_antenna_rules(
                rag_query,
                self.client,
                design_signature=design_signature,
            )
        except Exception as exc:
            logger.warning("RAG retrieval failed, using keyword fallback: %s", exc)
            record_observability_degradation(
                self,
                component="optimizer_rag",
                fallback="keyword_rules",
                error=exc,
            )
            try:
                from cst_agent_workbench.rag.knowledge_base import _keyword_fallback
                rag_query = (
                    f"谐振频率 {baseline.get('min_freq_ghz', '?')} GHz "
                    f"目标 {effective_target_freq} GHz "
                    f"S11 {baseline.get('min_s11_db', '?')} dB 如何调参改善"
                )
                rag_context = _keyword_fallback(rag_query, design_signature=design_signature)
            except Exception as fallback_exc:
                logger.warning("optimizer keyword RAG fallback failed: %s", fallback_exc)
                record_observability_degradation(
                    self,
                    component="optimizer_rag_keyword_fallback",
                    fallback="empty_rag_context",
                    error=fallback_exc,
                )
                rag_context = []
        return rag_context

    def _recall_optimization_memories(
        self, params: dict, baseline: dict,
        effective_target_freq: float, target_db: float,
        rag_context: list,
    ) -> tuple:
        """Recall optimization memories. Returns (failure_reasons, recalled_memories, memory_guidance_info)."""
        failure_reasons = []
        recalled_memories = []
        memory_guidance_info = {"expected_param": "", "expected_delta_sign": 0, "constraints": []}
        rag_query = (
            f"谐振频率 {baseline.get('min_freq_ghz', '?')} GHz "
            f"目标 {effective_target_freq} GHz "
            f"S11 {baseline.get('min_s11_db', '?')} dB 如何调参改善"
        )
        design_signature = ""
        try:
            from cst_agent_workbench.agent.memory import project_scope_from_path, recall_memory
            from cst_agent_workbench.rag.knowledge_base import design_signature_from_request
            mem = getattr(getattr(self, 'session', None), 'memory', None)
            if mem is not None:
                session = getattr(self, "session", None)
                artifacts = getattr(session, "artifacts", None) if session is not None else None
                design_signature = design_signature_from_request(
                    getattr(artifacts, "last_patch_request", None)
                )
                recalled_memories = recall_memory(
                    mem, rag_query,
                    scopes=["project", "session"],
                    k=3, entry_types=["lesson", "failure"],
                    client=getattr(self, "client", None),
                    min_score=config.MEMORY_RECALL_MIN_SCORE,
                    min_overlap=1,
                    project_scope=project_scope_from_path(mem.workspace.project_path),
                    design_signature=design_signature,
                )
                failure_reasons = [entry.text for entry in recalled_memories if entry.entry_type == "failure"]
                failure_reasons.extend(
                    str(item) for item in getattr(mem.decisions, 'failure_reasons', [])[-3:]
                    if str(item) not in failure_reasons
                )
        except Exception as exc:
            logger.warning("optimizer memory recall failed: %s", exc)
            record_observability_degradation(
                self,
                component="optimizer_memory_recall",
                fallback="continue_without_recalled_memory",
                error=exc,
            )

        try:
            memory_guidance_info = _memory_guidance(
                recalled_memories, dict(params), baseline,
                effective_target_freq, target_db,
            )
        except Exception as exc:
            logger.warning("optimizer memory guidance failed: %s", exc)
            record_observability_degradation(
                self,
                component="optimizer_memory_guidance",
                fallback="empty_memory_constraints",
                error=exc,
            )

        session = getattr(self, 'session', None)
        metadata = getattr(session, "metadata", None) if session is not None else None
        if isinstance(metadata, dict):
            metadata["optimizer_memory_recall"] = [entry.to_dict() for entry in recalled_memories]
        return failure_reasons, recalled_memories, memory_guidance_info

    def _account_llm_usage(self, usage: dict) -> tuple:
        """Account LLM token usage. Returns (parse_error, raw_excerpt)."""
        parse_error = ""
        raw_excerpt = ""
        if usage:
            self.token_stats["prompt"] += usage.get("prompt", 0)
            self.token_stats["completion"] += usage.get("completion", 0)
            if usage.get("cached", 0):
                self.token_stats["cached"] = self.token_stats.get("cached", 0) + usage["cached"]
            if usage.get("cache_write", 0):
                self.token_stats["cache_write"] = self.token_stats.get("cache_write", 0) + usage["cache_write"]
            self.token_stats["calls"] += 1
            parse_error = usage.get("parse_error", "") or parse_error
            raw_excerpt = usage.get("raw_text_excerpt", "") or raw_excerpt
        return parse_error, raw_excerpt

    def _handle_memory_violation(self, llm_proposal: dict, history: list, result: _LLMDecisionResult) -> tuple:
        """Handle memory constraint violation. Returns (updated_proposal, enforced)."""
        from cst_agent_workbench.agent.analyzer import validate_llm_proposal
        memory_fallback = _memory_constraint_fallback(llm_proposal)
        fallback_valid, fallback_reason = validate_llm_proposal(memory_fallback, history) if memory_fallback else (False, "memory fallback unavailable")
        if fallback_valid:
            result.llm_failure_reason = llm_proposal.get("memory_violation_reason", "")
            result.execution_path = "memory_constraint_fallback"
            return memory_fallback, True
        result.llm_failure_reason = f"memory constraint fallback invalid: {fallback_reason}"
        result.execution_path = "heuristic_fallback"
        return None, False

    def _record_memory_impact_metadata(
        self, recalled_memories: list, memory_guidance_info: dict,
        proposal_before: dict, proposal_after: dict,
        memory_enforced: bool, parse_error: str, raw_excerpt: str,
        fallback_reason: str,
    ):
        """Record memory impact metadata on session for observability."""
        session = getattr(self, 'session', None)
        if session is None:
            return
        impact_source = proposal_before or proposal_after
        session.metadata["optimizer_memory_impact"] = {
            "used_memory": bool(recalled_memories),
            "recalled_memory_ids": [entry.id for entry in recalled_memories],
            "recalled_lessons": [entry.text for entry in recalled_memories],
            "memory_expected_param": memory_guidance_info.get("expected_param", ""),
            "memory_expected_delta_sign": memory_guidance_info.get("expected_delta_sign", 0),
            "memory_constraints": memory_guidance_info.get("constraints", []),
            "memory_rule_names": memory_guidance_info.get("rule_names", []),
            "memory_enforced_rule": memory_guidance_info.get("enforced_rule", ""),
            "memory_complied": bool((impact_source or {}).get("memory_complied", False)),
            "memory_violation": bool((impact_source or {}).get("memory_violation", False)),
            "memory_violation_reason": (impact_source or {}).get("memory_violation_reason", ""),
            "memory_enforced_by_validator": memory_enforced,
            "proposal_before_validation": dict(proposal_before or {}),
            "proposal_after_validation": dict(proposal_after or {}),
            "llm_parse_error": parse_error,
            "llm_raw_excerpt": raw_excerpt,
            "fallback_reason": fallback_reason,
        }

    def _try_algorithmic_fallback(
        self, proposal, preferred_algorithm: str,
        params: dict, _get_float, execution_path: str,
    ) -> tuple:
        """Try algorithmic optimization fallback. Returns (proposal, execution_path)."""
        try:
            from cst_agent_workbench.optimization.algorithms import suggest_next_params, auto_select_algorithm
            patch_l = _get_float("patch_L")
            param_bounds = {
                "patch_L": (patch_l * 0.7, patch_l * 1.3),
                "inset_depth": (0.5, patch_l * 0.45),
                "feed_W": (0.5, 5.0),
            }
            algo = preferred_algorithm if preferred_algorithm != "auto" else auto_select_algorithm(len(self.opt_state.history), len(param_bounds))
            # metric_value=0.0（S11=0dB，最差）是合法值，不能用 `or` 判 falsy 兜底；
            # 缺失 metric 的轮次对算法无信息量，直接跳过。
            algo_params = suggest_next_params(algo, [
                {"params": r.get("param_snapshot", {}), "metric": -float(r["metric_value"])}
                for r in self.opt_state.history
                if r.get("param_snapshot") and r.get("metric_value") is not None
            ], param_bounds)
            if algo_params:
                best_param = max(algo_params, key=lambda k: abs(algo_params[k] - float(params.get(k, 0))))
                delta = algo_params[best_param] - float(params[best_param])
                if abs(delta) > 0.05:
                    from cst_agent_workbench.optimization.models import OptimizationProposal, ParameterUpdate
                    proposal = OptimizationProposal(
                        handled=True,
                        updates=[ParameterUpdate(name=best_param, old=float(params[best_param]),
                                                 new=round(float(params[best_param]) + delta, 4))],
                        strategy=f"{algo} 算法建议",
                        reason=f"{algo} 算法基于 {len(self.opt_state.history)} 轮历史建议调整 {best_param}",
                    )
                    execution_path = f"{algo}_algorithm"
        except Exception as exc:
            logger.warning("algorithm fallback failed: %s", exc)
            record_observability_degradation(
                self,
                component="optimizer_algorithm_fallback",
                fallback="retain_current_proposal",
                error=exc,
            )
        return proposal, execution_path

    def run_programmatic_patch_optimization_round(
        self,
        target_mode: str = "at_f0",
        target_freq_ghz: float = 0.0,
        target_db: float = -10.0,
        preferred_algorithm: str = "auto",
    ) -> dict:
        from cst_agent_workbench.cst.primitives import get_parameters, store_parameter as gen_store_parameter, register_parameter

        user_goal = (
            "执行一轮程序化贴片优化："
            f"mode={target_mode}, target_freq_ghz={target_freq_ghz}, "
            f"target_db={target_db}, algorithm={preferred_algorithm}"
        )
        user_entry = {"role": "user", "content": user_goal}
        trace_event_start = len(getattr(self, "tool_events", []) or [])
        trace_owned = False
        active_trace = getattr(self, "current_trace", None)
        if not (isinstance(active_trace, dict) and active_trace.get("status") == "running"):
            try:
                trace_owned = bool(
                    start_optimizer_trace_run(
                        self,
                        user_input=user_entry,
                        working_messages=[
                            {"role": "system", "content": "[programmatic optimizer]"},
                            user_entry,
                        ],
                        pending_history=[user_entry],
                    )
                )
            except Exception as exc:
                logger.warning("direct optimizer trace start failed: %s", exc)
                record_observability_degradation(
                    self,
                    component="optimizer_trace_start",
                    fallback="continue_without_optimizer_trace",
                    error=exc,
                )

        def _record_optimizer_result(result: dict) -> dict:
            session = getattr(self, "session", None)
            artifacts = getattr(session, "artifacts", None) if session is not None else None
            if artifacts is not None:
                artifacts.last_optimizer_result = dict(result or {})

            if trace_owned:
                final_text = str((result or {}).get("message") or "")
                try:
                    append_trace_turn(
                        self,
                        assistant_content=final_text,
                        request_messages=[user_entry],
                        model="programmatic_optimizer",
                    )
                    new_events = list(getattr(self, "tool_events", []) or [])[trace_event_start:]
                    for index, event in enumerate(new_events, start=1):
                        tool_trace = start_tool_call_trace(
                            self,
                            tool_call_id=f"optimizer_{index}",
                            tool_name=str(event.get("tool_name") or "optimizer_event"),
                            phase=str(event.get("phase") or "optimization"),
                            arguments={},
                            source="programmatic_optimizer",
                        )
                        finish_tool_call_trace(
                            tool_trace,
                            success=bool(event.get("success")),
                            result={
                                "message": event.get("message", ""),
                                "description": event.get("description", ""),
                            },
                            error="" if event.get("success") else event.get("message", ""),
                        )
                except Exception as exc:
                    logger.warning("direct optimizer trace event append failed: %s", exc)
                    record_observability_degradation(
                        self,
                        component="optimizer_trace_events",
                        fallback="finish_trace_without_synthetic_tool_events",
                        error=exc,
                    )
                try:
                    finish_trace_run(
                        self,
                        status="completed" if bool((result or {}).get("success")) else "failed",
                        final_response=final_text,
                        error="" if (result or {}).get("success") else final_text,
                    )
                except Exception as exc:
                    logger.warning("direct optimizer trace finish failed: %s", exc)
                    record_observability_degradation(
                        self,
                        component="optimizer_trace_finish",
                        fallback="retain_optimizer_result_with_unfinished_trace",
                        error=exc,
                    )
            return result

        if not self.can_use_programmatic_patch_optimizer():
            return _record_optimizer_result(
                {"handled": False, "success": False, "message": "当前模型不适合程序化贴片优化"}
            )

        params = get_parameters()

        def _get_float(name: str) -> float:
            return float(params[name])

        design_f0 = _get_float("f0")
        effective_target_freq = target_freq_ghz if target_mode == "at_f0" and target_freq_ghz > 0 else design_f0

        baseline = self._collect_s11_summary(effective_target_freq)
        if not baseline.get("success"):
            self.last_chat_status = {
                "ok": False,
                "error": baseline.get("message", ""),
                "had_tool_failure": True,
                "mode": "programmatic_optimizer",
            }
            return _record_optimizer_result({
                "handled": True,
                "success": False,
                "message": f"程序化优化读取当前 S11 失败: {baseline.get('message', '')}",
            })

        strategy = self._build_programmatic_patch_strategy()
        target = OptimizationTarget(
            mode=target_mode,
            target_freq_ghz=effective_target_freq,
            target_db=target_db,
        )
        s11_summary = S11Summary.from_mapping(baseline)
        diagnosis = diagnose_s11(s11_summary, target)
        proposal = strategy.propose_next_step(
            OptimizationContext(
                target=target,
                parameters={
                    "f0": design_f0,
                    "patch_L": _get_float("patch_L"),
                    "inset_depth": _get_float("inset_depth"),
                    "feed_W": _get_float("feed_W"),
                    "feed_L": _get_float("feed_L") if "feed_L" in params else 0.0,
                    "inset_gap": _get_float("inset_gap") if "inset_gap" in params else 0.0,
                    "notch_W": _get_float("notch_W") if "notch_W" in params else 0.0,
                    "substrate_h": _get_float("substrate_h"),
                    "copper_t": _get_float("copper_t"),
                },
                s11_summary=s11_summary,
                history=list(self.opt_state.history),
            )
        )

        # LLM decision layer (extracted)
        llm_decision = self._run_llm_decision_layer(
            proposal, baseline, dict(params), effective_target_freq, target_db, preferred_algorithm,
        )
        proposal = llm_decision.proposal
        _llm_source = llm_decision.llm_source
        _llm_failure_reason = llm_decision.llm_failure_reason
        _execution_path = llm_decision.execution_path

        # Algorithmic fallback
        if llm_decision.llm_proposal is None and len(self.opt_state.history) >= 4 and (preferred_algorithm != "auto" or not proposal.handled or not proposal.has_updates):
            proposal, _execution_path = self._try_algorithmic_fallback(
                proposal, preferred_algorithm, dict(params), _get_float,
                _execution_path,
            )

        if not proposal.handled:
            self.last_chat_status = {
                "ok": False,
                "error": proposal.message,
                "had_tool_failure": True,
                "mode": "programmatic_optimizer",
            }
            return _record_optimizer_result({
                "handled": True,
                "success": False,
                "message": proposal.message,
                "strategy": proposal.strategy,
                "diagnosis": diagnosis.to_dict(),
            })

        if not proposal.has_updates:
            check = evaluate_optimization_target(
                baseline.get("raw") or {},
                mode=target_mode,
                target_db=target_db,
                effective_target_freq=effective_target_freq,
                configured_target_freq=target_freq_ghz,
            ).to_dict()
            self.last_chat_status = {
                "ok": True,
                "error": "",
                "had_tool_failure": False,
                "mode": "programmatic_optimizer",
            }
            return _record_optimizer_result({
                "handled": True,
                "success": True,
                "message": proposal.message,
                "results_raw": baseline.get("raw"),
                "target_freq_ghz": effective_target_freq,
                "changed_params": {},
                "strategy": proposal.strategy,
                "effective_target_freq_ghz": effective_target_freq,
                "proposal_reason": proposal.reason,
                "check": check,
                "diagnosis": diagnosis.to_dict(),
            })

        reason = proposal.reason
        if _llm_source != "[LLM]" and _llm_failure_reason:
            reason = f"{reason}（LLM fallback: {_llm_failure_reason}）" if reason else f"LLM fallback: {_llm_failure_reason}"
        self._record_runtime_event(
            "1_环境与材料",
            "programmatic_patch_proposal",
            True,
            "",
            f"程序化优化策略({proposal.strategy}): {reason or proposal.message or '未生成额外说明'}",
        )

        vba_lines = []
        changed_params = {}
        for update in proposal.updates:
            _, vba_line = gen_store_parameter(update.name, self._format_mm(update.new))
            vba_lines.append(vba_line)
            changed_params[update.name] = {
                "old": self._format_mm(update.old),
                "new": self._format_mm(update.new),
            }
        update_vba = "\n".join(vba_lines)
        update_result = self.cst.execute_vba(update_vba, label="programmatic_patch_opt_update", timeout=120)
        self._record_runtime_event(
            "1_环境与材料",
            "programmatic_patch_update",
            bool(update_result.get("success")),
            update_result.get("message", ""),
            f"程序化优化更新参数: {json.dumps(changed_params, ensure_ascii=False)}",
        )
        if not update_result.get("success"):
            self.last_chat_status = {
                "ok": False,
                "error": update_result.get("message", ""),
                "had_tool_failure": True,
                "mode": "programmatic_optimizer",
            }
            return _record_optimizer_result({
                "handled": True,
                "success": False,
                "message": f"程序化优化更新参数失败: {update_result.get('message', '')}",
                "strategy": "programmatic_patch",
                "diagnosis": diagnosis.to_dict(),
            })

        if update_result.get("executed", False):
            for name, meta in changed_params.items():
                register_parameter(name, meta["new"])

        solver_result = self.cst.run_solver(timeout=300)
        self._record_runtime_event(
            "4_运行仿真",
            "programmatic_patch_run_solver",
            bool(solver_result.get("success")),
            solver_result.get("message", ""),
            "程序化贴片优化: 单轮调参后求解",
        )
        if not solver_result.get("success"):
            self.last_chat_status = {
                "ok": False,
                "error": solver_result.get("message", ""),
                "had_tool_failure": True,
                "mode": "programmatic_optimizer",
            }
            return _record_optimizer_result({
                "handled": True,
                "success": False,
                "message": f"程序化优化求解失败: {solver_result.get('message', '')}",
                "strategy": "programmatic_patch",
                "diagnosis": diagnosis.to_dict(),
            })

        attempted_after = self._collect_s11_summary(effective_target_freq)
        after, rolled_back, rollback_failed, rollback_reason = self._attempt_rollback(
            proposal, baseline, attempted_after, effective_target_freq, target_mode,
            gen_store_parameter, register_parameter, diagnosis.to_dict(),
        )
        operation_success = bool(after.get("success")) and not rollback_failed
        if after.get("success") and after.get("raw"):
            self.last_results = after["raw"]

        self.last_chat_status = {
            "ok": operation_success,
            "error": "" if operation_success else (rollback_reason or after.get("message", "")),
            "had_tool_failure": not operation_success,
            "mode": "programmatic_optimizer",
        }

        improvement_text = ""
        if after.get("success"):
            before_metric = baseline["target_s11_db"] if target_mode == "at_f0" else baseline["min_s11_db"]
            after_metric = after["target_s11_db"] if target_mode == "at_f0" else after["min_s11_db"]
            improvement_text = f"优化前 {before_metric:.2f} dB，优化后 {after_metric:.2f} dB。"

        changed_text = ", ".join(f"{name}: {meta['old']} -> {meta['new']}" for name, meta in changed_params.items())
        message = (
            f"已执行一轮程序化贴片优化。{reason}\n"
            f"参数调整: {changed_text or '无'}\n"
            f"{improvement_text}"
            f"{(' ' + rollback_reason) if rollback_reason else ''}".strip()
        )
        # Reflection：总结本轮经验写入 memory
        before_metric = baseline["target_s11_db"] if target_mode == "at_f0" else baseline["min_s11_db"]
        attempted_after_metric = (
            attempted_after.get("target_s11_db") if target_mode == "at_f0" else attempted_after.get("min_s11_db")
        )
        measured_improved = (
            attempted_after_metric is not None
            and before_metric is not None
            and attempted_after_metric < before_metric - 1e-9
        )
        self._run_reflection(
            proposal,
            baseline,
            _execution_path if "_execution_path" in locals() else "unknown",
            effective_target_freq=effective_target_freq,
            after_metric=attempted_after_metric,
            improved=measured_improved and not rolled_back,
            rolled_back=rolled_back,
        )
        # 优化轮结束是低频节点：把本轮 reflection/strategy 写入的 memory 落盘一次
        if hasattr(self, "_refresh_session_memory_from_runtime"):
            self._refresh_session_memory_from_runtime(persist=True)

        return _record_optimizer_result({
            "handled": True,
            "success": operation_success,
            "message": message,
            "results_raw": after.get("raw") if after.get("success") else baseline.get("raw"),
            "target_freq_ghz": effective_target_freq,
            "changed_params": changed_params,
            "strategy": proposal.strategy,
            "effective_target_freq_ghz": effective_target_freq,
            "proposal_reason": proposal.reason,
            "diagnosis": diagnosis.to_dict(),
            "attempted_check": {
                **evaluate_optimization_target(
                    attempted_after.get("raw") or {},
                    mode=target_mode,
                    target_db=target_db,
                    effective_target_freq=effective_target_freq,
                    configured_target_freq=target_freq_ghz,
                ).to_dict(),
                "resonances": list(attempted_after.get("resonances") or []),
            } if attempted_after.get("success") else {},
            "rolled_back": rolled_back,
            "rollback_failed": rollback_failed,
            "rollback_reason": rollback_reason,
        })
