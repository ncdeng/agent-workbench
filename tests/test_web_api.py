import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient

from cst_agent_workbench.agent.session import AgentSession
from cst_agent_workbench.bootstrap import get_app_state, reset_app_state
from cst_agent_workbench.cst.primitives import (
    register_farfield_monitor,
    register_frequency_range,
    register_object,
    register_port,
    reset_created_objects,
)
from cst_agent_workbench.optimization.state import OptimizationState
from fakes import FakeCSTController

from cst_agent_workbench.web_api import (
    _run_exclusive,
    _serialize_chat_history,
    _serialize_tool_event,
    _stream_produced_frames,
    build_dashboard_snapshot,
    create_app,
)


class _FakeCST:
    project_path = "D:/demo/project.cst"
    offline_mode = False

    def is_connected(self):
        return True


class _FakeAgent:
    def __init__(self, session):
        self.session = session
        self.opt_state = session.optimization_state


def test_build_dashboard_snapshot_projects_session_state():
    opt = OptimizationState()
    opt.active = True
    opt.round = 2
    opt.best_round = 1
    opt.best_metric_value = -12.5
    opt.target_mode = "at_f0"
    opt.target_freq = 9.4
    opt.target_db = -10.0
    session = AgentSession(session_id="web-api")
    session.bind_optimization_state(opt)
    session.artifacts.last_results = {
        "success": True,
        "item": "S1,1",
        "plot_data": [{"freq": 9.3, "s_db": -8.0}, {"freq": 9.4, "s_db": -12.5}],
    }
    session.artifacts.last_optimizer_result = {
        "strategy": "llm",
        "rolled_back": True,
        "rollback_reason": "worse metric",
    }
    session.metadata["optimizer_memory_recall"] = [{"id": "m1"}]
    session.metadata["optimizer_memory_impact"] = {"memory_enforced_by_validator": True}
    session.tool_events.append({
        "phase": "4_运行仿真",
        "tool_name": "run_solver",
        "success": True,
        "description": "solver done",
    })
    state = SimpleNamespace(
        cst=_FakeCST(),
        session=session,
        agent=_FakeAgent(session),
        opt_settings={"mode": "at_f0", "target_freq": 9.4, "target_db": -10.0},
    )

    snapshot = build_dashboard_snapshot(state)

    assert snapshot["project"]["connected"] is True
    assert snapshot["project"]["path"] == "D:/demo/project.cst"
    assert snapshot["optimization"]["active"] is True
    assert snapshot["optimization"]["round"] == 2
    assert snapshot["optimization"]["bestMetricValue"] == -12.5
    assert snapshot["optimization"]["lastStrategy"] == "llm"
    assert snapshot["optimization"]["lastRolledBack"] is True
    assert snapshot["optimization"]["lastMemoryRecallCount"] == 1
    assert snapshot["optimization"]["lastMemoryEnforced"] is True
    assert snapshot["results"]["available"] is True
    assert snapshot["results"]["points"] == 2
    assert snapshot["recentEvents"][0]["tool"] == "run_solver"


def test_serialize_tool_event_accepts_tool_name_and_strict_success():
    event = _serialize_tool_event({
        "phase": "5_results",
        "tool_name": "read_s11_direct",
        "success": "false",
        "message": "not connected",
    })

    assert event == {
        "phase": "5_results",
        "tool": "read_s11_direct",
        "success": False,
        "description": "not connected",
    }


def test_serialize_tool_event_preserves_approval_request_for_ui():
    event = _serialize_tool_event({
        "phase": "0_通用脚本",
        "tool_name": "execute_vba_script",
        "success": False,
        "message": "approval required",
        "error_type": "approval_required",
        "approval_request": {"request_id": "req-1", "tool_name": "execute_vba_script"},
    })

    assert event["errorType"] == "approval_required"
    assert event["approvalRequest"]["request_id"] == "req-1"


def test_direct_simulate_records_visible_event_when_disconnected():
    reset_app_state()
    app = create_app(dry_run=True)
    state = get_app_state(dry_run=True)

    response = TestClient(app).post("/api/cst/simulate")

    assert response.status_code == 200
    assert response.json()["success"] is False
    assert state.session.tool_events
    event = _serialize_tool_event(state.session.tool_events[-1])
    assert event["tool"] == "run_solver_direct"
    assert event["success"] is False
    assert event["description"]
    assert state.session.tool_events[-1]["error_type"] == "cst_connection"


def test_tool_approval_api_approves_only_server_owned_pending_request():
    import json

    from cst_agent_workbench.agent.tool_runtime import execute_tool

    reset_app_state()
    app = create_app(dry_run=True)
    state = get_app_state(dry_run=True)
    state.cst = FakeCSTController(connected=True, offline_mode=False)
    state.agent.cst = state.cst
    arguments = {"vba_code": "Sub Main()\nEnd Sub", "description": "web approval smoke"}
    rejected = json.loads(execute_tool(state.agent, "execute_vba_script", arguments))
    request_id = rejected["approval_request"]["request_id"]
    client = TestClient(app)

    pending = client.get("/api/approvals/pending").json()
    approved = client.post(
        f"/api/approvals/{request_id}/approve",
        json={"ttl_seconds": 120},
    )
    replay = json.loads(execute_tool(state.agent, "execute_vba_script", arguments))

    assert pending["requests"][0]["request_id"] == request_id
    assert "arguments" not in pending["requests"][0]
    assert approved.status_code == 200
    assert approved.json()["approved"] is True
    assert approved.json()["executed"] is True
    assert approved.json()["result"]["success"] is True
    assert state.cst.calls["execute_vba"]
    assert replay["error_type"] == "approval_required"


def test_tool_approval_api_rejects_unknown_request():
    reset_app_state()
    client = TestClient(create_app(dry_run=True))

    response = client.post(
        "/api/approvals/not-found/approve",
        json={"ttl_seconds": 120},
    )

    assert response.status_code == 409


def test_tool_approval_api_rejects_stale_plan_without_issuing_grant():
    import json

    from cst_agent_workbench.agent.tool_runtime import execute_tool

    reset_app_state()
    app = create_app(dry_run=True)
    state = get_app_state(dry_run=True)
    arguments = {"vba_code": "Sub Main()\nEnd Sub", "description": "stale plan"}
    requested = json.loads(execute_tool(state.agent, "execute_vba_script", arguments))
    request_id = requested["approval_request"]["request_id"]
    state.session.active_plan = {
        "current_step_id": "read",
        "steps": [{
            "step_id": "read",
            "kind": "read_result",
            "status": "in_progress",
            "allowed_tools": ["get_s_parameter"],
        }],
    }

    response = TestClient(app).post(
        f"/api/approvals/{request_id}/approve",
        json={"ttl_seconds": 120},
    )

    assert response.status_code == 409
    assert state.session.tool_approvals.requests[request_id].status == "stale"
    assert state.session.tool_approvals.projection()["active_grant_count"] == 0


def _register_web_solver_ready_model():
    reset_created_objects()
    register_object("component", "patch", "PEC")
    register_port(1, "50")
    register_frequency_range("8", "10")
    register_farfield_monitor("farfield (f=9)", "9", False)


def test_direct_simulate_preflight_blocks_missing_model_state():
    reset_created_objects()
    reset_app_state()
    app = create_app(dry_run=True)
    state = get_app_state(dry_run=True)
    state.cst = FakeCSTController(connected=True, offline_mode=False)

    response = TestClient(app).post("/api/cst/simulate")

    assert response.status_code == 200
    assert response.json()["success"] is False
    assert "未创建几何对象" in response.json()["message"]
    assert len(state.cst.calls["run_solver"]) == 0
    assert state.session.tool_events[-1]["tool_name"] == "run_solver_direct"
    assert state.session.tool_events[-1]["success"] is False


def test_direct_simulate_runs_solver_after_preflight_passes():
    _register_web_solver_ready_model()
    reset_app_state()
    app = create_app(dry_run=True)
    state = get_app_state(dry_run=True)
    state.cst = FakeCSTController(connected=True, offline_mode=False)

    response = TestClient(app).post("/api/cst/simulate")

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert len(state.cst.calls["run_solver"]) == 1


def test_create_app_exposes_shared_operation_lock_for_mutating_routes():
    reset_app_state()
    app = create_app(dry_run=True)

    assert app.state.operation_lock is not None


def test_readonly_routes_are_registered_after_split():
    reset_app_state()
    app = create_app(dry_run=True)
    client = TestClient(app)

    for path in (
        "/api/dashboard/snapshot",
        "/api/results/s11",
        "/api/results/farfield",
        "/api/agent/trace",
        "/api/state/token-stats",
        "/api/state/vba",
        "/api/state/plan",
        "/api/state/parameters",
        "/api/rag/status",
    ):
        response = client.get(path)
        assert response.status_code == 200, path

    missing_trace = client.get("/api/agent/trace/missing").json()
    assert missing_trace["error"] == "Trace missing not found"



def test_run_exclusive_waits_for_lock_and_releases_it():
    async def scenario():
        lock = asyncio.Lock()
        order = []
        async with lock:
            task = asyncio.create_task(_run_exclusive(lock, lambda: order.append("ran") or "ok"))
            await asyncio.sleep(0)
            assert not task.done()
        assert await task == "ok"
        assert order == ["ran"]
        assert not lock.locked()

    asyncio.run(scenario())


def test_run_exclusive_releases_lock_after_exception():
    async def scenario():
        lock = asyncio.Lock()

        def fail():
            raise RuntimeError("boom")

        try:
            await _run_exclusive(lock, fail)
        except RuntimeError as exc:
            assert str(exc) == "boom"
        else:
            raise AssertionError("expected RuntimeError")
        assert not lock.locked()
        assert await _run_exclusive(lock, lambda: "after") == "after"

    asyncio.run(scenario())


def test_stream_produced_frames_appends_done():
    async def scenario():
        async def produce(emit):
            emit("data: one\n\n")
            emit("data: two\n\n")

        return [frame async for frame in _stream_produced_frames(produce)]

    assert asyncio.run(scenario()) == ["data: one\n\n", "data: two\n\n", "data: [DONE]\n\n"]


def test_stream_produced_frames_finishes_after_producer_error():
    async def scenario():
        async def produce(emit):
            emit("data: before\n\n")
            raise RuntimeError("boom")

        return [frame async for frame in _stream_produced_frames(produce)]

    assert asyncio.run(scenario()) == ["data: before\n\n", "data: [DONE]\n\n"]


def test_cst_routes_are_registered_after_split():
    reset_app_state()
    app = create_app(dry_run=True)
    routes = {(route.path, frozenset(route.methods or [])) for route in app.routes}

    expected = {
        ("/api/cst/reconnect", "POST"),
        ("/api/cst/simulate", "POST"),
        ("/api/cst/read-s11", "POST"),
        ("/api/cst/read-farfield", "POST"),
        ("/api/cst/refresh-results", "POST"),
        ("/api/cst/farfield-items", "POST"),
    }

    for path, method in expected:
        assert any(route_path == path and method in methods for route_path, methods in routes), path



def test_state_and_rag_routes_are_registered_after_split():
    reset_app_state()
    app = create_app(dry_run=True)
    routes = {(route.path, frozenset(route.methods or [])) for route in app.routes}

    expected = {
        ("/api/state/clear", "POST"),
        ("/api/rag/import", "POST"),
    }

    for path, method in expected:
        assert any(route_path == path and method in methods for route_path, methods in routes), path



def test_optimization_routes_are_registered_after_split():
    reset_app_state()
    app = create_app(dry_run=True)
    routes = {(route.path, frozenset(route.methods or [])) for route in app.routes}

    expected = {
        ("/api/optimize/once", "POST"),
        ("/api/optimize/continuous", "POST"),
        ("/api/optimize/stop", "POST"),
        ("/api/optimize/rollback", "POST"),
        ("/api/optimize/history", "GET"),
    }

    for path, method in expected:
        assert any(route_path == path and method in methods for route_path, methods in routes), path



def test_settings_routes_get_and_update_optimization_settings():
    reset_app_state()
    app = create_app(dry_run=True)
    state = get_app_state(dry_run=True)
    state.opt_settings.update({
        "mode": "at_f0",
        "target_freq": 9.4,
        "target_db": -10,
        "max_rounds": 20,
        "stagnation": 3,
    })
    client = TestClient(app)

    initial = client.get("/api/settings/optimization")
    assert initial.status_code == 200
    assert initial.json()["targetFreqGhz"] == 9.4

    response = client.put(
        "/api/settings/optimization",
        json={"mode": "min", "targetFreqGhz": 10.0, "targetDb": -15, "maxRounds": 12, "stagnation": 4},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert state.opt_settings["mode"] == "min"
    assert state.opt_settings["target_freq"] == 10.0
    assert state.opt_settings["target_db"] == -15
    assert state.opt_settings["max_rounds"] == 12
    assert state.opt_settings["stagnation"] == 4



def test_chat_endpoint_propagates_agent_failure_status():
    reset_app_state()
    app = create_app(dry_run=True)
    state = get_app_state(dry_run=True)

    def fake_chat(message, images=None, skip_plan=False):
        state.agent.last_chat_status = {
            "ok": False,
            "error": "CST not connected",
            "had_tool_failure": False,
            "mode": "llm_path",
        }
        return "CST not connected"

    state.agent.chat = fake_chat

    response = TestClient(app).post("/api/chat", json={"message": "run solver"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["error"] == "CST not connected"
    assert body["reply"] == "CST not connected"


def test_chat_endpoint_routes_through_runtime_control_flow(monkeypatch):
    """生产 chat 路径直接调 agent.chat()（内部走 planner → tool loop → reflection 闭环），
    不再经过 LangGraph 图层。这把 ADR-001（LangGraph 撤销）的叙事与代码绑定。"""
    reset_app_state()
    app = create_app(dry_run=True)
    state = get_app_state(dry_run=True)

    captured = {}

    def fake_chat(message, images=None, skip_plan=False):
        captured["called"] = True
        captured["message"] = message
        captured["images"] = images
        state.agent.last_chat_status = {"ok": True, "error": "", "had_tool_failure": False, "mode": "llm_path"}
        return "routed via runtime"

    state.agent.chat = fake_chat

    response = TestClient(app).post("/api/chat", json={"message": "design a patch"})

    assert response.status_code == 200
    assert captured.get("called") is True
    assert captured["message"] == "design a patch"
    assert response.json()["reply"] == "routed via runtime"


def test_chat_endpoint_passes_images_to_agent_chat():
    """chat 端点应把 images 透传给 agent.chat（多模态支持）。"""
    reset_app_state()
    app = create_app(dry_run=True)
    state = get_app_state(dry_run=True)

    captured = {}

    def fake_chat(message, images=None, skip_plan=False):
        captured["images"] = images
        state.agent.last_chat_status = {"ok": True, "error": "", "had_tool_failure": False, "mode": "llm_path"}
        return "ok"

    state.agent.chat = fake_chat

    TestClient(app).post("/api/chat", json={"message": "analyze this", "images": ["/tmp/a.png"]})
    assert captured["images"] == ["/tmp/a.png"]


def test_chat_stream_emits_error_when_agent_reports_failure():
    reset_app_state()
    app = create_app(dry_run=True)
    state = get_app_state(dry_run=True)

    def fake_chat(message, images=None, skip_plan=False):
        state.agent.last_chat_status = {
            "ok": False,
            "error": "OPENAI_API_KEY missing",
            "had_tool_failure": False,
            "mode": "llm_path",
        }
        return "OPENAI_API_KEY missing"

    state.agent.chat = fake_chat

    with TestClient(app).stream("POST", "/api/chat/stream", json={"message": "hello"}) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert '"type": "error"' in body
    assert "OPENAI_API_KEY missing" in body
    assert "data: [DONE]" in body


def test_chat_stream_does_not_pin_tool_events_to_previous_assistant(monkeypatch):
    reset_app_state()
    app = create_app(dry_run=True)
    state = get_app_state(dry_run=True)
    state.session.history.extend([
        {"role": "user", "content": "previous"},
        {"role": "assistant", "content": "previous reply"},
    ])

    def fake_chat(message, images=None, skip_plan=False):
        state.session.tool_events.append({
            "phase": "4_运行仿真",
            "tool_name": "run_solver",
            "success": True,
            "description": "solver done",
        })
        state.agent.last_chat_status = {"ok": True, "error": "", "had_tool_failure": False, "mode": "llm_path"}
        return ""

    state.agent.chat = fake_chat

    with TestClient(app).stream("POST", "/api/chat/stream", json={"message": "run solver"}) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert '"type": "tool_event"' in body
    assert "data: [DONE]" in body
    assert "_tool_events" not in state.session.history[1]


def test_read_farfield_endpoint_uses_result_service(monkeypatch):
    reset_app_state()
    app = create_app(dry_run=False)
    state = get_app_state(dry_run=False)
    state.cst.connected = True
    state.cst.offline_mode = False
    state.cst.project_path = "D:/demo/project.cst"

    called = {}

    def fake_read_farfield_result(reader, project_path, item_path=None, cut_type="phi", cut_value_deg=0.0, cst=None):
        called.update(
            {
                "reader": reader,
                "project_path": project_path,
                "item_path": item_path,
                "cut_type": cut_type,
                "cut_value_deg": cut_value_deg,
                "cst": cst,
            }
        )
        return {"success": True, "result_kind": "farfield_cut", "plot_data": [{"angle_deg": 0, "gain_dbi": 6.0}]}

    monkeypatch.setattr("cst_agent_workbench.results.service.read_farfield_result", fake_read_farfield_result)

    response = TestClient(app).post(
        "/api/cst/read-farfield",
        json={"item": "Farfields\\farfield (f=9.4) [1]", "cutType": "theta", "cutValueDeg": 90.0},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert called["project_path"] == "D:/demo/project.cst"
    assert called["item_path"] == "Farfields\\farfield (f=9.4) [1]"
    assert called["cut_type"] == "theta"
    assert called["cut_value_deg"] == 90.0
    assert called["cst"] is state.cst
    assert state.session.artifacts.last_farfield_results["result_kind"] == "farfield_cut"


def test_rag_import_endpoint_uses_pdf_ingest(monkeypatch):
    reset_app_state()
    app = create_app(dry_run=True)

    called = {}

    def fake_ingest_pdf_directory(folder, *, html_dir=None, reset=False, max_files=None):
        called["folder"] = folder
        called["html_dir"] = html_dir
        called["reset"] = reset
        called["max_files"] = max_files
        return {"files": 2, "chunks": 7, "skipped": 1}

    monkeypatch.setattr("cst_agent_workbench.rag.pdf_ingest.ingest_pdf_directory", fake_ingest_pdf_directory)

    response = TestClient(app).post("/api/rag/import", json={"folder": "D:/pdfs"})

    assert response.status_code == 200
    assert response.json() == {"ok": True, "files": 2, "chunks": 7, "skipped": 1}
    assert called["folder"] == "D:/pdfs"


def test_rag_query_endpoint_returns_structured_provenance(monkeypatch):
    reset_app_state()
    app = create_app(dry_run=True)
    hit = {
        "text": "Waveguide port documentation",
        "source_path": "mws/ports/waveguide.htm",
        "source_type": "html",
        "page": 1,
        "chunk_idx": 3,
        "score": 0.82,
    }
    monkeypatch.setattr(
        "cst_agent_workbench.rag.chroma_store.query_document_knowledge",
        lambda query, top_k=5, filter_topic=None: [hit],
    )
    monkeypatch.setattr(
        "cst_agent_workbench.rag.chroma_store.get_collection_stats",
        lambda: {"available": True, "healthy": True, "count": 10, "error": ""},
    )

    response = TestClient(app).post(
        "/api/rag/query",
        json={"query": "how to create a waveguide port", "topK": 3},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["hits"][0]["source_path"] == "mws/ports/waveguide.htm"


def test_serialize_chat_history_filters_intermediate_messages():
    history = [
        {"role": "user", "content": "design a 2.4GHz patch"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call_1", "function": {"name": "set_param"}}],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
        {"role": "assistant", "content": "Done. Patch created.", "_tool_events": [
            {"phase": "3", "tool": "set_param", "success": True, "description": "set freq=2.4"},
        ]},
        {"role": "user", "content": "now run solver"},
        {"role": "assistant", "content": "Solver finished."},
    ]
    result = _serialize_chat_history(history)
    assert [m["role"] for m in result] == ["user", "assistant", "user", "assistant"]
    assert result[1]["content"] == "Done. Patch created."
    assert result[1]["toolEvents"] == [
        {"phase": "3", "tool": "set_param", "success": True, "description": "set freq=2.4"},
    ]
    # Assistant with tool_calls dropped; tool result dropped.
    assert all("tool_calls" not in m for m in result)
    # No 'tool' role survives.
    assert all(m["role"] in {"user", "assistant"} for m in result)
    # Last final assistant has no toolEvents key when none were pinned.
    assert "toolEvents" not in result[3]


def test_serialize_chat_history_flattens_multimodal_user():
    history = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Compare this layout:"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA" + "B" * 4000}},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,CCCC"}},
                {"type": "text", "text": "with the previous one."},
            ],
        },
    ]
    result = _serialize_chat_history(history)
    assert len(result) == 1
    assert result[0]["role"] == "user"
    assert result[0]["content"] == "Compare this layout:\nwith the previous one."
    assert result[0]["images"] == 2
    # No base64 data should leak through.
    assert "base64" not in result[0]["content"]
    assert "image_url" not in result[0]


def test_serialize_chat_history_treats_empty_tool_calls_as_final():
    # Defensive: some intermediate entries may carry tool_calls=[] (falsy) -- treat as final.
    history = [
        {"role": "assistant", "content": "Final reply", "tool_calls": []},
    ]
    result = _serialize_chat_history(history)
    assert result == [{"role": "assistant", "content": "Final reply"}]


def test_chat_history_endpoint_returns_filtered(monkeypatch):
    reset_app_state()
    app = create_app(dry_run=True)
    state = get_app_state(dry_run=True)
    state.session.history.extend([
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
        {"role": "assistant", "content": "hi there", "_tool_events": [
            {"phase": "p", "tool": "t", "success": True, "description": "d"},
        ]},
    ])

    response = TestClient(app).get("/api/chat/history")
    assert response.status_code == 200
    body = response.json()
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert body["messages"][1]["content"] == "hi there"
    assert body["messages"][1]["toolEvents"] == [
        {"phase": "p", "tool": "t", "success": True, "description": "d"},
    ]


def test_state_clear_removes_plan_tool_cache_and_optimizer_artifacts():
    reset_app_state()
    app = create_app(dry_run=True)
    state = get_app_state(dry_run=True)
    state.session.active_plan = {"intent": {"kind": "chat_task"}, "status": "running"}
    state.session.artifacts.tool_results = {"tool-1": {"success": True}}
    state.session.artifacts.last_optimizer_result = {"success": True, "strategy": "old"}
    state.session.artifacts.results_invalidated = True
    state.session.artifacts.results_invalidated_reason = "old rollback"
    state.session.metadata["optimizer_memory_recall"] = [{"id": "old"}]
    state.session.metadata["optimizer_memory_impact"] = {"memory_enforced_by_validator": True}
    state.session.metadata["memory_scope"] = {"session_memory_path": "keep"}
    pending = state.session.tool_approvals.request(
        "execute_vba_script",
        {"vba_code": "Sub Pending()\nEnd Sub", "description": "pending"},
        actor="local-desktop-user",
    )
    state.session.tool_approvals.issue(
        "execute_vba_script",
        {"vba_code": "Sub Approved()\nEnd Sub", "description": "approved"},
        actor="local-desktop-user",
    )

    response = TestClient(app).post("/api/state/clear")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert state.session.active_plan is None
    assert state.session.artifacts.tool_results == {}
    assert state.session.artifacts.last_optimizer_result == {}
    assert state.session.artifacts.results_invalidated is False
    assert state.session.artifacts.results_invalidated_reason == ""
    assert "optimizer_memory_recall" not in state.session.metadata
    assert "optimizer_memory_impact" not in state.session.metadata
    assert state.session.metadata["memory_scope"] == {"session_memory_path": "keep"}
    assert state.session.tool_approvals.projection()["pending_count"] == 0
    assert state.session.tool_approvals.projection()["active_grant_count"] == 0
    assert TestClient(app).post(
        f"/api/approvals/{pending.request_id}/approve",
        json={"ttl_seconds": 120},
    ).status_code == 409


def test_copy_message_strips_private_keys_before_llm():
    """Regression: _tool_events on history must not leak into LLM request payload."""
    from cst_agent_workbench.agent.runtime import _copy_message

    msg = {
        "role": "assistant",
        "content": "hello",
        "_tool_events": [{"tool": "t", "success": True}],
    }
    copied = _copy_message(msg)
    assert "_tool_events" not in copied
    assert copied == {"role": "assistant", "content": "hello"}
    # Original is untouched.
    assert "_tool_events" in msg


# ---------------------------------------------------------------------------
# B4: 前后端字段名契约测试
# ---------------------------------------------------------------------------

# frontend/e2e/workflow.spec.ts 的 installApiMocks 里 snapshot 对象的字段名集合。
# 这是 Playwright e2e 全 mock 路由用的数据形状，如果后端字段名漂移，e2e 仍会绿（因为 mock 不打真后端）。
# 此测试把 e2e mock 的字段名与 build_dashboard_snapshot 真实输出做 diff，防漂移。
_E2E_MOCK_SNAPSHOT_FIELDS = {
    "generatedAt", "project", "execution", "optimization", "results", "model", "trace", "recentEvents",
}
_E2E_MOCK_PROJECT_FIELDS = {"connected", "offlineMode", "path"}
_E2E_MOCK_EXECUTION_FIELDS = {"agentBrain", "executionStrategy"}
_E2E_MOCK_OPTIMIZATION_FIELDS = {
    "active", "round", "bestRound", "bestMetricValue", "targetMode", "targetFreqGhz", "targetDb",
    "lastStrategy", "lastRolledBack", "lastRollbackReason", "lastMemoryRecallCount", "lastMemoryEnforced",
}
_E2E_MOCK_MODEL_FIELDS = {"objectCount", "portCount", "parameterCount"}
_E2E_MOCK_TRACE_FIELDS = {"status", "runId", "toolCalls", "failedToolCalls"}


def _field_keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value.keys())
    return set()


def test_e2e_mock_snapshot_matches_backend_field_names():
    """B4: 验证 Playwright e2e mock 的 snapshot 字段名与 build_dashboard_snapshot 真实输出一致。

    原 B4 问题：Playwright e2e 12 个路由全 mock，前后端字段名漂移时两边测试都是绿的。
    此测试用 TestClient 打真 build_dashboard_snapshot，把它的顶层/各子段字段名与 e2e mock
    的字段名集合做对比，任何一方新增/删除字段都会失败。
    """
    opt = OptimizationState()
    session = AgentSession(session_id="contract")
    session.bind_optimization_state(opt)
    session.artifacts.last_results = {"plot_data": [{"freq": 9.4, "s_db": -12.0}]}
    state = SimpleNamespace(
        cst=_FakeCST(),
        session=session,
        agent=_FakeAgent(session),
        opt_settings={"mode": "at_f0", "target_freq": 9.4, "target_db": -10.0},
    )

    real = build_dashboard_snapshot(state)

    # 顶层字段名集合必须完全一致（新增字段需同步更新 e2e mock）
    assert _field_keys(real) == _E2E_MOCK_SNAPSHOT_FIELDS, (
        f"顶层字段名漂移: real={_field_keys(real) - _E2E_MOCK_SNAPSHOT_FIELDS}, "
        f"mock={_E2E_MOCK_SNAPSHOT_FIELDS - _field_keys(real)}"
    )
    # 各子段：e2e mock 的字段名必须是真实输出的子集（后端可有多余字段，但 mock 不能引用不存在的字段）
    assert _E2E_MOCK_PROJECT_FIELDS <= _field_keys(real["project"]), (
        f"project 字段名漂移: mock_only={_E2E_MOCK_PROJECT_FIELDS - _field_keys(real['project'])}"
    )
    assert _E2E_MOCK_EXECUTION_FIELDS <= _field_keys(real["execution"]), (
        f"execution 字段名漂移: mock_only={_E2E_MOCK_EXECUTION_FIELDS - _field_keys(real['execution'])}"
    )
    assert _E2E_MOCK_OPTIMIZATION_FIELDS <= _field_keys(real["optimization"]), (
        f"optimization 字段名漂移: mock_only={_E2E_MOCK_OPTIMIZATION_FIELDS - _field_keys(real['optimization'])}"
    )
    assert _E2E_MOCK_MODEL_FIELDS <= _field_keys(real["model"]), (
        f"model 字段名漂移: mock_only={_E2E_MOCK_MODEL_FIELDS - _field_keys(real['model'])}"
    )
    assert _E2E_MOCK_TRACE_FIELDS <= _field_keys(real["trace"]), (
        f"trace 字段名漂移: mock_only={_E2E_MOCK_TRACE_FIELDS - _field_keys(real['trace'])}"
    )
