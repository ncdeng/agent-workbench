"""验证 reflection_node 把最近失败 tool_events 的 error_type 注入 round_summary。

直接测试 _collect_recent_errors 和 reflection_node 的 round_summary 构造，
不真正调用 LLM（用 stub client 拦截 reflect_on_round 的调用参数）。
"""
from types import SimpleNamespace
from typing import Any, Dict, List


from cst_agent_workbench.agent.reflection import collect_recent_errors, run_reflection_for_agent


def _make_event(success: bool, error_type: str = None, message: str = "",
                phase: str = "0_init", tool_name: str = "tool_x") -> Dict[str, Any]:
    ev = {
        "phase": phase,
        "tool_name": tool_name,
        "success": success,
        "message": message,
        "description": "",
    }
    if error_type:
        ev["error_type"] = error_type
    return ev


# ── _collect_recent_errors ──────────────────────────────────────────────


def test_collect_recent_errors_empty_when_no_events():
    agent = SimpleNamespace(tool_events=[])
    assert collect_recent_errors(agent) == []


def test_collect_recent_errors_skips_successful_events():
    agent = SimpleNamespace(tool_events=[
        _make_event(success=True, tool_name="ok"),
        _make_event(success=True, tool_name="ok2"),
    ])
    assert collect_recent_errors(agent) == []


def test_collect_recent_errors_skips_failures_without_error_type():
    """老的 fail 事件没 error_type 字段（C-3 之前生成的），应被跳过避免噪声。"""
    agent = SimpleNamespace(tool_events=[
        _make_event(success=False, message="legacy fail without classification"),
    ])
    assert collect_recent_errors(agent) == []


def test_collect_recent_errors_returns_at_most_limit_in_chronological_order():
    agent = SimpleNamespace(tool_events=[
        _make_event(success=False, error_type="vba_execution", message="m1", tool_name="t1"),
        _make_event(success=True, tool_name="ok"),
        _make_event(success=False, error_type="result_read", message="m2", tool_name="t2"),
        _make_event(success=False, error_type="cst_timeout", message="m3", tool_name="t3"),
        _make_event(success=False, error_type="vba_execution", message="m4", tool_name="t4"),
    ])
    result = collect_recent_errors(agent, limit=3)
    assert len(result) == 3
    # 按时间顺序（最近 3 个失败事件，按发生顺序），不是反序
    assert [e["tool_name"] for e in result] == ["t2", "t3", "t4"]
    assert [e["error_type"] for e in result] == ["result_read", "cst_timeout", "vba_execution"]


def test_collect_recent_errors_truncates_long_messages():
    long_msg = "x" * 500
    agent = SimpleNamespace(tool_events=[
        _make_event(success=False, error_type="unknown", message=long_msg),
    ])
    result = collect_recent_errors(agent)
    assert len(result) == 1
    assert len(result[0]["message"]) == 200


def test_collect_recent_errors_handles_missing_attribute():
    """tool_events 字段不存在或为 None 时不抛异常。"""
    assert collect_recent_errors(SimpleNamespace()) == []
    assert collect_recent_errors(SimpleNamespace(tool_events=None)) == []


# ── reflection_node 的 round_summary 注入 ──────────────────────────────


class _CapturingReflect:
    """拦截 reflect_on_round 调用，记录传入的 round_summary。"""

    def __init__(self):
        self.captured: List[Dict[str, Any]] = []
        self.kwargs: List[Dict[str, Any]] = []

    def __call__(self, *, client, model, round_summary, memory, **kwargs):
        self.captured.append(round_summary)
        self.kwargs.append(dict(kwargs))
        return True


def test_reflection_node_includes_recent_errors_in_round_summary(monkeypatch):
    capture = _CapturingReflect()
    monkeypatch.setattr(
        "cst_agent_workbench.agent.reflection.reflect_on_round",
        capture,
    )

    opt_state = SimpleNamespace(active=True, current_round=2, best_value=-12.5)
    session = SimpleNamespace(optimization_state=opt_state, memory={})
    agent = SimpleNamespace(
        session=session,
        client="fake_client",
        model="fake_model",
        tool_events=[
            _make_event(success=False, error_type="vba_execution",
                        message="矩形贴片快速路径在端口阶段失败"),
            _make_event(success=False, error_type="result_read",
                        message="ASCIIExport 失败"),
        ],
    )

    run_reflection_for_agent(agent)

    assert len(capture.captured) == 1
    assert "min_confidence" in capture.kwargs[0]
    summary = capture.captured[0]
    assert summary["round"] == 2
    assert summary["best_value"] == -12.5
    assert "recent_errors" in summary
    assert len(summary["recent_errors"]) == 2
    assert summary["recent_errors"][0]["error_type"] == "vba_execution"
    assert summary["recent_errors"][1]["error_type"] == "result_read"


def test_reflection_node_round_summary_recent_errors_empty_when_no_failures(monkeypatch):
    capture = _CapturingReflect()
    monkeypatch.setattr(
        "cst_agent_workbench.agent.reflection.reflect_on_round",
        capture,
    )
    opt_state = SimpleNamespace(active=True, current_round=1, best_value=-8.0)
    session = SimpleNamespace(optimization_state=opt_state, memory={})
    agent = SimpleNamespace(
        session=session,
        client="fake",
        model="fake",
        tool_events=[_make_event(success=True, tool_name="ok")],
    )
    run_reflection_for_agent(agent)
    assert capture.captured[0]["recent_errors"] == []


def test_reflection_node_skipped_when_optimization_not_active(monkeypatch):
    """opt_state.active=False 时不调 reflect_on_round。"""
    capture = _CapturingReflect()
    monkeypatch.setattr(
        "cst_agent_workbench.agent.reflection.reflect_on_round",
        capture,
    )
    opt_state = SimpleNamespace(active=False, current_round=0, best_value=None)
    session = SimpleNamespace(optimization_state=opt_state, memory={})
    agent = SimpleNamespace(session=session, client="fake", model="fake", tool_events=[])
    result = run_reflection_for_agent(agent)
    assert capture.captured == []
    assert result == {}
