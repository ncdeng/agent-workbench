"""Tests for context token budget helpers in runtime.py."""
from types import SimpleNamespace

from cst_agent_workbench.agent.context_summary import make_context_summary_message
from cst_agent_workbench.agent.memory import StructuredMemory
from cst_agent_workbench.agent.runtime import _estimate_tokens, _trim_messages_to_budget, filter_tools_for_active_step


def test_estimate_tokens_empty():
    assert _estimate_tokens([]) == 0


def test_estimate_tokens_basic():
    msgs = [
        {"role": "user", "content": "abcd"},
        {"role": "assistant", "content": "abcdefgh"},
    ]
    assert _estimate_tokens(msgs) > 3


def test_estimate_tokens_includes_tool_schema():
    msgs = [{"role": "user", "content": "run"}]
    tools = [{
        "type": "function",
        "function": {
            "name": "run_solver",
            "description": "R" * 400,
            "parameters": {"type": "object", "properties": {"timeout": {"type": "integer"}}},
        },
    }]

    assert _estimate_tokens(msgs, tools=tools) > _estimate_tokens(msgs)


def test_trim_no_truncation_needed():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    result = _trim_messages_to_budget(msgs, max_tokens=6000)
    assert result == msgs


def test_trim_truncates_long_history():
    system_msg = {"role": "system", "content": "S" * 4}
    long_pairs = []
    for i in range(5):
        long_pairs.append({"role": "user", "content": "U" * 200})
        long_pairs.append({"role": "assistant", "content": "A" * 200})
    msgs = [system_msg] + long_pairs
    original_len = len(msgs)

    result = _trim_messages_to_budget(msgs, max_tokens=50)

    assert len(result) < original_len
    assert result[0] == system_msg


def test_trim_inserts_truncation_notice():
    system_msg = {"role": "system", "content": "S" * 4}
    long_pairs = []
    for i in range(5):
        long_pairs.append({"role": "user", "content": "U" * 200})
        long_pairs.append({"role": "assistant", "content": "A" * 200})
    msgs = [system_msg] + long_pairs

    result = _trim_messages_to_budget(msgs, max_tokens=50)

    system_contents = [m["content"] for m in result if m.get("role") == "system"]
    assert any("token" in c or "截断" in c for c in system_contents)


def _tool(name):
    return {"type": "function", "function": {"name": name}}


def test_tool_filter_read_result_excludes_geometry_tools():
    session = SimpleNamespace(
        active_plan={
            "current_step_id": "read",
            "steps": [{"step_id": "read", "kind": "read_result"}],
        },
        metadata={},
    )
    tools = [_tool("read_result"), _tool("get_s_parameter"), _tool("recall_tool_result"), _tool("create_brick"), _tool("run_solver")]

    filtered = filter_tools_for_active_step(session, tools)
    names = [(tool["function"] or {})["name"] for tool in filtered]

    assert names == ["read_result", "get_s_parameter", "recall_tool_result"]
    assert session.metadata["tool_filter"]["active_step_kind"] == "read_result"
    assert session.metadata["tool_filter"]["filtered_out_count"] == 2


def test_tool_filter_analyze_direct_action_keeps_action_tools_available():
    session = SimpleNamespace(
        active_plan={
            "intent": {"kind": "direct_action"},
            "current_step_id": "analyze",
            "steps": [{"step_id": "analyze", "kind": "analyze"}],
        },
        metadata={},
    )
    tools = [_tool("check_cst_status"), _tool("build_rectangular_patch_fast"), _tool("create_brick"), _tool("read_result")]

    filtered = filter_tools_for_active_step(session, tools)
    names = [(tool["function"] or {})["name"] for tool in filtered]

    assert names == ["check_cst_status", "build_rectangular_patch_fast", "create_brick", "read_result"]
    assert session.metadata["tool_filter"]["active_step_kind"] == "analyze"


def test_tool_filter_geometry_excludes_result_tools():
    session = SimpleNamespace(
        active_plan={
            "current_step_id": "geometry",
            "steps": [{"step_id": "geometry", "kind": "geometry"}],
        },
        metadata={},
    )
    tools = [_tool("create_brick"), _tool("create_cylinder"), _tool("get_s_parameter"), _tool("read_result")]

    filtered = filter_tools_for_active_step(session, tools)
    names = [(tool["function"] or {})["name"] for tool in filtered]

    assert names == ["create_brick", "create_cylinder"]
    assert session.metadata["tool_filter"]["filtered_out_count"] == 2


def test_tool_filter_respond_allows_only_recall_tool():
    session = SimpleNamespace(
        active_plan={
            "current_step_id": "respond",
            "steps": [{"step_id": "respond", "kind": "respond"}],
        },
        metadata={},
    )
    tools = [_tool("recall_tool_result"), _tool("read_result"), _tool("create_brick")]

    filtered = filter_tools_for_active_step(session, tools)
    names = [(tool["function"] or {})["name"] for tool in filtered]

    assert names == ["recall_tool_result"]


def test_tool_filter_unknown_step_kind_fails_closed():
    session = SimpleNamespace(
        active_plan={
            "current_step_id": "bad",
            "steps": [{"step_id": "bad", "kind": "invented_kind"}],
        },
        metadata={},
    )

    assert filter_tools_for_active_step(session, [_tool("run_solver")]) == []


def test_explicit_allowed_tools_cannot_escape_step_safety_boundary():
    session = SimpleNamespace(
        active_plan={
            "current_step_id": "respond",
            "steps": [{
                "step_id": "respond",
                "kind": "respond",
                "allowed_tools": ["recall_tool_result", "execute_vba_script"],
            }],
        },
        metadata={},
    )

    filtered = filter_tools_for_active_step(
        session,
        [_tool("recall_tool_result"), _tool("execute_vba_script")],
    )

    assert [tool["function"]["name"] for tool in filtered] == ["recall_tool_result"]


def test_trim_preserves_latest_user_and_canonical_summary():
    """Canonical goal lives in summary; latest user turn is positionally protected."""
    system_msg = {"role": "system", "content": "S" * 4}
    first_user = {"role": "user", "content": "我要设计 9.4GHz patch antenna，基板 RO4350B"}
    long_pairs = []
    for i in range(8):
        long_pairs.append({"role": "user", "content": "U" * 200})
        long_pairs.append({"role": "assistant", "content": "A" * 200})
    latest_user = {"role": "user", "content": "停止优化，只导出结果"}
    msgs = [system_msg, first_user] + long_pairs + [latest_user]
    summary = {"role": "system", "content": "[历史摘要]\n[用户目标]\n优化 9.4GHz 贴片天线"}

    result = _trim_messages_to_budget(msgs, max_tokens=120, context_summary_message=summary)

    assert latest_user in result
    assert "9.4GHz" in str(result)
    assert first_user not in result


def test_trim_clears_old_tool_results():
    """P2: 旧 tool 结果被替换成短占位符，保留 tool_use 记录。"""
    from cst_agent_workbench.agent.runtime import _clear_old_tool_results
    messages = [
        {"role": "user", "content": "run solver"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t1", "function": {"name": "run_solver"}}]},
        {"role": "tool", "tool_call_id": "t1", "content": "X" * 500},  # 旧的大结果
        {"role": "assistant", "content": "solver done"},
        {"role": "user", "content": "read s11"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t2", "function": {"name": "read_s11"}}]},
        {"role": "tool", "tool_call_id": "t2", "content": "Y" * 500},  # 旧的大结果
        {"role": "assistant", "content": "s11 done"},
        {"role": "user", "content": "recent query"},
        {"role": "assistant", "content": "recent reply"},
    ]
    cleared = _clear_old_tool_results(messages, keep_recent=4)
    # 最近 4 条非 tool 消息内的 tool 结果保留
    # 旧的 tool 结果被清空
    tool_contents = [m["content"] for m in cleared if m.get("role") == "tool"]
    assert any("cleared" in c for c in tool_contents), "old tool result should be cleared"
    assert any(len(c) > 100 for c in tool_contents)  # 至少有一个保留完整


def test_trim_inserts_semantic_context_summary_when_available():
    memory = StructuredMemory()
    memory.conversation.user_goal = "优化 9.4GHz 贴片天线"
    memory.conversation.constraints = ["Rogers5880", "probe-fed"]
    memory.workspace.last_results_summary = {"min_s11_db": -8.0, "min_freq_ghz": 9.8}
    memory.decisions.best_so_far = {"round": 2, "metric_value": -9.5}
    memory.decisions.failure_reasons = ["patch_L 增大导致频率偏低"]
    session = SimpleNamespace(memory=memory)
    summary_message = make_context_summary_message(session)
    msgs = [{"role": "system", "content": "sys"}]
    for i in range(20):
        msgs.append({"role": "user", "content": f"old user {i} " + "U" * 200})
        msgs.append({"role": "assistant", "content": f"old assistant {i} " + "A" * 200})

    result = _trim_messages_to_budget(msgs, max_tokens=350, context_summary_message=summary_message)
    joined = "\n".join(str(m.get("content", "")) for m in result)

    assert "[历史摘要]" in joined
    assert "9.4GHz" in joined
    assert "Rogers5880" in joined
    assert "probe-fed" in joined
    assert "patch_L 增大导致频率偏低" in joined
    assert "best_so_far" in joined
