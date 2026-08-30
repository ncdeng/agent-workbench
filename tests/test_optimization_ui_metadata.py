from cst_agent_workbench.agent.runtime_state import get_selected_trace
from cst_agent_workbench.agent.session import AgentSession
from cst_agent_workbench.optimization.state import OptimizationState
from cst_agent_workbench.web.optimization_helpers import run_programmatic_optimization_round


class _FakeAgent:
    def __init__(self):
        self.opt_state = OptimizationState()
        self.last_execution_mode = ""
        self.last_results = {}
        self.tool_events = []

    def can_use_programmatic_patch_optimizer(self):
        return True

    def run_programmatic_patch_optimization_round(self, **kwargs):
        return {
            "success": True,
            "message": "done",
            "strategy": "llm",
            "proposal_reason": "[LLM] 当前谐振偏低，减小 patch_L 抬高频率。",
            "changed_params": {"patch_L": {"old": "10", "new": "9.4"}},
            "results_raw": {"plot_data": [{"freq": 9.4, "s_db": -11.0}]},
            "rolled_back": True,
            "rollback_reason": "本轮退化，自动回滚。",
        }


class _FakeTraceAgent:
    """Fake agent with trace support to verify optimization rounds are recorded."""

    def __init__(self):
        self.opt_state = OptimizationState()
        self.session = AgentSession(session_id="ui-opt-trace")
        self.session.bind_optimization_state(self.opt_state)
        self.last_execution_mode = ""
        self.last_results = {}
        self.history = self.session.history
        self.tool_events = self.session.tool_events
        self.tools = []
        self.trace_enabled = True
        self.trace_retention_limit = 10
        self.current_trace = None
        self.current_run_id = None
        self.selected_trace_run_id = None
        self.trace_history = []

    def get_token_stats(self):
        return {"prompt": 0, "completion": 0, "total": 0, "calls": 0, "cost_usd": 0.0}

    def can_use_programmatic_patch_optimizer(self):
        return True

    def run_programmatic_patch_optimization_round(self, **kwargs):
        self.tool_events.append({
            "phase": "1_环境与材料",
            "tool_name": "programmatic_patch_proposal",
            "success": True,
            "message": "",
            "description": "程序化优化策略(heuristic): 优先增大 patch_L 做频率校正。",
        })
        self.tool_events.append({
            "phase": "4_运行仿真",
            "tool_name": "programmatic_patch_run_solver",
            "success": True,
            "message": "solver ok",
            "description": "程序化贴片优化: 单轮调参后求解",
        })
        return {
            "success": True,
            "message": "done",
            "strategy": "heuristic",
            "proposal_reason": "优先增大 patch_L 做频率校正。",
            "changed_params": {"patch_L": {"old": "10", "new": "9.4"}},
            "results_raw": {"plot_data": [{"freq": 9.4, "s_db": -11.0}]},
            "rolled_back": False,
        }


class _BaselineFakeAgent(_FakeAgent):
    def _collect_s11_summary(self, target_freq_ghz):
        return {
            "success": True,
            "raw": {"plot_data": [{"freq": target_freq_ghz, "s_db": -8.0}]},
            "resonances": [],
        }


def _fake_app_state(agent, session, opt_settings=None):
    return type(
        "AppState",
        (),
        {
            "agent": agent,
            "session": session,
            "opt_settings": opt_settings or {"mode": "at_f0", "target_freq": 9.4, "target_db": -10.0},
        },
    )()


def test_run_programmatic_optimization_round_threads_strategy_reason(monkeypatch):
    fake_agent = _FakeAgent()
    fake_session = AgentSession(session_id="ui-opt")
    fake_session.bind_optimization_state(fake_agent.opt_state)
    fake_session.metadata["optimizer_memory_impact"] = {
        "memory_enforced_by_validator": True,
        "fallback_reason": "feed_W must increase",
        "llm_parse_error": "no_parseable_optimization_proposal",
    }
    state = _fake_app_state(fake_agent, fake_session, opt_settings={"mode": "at_f0", "target_freq": 9.4, "target_db": -10.0})
    monkeypatch.setattr("cst_agent_workbench.web.optimization_helpers.resolve_target_frequency", lambda **kwargs: 9.4)

    result = run_programmatic_optimization_round(state)

    assert result["strategy"] == "llm"
    assert result["proposal_reason"] == "[LLM] 当前谐振偏低，减小 patch_L 抬高频率。"
    assert result["rolled_back"] is True
    assert result["memory_impact"]["memory_enforced_by_validator"] is True
    assert fake_session.artifacts.last_optimizer_result["rollback_reason"] == "本轮退化，自动回滚。"
    assert fake_agent.opt_state.round == 1
    assert len(fake_agent.opt_state.history) == 1
    assert fake_agent.opt_state.history[0]["rolled_back"] is True


def test_run_programmatic_optimization_round_records_trace(monkeypatch):
    """优化一轮执行后必须出现在 trace_history，否则前端 Trace 视图不可见。"""
    fake_agent = _FakeTraceAgent()
    fake_session = fake_agent.session
    state = _fake_app_state(fake_agent, fake_session, opt_settings={"mode": "at_f0", "target_freq": 9.4, "target_db": -10.0})
    monkeypatch.setattr("cst_agent_workbench.web.optimization_helpers.resolve_target_frequency", lambda **kwargs: 9.4)

    result = run_programmatic_optimization_round(state)

    assert result["success"] is True
    assert len(fake_agent.trace_history) == 1
    trace = fake_agent.trace_history[0]
    assert trace["status"] == "completed"
    assert len(trace["turns"]) == 1
    turn = trace["turns"][0]
    assert turn["tool_calls"]
    tool_names = [call["tool_name"] for call in turn["tool_calls"]]
    assert "programmatic_patch_proposal" in tool_names
    assert "programmatic_patch_run_solver" in tool_names
    assert fake_agent.selected_trace_run_id == trace["run_id"]
    assert get_selected_trace(fake_agent) is trace


def test_web_optimization_records_baseline_and_round(monkeypatch):
    fake_agent = _BaselineFakeAgent()
    fake_session = AgentSession(session_id="ui-opt-baseline")
    fake_session.bind_optimization_state(fake_agent.opt_state)
    state = _fake_app_state(fake_agent, fake_session)
    monkeypatch.setattr("cst_agent_workbench.web.optimization_helpers.resolve_target_frequency", lambda **kwargs: 9.4)

    result = run_programmatic_optimization_round(state)

    assert result["round"] == 1
    assert [item["round"] for item in fake_agent.opt_state.history] == [0, 1]
    assert fake_agent.opt_state.baseline_metric_value == -8.0
    assert fake_agent.opt_state.best_metric_value == -11.0
