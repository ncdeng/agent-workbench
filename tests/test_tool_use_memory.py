from types import SimpleNamespace

from cst_agent_workbench.agent.runtime import absorb_round_result
from cst_agent_workbench.agent import tool_use_memory
from cst_agent_workbench.agent.tool_use_memory import (
    ToolUseMemoryRecord,
    ToolUseMemoryStore,
    build_tool_use_memory_guidance,
    load_tool_use_memory,
    rerank_safe_tools,
    save_tool_use_memory,
    should_write_tool_memory,
)


def test_ordinary_chat_summary_does_not_write_tool_memory():
    assert should_write_tool_memory(
        task_signature="chat_summary",
        selected_tools=("summarize_conversation",),
        success=True,
        is_chat_summary=True,
    ) is False

    store = ToolUseMemoryStore()
    stored = store.add(
        ToolUseMemoryRecord(
            task_signature="chat_summary",
            selected_tools=("summarize_conversation",),
            success=True,
            timestamp="2026-06-03T00:00:00Z",
        ),
        is_chat_summary=True,
    )

    assert stored is False
    assert store.records == []


def test_tool_use_memory_is_production_scoped_memory():
    notice = tool_use_memory.__doc__ or ""

    assert "Scoped procedural memory" in notice
    assert hasattr(tool_use_memory, "rerank_safe_tools")
    assert hasattr(tool_use_memory, "persist_session_tool_use_memory")


def test_failures_and_optimization_improvements_write_tool_memory():
    failure = ToolUseMemoryRecord(
        task_signature="read_s11_result",
        selected_tools=("open_results", "get_s_parameter"),
        success=False,
        failure_reason="get_s_parameter failed because the result tree was not opened",
        corrective_hint="Use open_results before get_s_parameter when S11 data is missing.",
        timestamp="2026-06-03T00:00:01Z",
        confidence=0.9,
    )
    improvement = ToolUseMemoryRecord(
        task_signature="optimize_patch_s11",
        selected_tools=("build_rectangular_patch_fast", "run_solver", "get_s_parameter"),
        success=True,
        corrective_hint="Prefer increasing feed_W when prior S11 matching improves after wider feed.",
        metric_delta=4.2,
        timestamp="2026-06-03T00:00:02Z",
        confidence=0.8,
    )

    assert should_write_tool_memory(
        task_signature=failure.task_signature,
        selected_tools=failure.selected_tools,
        success=failure.success,
        failure_reason=failure.failure_reason,
    ) is True
    assert should_write_tool_memory(
        task_signature=improvement.task_signature,
        selected_tools=improvement.selected_tools,
        success=improvement.success,
        metric_delta=improvement.metric_delta,
    ) is True

    store = ToolUseMemoryStore()
    assert store.add(failure) is True
    assert store.add(improvement) is True
    assert [record.task_signature for record in store.records] == ["read_s11_result", "optimize_patch_s11"]


def test_recall_returns_relevant_experience_by_signature_tool_and_text():
    store = ToolUseMemoryStore(
        [
            ToolUseMemoryRecord(
                task_signature="read_s11_result",
                selected_tools=("open_results", "get_s_parameter"),
                success=False,
                failure_reason="result tree closed before reading S11",
                corrective_hint="Call open_results before get_s_parameter.",
                timestamp="2026-06-03T00:00:01Z",
                confidence=0.9,
            ),
            ToolUseMemoryRecord(
                task_signature="optimize_patch_s11",
                selected_tools=("build_rectangular_patch_fast", "run_solver", "get_s_parameter"),
                success=True,
                corrective_hint="Increase feed_W when wider feed improved S11 matching.",
                metric_delta=3.5,
                timestamp="2026-06-03T00:00:02Z",
                confidence=0.8,
            ),
            ToolUseMemoryRecord(
                task_signature="farfield_export",
                selected_tools=("create_farfield_monitor", "run_solver"),
                success=False,
                failure_reason="farfield monitor missing",
                corrective_hint="Create a farfield monitor before exporting gain.",
                timestamp="2026-06-03T00:00:03Z",
                confidence=0.6,
            ),
        ]
    )

    by_signature = store.recall(task_signature="optimize_patch_s11", k=1)
    by_tool = store.recall(tools=("open_results",), k=1)
    by_text = store.recall(text="wider feed improved S11", k=1)

    assert by_signature[0].task_signature == "optimize_patch_s11"
    assert by_tool[0].task_signature == "read_s11_result"
    assert by_text[0].task_signature == "optimize_patch_s11"


def test_roundtrip_clamps_confidence_and_applies_limit():
    data = [
        {
            "task_signature": "old_solver_failure",
            "selected_tools": ["run_solver"],
            "success": False,
            "failure_reason": "missing port",
            "timestamp": "2026-06-03T00:00:01Z",
            "confidence": 2.0,
        },
        {
            "task_signature": "latest_s11_read",
            "selected_tools": ["get_s_parameter"],
            "success": True,
            "corrective_hint": "Open results before reading S11.",
            "timestamp": "2026-06-03T00:00:02Z",
            "confidence": -1.0,
        },
    ]

    store = ToolUseMemoryStore.from_list(data, limit=1)

    assert len(store.records) == 1
    assert store.records[0].task_signature == "latest_s11_read"
    assert store.records[0].confidence == 0.0
    assert store.to_list()[0]["selected_tools"] == ["get_s_parameter"]


def test_store_persists_and_loads_from_configured_path(tmp_path):
    path = tmp_path / "tool_use_memory.json"
    store = ToolUseMemoryStore([
        ToolUseMemoryRecord(
            task_signature="read_s11",
            selected_tools=("open_results",),
            success=True,
            corrective_hint="open result tree first",
            confidence=0.8,
        )
    ])

    assert save_tool_use_memory(store, str(path)) is True
    loaded = load_tool_use_memory(str(path))

    assert loaded.to_list() == store.to_list()


def test_recall_isolated_by_project_and_design_scope():
    store = ToolUseMemoryStore([
        ToolUseMemoryRecord(
            task_signature="optimize",
            selected_tools=("run_solver",),
            success=True,
            corrective_hint="project A hint",
            metadata={"project_scope": "project:a", "design_signature": "sig-a"},
        ),
        ToolUseMemoryRecord(
            task_signature="optimize",
            selected_tools=("get_s_parameter",),
            success=True,
            corrective_hint="project B hint",
            metadata={"project_scope": "project:b", "design_signature": "sig-b"},
        ),
    ])

    recalled = store.recall(
        task_signature="optimize",
        project_scope="project:a",
        design_signature="sig-a",
        k=5,
    )

    assert [record.corrective_hint for record in recalled] == ["project A hint"]


def test_rerank_changes_order_without_adding_or_removing_safe_tools():
    tools = [
        {"type": "function", "function": {"name": "get_s_parameter"}},
        {"type": "function", "function": {"name": "open_results"}},
    ]
    records = [
        ToolUseMemoryRecord(
            task_signature="read_s11",
            selected_tools=("open_results",),
            success=True,
            corrective_hint="open first",
            confidence=0.9,
        ),
        ToolUseMemoryRecord(
            task_signature="read_s11",
            selected_tools=("get_s_parameter",),
            success=False,
            failure_reason="tree closed",
            confidence=0.9,
        ),
    ]

    reranked = rerank_safe_tools(tools, records)

    assert [tool["function"]["name"] for tool in reranked] == ["open_results", "get_s_parameter"]
    assert {tool["function"]["name"] for tool in reranked} == {"open_results", "get_s_parameter"}


def test_guidance_exposes_recalled_failure_and_correction():
    store = ToolUseMemoryStore([
        ToolUseMemoryRecord(
            task_signature="read_s11",
            selected_tools=("get_s_parameter",),
            success=False,
            failure_reason="result tree closed",
            corrective_hint="call open_results first",
            confidence=0.9,
            metadata={"project_scope": "project:a", "design_signature": "sig-a"},
        )
    ])
    session = SimpleNamespace(
        tool_use_memory=store,
        metadata={
            "memory_scope": {"project_scope": "project:a"},
            "current_design_signature": "sig-a",
        },
    )

    guidance, records = build_tool_use_memory_guidance(
        session,
        "read s11",
        allowed_tool_names=("get_s_parameter",),
    )

    assert len(records) == 1
    assert "result tree closed" in guidance
    assert "call open_results first" in guidance


def test_write_policy_requires_task_signature_and_selected_tools():
    assert should_write_tool_memory(
        task_signature="",
        selected_tools=("run_solver",),
        success=False,
        failure_reason="solver failed",
    ) is False


def test_success_without_hint_or_metric_does_not_write():
    assert should_write_tool_memory(
        task_signature="read_s11_result",
        selected_tools=("get_s_parameter",),
        success=True,
    ) is False


class _FakeOptState:
    def __init__(self, *, improved: bool):
        self.best_metric_value = -8.0
        self.history = []
        self.round = 0
        self._improved = improved

    def record_round(self, check, param_snapshot, *, strategy="", proposal_reason="", backend=""):
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
        if self._improved:
            self.best_metric_value = check.get("min_s11")
        return self._improved


def test_absorb_round_result_writes_tool_use_memory_only_on_improvement():
    session = SimpleNamespace(active_plan=None, metadata={}, tool_use_memory=ToolUseMemoryStore())
    opt_state = _FakeOptState(improved=True)

    result = absorb_round_result(
        session=session,
        opt_state=opt_state,
        check={"min_s11": -10.0, "met": False},
        param_snapshot={"feed_W": 2.4},
        changed_params={"feed_W": 2.4},
        strategy="increase feed width",
        proposal_reason="wider feed improved S11",
        backend="build_rectangular_patch_fast",
        format_status=lambda *args: "status",
    )

    assert result["improved"] is True
    assert session.tool_use_memory.records[-1].task_signature == "optimization_round:universal"
    assert session.tool_use_memory.records[-1].selected_tools == ("build_rectangular_patch_fast",)
    assert session.tool_use_memory.records[-1].metric_delta == 2.0

    no_write_session = SimpleNamespace(active_plan=None, metadata={}, tool_use_memory=ToolUseMemoryStore())
    no_write_state = _FakeOptState(improved=False)
    absorb_round_result(
        session=no_write_session,
        opt_state=no_write_state,
        check={"min_s11": -7.0, "met": False},
        param_snapshot={"feed_W": 2.0},
        changed_params={"feed_W": 2.0},
        backend="build_rectangular_patch_fast",
        format_status=lambda *args: "status",
    )

    assert no_write_session.tool_use_memory.records == []
    assert should_write_tool_memory(
        task_signature="solver_failure",
        selected_tools=(),
        success=False,
        failure_reason="solver failed",
    ) is False
