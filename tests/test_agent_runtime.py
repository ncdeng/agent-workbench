import json
from types import SimpleNamespace

from cst_agent_workbench.agent import agent as agent_module
from cst_agent_workbench.agent.agent import CSTAgent
from cst_agent_workbench.agent.memory import StructuredMemory
from cst_agent_workbench.agent.session import AgentSession
from cst_agent_workbench.agent.runtime import (
    _build_planner_context,
    absorb_round_result,
    build_initial_plan,
    evaluate_optimization_next_action,
    evaluate_replan_or_stop,
    prepare_history_for_context,
    run_chat_completion_loop,
    update_plan_after_turn,
)
from cst_agent_workbench.agent.runtime_state import (
    append_llm_turn,
    finish_trace_run,
    get_selected_trace,
    start_tool_call_trace,
    start_trace_run,
)


class _FakeToolCall:
    def __init__(self, tool_id, name, arguments):
        self.id = tool_id
        self.function = SimpleNamespace(name=name, arguments=arguments)

    def model_dump(self):
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.function.name, "arguments": self.function.arguments},
        }


class _FakeMessage:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class _FakeUsage:
    def __init__(self, prompt_tokens, completion_tokens, cached_tokens=0, cache_write_tokens=0):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = prompt_tokens + completion_tokens
        self.cached_tokens = cached_tokens
        self.cache_write_tokens = cache_write_tokens


class _FakeResponse:
    def __init__(self, message, usage):
        self.choices = [SimpleNamespace(message=message)]
        self.usage = usage


class _FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        assert kwargs["model"] == "demo-model"
        return self._responses.pop(0)


class _FakeCST:
    def __init__(self):
        self.project_path = "D:/demo/project.cst"
        self.offline_mode = False

    def is_connected(self):
        return True


class _FakeOptState:
    def __init__(self):
        self.target_freq = 9.4
        self.active = False
        self.round = 0
        self.history = []
        self.target_mode = "at_f0"
        self.target_db = -10.0
        self.best_metric_value = None
        self.best_round = 0


class _FakeSession:
    def __init__(self):
        self.session_id = "sess-123"
        self.active_plan = None
        self.optimization_state = _FakeOptState()
        self.memory = StructuredMemory()
        self.history = []
        self.tool_events = []
        self.artifacts = SimpleNamespace(
            last_results={},
            last_farfield_results={},
            last_vba="",
            last_tool_message="",
            results_invalidated=False,
            results_invalidated_reason="",
        )
        self.trace = SimpleNamespace(
            current_run_id=None,
            current_trace=None,
            trace_history=[],
            selected_trace_run_id=None,
            trace_enabled=True,
            trace_retention_limit=10,
            active_tool_call_id=None,
        )

    def get_projection(self):
        return {"session_id": "sess-123", "history_length": len(self.history)}


class _FakeAgent:
    def __init__(self):
        self.cst = _FakeCST()
        self.history = []
        self.last_chat_status = {"ok": True, "error": "", "had_tool_failure": False, "mode": "llm"}
        self.last_tool_message = ""
        self.last_vba = ""
        self.last_results = {}
        self.last_farfield_results = {}
        self.tool_events = []
        self.opt_state = _FakeOptState()
        self._optimization_mode = False
        self._patch_feed_strategy = "microstrip"
        self._fast_path_counter = 0
        self.trace_enabled = True
        self.trace_retention_limit = 2
        self.trace_history = []
        self.current_trace = None
        self.current_run_id = None
        self.selected_trace_run_id = None
        self._active_tool_call_id = None
        self.token_stats = {"prompt": 0, "completion": 0, "calls": 0}
        self.session = _FakeSession()

    def get_token_stats(self):
        total = self.token_stats["prompt"] + self.token_stats["completion"]
        return {**self.token_stats, "total": total, "cost_usd": 0.0}


def _is_noise(msg):
    return msg.get("content") == "noise"



def _truncate(msg):
    if msg.get("role") != "tool":
        return msg
    data = json.loads(msg["content"])
    if "plot_data" in data:
        data["plot_data"] = ["trimmed"]
    return {**msg, "content": json.dumps(data, ensure_ascii=False)}



def test_prepare_history_for_context_drops_dangling_tool_prefix():
    history = [
        {"role": "tool", "tool_call_id": "x", "content": "{}"},
        {"role": "user", "content": "hello"},
    ]

    prepared = prepare_history_for_context(
        history,
        max_history_messages=10,
        is_noise_message=_is_noise,
        truncate_tool_result=_truncate,
    )

    assert prepared == [{"role": "user", "content": "hello"}]



def test_prepare_history_for_context_filters_noise_and_images():
    history = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "keep me"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,aaa"}},
            ],
        },
        {"role": "assistant", "content": "noise"},
    ]

    prepared = prepare_history_for_context(
        history,
        max_history_messages=10,
        is_noise_message=_is_noise,
        truncate_tool_result=_truncate,
    )

    assert prepared == [{"role": "user", "content": [{"type": "text", "text": "keep me"}]}]



def test_prepare_history_for_context_truncates_tool_result_payload():
    history = [
        {"role": "assistant", "content": "call", "tool_calls": [{"id": "tc1"}]},
        {
            "role": "tool",
            "tool_call_id": "tc1",
            "content": json.dumps({"plot_data": list(range(100)), "value": 1}, ensure_ascii=False),
        },
    ]

    prepared = prepare_history_for_context(
        history,
        max_history_messages=10,
        is_noise_message=_is_noise,
        truncate_tool_result=_truncate,
    )

    assert len(prepared) == 2
    tool_msg = prepared[1]
    assert tool_msg["role"] == "tool"
    assert json.loads(tool_msg["content"])["plot_data"] == ["trimmed"]


def test_planner_context_records_rag_rule_scores(monkeypatch):
    from cst_agent_workbench.rag import knowledge_base as kb_module

    session = _FakeSession()
    session.trace.current_trace = {"turns": []}

    def fake_retrieve(query, client, **kwargs):
        if kwargs.get("filter_type") == "history":
            return []
        assert kwargs.get("with_scores") is True
        return [("rule alpha", 0.42)]

    monkeypatch.setattr(kb_module, "retrieve_antenna_rules", fake_retrieve)
    monkeypatch.setattr(kb_module, "retrieve_official_document_hits", lambda *a, **k: [])

    context = _build_planner_context(session, client=object(), user_message="patch query")

    assert "rule alpha" in context
    assert session.trace.current_trace["rag_context"]["rules"] == [
        {
            "channel": "rule",
            "query": "patch query",
            "text": "rule alpha",
            "score": 0.42,
        }
    ]


def test_planner_context_preserves_document_provenance_in_prompt_and_trace(monkeypatch):
    from cst_agent_workbench.rag import knowledge_base as kb_module

    session = _FakeSession()
    session.trace.current_trace = {"turns": []}
    session.metadata = {}
    hit = {
        "text": "Waveguide ports are defined on a simulation boundary. " * 20,
        "source": "port_waveguide.htm",
        "source_path": "Filter_Designer_3D/example/port_waveguide.htm",
        "source_type": "html",
        "source_hash": "abc123",
        "page": 1,
        "chunk_idx": 7,
        "distance": 0.18,
        "score": 0.82,
        "dense_score": 0.82,
        "rerank_score": 0.91,
        "rank_before": 4,
        "rank_after": 1,
        "reranker_model": "test-reranker",
        "rerank_applied": True,
        "matched_query": "waveguide port",
    }
    monkeypatch.setattr(kb_module, "retrieve_antenna_rules", lambda *a, **k: [])
    monkeypatch.setattr(kb_module, "retrieve_official_document_hits", lambda *a, **k: [hit])

    context = _build_planner_context(session, client=object(), user_message="waveguide port")

    assert (
        "source=Filter_Designer_3D/example/port_waveguide.htm, chunk=7, "
        "rerank=0.910, dense=0.820"
    ) in context
    trace_hit = session.trace.current_trace["rag_context"]["document"][0]
    assert trace_hit["source_path"] == hit["source_path"]
    assert trace_hit["chunk_idx"] == 7
    assert trace_hit["score"] == 0.82
    assert trace_hit["dense_score"] == 0.82
    assert trace_hit["rerank_score"] == 0.91
    assert trace_hit["rank_before"] == 4
    assert trace_hit["rank_after"] == 1
    assert trace_hit["reranker_model"] == "test-reranker"
    assert session.metadata["last_rag_context"]["documents"] == [hit]



def test_prepare_history_for_context_keeps_complete_tool_exchange_when_present():
    history = [
        {"role": "assistant", "content": "call", "tool_calls": [{"id": "tc1"}]},
        {"role": "tool", "tool_call_id": "tc1", "content": json.dumps({"value": 1})},
        {"role": "assistant", "content": "done"},
    ]

    prepared = prepare_history_for_context(
        history,
        max_history_messages=10,
        is_noise_message=_is_noise,
        truncate_tool_result=_truncate,
    )

    assert [msg["role"] for msg in prepared] == ["assistant", "tool", "assistant"]



def test_prepare_history_for_context_keeps_tool_message_even_if_tool_content_matches_noise():
    def _tool_noise(msg):
        return "VBA 已在 CST 中执行" in str(msg.get("content", ""))

    history = [
        {"role": "assistant", "content": "call", "tool_calls": [{"id": "tc1"}]},
        {
            "role": "tool",
            "tool_call_id": "tc1",
            "content": json.dumps({"success": True, "message": "VBA 已在 CST 中执行"}, ensure_ascii=False),
        },
        {"role": "assistant", "content": "done"},
    ]

    prepared = prepare_history_for_context(
        history,
        max_history_messages=10,
        is_noise_message=_tool_noise,
        truncate_tool_result=_truncate,
    )

    assert [msg["role"] for msg in prepared] == ["assistant", "tool", "assistant"]
    assert prepared[1]["tool_call_id"] == "tc1"



def test_trace_run_records_turns_and_token_delta():
    agent = _FakeAgent()
    working_messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hello"}]
    pending_history = [{"role": "user", "content": "hello"}]
    start_trace_run(
        agent,
        user_input=pending_history[0],
        working_messages=working_messages,
        filtered_history=[],
        pending_history=pending_history,
    )

    responses = [
        _FakeResponse(_FakeMessage(content="done"), _FakeUsage(10, 5, cached_tokens=6, cache_write_tokens=2)),
    ]
    client = _FakeClient(responses)

    loop_result = run_chat_completion_loop(
        client=client,
        model="demo-model",
        tools=[],
        working_messages=working_messages,
        pending_history=pending_history,
        execute_tool=lambda name, args: json.dumps({"success": True}),
        token_stats=agent.token_stats,
        offline_notice="",
        optimization_mode=False,
        max_tool_iterations=3,
        trace_turn_callback=lambda payload: append_llm_turn(
            agent,
            turn_index=payload["turn_index"],
            started_at=payload["started_at"],
            finished_at=payload["finished_at"],
            model=payload["model"],
            request_messages=payload["request_messages"],
            tools=payload["tools"],
            assistant_content=payload["assistant_content"],
            tool_calls_raw=payload["tool_calls_raw"],
            usage=payload["usage"],
        ),
    )

    trace = finish_trace_run(agent, status="completed", final_response=loop_result.final_text)
    assert trace is not None
    assert trace["token_delta"]["prompt"] == 10
    assert trace["token_delta"]["completion"] == 5
    assert trace["token_delta"]["total"] == 15
    assert agent.token_stats["cached"] == 6
    assert agent.token_stats["cache_write"] == 2
    assert trace["turns"][0]["usage"]["cached_tokens"] == 6
    assert trace["turns"][0]["usage"]["cache_write_tokens"] == 2
    assert len(trace["turns"]) == 1
    assert trace["final_response"]["preview"] == "done"
    assert trace["decision_summary"]["final_action"] == "answer_only"
    assert trace["run_metrics"]["turn_count"] == 1
    assert trace["turns"][0]["decision_summary"]["decision_result"] == "answered"
    assert trace["turns"][0]["request_summary"]["message_count"] == 2
    assert trace["decision_summary"]["plan_summary"]["intent_kind"] == "chat_task"



def test_run_chat_completion_loop_refuses_empty_step_allowlist():
    """M1：step kind/allowed_tools 与静态白名单交集为空时 fail-closed 拒绝请求模型，
    而不是把 tools=[] + tool_choice="auto" 发给 API（OpenAI 会 400）。"""
    agent = _FakeAgent()
    agent.session.active_plan = {
        "current_step_id": "s1",
        "steps": [
            {"step_id": "s1", "kind": "respond", "allowed_tools": ["run_solver"], "required_tools": []}
        ],
        "intent": {"kind": "chat_task"},
        "status": "active",
    }

    class _RefuseClient:
        def __init__(self):
            self.calls = 0
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **kwargs):
            self.calls += 1
            raise AssertionError("model must not be called with an empty tool allowlist")

    client = _RefuseClient()
    tools = [
        {
            "type": "function",
            "function": {
                "name": "run_solver",
                "description": "run",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    working_messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hello"}]
    pending_history = [{"role": "user", "content": "hello"}]

    result = run_chat_completion_loop(
        client=client,
        model="demo-model",
        tools=tools,
        working_messages=working_messages,
        pending_history=pending_history,
        execute_tool=lambda name, args: json.dumps({"success": True}),
        token_stats=agent.token_stats,
        offline_notice="",
        optimization_mode=False,
        max_tool_iterations=3,
        session=agent.session,
    )

    assert result.final_mode == "empty_step_allowlist"
    assert result.ok is False
    assert client.calls == 0
    assert "白名单为空" in result.final_text


def test_run_chat_completion_loop_appends_failure_tool_message_when_tool_raises():
    working_messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hello"}]
    pending_history = [{"role": "user", "content": "hello"}]
    responses = [
        _FakeResponse(
            _FakeMessage(tool_calls=[_FakeToolCall("tc1", "execute_vba_script", json.dumps({"vba_code": "Sub Test()\nEnd Sub"}, ensure_ascii=False))]),
            _FakeUsage(10, 5),
        ),
        _FakeResponse(_FakeMessage(content="tool failed but history is consistent"), _FakeUsage(8, 4)),
    ]
    client = _FakeClient(responses)

    def _raise_tool(name, args):
        raise RuntimeError("boom")

    loop_result = run_chat_completion_loop(
        client=client,
        model="demo-model",
        tools=[],
        working_messages=working_messages,
        pending_history=pending_history,
        execute_tool=_raise_tool,
        token_stats={"prompt": 0, "completion": 0, "calls": 0},
        offline_notice="",
        optimization_mode=False,
        max_tool_iterations=3,
    )

    assert loop_result.final_text == "tool failed but history is consistent"
    assert loop_result.had_tool_failure is True
    assert [msg["role"] for msg in pending_history] == ["user", "assistant", "tool", "assistant"]
    tool_msg = pending_history[2]
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == "tc1"
    tool_payload = json.loads(tool_msg["content"])
    assert tool_payload["success"] is False
    assert tool_payload["tool_name"] == "execute_vba_script"
    assert tool_payload["error_type"] == "RuntimeError"
    assert "工具执行异常" in tool_payload["message"]


def test_native_loop_preserves_host_approval_required_semantics():
    working_messages = [{"role": "user", "content": "Run raw VBA."}]
    pending_history = [{"role": "user", "content": "Run raw VBA."}]
    responses = [
        _FakeResponse(
            _FakeMessage(tool_calls=[_FakeToolCall(
                "tc-approval",
                "execute_vba_script",
                json.dumps({"vba_code": "Sub Main()\nEnd Sub", "description": "probe"}),
            )]),
            _FakeUsage(10, 5),
        ),
        _FakeResponse(_FakeMessage(content="Approval is required."), _FakeUsage(8, 4)),
    ]
    approval_payload = {
        "success": False,
        "message": "approval required",
        "error_type": "approval_required",
        "approval_request": {"request_id": "req-native", "tool_name": "execute_vba_script"},
    }

    result = run_chat_completion_loop(
        client=_FakeClient(responses),
        model="demo-model",
        tools=[],
        working_messages=working_messages,
        pending_history=pending_history,
        execute_tool=lambda _name, _args: json.dumps(approval_payload),
        token_stats={"prompt": 0, "completion": 0, "calls": 0},
        offline_notice="",
        optimization_mode=False,
        max_tool_iterations=3,
    )

    assert result.had_tool_failure is False
    assert result.executed_tool_names == ["execute_vba_script"]
    assert result.successful_tool_names == []
    assert result.approval_pending is True
    assert json.loads(pending_history[2]["content"])["error_type"] == "approval_required"


def test_run_chat_completion_loop_treats_string_false_tool_result_as_failure():
    working_messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hello"}]
    pending_history = [{"role": "user", "content": "hello"}]
    responses = [
        _FakeResponse(
            _FakeMessage(tool_calls=[_FakeToolCall("tc1", "get_s_parameter", json.dumps({"port_i": 1, "port_j": 1}))]),
            _FakeUsage(10, 5),
        ),
        _FakeResponse(_FakeMessage(content="result read failed"), _FakeUsage(8, 4)),
    ]
    client = _FakeClient(responses)

    loop_result = run_chat_completion_loop(
        client=client,
        model="demo-model",
        tools=[],
        working_messages=working_messages,
        pending_history=pending_history,
        execute_tool=lambda name, args: json.dumps({"success": "false", "message": "missing S11"}, ensure_ascii=False),
        token_stats={"prompt": 0, "completion": 0, "calls": 0},
        offline_notice="",
        optimization_mode=False,
        max_tool_iterations=3,
    )

    assert loop_result.final_text == "result read failed"
    assert loop_result.had_tool_failure is True
    assert loop_result.executed_tool_names == ["get_s_parameter"]


def test_successful_reconnect_retry_removes_stale_offline_notice_from_final_answer():
    working_messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "probe"}]
    pending_history = [{"role": "user", "content": "probe"}]
    responses = [
        _FakeResponse(
            _FakeMessage(tool_calls=[_FakeToolCall("tc1", "execute_vba_script", "{}")]),
            _FakeUsage(10, 5),
        ),
        _FakeResponse(_FakeMessage(content="探针已在重连后成功执行。"), _FakeUsage(8, 4)),
    ]
    client = _FakeClient(responses)
    tool_result = {
        "success": True,
        "message": "retry succeeded",
        "recovery_result": {
            "action_name": "reconnect_cst",
            "recovered": True,
            "retry_success": True,
        },
    }

    loop_result = run_chat_completion_loop(
        client=client,
        model="demo-model",
        tools=[],
        working_messages=working_messages,
        pending_history=pending_history,
        execute_tool=lambda name, args: json.dumps(tool_result, ensure_ascii=False),
        token_stats={"prompt": 0, "completion": 0, "calls": 0},
        offline_notice="[离线模式] CST 未连接。\n\n",
        optimization_mode=False,
        max_tool_iterations=3,
    )

    assert loop_result.final_text == "探针已在重连后成功执行。"
    assert not loop_result.final_text.startswith("[离线模式]")


def test_tool_catalog_is_recomputed_after_each_plan_step():
    session = _FakeSession()
    session.metadata = {}
    session.active_plan = {
        "plan_id": "stepwise",
        "intent": {"kind": "direct_action", "user_goal": "run", "constraints": []},
        "current_step_id": "analyze",
        "status": "active",
        "steps": [
            {"step_id": "analyze", "kind": "analyze", "status": "in_progress", "allowed_tools": ["check_cst_status"]},
            {"step_id": "tool", "kind": "tool", "status": "pending", "allowed_tools": ["run_solver"]},
            {"step_id": "respond", "kind": "respond", "status": "pending", "allowed_tools": ["recall_tool_result"]},
        ],
    }
    responses = [
        _FakeResponse(
            _FakeMessage(tool_calls=[_FakeToolCall("tc1", "check_cst_status", "{}")]),
            _FakeUsage(5, 2),
        ),
        _FakeResponse(_FakeMessage(content="done"), _FakeUsage(5, 2)),
    ]

    class _CapturingClient(_FakeClient):
        def __init__(self, values):
            super().__init__(values)
            self.catalogs = []

        def create(self, **kwargs):
            self.catalogs.append([tool["function"]["name"] for tool in kwargs["tools"]])
            return super().create(**kwargs)

    client = _CapturingClient(responses)
    tools = [
        {"type": "function", "function": {"name": "check_cst_status"}},
        {"type": "function", "function": {"name": "run_solver"}},
        {"type": "function", "function": {"name": "recall_tool_result"}},
    ]

    result = run_chat_completion_loop(
        client=client,
        model="demo-model",
        tools=tools,
        working_messages=[{"role": "user", "content": "run"}],
        pending_history=[{"role": "user", "content": "run"}],
        execute_tool=lambda name, args: json.dumps({"success": True, "message": "ok"}),
        token_stats={"prompt": 0, "completion": 0, "calls": 0},
        offline_notice="",
        optimization_mode=False,
        max_tool_iterations=3,
        session=session,
    )

    assert result.final_text == "done"
    assert client.catalogs == [["check_cst_status"], ["run_solver"]]


def test_run_agent_turn_retries_once_after_successful_replan(monkeypatch):
    from cst_agent_workbench.agent import runtime as runtime_module
    from cst_agent_workbench.agent.runtime import ToolLoopResult, run_agent_turn

    session = AgentSession(session_id="replan")
    session.bind_optimization_state(_FakeOptState())
    build_initial_plan(session=session, user_message="读取结果", optimization_mode=False)
    loop_results = [
        ToolLoopResult("first failed", "llm_path", False, "failed", False, []),
        ToolLoopResult("retry succeeded", "llm_path", True, "", False, []),
    ]
    monkeypatch.setattr(
        runtime_module,
        "run_chat_completion_loop",
        lambda **kwargs: loop_results.pop(0),
    )
    eval_calls = []

    def fake_eval(**kwargs):
        eval_calls.append(kwargs["replan_with_llm"])
        if len(eval_calls) == 1:
            return {"needs_replan": True, "replanned_via_llm": True, "next_action": "replan"}
        return {"needs_replan": False, "next_action": "continue"}

    monkeypatch.setattr(runtime_module, "evaluate_optimization_next_action", fake_eval)

    result = run_agent_turn(
        session=session,
        client=object(),
        model="demo",
        user_message="读取结果",
        working_messages=[{"role": "user", "content": "读取结果"}],
        pending_history=[{"role": "user", "content": "读取结果"}],
        tools=[],
        execute_tool_fn=lambda name, args: "{}",
        token_stats={"prompt": 0, "completion": 0, "calls": 0},
    )

    assert result.final_text == "retry succeeded"
    assert result.plan_eval["same_turn_replan_retry"] is True
    assert eval_calls == [True, False]



def test_trace_history_retention_limit_keeps_recent_runs():
    agent = _FakeAgent()
    for idx in range(3):
        start_trace_run(
            agent,
            user_input={"role": "user", "content": f"u{idx}"},
            working_messages=[{"role": "user", "content": f"u{idx}"}],
            filtered_history=[],
            pending_history=[{"role": "user", "content": f"u{idx}"}],
        )
        finish_trace_run(agent, status="completed", final_response=f"r{idx}")

    assert len(agent.trace_history) == 2
    assert agent.trace_history[0]["final_response"]["preview"] == "r1"
    assert agent.trace_history[1]["final_response"]["preview"] == "r2"



def test_build_runtime_snapshot_includes_session_projection():
    agent = _FakeAgent()
    agent.session = SimpleNamespace(
        session_id="sess-123",
        get_projection=lambda: {"session_id": "sess-123", "history_length": 0},
    )

    start_trace_run(
        agent,
        user_input={"role": "user", "content": "hello"},
        working_messages=[{"role": "user", "content": "hello"}],
        filtered_history=[],
        pending_history=[{"role": "user", "content": "hello"}],
    )

    trace = finish_trace_run(agent, status="completed", final_response="ok")
    assert trace is not None
    assert trace["entry_snapshot"]["session"]["session_id"] == "sess-123"


def test_tool_trace_adds_status_and_result_kind_fields():
    agent = _FakeAgent()
    start_trace_run(
        agent,
        user_input={"role": "user", "content": "hello"},
        working_messages=[{"role": "user", "content": "hello"}],
        filtered_history=[],
        pending_history=[{"role": "user", "content": "hello"}],
    )
    append_llm_turn(
        agent,
        turn_index=1,
        started_at="2026-03-20T10:00:00",
        finished_at="2026-03-20T10:00:01",
        model="demo-model",
        request_messages=[{"role": "user", "content": "hello"}],
        tools=[],
        assistant_content="准备读取结果",
        tool_calls_raw=[{"id": "call-1", "function": {"name": "get_s_parameter"}}],
        usage={"total_tokens": 12},
    )

    entry = start_tool_call_trace(
        agent,
        tool_call_id="call-1",
        tool_name="get_s_parameter",
        phase="5_读取结果",
        arguments={"port_i": 1, "port_j": 1},
    )
    assert entry is not None

    from cst_agent_workbench.agent.runtime_state import finish_tool_call_trace

    finish_tool_call_trace(entry, success=True, result=json.dumps({"success": True, "result_kind": "s_parameter"}, ensure_ascii=False))

    turn = agent.current_trace["turns"][-1]
    tool_call = turn["tool_calls"][0]
    assert tool_call["status_label"] == "success"
    assert tool_call["result_kind"] == "s_parameter"
    assert turn["decision_summary"]["successful_tool_call_count"] == 1
    assert "get_s_parameter" in turn["decision_summary"]["tool_names"]



def test_runtime_planner_helpers_update_session_plan_state():
    session = _FakeSession()

    plan = build_initial_plan(session=session, user_message="执行连续优化", optimization_mode=True)
    updated = update_plan_after_turn(
        session=session,
        assistant_text="本轮已完成调参与判定",
        had_tool_calls=True,
        had_tool_failure=False,
        tool_names=["run_solver"],
        observation="已得到新的 S11 结果",
        final_action="tool_then_answer",
    )
    evaluation = evaluate_replan_or_stop(session=session, result_available=True, target_met=False)

    assert plan["intent"]["kind"] in {"optimization_round", "continuous_optimization"}
    assert session.active_plan["current_step_id"] == updated["current_step_id"]
    assert session.active_plan["steps"][0]["status"] == "completed"
    assert evaluation["continue"] is True



def test_optimization_tooluse_signature_distinct():
    from cst_agent_workbench.agent.tool_use_memory import ToolUseMemoryStore

    class _RoundState:
        def __init__(self):
            self.best_metric_value = -8.0
            self.history = []
            self.round = 0

        def record_round(self, check, param_snapshot, *, strategy="", proposal_reason=""):
            self.round += 1
            latest = {
                "round": self.round,
                "strategy": strategy,
                "proposal_reason": proposal_reason,
                "changed_params": "feed_W:+0.3",
                "metric_value": check.get("min_s11"),
                "met": False,
            }
            self.history.append(latest)
            self.best_metric_value = check.get("min_s11")
            return True

    def write_record(request):
        session = SimpleNamespace(
            active_plan=None,
            metadata={},
            tool_use_memory=ToolUseMemoryStore(),
            artifacts=SimpleNamespace(last_patch_request=request),
        )
        absorb_round_result(
            session=session,
            opt_state=_RoundState(),
            check={"min_s11": -10.0, "met": False},
            param_snapshot={"feed_W": 2.4},
            changed_params={"feed_W": 2.4},
            strategy="increase feed width",
            proposal_reason="wider feed improved S11",
            backend="build_rectangular_patch_fast",
            format_status=lambda *args: "status",
        )
        return session.tool_use_memory.records[-1]

    record_a = write_record(SimpleNamespace(epsilon_r=2.2, f0_ghz=9.4, feed_strategy="microstrip"))
    record_b = write_record(SimpleNamespace(epsilon_r=4.4, f0_ghz=2.4, feed_strategy="microstrip"))

    assert record_a.task_signature == "optimization_round:erlt3_f9-10ghz_inset"
    assert record_b.task_signature == "optimization_round:er3-6_f2-3ghz_inset"
    assert record_a.task_signature != record_b.task_signature
    assert record_a.metadata["design_signature"] == "erlt3_f9-10ghz_inset"


def test_evaluate_optimization_next_action_maps_stop_reason_to_ui_action():
    session = _FakeSession()
    build_initial_plan(session=session, user_message="执行连续优化", optimization_mode=True)

    evaluation = evaluate_optimization_next_action(
        session=session,
        result_available=True,
        target_met=False,
        stagnation_hit=True,
        max_round_reached=False,
    )

    assert evaluation["stop_reason"] == "stagnation_limit"
    assert evaluation["next_action"] == "stop_stagnation"


def test_start_trace_run_seeds_plan_summary_from_session():
    agent = _FakeAgent()
    build_initial_plan(session=agent.session, user_message="读取当前 S11 并总结", optimization_mode=False)

    start_trace_run(
        agent,
        user_input={"role": "user", "content": "读取当前 S11 并总结"},
        working_messages=[{"role": "user", "content": "读取当前 S11 并总结"}],
        filtered_history=[],
        pending_history=[{"role": "user", "content": "读取当前 S11 并总结"}],
    )

    assert agent.current_trace is not None
    assert agent.current_trace["plan_state"]["intent_kind"] == "direct_action"


def test_finish_trace_run_uses_full_plan_state_for_decision_summary():
    agent = _FakeAgent()
    build_initial_plan(session=agent.session, user_message="执行连续优化", optimization_mode=True)
    agent.session.active_plan["intent"]["kind"] = "continuous_optimization"
    agent.session.active_plan["stop_reason"] = "stagnation_limit"

    start_trace_run(
        agent,
        user_input={"role": "user", "content": "执行连续优化"},
        working_messages=[{"role": "user", "content": "执行连续优化"}],
        filtered_history=[],
        pending_history=[{"role": "user", "content": "执行连续优化"}],
    )
    agent.current_trace["plan_state"] = agent.session.active_plan

    trace = finish_trace_run(agent, status="completed", final_response="done")

    assert trace is not None
    assert trace["decision_summary"]["plan_summary"]["intent_kind"] == "continuous_optimization"
    assert trace["decision_summary"]["plan_summary"]["stop_reason"] == "stagnation_limit"


def test_chat_meta_reply_short_circuit_still_records_trace_and_plan(monkeypatch):
    agent = CSTAgent.__new__(CSTAgent)
    agent.session = _FakeSession()
    agent.cst = _FakeCST()
    agent.history = []
    agent.last_chat_status = {"ok": True, "error": "", "had_tool_failure": False}
    agent.opt_state = _FakeOptState()
    agent.last_tool_message = ""
    agent.last_vba = ""
    agent.last_results = {}
    agent.last_farfield_results = {}
    agent.tool_events = []
    agent.opt_state = _FakeOptState()
    agent._optimization_mode = False
    agent._patch_feed_strategy = "microstrip"
    agent._fast_path_counter = 0
    agent.trace_enabled = True
    agent.trace_retention_limit = 10
    agent.trace_history = []
    agent.current_trace = None
    agent.current_run_id = None
    agent.selected_trace_run_id = None
    agent._active_tool_call_id = None
    agent.token_stats = {"prompt": 0, "completion": 0, "calls": 0}
    agent.session = _FakeSession()
    agent.tools = []
    agent.client = object()
    agent.model = "demo-model"
    agent.last_execution_mode = "fast_path"
    agent.last_patch_request = None

    monkeypatch.setattr(CSTAgent, "_build_meta_llm_query_response", lambda self, message: "解释：上一条走的是 fast path")

    result = agent.chat("上一条有没有调用大模型？")

    assert result == "解释：上一条走的是 fast path"
    assert agent.trace_history
    trace = agent.trace_history[-1]
    assert trace["decision_summary"]["final_action"] == "answer_only"
    assert trace["decision_summary"]["plan_summary"]["intent_kind"] == "chat_task"
    assert agent.session.active_plan["status"] == "completed"


def test_chat_fast_path_short_circuit_still_records_trace_and_plan(monkeypatch):
    agent = CSTAgent.__new__(CSTAgent)
    agent.session = _FakeSession()
    agent.cst = _FakeCST()
    agent.history = []
    agent.last_chat_status = {"ok": True, "error": "", "had_tool_failure": False, "mode": "fast_path"}
    agent.opt_state = _FakeOptState()
    agent.last_tool_message = ""
    agent.last_vba = ""
    agent.last_results = {}
    agent.last_farfield_results = {}
    agent.tool_events = []
    agent.opt_state = _FakeOptState()
    agent._optimization_mode = False
    agent._patch_feed_strategy = "microstrip"
    agent._fast_path_counter = 0
    agent.trace_enabled = True
    agent.trace_retention_limit = 10
    agent.trace_history = []
    agent.current_trace = None
    agent.current_run_id = None
    agent.selected_trace_run_id = None
    agent._active_tool_call_id = None
    agent.token_stats = {"prompt": 0, "completion": 0, "calls": 0}
    agent.session = _FakeSession()
    agent.tools = []
    agent.client = object()
    agent.model = "demo-model"
    agent.last_execution_mode = ""
    agent.last_patch_request = None

    monkeypatch.setattr(CSTAgent, "_build_meta_llm_query_response", lambda self, message: None)
    monkeypatch.setattr(CSTAgent, "_run_rectangular_patch_fast_path", lambda self, message: "fast path 已完成建模")

    result = agent.chat("帮我创建一个中心频率为7GHz的矩形微带贴片天线")

    assert result == "fast path 已完成建模"
    assert agent.trace_history
    trace = agent.trace_history[-1]
    assert trace["decision_summary"]["final_action"] == "answer_only"
    assert trace["decision_summary"]["plan_summary"]["intent_kind"] == "chat_task"
    assert agent.last_execution_mode == "fast_path"


def _build_short_circuit_agent():
    agent = CSTAgent.__new__(CSTAgent)
    agent.session = _FakeSession()
    agent.cst = _FakeCST()
    agent.history = []
    agent.last_chat_status = {"ok": True, "error": "", "had_tool_failure": False, "mode": "fast_path"}
    agent.opt_state = _FakeOptState()
    agent.last_tool_message = ""
    agent.last_vba = ""
    agent.last_results = {}
    agent.last_farfield_results = {}
    agent.tool_events = []
    agent._optimization_mode = False
    agent._patch_feed_strategy = "microstrip"
    agent._fast_path_counter = 0
    agent.trace_enabled = True
    agent.trace_retention_limit = 10
    agent.trace_history = []
    agent.current_trace = None
    agent.current_run_id = None
    agent.selected_trace_run_id = None
    agent._active_tool_call_id = None
    agent.token_stats = {"prompt": 0, "completion": 0, "calls": 0}
    agent.tools = []
    agent.client = object()
    agent.model = "demo-model"
    agent.last_execution_mode = ""
    agent.last_patch_request = None
    return agent


def test_chat_fast_path_short_circuit_never_calls_the_llm_planner(monkeypatch):
    """A fast-path turn must cost zero model calls.

    The short-circuit probe runs before the LLM planner and the turn falls back to
    a heuristic plan, so the agent's own "no model was called" answer stays true.
    Without this guard the planner would silently run and be thrown away.
    """
    agent = _build_short_circuit_agent()

    planner_calls = []

    def _fail_if_planner_runs(**kwargs):
        planner_calls.append(kwargs)
        raise AssertionError("fast path must not invoke the LLM planner")

    monkeypatch.setattr(agent_module, "build_initial_plan_with_usage", _fail_if_planner_runs)
    monkeypatch.setattr(CSTAgent, "_build_meta_llm_query_response", lambda self, message: None)
    monkeypatch.setattr(CSTAgent, "_run_rectangular_patch_fast_path", lambda self, message: "fast path 已完成建模")

    result = agent.chat("帮我创建一个中心频率为7GHz的矩形微带贴片天线")

    assert result == "fast path 已完成建模"
    assert planner_calls == []
    assert agent.token_stats["calls"] == 0
    # The Trace projection still needs plan state even though no planner ran.
    assert agent.trace_history[-1]["decision_summary"]["plan_summary"]["intent_kind"] == "chat_task"


def test_get_selected_trace_returns_latest_when_selected_missing():
    agent = _FakeAgent()
    start_trace_run(
        agent,
        user_input={"role": "user", "content": "hello"},
        working_messages=[{"role": "user", "content": "hello"}],
        filtered_history=[],
        pending_history=[{"role": "user", "content": "hello"}],
    )
    trace = finish_trace_run(agent, status="completed", final_response="done")

    assert get_selected_trace(agent)["run_id"] == trace["run_id"]


