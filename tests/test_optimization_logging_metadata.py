from types import SimpleNamespace

from cst_agent_workbench.agent.memory import MemoryManager, StructuredMemory
from cst_agent_workbench.optimization.optimizer import PatchOptimizerMixin
from cst_agent_workbench.optimization.state import OptimizationState


class _FakeCST:
    def __init__(self):
        self.project_path = "D:/demo/project.cst"
        self.executed_vba = []

    def is_connected(self):
        return True

    def execute_vba(self, vba_code, label=None, timeout=None):
        self.executed_vba.append((label, vba_code, timeout))
        return {"success": True, "message": "ok", "executed": True}

    def run_solver(self, timeout=300):
        return {"success": True, "message": "solver ok"}


class _FakeResults:
    def open(self, project_path):
        return {"success": True, "message": "opened"}

    def get_s_parameter(self, port_i, port_j):
        return {
            "success": True,
            "item": "S1,1",
            "plot_data": [{"freq": 9.4, "s_db": -12.0}, {"freq": 9.8, "s_db": -8.0}],
        }


class _FakeOptimizer(PatchOptimizerMixin):
    def __init__(self):
        self.cst = _FakeCST()
        self.results = _FakeResults()
        self.last_chat_status = {}
        self.last_results = {}
        self.opt_state = OptimizationState()
        self.tool_events = []
        self.client = object()
        self.model = "fake-model"
        self.token_stats = {"prompt": 0, "completion": 0, "calls": 0}
        self.session = SimpleNamespace(memory=StructuredMemory(), metadata={})

    def _collect_s11_summary(self, target_freq_ghz: float):
        return {
            "success": True,
            "min_freq_ghz": 9.8,
            "min_s11_db": -16.0,
            "target_s11_db": -8.0,
            "raw": {"plot_data": [{"freq": 9.4, "s_db": -8.0}, {"freq": 9.8, "s_db": -16.0}]},
        }

    def _format_mm(self, value: float) -> str:
        return f"{value:.4f}".rstrip("0").rstrip(".")

    def _record_runtime_event(self, phase, tool_name, success, message, description):
        self.tool_events.append(
            {
                "phase": phase,
                "tool_name": tool_name,
                "success": success,
                "message": message,
                "description": description,
            }
        )

    def _build_programmatic_patch_strategy(self):
        class _FakeStrategy:
            name = "heuristic"

            def propose_next_step(self, context):
                from cst_agent_workbench.optimization.models import OptimizationProposal, ParameterUpdate

                return OptimizationProposal(
                    strategy="heuristic",
                    reason="优先增大 patch_L 做频率校正。",
                    updates=[ParameterUpdate(name="patch_L", old=10.0, new=10.4)],
                )

        return _FakeStrategy()


def _patch_optimizer_io(monkeypatch, feed_w="1.0"):
    monkeypatch.setattr(
        "cst_agent_workbench.cst.primitives.get_parameters",
        lambda: {
            "f0": "9.4",
            "patch_L": "10.0",
            "feed_W": feed_w,
            "inset_depth": "2.0",
            "substrate_h": "0.51",
            "copper_t": "0.035",
        },
    )
    monkeypatch.setattr(
        "cst_agent_workbench.cst.primitives.store_parameter",
        lambda name, value: (None, f"Store {name}={value}"),
    )
    monkeypatch.setattr("cst_agent_workbench.cst.primitives.register_parameter", lambda name, value: None)


def _seed_feed_w_memory(optimizer):
    MemoryManager.update_decisions(
        optimizer.session.memory,
        strategy_entry={
            "round": 1,
            "lesson": "S11 匹配恶化时优先增大 feed_W",
            "failure_pattern": "feed_W 过小会导致 S11 继续恶化",
            "effective_action": "增大 feed_W 可改善馈线阻抗匹配",
            "avoid_next": "避免在 feed_W 过小时只调 inset_depth",
            "reuse_condition": "S11 未达标且 feed_W 偏小时",
        },
    )
    MemoryManager.update_decisions(optimizer.session.memory, failure_reason="S11 历史失败：feed_W 过小导致匹配恶化")


def _seed_low_frequency_memory(optimizer):
    MemoryManager.update_decisions(
        optimizer.session.memory,
        strategy_entry={
            "round": 1,
            "lesson": "谐振频率偏低时优先减小 patch_L 抬高频率",
            "failure_pattern": "谐振偏低时增大 patch_L 会让频率继续降低",
            "effective_action": "减小 patch_L 可抬高谐振频率",
            "avoid_next": "避免在谐振偏低时增大 patch_L",
            "reuse_condition": "min_freq 低于目标频率且 S11 未达标",
        },
    )
    MemoryManager.update_decisions(optimizer.session.memory, failure_reason="S11 历史失败：谐振偏低时 patch_L 方向错误")


def test_programmatic_optimizer_records_proposal_event(monkeypatch):
    optimizer = _FakeOptimizer()
    monkeypatch.setattr(
        "cst_agent_workbench.cst.primitives.get_parameters",
        lambda: {
            "f0": "9.4",
            "patch_L": "10.0",
            "feed_W": "1.0",
            "inset_depth": "2.0",
            "substrate_h": "0.51",
            "copper_t": "0.035",
        },
    )
    monkeypatch.setattr(
        "cst_agent_workbench.cst.primitives.store_parameter",
        lambda name, value: (None, f"Store {name}={value}"),
    )
    monkeypatch.setattr("cst_agent_workbench.cst.primitives.register_parameter", lambda name, value: None)

    result = optimizer.run_programmatic_patch_optimization_round(target_mode="at_f0", target_freq_ghz=9.4, target_db=-10.0)

    assert result["success"] is True
    proposal_events = [event for event in optimizer.tool_events if event["tool_name"] == "programmatic_patch_proposal"]
    assert proposal_events
    assert proposal_events[-1]["description"] == "程序化优化策略(heuristic): 优先增大 patch_L 做频率校正。（LLM fallback: LLM 未返回可解析 proposal）"


def test_direct_programmatic_optimizer_records_unified_trace(monkeypatch):
    optimizer = _FakeOptimizer()
    _patch_optimizer_io(monkeypatch)

    result = optimizer.run_programmatic_patch_optimization_round(
        target_mode="at_f0",
        target_freq_ghz=9.4,
        target_db=-10.0,
    )

    assert result["success"] is True
    assert len(optimizer.trace_history) == 1
    trace = optimizer.trace_history[0]
    assert trace["status"] == "completed"
    assert trace["turns"]
    tool_names = [item["tool_name"] for item in trace["turns"][0]["tool_calls"]]
    assert "programmatic_patch_proposal" in tool_names
    assert "programmatic_patch_run_solver" in tool_names


def test_programmatic_optimizer_reflection_uses_parameter_update_fields(monkeypatch):
    optimizer = _FakeOptimizer()
    captured = {}
    _patch_optimizer_io(monkeypatch)

    def fake_reflect(client, model, round_summary, memory, **kwargs):
        captured["client"] = client
        captured["model"] = model
        captured["round_summary"] = round_summary
        captured["memory"] = memory
        captured["kwargs"] = kwargs

    monkeypatch.setattr("cst_agent_workbench.agent.reflection.collect_recent_errors", lambda agent: [])
    monkeypatch.setattr("cst_agent_workbench.agent.reflection.reflect_on_round", fake_reflect)

    result = optimizer.run_programmatic_patch_optimization_round(target_mode="at_f0", target_freq_ghz=9.4, target_db=-10.0)

    assert result["success"] is True
    assert captured["client"] is optimizer.client
    assert captured["model"] == "fake-model"
    assert captured["memory"] is optimizer.session.memory
    assert "min_confidence" in captured["kwargs"]
    assert captured["round_summary"]["param"] == "patch_L"
    assert round(captured["round_summary"]["delta"], 4) == 0.4
    assert captured["round_summary"]["execution_path"] == "heuristic_fallback"
    assert captured["round_summary"]["after_metric"] == -8.0
    assert captured["round_summary"]["improved"] is False
    assert captured["round_summary"]["rolled_back"] is False


def test_programmatic_optimizer_rolls_back_when_frequency_correction_worsens_resonance(monkeypatch):
    optimizer = _FakeOptimizer()
    summaries = [
        {
            "success": True,
            "min_freq_ghz": 9.8,
            "min_s11_db": -16.0,
            "target_s11_db": -8.0,
            "raw": {"plot_data": [{"freq": 9.4, "s_db": -8.0}]},
        },
        {
            "success": True,
            "min_freq_ghz": 9.9,
            "min_s11_db": -12.0,
            "target_s11_db": -5.0,
            "raw": {"plot_data": [{"freq": 9.4, "s_db": -5.0}]},
        },
        {
            "success": True,
            "min_freq_ghz": 9.8,
            "min_s11_db": -16.0,
            "target_s11_db": -8.0,
            "raw": {"plot_data": [{"freq": 9.4, "s_db": -8.0}]},
        },
    ]
    calls = {"index": 0}
    _patch_optimizer_io(monkeypatch)

    def fake_summary(target_freq_ghz: float):
        value = summaries[calls["index"]]
        calls["index"] += 1
        return value

    monkeypatch.setattr(optimizer, "_collect_s11_summary", fake_summary)

    result = optimizer.run_programmatic_patch_optimization_round(target_mode="at_f0", target_freq_ghz=9.4, target_db=-10.0)

    assert result["success"] is True
    assert result["rolled_back"] is True
    assert "谐振偏差" in result["rollback_reason"]
    assert result["results_raw"] == summaries[2]["raw"]
    assert [event["tool_name"] for event in optimizer.tool_events if "rollback" in event["tool_name"]] == [
        "programmatic_patch_rollback",
        "programmatic_patch_rollback_run_solver",
    ]


def test_programmatic_optimizer_keeps_frequency_correction_when_resonance_moves_toward_target(monkeypatch):
    optimizer = _FakeOptimizer()
    summaries = [
        {
            "success": True,
            "min_freq_ghz": 9.8,
            "min_s11_db": -16.0,
            "target_s11_db": -8.0,
            "raw": {"plot_data": [{"freq": 9.4, "s_db": -8.0}]},
        },
        {
            "success": True,
            "min_freq_ghz": 9.55,
            "min_s11_db": -11.0,
            "target_s11_db": -5.0,
            "raw": {"plot_data": [{"freq": 9.4, "s_db": -5.0}]},
        },
    ]
    calls = {"index": 0}
    _patch_optimizer_io(monkeypatch)

    def fake_summary(target_freq_ghz: float):
        value = summaries[calls["index"]]
        calls["index"] += 1
        return value

    monkeypatch.setattr(optimizer, "_collect_s11_summary", fake_summary)

    result = optimizer.run_programmatic_patch_optimization_round(target_mode="at_f0", target_freq_ghz=9.4, target_db=-10.0)

    assert result["success"] is True
    assert result["rolled_back"] is False
    assert result["results_raw"] == summaries[1]["raw"]
    assert not [event for event in optimizer.tool_events if "rollback" in event["tool_name"]]


def test_programmatic_optimizer_rolls_back_using_nearest_resonance_not_global_min(monkeypatch):
    optimizer = _FakeOptimizer()
    summaries = [
        {
            "success": True,
            "min_freq_ghz": 9.8,
            "min_s11_db": -16.0,
            "target_s11_db": -8.0,
            "resonances": [
                {"freq_ghz": 9.8, "depth_db": -16.0},
            ],
            "raw": {"plot_data": [{"freq": 9.4, "s_db": -8.0}]},
        },
        {
            "success": True,
            "min_freq_ghz": 11.0,
            "min_s11_db": -18.0,
            "target_s11_db": -5.0,
            "resonances": [
                {"freq_ghz": 9.9, "depth_db": -5.5},
                {"freq_ghz": 11.0, "depth_db": -18.0},
            ],
            "raw": {"plot_data": [{"freq": 9.4, "s_db": -5.0}]},
        },
        {
            "success": True,
            "min_freq_ghz": 9.8,
            "min_s11_db": -16.0,
            "target_s11_db": -8.0,
            "resonances": [
                {"freq_ghz": 9.8, "depth_db": -16.0},
            ],
            "raw": {"plot_data": [{"freq": 9.4, "s_db": -8.0}]},
        },
    ]
    calls = {"index": 0}
    _patch_optimizer_io(monkeypatch)

    def fake_summary(target_freq_ghz: float):
        value = summaries[calls["index"]]
        calls["index"] += 1
        return value

    monkeypatch.setattr(optimizer, "_collect_s11_summary", fake_summary)

    result = optimizer.run_programmatic_patch_optimization_round(target_mode="at_f0", target_freq_ghz=9.4, target_db=-10.0)

    assert result["success"] is True
    assert result["rolled_back"] is True
    assert "0.400 GHz 退化到 0.500 GHz" in result["rollback_reason"]
    assert result["results_raw"] == summaries[2]["raw"]


def test_programmatic_optimizer_preserves_previous_result_when_rollback_solver_fails(monkeypatch):
    optimizer = _FakeOptimizer()
    summaries = [
        {
            "success": True,
            "min_freq_ghz": 9.8,
            "min_s11_db": -16.0,
            "target_s11_db": -8.0,
            "raw": {"plot_data": [{"freq": 9.4, "s_db": -8.0}]},
        },
        {
            "success": True,
            "min_freq_ghz": 9.9,
            "min_s11_db": -12.0,
            "target_s11_db": -5.0,
            "raw": {"plot_data": [{"freq": 9.4, "s_db": -5.0}]},
        },
    ]
    calls = {"index": 0}
    solver_calls = {"count": 0}
    _patch_optimizer_io(monkeypatch)

    def fake_summary(target_freq_ghz: float):
        value = summaries[calls["index"]]
        calls["index"] += 1
        return value

    def fake_run_solver(timeout=300):
        solver_calls["count"] += 1
        if solver_calls["count"] == 1:
            return {"success": True, "message": "solver ok"}
        return {"success": False, "message": "rollback solver failed"}

    monkeypatch.setattr(optimizer, "_collect_s11_summary", fake_summary)
    monkeypatch.setattr(optimizer.cst, "run_solver", fake_run_solver)

    result = optimizer.run_programmatic_patch_optimization_round(target_mode="at_f0", target_freq_ghz=9.4, target_db=-10.0)

    assert result["success"] is False
    assert result["rolled_back"] is True
    assert result["rollback_failed"] is True
    assert "回滚后重新求解失败" in result["rollback_reason"]
    assert result["results_raw"] == summaries[0]["raw"]
    assert optimizer.last_results == summaries[0]["raw"]


def test_programmatic_optimizer_records_llm_parse_failure_metadata(monkeypatch):
    optimizer = _FakeOptimizer()
    _patch_optimizer_io(monkeypatch)

    def fake_propose(*args, **kwargs):
        return (None, {"prompt": 5, "completion": 3, "parse_error": "no_parseable_optimization_proposal", "raw_text_excerpt": "建议调 inset_depth，但未输出 JSON"})

    monkeypatch.setattr("cst_agent_workbench.agent.analyzer.propose_patch_optimization_with_llm", fake_propose)

    result = optimizer.run_programmatic_patch_optimization_round(target_mode="at_f0", target_freq_ghz=9.4, target_db=-10.0)

    assert result["success"] is True
    impact = optimizer.session.metadata["optimizer_memory_impact"]
    assert impact["llm_parse_error"] == "no_parseable_optimization_proposal"
    assert impact["llm_raw_excerpt"] == "建议调 inset_depth，但未输出 JSON"



def test_programmatic_optimizer_uses_llm_proposal_when_available(monkeypatch):
    optimizer = _FakeOptimizer()
    monkeypatch.setattr(
        "cst_agent_workbench.cst.primitives.get_parameters",
        lambda: {
            "f0": "9.4",
            "patch_L": "10.0",
            "feed_W": "1.0",
            "inset_depth": "2.0",
            "substrate_h": "0.51",
            "copper_t": "0.035",
        },
    )
    monkeypatch.setattr(
        "cst_agent_workbench.cst.primitives.store_parameter",
        lambda name, value: (None, f"Store {name}={value}"),
    )
    monkeypatch.setattr("cst_agent_workbench.cst.primitives.register_parameter", lambda name, value: None)
    monkeypatch.setattr(
        "cst_agent_workbench.agent.analyzer.propose_patch_optimization_with_llm",
        lambda *args, **kwargs: ({"param": "patch_L", "delta_mm": -0.6, "reason": "当前谐振偏低，减小 patch_L 抬高频率。"}, {"prompt": 11, "completion": 7}),
    )

    result = optimizer.run_programmatic_patch_optimization_round(target_mode="at_f0", target_freq_ghz=9.4, target_db=-10.0)

    assert result["success"] is True
    assert result["strategy"] == "llm"
    assert result["proposal_reason"] == "[LLM] 当前谐振偏低，减小 patch_L 抬高频率。"
    assert result["changed_params"]["patch_L"]["new"] == "9.4"
    assert optimizer.token_stats == {"prompt": 11, "completion": 7, "calls": 1}
    proposal_events = [event for event in optimizer.tool_events if event["tool_name"] == "programmatic_patch_proposal"]
    assert proposal_events[-1]["description"] == "程序化优化策略(llm): [LLM] 当前谐振偏低，减小 patch_L 抬高频率。"


def test_programmatic_optimizer_records_memory_impact_metadata(monkeypatch):
    optimizer = _FakeOptimizer()
    _seed_feed_w_memory(optimizer)
    captured_kwargs = {}
    _patch_optimizer_io(monkeypatch)

    def fake_propose(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return ({"param": "patch_L", "delta_mm": -0.6, "reason": "根据 memory 避免重复失败方向。"}, {"prompt": 5, "completion": 3})

    monkeypatch.setattr("cst_agent_workbench.agent.analyzer.propose_patch_optimization_with_llm", fake_propose)

    result = optimizer.run_programmatic_patch_optimization_round(target_mode="at_f0", target_freq_ghz=9.4, target_db=-10.0)

    assert result["success"] is True
    assert result["changed_params"]["feed_W"] == {"old": "1", "new": "1.35"}
    assert captured_kwargs["failure_reasons"]
    assert any("feed_W 过小" in reason for reason in captured_kwargs["failure_reasons"])
    assert captured_kwargs["memory_constraints"]
    assert any("feed_W" in constraint for constraint in captured_kwargs["memory_constraints"])
    recall = optimizer.session.metadata["optimizer_memory_recall"]
    impact = optimizer.session.metadata["optimizer_memory_impact"]
    assert recall
    assert impact["used_memory"] is True
    assert impact["recalled_memory_ids"] == [entry["id"] for entry in recall]
    assert any("S11" in lesson for lesson in impact["recalled_lessons"])
    assert impact["memory_expected_param"] == "feed_W"
    assert impact["memory_expected_delta_sign"] == 1
    assert impact["memory_constraints"]
    assert "feed_width_too_small" in impact["memory_rule_names"]
    assert impact["memory_enforced_rule"] == "feed_width_too_small"
    assert impact["memory_complied"] is False
    assert impact["memory_violation"] is True
    assert "memory expected feed_W" in impact["memory_violation_reason"]
    assert impact["memory_enforced_by_validator"] is True
    assert impact["proposal_before_validation"]["param"] == "patch_L"
    assert impact["proposal_after_validation"]["param"] == "feed_W"
    assert impact["proposal_after_validation"]["delta_mm"] == 0.35
    assert impact["fallback_reason"]


def test_no_duplicate_lesson_across_channels(monkeypatch):
    optimizer = _FakeOptimizer()
    duplicate_lesson = "S11 \u5339\u914d\u6076\u5316\u65f6\u4f18\u5148\u589e\u5927 feed_W"
    MemoryManager.update_decisions(
        optimizer.session.memory,
        strategy_entry={
            "round": 1,
            "lesson": duplicate_lesson,
            "failure_pattern": "feed_W \u8fc7\u5c0f\u4f1a\u5bfc\u81f4 S11 \u7ee7\u7eed\u6076\u5316",
            "effective_action": "\u589e\u5927 feed_W \u53ef\u6539\u5584\u9988\u7ebf\u963b\u6297\u5339\u914d",
        },
    )
    captured_kwargs = {}
    _patch_optimizer_io(monkeypatch, feed_w="1.0")

    def boom_embed(*args, **kwargs):
        raise RuntimeError("embedding unavailable in this unit test")

    def fake_retrieve(*args, **kwargs):
        return [duplicate_lesson, "\u8d34\u7247\u5bbd\u5ea6 patch_W \u4e3b\u8981\u5f71\u54cd\u589e\u76ca\uff0c\u5bf9\u8c10\u632f\u9891\u7387\u5f71\u54cd\u8f83\u5c0f\u3002"]

    def fake_propose(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return ({"param": "feed_W", "delta_mm": 0.35, "reason": "follow memory"}, {"prompt": 5, "completion": 3})

    monkeypatch.setattr("cst_agent_workbench.rag.knowledge_base.embed_texts", boom_embed)
    monkeypatch.setattr("cst_agent_workbench.rag.knowledge_base.retrieve_antenna_rules", fake_retrieve)
    monkeypatch.setattr("cst_agent_workbench.agent.analyzer.propose_patch_optimization_with_llm", fake_propose)

    result = optimizer.run_programmatic_patch_optimization_round(target_mode="at_f0", target_freq_ghz=9.4, target_db=-10.0)

    assert result["success"] is True
    assert duplicate_lesson in captured_kwargs["memory_lessons"]
    assert duplicate_lesson not in captured_kwargs["rag_context"]
    assert captured_kwargs["rag_context"] == ["\u8d34\u7247\u5bbd\u5ea6 patch_W \u4e3b\u8981\u5f71\u54cd\u589e\u76ca\uff0c\u5bf9\u8c10\u632f\u9891\u7387\u5f71\u54cd\u8f83\u5c0f\u3002"]


def test_programmatic_optimizer_accepts_memory_compliant_llm_proposal(monkeypatch):
    optimizer = _FakeOptimizer()
    _seed_feed_w_memory(optimizer)
    captured_kwargs = {}
    _patch_optimizer_io(monkeypatch)

    def fake_propose(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return ({"param": "feed_W", "delta_mm": 0.4, "reason": "根据记忆增大 feed_W 改善匹配。"}, {"prompt": 5, "completion": 3})

    monkeypatch.setattr("cst_agent_workbench.agent.analyzer.propose_patch_optimization_with_llm", fake_propose)

    result = optimizer.run_programmatic_patch_optimization_round(target_mode="at_f0", target_freq_ghz=9.4, target_db=-10.0)

    assert result["success"] is True
    assert result["strategy"] == "llm"
    assert result["changed_params"]["feed_W"] == {"old": "1", "new": "1.4"}
    assert captured_kwargs["memory_constraints"]
    impact = optimizer.session.metadata["optimizer_memory_impact"]
    assert impact["memory_expected_param"] == "feed_W"
    assert impact["memory_expected_delta_sign"] == 1
    assert impact["memory_enforced_rule"] == "feed_width_too_small"
    assert impact["memory_complied"] is True
    assert impact["memory_violation"] is False
    assert impact["memory_violation_reason"] == ""
    assert impact["memory_enforced_by_validator"] is False
    assert impact["proposal_before_validation"]["param"] == "feed_W"
    assert impact["proposal_after_validation"]["param"] == "feed_W"


def test_programmatic_optimizer_passes_memory_constraints_to_retry(monkeypatch):
    optimizer = _FakeOptimizer()
    _seed_feed_w_memory(optimizer)
    calls = []
    _patch_optimizer_io(monkeypatch)

    def fake_propose(*args, **kwargs):
        calls.append(dict(kwargs))
        if len(calls) == 1:
            return ({"param": "unknown_param", "delta_mm": 0.3, "reason": "bad"}, {"prompt": 5, "completion": 3})
        return ({"param": "feed_W", "delta_mm": 0.35, "reason": "retry follows memory"}, {"prompt": 7, "completion": 4})

    monkeypatch.setattr("cst_agent_workbench.agent.analyzer.propose_patch_optimization_with_llm", fake_propose)

    result = optimizer.run_programmatic_patch_optimization_round(target_mode="at_f0", target_freq_ghz=9.4, target_db=-10.0)

    assert result["success"] is True
    assert result["changed_params"]["feed_W"]["new"] == "1.35"
    assert len(calls) == 2
    assert calls[0]["memory_constraints"]
    assert calls[1]["memory_constraints"]
    assert calls[1]["reject_reason"]
    impact = optimizer.session.metadata["optimizer_memory_impact"]
    assert impact["proposal_after_validation"]["param"] == "feed_W"
    assert impact["memory_enforced_by_validator"] is False
    assert impact["memory_violation"] is False


def test_infer_param_direction_uses_local_metric_improvement():
    history = [
        {"round": 1, "param_snapshot": {"patch_L": "9.9918"}, "metric_value": -0.86, "improved": False},
        {"round": 2, "param_snapshot": {"patch_L": "9.5921"}, "metric_value": -4.98, "improved": False},
    ]

    direction = PatchOptimizerMixin._infer_param_direction_from_history(history, "patch_L", default_sign=1)

    assert direction == -1


def test_infer_param_direction_reverses_when_local_metric_worsens():
    history = [
        {"round": 1, "param_snapshot": {"patch_L": "9.7008"}, "metric_value": -6.37, "improved": False},
        {"round": 2, "param_snapshot": {"patch_L": "9.9918"}, "metric_value": -0.86, "improved": False},
    ]

    direction = PatchOptimizerMixin._infer_param_direction_from_history(history, "patch_L", default_sign=1)

    assert direction == -1


def test_infer_param_direction_avoids_recent_rolled_back_attempt():
    history = [
        {"round": 1, "param_snapshot": {"inset_depth": "3.7649"}, "metric_value": -6.83},
        {
            "round": 2,
            "param_snapshot": {"inset_depth": "3.7649"},
            "metric_value": -6.83,
            "rolled_back": True,
            "attempted_changed_params": {"inset_depth": {"old": "3.7649", "new": "3.9908"}},
        },
    ]

    direction = PatchOptimizerMixin._infer_param_direction_from_history(history, "inset_depth", default_sign=1)

    assert direction == -1


def test_programmatic_optimizer_algorithm_fallback_accepts_string_param_history(monkeypatch):
    optimizer = _FakeOptimizer()
    optimizer.client = None
    optimizer.opt_state.history = [
        {
            "round": 0,
            "param_snapshot": {"patch_L": "10.0", "inset_depth": "2.0", "feed_W": "1.0"},
            "metric_value": -8.0,
        },
        {
            "round": 1,
            "param_snapshot": {"patch_L": "10.4", "inset_depth": "2.0", "feed_W": "1.0"},
            "metric_value": -7.5,
        },
        {
            "round": 2,
            "param_snapshot": {"patch_L": "10.1", "inset_depth": "2.1", "feed_W": "1.0"},
            "metric_value": -8.5,
        },
        {
            "round": 3,
            "param_snapshot": {"patch_L": "9.8", "inset_depth": "2.1", "feed_W": "1.1"},
            "metric_value": -9.0,
        },
    ]
    captured = {}
    _patch_optimizer_io(monkeypatch)

    def fake_suggest_next_params(algorithm, history, param_bounds):
        captured["algorithm"] = algorithm
        captured["history"] = history
        captured["param_bounds"] = param_bounds
        return {"patch_L": 9.4, "inset_depth": 2.0, "feed_W": 1.0}

    monkeypatch.setattr("cst_agent_workbench.optimization.algorithms.auto_select_algorithm", lambda history_len, n_params: "bayesian")
    monkeypatch.setattr("cst_agent_workbench.optimization.algorithms.suggest_next_params", fake_suggest_next_params)

    result = optimizer.run_programmatic_patch_optimization_round(target_mode="at_f0", target_freq_ghz=9.4, target_db=-10.0, preferred_algorithm="bayesian")

    assert result["success"] is True
    assert result["strategy"] == "bayesian 算法建议"
    assert result["changed_params"]["patch_L"] == {"old": "10", "new": "9.4"}
    assert captured["algorithm"] == "bayesian"
    assert captured["param_bounds"]["patch_L"] == (7.0, 13.0)
    assert captured["param_bounds"]["inset_depth"] == (0.5, 4.5)
    assert captured["history"][0]["params"]["patch_L"] == "10.0"


def test_programmatic_optimizer_keeps_heuristic_when_algorithm_not_explicit(monkeypatch):
    optimizer = _FakeOptimizer()
    optimizer.client = None
    optimizer.opt_state.history = [
        {"round": 0, "param_snapshot": {"patch_L": "10.0", "inset_depth": "2.0", "feed_W": "1.0"}, "metric_value": -8.0},
        {"round": 1, "param_snapshot": {"patch_L": "10.4", "inset_depth": "2.0", "feed_W": "1.0"}, "metric_value": -7.5},
        {"round": 2, "param_snapshot": {"patch_L": "10.1", "inset_depth": "2.1", "feed_W": "1.0"}, "metric_value": -8.5},
        {"round": 3, "param_snapshot": {"patch_L": "9.8", "inset_depth": "2.1", "feed_W": "1.1"}, "metric_value": -9.0},
    ]
    _patch_optimizer_io(monkeypatch)
    monkeypatch.setattr(
        "cst_agent_workbench.optimization.algorithms.suggest_next_params",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("algorithm should not override heuristic in auto mode")),
    )

    result = optimizer.run_programmatic_patch_optimization_round(target_mode="at_f0", target_freq_ghz=9.4, target_db=-10.0)

    assert result["success"] is True
    assert result["strategy"] == "heuristic"



def test_programmatic_optimizer_defers_algorithm_fallback_with_sparse_history(monkeypatch):
    optimizer = _FakeOptimizer()
    optimizer.client = None
    optimizer.opt_state.history = [
        {
            "round": 0,
            "param_snapshot": {"patch_L": "10.0", "inset_depth": "2.0", "feed_W": "1.0"},
            "metric_value": -8.0,
        },
        {
            "round": 1,
            "param_snapshot": {"patch_L": "10.4", "inset_depth": "2.0", "feed_W": "1.0"},
            "metric_value": -7.5,
        },
    ]
    _patch_optimizer_io(monkeypatch)
    monkeypatch.setattr(
        "cst_agent_workbench.optimization.algorithms.suggest_next_params",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("algorithm should not run")),
    )

    result = optimizer.run_programmatic_patch_optimization_round(target_mode="at_f0", target_freq_ghz=9.4, target_db=-10.0)

    assert result["success"] is True
    assert result["strategy"] == "heuristic"


def test_programmatic_optimizer_without_memory_constraints_keeps_llm_proposal(monkeypatch):
    optimizer = _FakeOptimizer()
    captured_kwargs = {}
    _patch_optimizer_io(monkeypatch)

    def fake_propose(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return ({"param": "patch_L", "delta_mm": -0.6, "reason": "当前谐振偏低，减小 patch_L 抬高频率。"}, {"prompt": 11, "completion": 7})

    monkeypatch.setattr("cst_agent_workbench.agent.analyzer.propose_patch_optimization_with_llm", fake_propose)

    result = optimizer.run_programmatic_patch_optimization_round(target_mode="at_f0", target_freq_ghz=9.4, target_db=-10.0)

    assert result["success"] is True
    assert result["changed_params"]["patch_L"]["new"] == "9.4"
    assert captured_kwargs["memory_constraints"] == []
    impact = optimizer.session.metadata["optimizer_memory_impact"]
    assert impact["used_memory"] is False
    assert impact["memory_expected_param"] == ""
    assert impact["memory_expected_delta_sign"] == 0
    assert impact["memory_constraints"] == []
    assert impact["memory_rule_names"] == []
    assert impact["memory_enforced_rule"] == ""
    assert impact["memory_enforced_by_validator"] is False


def test_programmatic_optimizer_enforces_low_frequency_patch_l_memory(monkeypatch):
    optimizer = _FakeOptimizer()
    _seed_low_frequency_memory(optimizer)
    captured_kwargs = {}
    _patch_optimizer_io(monkeypatch)

    def low_frequency_summary(target_freq_ghz: float):
        return {
            "success": True,
            "min_freq_ghz": 9.0,
            "min_s11_db": -16.0,
            "target_s11_db": -8.0,
            "raw": {"plot_data": [{"freq": 9.0, "s_db": -16.0}, {"freq": 9.4, "s_db": -8.0}]},
        }

    def fake_propose(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return ({"param": "feed_W", "delta_mm": 0.3, "reason": "误判为匹配问题。"}, {"prompt": 5, "completion": 3})

    monkeypatch.setattr(optimizer, "_collect_s11_summary", low_frequency_summary)
    monkeypatch.setattr("cst_agent_workbench.agent.analyzer.propose_patch_optimization_with_llm", fake_propose)

    result = optimizer.run_programmatic_patch_optimization_round(target_mode="at_f0", target_freq_ghz=9.4, target_db=-10.0)

    assert result["success"] is True
    assert result["changed_params"]["patch_L"] == {"old": "10", "new": "9.65"}
    assert any("减小 patch_L" in constraint for constraint in captured_kwargs["memory_constraints"])
    impact = optimizer.session.metadata["optimizer_memory_impact"]
    assert impact["memory_expected_param"] == "patch_L"
    assert impact["memory_expected_delta_sign"] == -1
    assert "resonance_too_low_patch_length" in impact["memory_rule_names"]
    assert impact["memory_enforced_rule"] == "resonance_too_low_patch_length"
    assert impact["memory_violation"] is True
    assert impact["memory_enforced_by_validator"] is True
    assert impact["proposal_before_validation"]["param"] == "feed_W"
    assert impact["proposal_after_validation"]["param"] == "patch_L"
    assert impact["proposal_after_validation"]["delta_mm"] == -0.35
