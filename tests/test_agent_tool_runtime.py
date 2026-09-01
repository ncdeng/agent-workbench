import json
from types import SimpleNamespace

import pytest

from cst_agent_workbench.agent.fast_path import FastPathMixin
from cst_agent_workbench.agent.tool_runtime import execute_tool, do_execute_tool
from cst_agent_workbench.agent.tools import TOOLS
from cst_agent_workbench.cst.rectangular_patch_fast import (
    DEFAULT_PATCH_CONDUCTOR_THICKNESS_MM,
    DEFAULT_PATCH_EPSILON_R,
    DEFAULT_PATCH_LOSS_TANGENT,
    DEFAULT_PATCH_SUBSTRATE_NAME,
    DEFAULT_PATCH_SUBSTRATE_THICKNESS_MM,
    RectangularPatchRequest,
)


def test_tool_catalog_size_matches_documented_count():
    """The canonical catalog includes typed complex-geometry operations.

    注意：`grep -c '"name"' tools.py` 会数到嵌套 JSON-schema 属性里的 name 键
    （38 个），不是工具数——曾经据此得出"32 个工具"的错误结论。
    """
    assert len(TOOLS) == 42
    names = [tool["function"]["name"] for tool in TOOLS]
    assert len(set(names)) == len(names), "tool names must be unique for LLM selection"
    assert {
        "create_extruded_polygon",
        "transform_shape",
        "set_wcs",
        "create_frequency_field_monitor",
        "create_mesh_refinement",
        "add_solids_to_mesh_group",
        "set_global_hexahedral_mesh",
        "create_waveguide_port",
        "export_result_ascii",
        "boolean_add",
    }.issubset(names)


def test_execute_tool_rejects_invalid_arguments_before_side_effect_and_records_trace():
    agent = FakeAgent()

    result = json.loads(
        execute_tool(
            agent,
            "create_brick",
            {
                "name": "bad",
                "component": "component1",
                "material": "PEC",
                "xmin": "0",
                "xmax": "1",
                "ymin": "0",
                "ymax": "1",
                "zmin": "0",
                "zmax": "1",
                "unexpected": True,
            },
        )
    )

    assert result["success"] is False
    assert result["error_type"] == "invalid_tool_arguments"
    assert agent.cst.calls == []
    assert agent.tool_events[-1]["error_type"] == "invalid_tool_arguments"
    assert agent.tool_events[-1]["recovery_result"]["reason"] == "contract_violation"


def test_raw_vba_requires_parameter_bound_single_use_approval():
    from cst_agent_workbench.agent.tool_approval import DEFAULT_APPROVAL_ACTOR

    agent = FakeAgent()
    arguments = {"vba_code": "Sub Main()\nEnd Sub", "description": "approved smoke"}

    rejected = json.loads(execute_tool(agent, "execute_vba_script", arguments))

    assert rejected["success"] is False
    assert rejected["error_type"] == "approval_required"
    assert rejected["approval_request"]["tool_name"] == "execute_vba_script"
    assert agent.cst.calls == []
    assert agent.tool_events[-1]["recovery_result"]["reason"] == "approval_required"

    request_id = rejected["approval_request"]["request_id"]
    agent.session.tool_approvals.approve_request(
        request_id,
        actor=DEFAULT_APPROVAL_ACTOR,
    )
    approved = json.loads(execute_tool(agent, "execute_vba_script", arguments))

    assert approved["success"] is True
    assert agent.cst.calls[-1][0] == "execute_vba"
    assert agent.tool_events[-1]["approval"]["consumed"] is True

    replay = json.loads(execute_tool(agent, "execute_vba_script", arguments))
    changed = json.loads(execute_tool(agent, "execute_vba_script", {
        **arguments,
        "vba_code": "Sub Main()\nDebug.Print 1\nEnd Sub",
    }))
    assert replay["error_type"] == "approval_required"
    assert changed["error_type"] == "approval_required"


def test_raw_vba_schema_violation_precedes_approval_request():
    agent = FakeAgent()

    result = json.loads(execute_tool(agent, "execute_vba_script", {}))

    assert result["error_type"] == "invalid_tool_arguments"
    assert agent.session.tool_approvals.projection()["pending_count"] == 0


def test_raw_vba_allowlist_rejection_precedes_approval_request():
    agent = FakeAgent()
    agent.session.active_plan = {
        "current_step_id": "read",
        "steps": [{
            "step_id": "read",
            "kind": "read_result",
            "status": "in_progress",
            "allowed_tools": ["get_s_parameter"],
        }],
    }

    result = json.loads(execute_tool(
        agent,
        "execute_vba_script",
        {"vba_code": "Sub Main()\nEnd Sub", "description": "must not be requested"},
    ))

    assert result["error_type"] == "tool_not_allowed"
    assert agent.session.tool_approvals.projection()["pending_count"] == 0


def test_failed_approved_raw_vba_attempt_consumes_grant():
    from cst_agent_workbench.agent.tool_approval import DEFAULT_APPROVAL_ACTOR

    agent = FakeAgent()
    agent.cst.execute_vba = lambda *_args, **_kwargs: {
        "success": False,
        "message": "CST rejected the script",
        "executed": False,
    }
    arguments = {"vba_code": "Sub Main()\nEnd Sub", "description": "failing attempt"}
    initial = json.loads(execute_tool(agent, "execute_vba_script", arguments))
    agent.session.tool_approvals.approve_request(
        initial["approval_request"]["request_id"],
        actor=DEFAULT_APPROVAL_ACTOR,
    )

    failed = json.loads(execute_tool(agent, "execute_vba_script", arguments))
    replay = json.loads(execute_tool(agent, "execute_vba_script", arguments))

    assert failed["success"] is False
    assert agent.tool_events[-2]["approval"]["consumed"] is True
    assert replay["error_type"] == "approval_required"


def test_recovery_argument_change_requires_a_new_parameter_bound_approval():
    from cst_agent_workbench.agent.failure_recovery import RecoveryResult
    from cst_agent_workbench.agent.tool_approval import DEFAULT_APPROVAL_ACTOR

    class _RecoveryEngine:
        def attempt_recovery(self, _agent, _event):
            return RecoveryResult(
                action_name="repair_then_raw_vba",
                recovered=True,
                message="retry with repaired script",
                retry_tool="execute_vba_script",
                retry_arguments={
                    "vba_code": "Sub Main()\nDebug.Print 2\nEnd Sub",
                    "description": "repaired",
                },
            )

    agent = FakeAgent()
    agent.results.read_result = lambda _item_path, **_kwargs: {
        "success": False,
        "message": "read result failed",
    }
    agent._failure_recovery_engine = _RecoveryEngine()
    original_arguments = {
        "vba_code": "Sub Main()\nDebug.Print 1\nEnd Sub",
        "description": "original",
    }
    old_grant = agent.session.tool_approvals.issue(
        "execute_vba_script",
        original_arguments,
        actor=DEFAULT_APPROVAL_ACTOR,
    )

    result = json.loads(execute_tool(
        agent,
        "read_result",
        {"item_path": "1D Results\\Missing"},
    ))

    assert result["success"] is False
    assert result["recovery_result"]["reason"] == "retry_approval_required"
    assert result["approval_request"]["tool_name"] == "execute_vba_script"
    assert agent.tool_events[-1]["approval_request"] == result["approval_request"]
    assert agent.session.tool_approvals.grants[old_grant.approval_id].consumed is False
    assert agent.cst.calls == []


def test_complex_geometry_tools_are_reachable_in_geometry_plan_step():
    from cst_agent_workbench.agent.runtime import allowed_tool_names_for_active_step

    session = SimpleNamespace(
        active_plan={
            "current_step_id": "geometry",
            "steps": [{"step_id": "geometry", "kind": "geometry", "status": "in_progress"}],
        }
    )

    assert {
        "create_extruded_polygon",
        "transform_shape",
        "set_wcs",
    }.issubset(allowed_tool_names_for_active_step(session))


def test_allowlist_rejection_takes_precedence_over_argument_validation():
    agent = FakeAgent()
    agent.session.active_plan = {
        "current_step_id": "read",
        "steps": [
            {
                "step_id": "read",
                "kind": "read_result",
                "status": "in_progress",
                "allowed_tools": ["get_s_parameter"],
            }
        ],
    }

    result = json.loads(execute_tool(agent, "create_brick", "not-an-object"))

    assert result["error_type"] == "tool_not_allowed"
    assert agent.cst.calls == []


class FakeCST:
    def __init__(self):
        self.project_path = "D:/demo/project.cst"
        self.offline_mode = False
        self.connected = True
        self.export_calls = []
        self.calls = []

    def execute_vba(self, vba_code, label=None, timeout=None):
        self.calls.append(("execute_vba", label))
        return {"success": True, "message": "ok", "executed": True, "label": label}

    def new_project(self, project_path, timeout=120):
        self.calls.append(("new_project", project_path))
        self.project_path = project_path
        return {"success": True, "message": "created", "project_file": project_path}

    def run_solver(self, timeout=300):
        self.calls.append(("run_solver", timeout))
        return {"success": True, "message": "solver ok"}

    def set_mesh_by_frequency(self, f0_ghz, epsilon_r=1.0, steps=15, timeout=30, project_path=""):
        self.calls.append(("set_mesh_by_frequency", f0_ghz, epsilon_r, steps, project_path))
        return {"success": True, "message": "mesh ok"}

    def set_global_hexahedral_mesh(self, lines_per_wavelength=15, minimum_step_number=5, timeout=30, project_path=""):
        self.calls.append(("set_global_hexahedral_mesh", lines_per_wavelength, minimum_step_number, project_path))
        return {"success": True, "message": "mesh ok"}

    def get_mesh_signature(self, timeout=30):
        return {
            "success": False,
            "error_type": "unsupported_mesh_signature",
            "message": "no verified getter",
            "configured_mesh_is_not_signature": True,
        }

    def export_farfield_ascii(self, **kwargs):
        self.export_calls.append(kwargs)
        return {"success": True, "message": "export ok", "output_path": kwargs.get("output_path")}

    def export_result_ascii(self, item_path, output_path, timeout=120):
        from pathlib import Path

        self.export_calls.append({"item_path": item_path, "output_path": output_path, "timeout": timeout})
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("frequency s11\n9.4 -12\n", encoding="utf-8")
        return {"success": True, "message": "export ok", "output_path": output_path}

    def is_connected(self):
        return self.connected and not self.offline_mode

    def get_status(self):
        return "connected"


class FakeResults:
    def __init__(self):
        self.open_calls = []

    def open(self, project_path):
        self.open_calls.append(project_path)
        return {"success": True, "message": "opened"}

    def get_s_parameter(self, port_i, port_j, *, max_points=None):
        return {
            "success": True,
            "message": "s11",
            "item": f"S{port_i},{port_j}",
            "plot_data": [{"freq": 9.3, "s_db": -8.0}, {"freq": 9.4, "s_db": -12.0}],
        }

    def read_result(self, item_path, *, max_points=None):
        return {"success": True, "message": "read", "item": item_path, "plot_data": [{"freq": 1.0, "s_db": -3.0}]}


class FakeAgent:
    def __init__(self):
        from cst_agent_workbench.agent.tool_approval import ToolApprovalStore

        self.cst = FakeCST()
        self.results = FakeResults()
        self.last_results = {}
        self.last_vba = ""
        self.last_tool_message = ""
        self.tool_events = []
        self.last_chat_status = {"ok": True, "had_tool_failure": False}
        self.current_trace = {"turns": [{"tool_calls": []}]}
        self.trace_enabled = True
        self._active_tool_call_id = "tool-1"
        # fast_path._execute_dipole_request 会写 opt_state.target_freq
        self.opt_state = SimpleNamespace(target_freq=0.0)
        self.session = SimpleNamespace(
            artifacts=SimpleNamespace(tool_results={}),
            metadata={},
            tool_approvals=ToolApprovalStore(),
            memory=SimpleNamespace(
                decisions=SimpleNamespace(failure_reasons=[]),
                workspace=SimpleNamespace(last_results_summary={}),
            )
        )

    def _get_phase(self, tool_name):
        return f"phase:{tool_name}"

    def _resolve_rectangular_patch_request_from_tool_arguments(self, arguments):
        raise AssertionError("not used in these tests")

    def _run_rectangular_patch_fast_path_from_request(self, request, execution_mode="", allow_solver=False):
        raise AssertionError("not used in these tests")

    def _refresh_session_memory_from_runtime(self, persist: bool = False):
        item = self.last_results.get("item", "")
        self.session.memory.workspace.last_results_summary = {"item": item}

    def _remember_failure(self, reason: str):
        self.session.memory.decisions.failure_reasons.append(reason)


class DummyFastPathAgent(FastPathMixin):
    def __init__(self):
        self._patch_feed_strategy = "microstrip"
        self.last_patch_request = None


class DipoleRuntimeAgent(FastPathMixin, FakeAgent):
    def __init__(self):
        FakeAgent.__init__(self)
        self.client = None
        self._fast_path_counter = 0

    def _fast_path_project_dir(self):
        return "D:/cst_agent_rag_data/tmp/dipole-runtime-tests"

    def _try_install_farfield_export_template(self, _project_path):
        return {"success": True, "skipped": True}

    def _export_farfield_online_after_solver(self, _project_path, _fallback_item_path):
        return {"success": True, "skipped": True}


class FailingDipoleProjectAgent(DipoleRuntimeAgent):
    def __init__(self):
        super().__init__()
        self.cst.new_project = lambda *_args, **_kwargs: {
            "success": False,
            "message": "project transition failed",
        }


class RectangularPatchRuntimeAgent(DipoleRuntimeAgent):
    def __init__(self, *, feed_strategy="microstrip"):
        super().__init__()
        self._optimization_mode = False
        self._patch_feed_strategy = feed_strategy
        self.last_patch_request = None
        self.token_stats = {"prompt": 0, "completion": 0, "calls": 0}
        self.model = "test-model"

    _format_mm = staticmethod(lambda value: f"{float(value):.6g}")

    def _record_fast_path_event(self, *_args, **_kwargs):
        return None

    def _cleanup_old_fast_path_projects(self, keep_latest=None):
        return None


def test_canonical_dipole_builder_without_solver_only_builds_model():
    agent = DipoleRuntimeAgent()

    result = json.loads(execute_tool(agent, "build_dipole_fast", {"f0_ghz": 2.4}))
    full_result = agent.session.artifacts.tool_results[result["tool_event_id"]]

    assert result["success"] is True
    assert full_result["solver"]["skipped"] is True
    assert [call[0] for call in agent.cst.calls] == ["new_project", "execute_vba"]
    assert agent.last_results == {}


def test_canonical_dipole_builder_keeps_registry_when_new_project_fails():
    from cst_agent_workbench.cst import primitives

    primitives.reset_created_objects()
    primitives.register_object("existing", "body", "Copper (annealed)")
    agent = FailingDipoleProjectAgent()

    result = json.loads(execute_tool(agent, "build_dipole_fast", {"f0_ghz": 2.4}))

    assert result["success"] is False
    assert primitives._registry.objects == {"existing:body": "Copper (annealed)"}
    primitives.reset_created_objects()


def test_canonical_dipole_builder_requires_nonempty_s11_for_solver_success():
    agent = DipoleRuntimeAgent()

    result = json.loads(execute_tool(
        agent,
        "build_dipole_fast",
        {"f0_ghz": 9.4, "wire_or_plate": "plate", "run_solver": True},
    ))
    full_result = agent.session.artifacts.tool_results[result["tool_event_id"]]

    assert result["success"] is True
    assert full_result["preflight"]["success"] is True
    assert full_result["solver"]["success"] is True
    assert full_result["s11"]["success"] is True
    assert full_result["s11"]["plot_data"]
    assert agent.last_results["item"] == "S1,1"
    assert [call[0] for call in agent.cst.calls] == ["new_project", "execute_vba", "run_solver"]


def test_canonical_dipole_builder_fails_closed_when_solver_has_no_s11():
    agent = DipoleRuntimeAgent()
    agent.results.get_s_parameter = lambda *_args, **_kwargs: {
        "success": True,
        "message": "empty S11 tree item",
        "item": "S1,1",
        "plot_data": [],
    }

    result = json.loads(execute_tool(
        agent,
        "build_dipole_fast",
        {"f0_ghz": 2.4, "run_solver": True},
    ))
    full_result = agent.session.artifacts.tool_results[result["tool_event_id"]]

    assert result["success"] is False
    assert full_result["solver"]["success"] is True
    assert full_result["s11"]["plot_data"] == []
    assert agent.last_chat_status["had_tool_failure"] is True
    assert agent.last_results == {}


@pytest.mark.parametrize("feed_strategy", ["microstrip", "probe"])
def test_canonical_rectangular_patch_fails_closed_when_solver_has_no_s11(feed_strategy):
    agent = RectangularPatchRuntimeAgent(feed_strategy=feed_strategy)
    agent.results.get_s_parameter = lambda *_args, **_kwargs: {
        "success": True,
        "message": "empty S11 tree item",
        "item": "S1,1",
        "plot_data": [],
    }

    result = json.loads(execute_tool(
        agent,
        "build_rectangular_patch_fast",
        {"f0_ghz": 9.4, "feed_strategy": feed_strategy, "run_solver": True},
    ))

    assert result["success"] is False
    assert agent.last_chat_status["error"] == "empty S11 tree item"
    assert agent.last_chat_status["had_tool_failure"] is True
    assert agent.last_results == {}


def test_rectangular_patch_fast_tool_accepts_frequency_only_defaults():
    agent = DummyFastPathAgent()

    req = agent._resolve_rectangular_patch_request_from_tool_arguments({"f0_ghz": 9.4})

    assert req.f0_ghz == 9.4
    assert req.substrate_name == DEFAULT_PATCH_SUBSTRATE_NAME
    assert req.feed_strategy == "microstrip"


def test_rectangular_patch_fast_tool_handles_explicit_none_values():
    """Regression: LLM tool-call payloads may include explicit `null` for unspecified
    material parameters. `float(None)` used to crash; defaults must kick in instead."""
    agent = DummyFastPathAgent()

    req = agent._resolve_rectangular_patch_request_from_tool_arguments({
        "f0_ghz": 9.4,
        "substrate_name": None,
        "epsilon_r": None,
        "loss_tangent": None,
        "substrate_thickness_mm": None,
        "conductor_name": None,
        "conductor_thickness_mm": None,
    })

    assert req.f0_ghz == 9.4
    assert req.substrate_name == DEFAULT_PATCH_SUBSTRATE_NAME
    assert req.epsilon_r == DEFAULT_PATCH_EPSILON_R
    assert req.loss_tangent == DEFAULT_PATCH_LOSS_TANGENT
    assert req.substrate_thickness_mm == DEFAULT_PATCH_SUBSTRATE_THICKNESS_MM
    assert req.conductor_thickness_mm == DEFAULT_PATCH_CONDUCTOR_THICKNESS_MM


def _probe_patch_request() -> RectangularPatchRequest:
    return RectangularPatchRequest(
        f0_ghz=9.4,
        substrate_name="Rogers5880",
        epsilon_r=2.2,
        loss_tangent=0.0009,
        substrate_thickness_mm=0.508,
        conductor_name="Copper (annealed)",
        conductor_thickness_mm=0.035,
        feed_strategy="probe",
    )


def test_rectangular_patch_fast_tool_inherits_previous_feed_strategy():
    agent = DummyFastPathAgent()
    agent.last_patch_request = _probe_patch_request()

    req = agent._resolve_rectangular_patch_request_from_tool_arguments({
        "f0_ghz": 10.2,
        "inherit_previous_request": True,
    })

    assert req.f0_ghz == 10.2
    assert req.feed_strategy == "probe"
    assert req.substrate_name == "Rogers5880"
    assert req.substrate_thickness_mm == 0.508


def test_rectangular_patch_fast_tool_blank_feed_does_not_reset_inherited_probe():
    agent = DummyFastPathAgent()
    agent.last_patch_request = _probe_patch_request()

    req = agent._resolve_rectangular_patch_request_from_tool_arguments({
        "f0_ghz": 10.2,
        "inherit_previous_request": True,
        "feed_strategy": "",
    })

    assert req.feed_strategy == "probe"


def test_rectangular_patch_fast_tool_explicit_feed_overrides_previous():
    agent = DummyFastPathAgent()
    agent.last_patch_request = _probe_patch_request()

    req = agent._resolve_rectangular_patch_request_from_tool_arguments({
        "f0_ghz": 10.2,
        "inherit_previous_request": True,
        "feed_strategy": "microstrip",
    })

    assert req.feed_strategy == "microstrip"


_PATCH_HISTORY_TEXT = (
    "创建一个矩形微带贴片天线, 中心频率 9.4 GHz, 基板材料 Rogers5880, "
    "介电常数 2.2, 损耗角正切 0.0009, 基板厚度 1.6 mm, 铜厚 0.035 mm"
)


def test_followup_history_walk_ignores_generic_continue():
    """Regression: bare '继续' used to trigger history rescan and could resurrect an old
    patch request when the user was actually changing topics."""
    agent = DummyFastPathAgent()
    agent.history = [
        {"role": "user", "content": _PATCH_HISTORY_TEXT},
        {"role": "assistant", "content": "已经创建好了模型..."},
        {"role": "user", "content": "讲讲卷积神经网络"},
        {"role": "assistant", "content": "卷积神经网络是..."},
    ]

    result = agent._resolve_rectangular_patch_followup_from_history("继续讲讲池化层")
    assert result is None, "Bare '继续' must not retrigger patch fast path on unrelated topic"


def test_followup_history_walk_still_works_on_explicit_phrase():
    """Sanity: explicit '继续创建' should still find the most recent patch description."""
    agent = DummyFastPathAgent()
    agent.history = [
        {"role": "user", "content": _PATCH_HISTORY_TEXT},
        {"role": "assistant", "content": "需要确认尺寸吗？"},
    ]

    result = agent._resolve_rectangular_patch_followup_from_history("继续创建吧")
    assert result is not None
    assert abs(result.f0_ghz - 9.4) < 1e-9


def test_execute_tool_caches_get_s_parameter_and_records_event():
    agent = FakeAgent()

    result_text = execute_tool(agent, "get_s_parameter", {"port_i": 1, "port_j": 1})
    result = json.loads(result_text)

    assert result["success"] is True
    assert result["tool_event_id"] == "tool-1"
    assert result["metadata"]["plot_data_points"] == 2
    assert "plot_data" not in result
    assert agent.session.artifacts.tool_results["tool-1"]["plot_data"][0]["freq"] == 9.3
    assert agent.last_results["item"] == "S1,1"
    assert agent.last_tool_message == "读取 S1,1"
    assert agent.tool_events[-1]["success"] is True
    assert agent.tool_events[-1]["phase"] == "phase:get_s_parameter"
    assert not hasattr(agent.session, "tool_use_memory")


def test_execute_tool_treats_string_false_success_as_failure():
    agent = FakeAgent()

    def failed_s_parameter(port_i, port_j, **_kwargs):
        return {
            "success": "false",
            "message": "reader failed",
            "item": f"S{port_i},{port_j}",
            "plot_data": [{"freq": 9.4, "s_db": -3.0}],
        }

    agent.results.get_s_parameter = failed_s_parameter

    result_text = execute_tool(agent, "get_s_parameter", {"port_i": 1, "port_j": 1})
    result = json.loads(result_text)

    assert result["success"] is False
    assert result["message"] == "reader failed"
    assert agent.last_results == {}
    assert agent.tool_events[-1]["success"] is False
    assert agent.session.memory.decisions.failure_reasons[-1] == "get_s_parameter: reader failed"
    assert agent.session.tool_use_memory.records[-1].task_signature == "tool_failure:get_s_parameter"
    assert agent.session.tool_use_memory.records[-1].failure_reason == "reader failed"


def test_execute_tool_caches_read_result_and_records_event():
    agent = FakeAgent()

    result_text = execute_tool(agent, "read_result", {"item_path": "1D Results\\S-Parameters\\S1,1"})
    result = json.loads(result_text)

    assert result["success"] is True
    assert result["tool_event_id"] == "tool-1"
    assert result["metadata"]["plot_data_points"] == 1
    assert "plot_data" not in result
    assert agent.session.artifacts.tool_results["tool-1"]["plot_data"][0]["freq"] == 1.0
    assert agent.last_results["item"] == "1D Results\\S-Parameters\\S1,1"
    assert agent.tool_events[-1]["tool_name"] == "read_result"
    assert agent.session.memory.workspace.last_results_summary["item"] == "1D Results\\S-Parameters\\S1,1"


def test_execute_tool_recalls_full_tool_result_payload():
    agent = FakeAgent()
    result_text = execute_tool(agent, "get_s_parameter", {"port_i": 1, "port_j": 1})
    result = json.loads(result_text)

    recall_text = execute_tool(agent, "recall_tool_result", {"tool_event_id": result["tool_event_id"]})
    recalled = json.loads(recall_text)

    assert recalled["success"] is True
    assert recalled["payload"]["plot_data"][0]["freq"] == 9.3


@pytest.mark.windows_d_drive
def test_execute_tool_exports_result_through_canonical_runtime():
    from pathlib import Path

    agent = FakeAgent()
    target = Path("D:/cst_agent_rag_data/tmp/canonical-runtime-export.txt")
    target.unlink(missing_ok=True)
    try:
        result = json.loads(execute_tool(agent, "export_result_ascii", {
            "item_path": r"1D Results\S-Parameters\S1,1",
            "output_path": str(target),
        }))
        assert result["success"] is True
        event_id = result["full_payload_ref"].rsplit(".", 1)[-1]
        assert event_id in agent.session.artifacts.tool_results
        event = agent.tool_events[-1]
        assert event["tool_name"] == "export_result_ascii"
        assert result["metadata"]["result_kind"] == "artifact_ref"
        assert result["metadata"]["output_path"] == str(target)
    finally:
        target.unlink(missing_ok=True)


def test_execute_tool_remembers_failure_reason_when_tool_returns_error():
    agent = FakeAgent()

    result_text = execute_tool(agent, "unknown_tool", {})
    result = json.loads(result_text)

    assert result["success"] is False
    assert agent.session.memory.decisions.failure_reasons[-1].startswith("unknown_tool:")


def test_execute_rejects_tool_outside_step_allowlist():
    agent = FakeAgent()
    agent.session.active_plan = {
        "current_step_id": "read",
        "steps": [{"step_id": "read", "kind": "read_result", "status": "in_progress"}],
    }

    result = json.loads(do_execute_tool(agent, "boolean_add", {}))

    assert result["success"] is False
    assert result["error_type"] == "tool_not_allowed"
    assert "boolean_add" not in result["allowed_tools"]


def test_boolean_add_is_typed_and_executes_through_canonical_runtime():
    agent = FakeAgent()

    result = json.loads(
        execute_tool(
            agent,
            "boolean_add",
            {"obj1": "component1:main", "obj2": "component1:tab"},
        )
    )

    assert result["success"] is True
    assert agent.cst.calls[-1] == ("execute_vba", "boolean add")
    assert agent.last_vba == 'Solid.Add "component1:main", "component1:tab"'


def test_boolean_add_contract_rejects_malformed_reference_before_side_effect():
    agent = FakeAgent()

    result = json.loads(
        execute_tool(
            agent,
            "boolean_add",
            {"obj1": "component1:main", "obj2": "missing_component_separator"},
        )
    )

    assert result["success"] is False
    assert result["error_type"] == "invalid_tool_arguments"
    assert agent.cst.calls == []


def test_allowlist_evaluation_failure_is_fail_closed(monkeypatch):
    agent = FakeAgent()
    agent.session.metadata = {}

    def _raise(_session):
        raise RuntimeError("planner state unavailable")

    monkeypatch.setattr(
        "cst_agent_workbench.agent.runtime.allowed_tool_names_for_active_step",
        _raise,
    )

    result = json.loads(do_execute_tool(agent, "boolean_add", {}))

    assert result["success"] is False
    assert result["error_type"] == "allowlist_unavailable"
    assert agent.cst.calls == []
    degradation = agent.session.metadata["observability_degradations"][-1]
    assert degradation["component"] == "tool_allowlist"
    assert degradation["fallback"] == "fail_closed"


def _register_solver_ready_model(*, include_port=True, include_frequency=True, include_farfield=True):
    from cst_agent_workbench.cst.primitives import (
        register_farfield_monitor,
        register_frequency_range,
        register_object,
        register_port,
        reset_created_objects,
    )

    reset_created_objects()
    register_object("component", "patch", "PEC")
    if include_port:
        register_port(1, "50")
    if include_frequency:
        register_frequency_range("8", "10")
    if include_farfield:
        register_farfield_monitor("farfield (f=9)", "9", False)


def test_run_solver_auto_creates_farfield_monitor_before_solving():
    _register_solver_ready_model(include_farfield=False)
    agent = FakeAgent()

    result = json.loads(do_execute_tool(agent, "run_solver", {}))

    assert result["success"] is True
    assert agent.cst.calls[-2][0] == "execute_vba"
    assert agent.cst.calls[-2][1] == "auto_farfield_monitor_before_solver"
    assert agent.cst.calls[-1][0] == "run_solver"


def test_typed_monitor_and_mesh_tools_reach_single_runtime():
    agent = FakeAgent()

    monitor = json.loads(execute_tool(agent, "create_frequency_field_monitor", {
        "name": "efield (f=9.4)", "frequency": 9.4, "field_type": "Efield",
    }))
    refinement = json.loads(execute_tool(agent, "create_mesh_refinement", {
        "name": "feed_zone", "step_mm": 0.5,
    }))
    members = json.loads(execute_tool(agent, "add_solids_to_mesh_group", {
        "group_name": "feed_zone",
        "solids": [{"component": "Antenna", "name": "Feed"}],
    }))
    global_mesh = json.loads(execute_tool(agent, "set_global_hexahedral_mesh", {
        "lines_per_wavelength": 20, "minimum_step_number": 5,
    }))

    assert monitor["success"] is True
    assert refinement["success"] is True
    assert members["success"] is True
    assert global_mesh["verification"] == "history_accepted"
    assert agent.cst.calls[-1] == ("set_global_hexahedral_mesh", 20, 5, "")


def test_mesh_signature_fails_closed_when_realized_getter_is_unavailable():
    agent = FakeAgent()

    result = json.loads(execute_tool(agent, "get_mesh_signature", {}))
    full = agent.session.artifacts.tool_results[result["tool_event_id"]]

    assert result["success"] is False
    assert result["error_type"] == "unsupported_mesh_signature"
    assert full["configured_mesh_is_not_signature"] is True


def test_monitor_and_mesh_contracts_fail_before_cst_side_effects():
    agent = FakeAgent()

    for tool_name, arguments in [
        ("create_frequency_field_monitor", {"name": "e", "frequency": 9.4, "field_type": "Electric"}),
        ("create_mesh_refinement", {"name": "g", "step_mm": 0}),
        ("add_solids_to_mesh_group", {"group_name": "g", "solids": []}),
        ("set_global_hexahedral_mesh", {"lines_per_wavelength": 1}),
    ]:
        result = json.loads(execute_tool(agent, tool_name, arguments))
        assert result["error_type"] == "invalid_tool_arguments"

    assert agent.cst.calls == []


def test_waveguide_port_runtime_registers_only_after_cst_accepts():
    from cst_agent_workbench.cst.primitives import get_model_summary, reset_created_objects

    reset_created_objects()
    agent = FakeAgent()
    result = json.loads(execute_tool(agent, "create_waveguide_port", {
        "port_number": 3,
        "coordinate_mode": "Free",
        "orientation": "zmax",
        "number_of_modes": 2,
        "ranges": {"x": ["-1", "1"], "y": ["-1", "1"], "z": ["2", "2"]},
        "port_on_bound": False,
    }))

    assert result["success"] is True
    assert result["verification"] == "history_accepted"
    assert get_model_summary()["ports"][-1] == {
        "port_number": 3,
        "port_type": "waveguide",
        "number_of_modes": 2,
        "coordinate_mode": "Free",
    }


def test_waveguide_port_runtime_does_not_register_rejected_or_unexecuted_commands():
    from cst_agent_workbench.cst.primitives import get_model_summary, reset_created_objects

    for cst_result in [
        {"success": False, "message": "CST rejected", "executed": False},
        {"success": True, "message": "offline VBA only", "executed": False},
    ]:
        reset_created_objects()
        agent = FakeAgent()
        agent.cst.execute_vba = lambda *args, result=cst_result, **kwargs: dict(result)

        result = json.loads(execute_tool(agent, "create_waveguide_port", {
            "port_number": 3,
            "coordinate_mode": "Full",
            "orientation": "zmax",
        }))

        assert result.get("verification") != "history_accepted"
        assert result.get("success", False) is False or cst_result["executed"] is False
        assert get_model_summary()["ports"] == []


def test_waveguide_port_invalid_mode_combination_has_no_cst_side_effect():
    agent = FakeAgent()
    result = json.loads(execute_tool(agent, "create_waveguide_port", {
        "port_number": 1,
        "coordinate_mode": "Picks",
        "orientation": "zmin",
        "pick": {"solid": "A:B", "face_id": 1},
    }))

    assert result["success"] is False
    assert agent.cst.calls == []


def test_run_solver_preflight_blocks_missing_port():
    _register_solver_ready_model(include_port=False)
    agent = FakeAgent()

    result = json.loads(do_execute_tool(agent, "run_solver", {}))

    assert result["success"] is False
    assert "未创建端口" in result["message"]
    assert all(call[0] != "run_solver" for call in agent.cst.calls)


def test_run_solver_preflight_blocks_missing_frequency_range():
    _register_solver_ready_model(include_frequency=False)
    agent = FakeAgent()

    result = json.loads(do_execute_tool(agent, "run_solver", {}))

    assert result["success"] is False
    assert "频率" in result["message"]
    assert all(call[0] != "run_solver" for call in agent.cst.calls)


def test_run_solver_preflight_blocks_disconnected_cst():
    _register_solver_ready_model()
    agent = FakeAgent()
    agent.cst.connected = False

    result = json.loads(do_execute_tool(agent, "run_solver", {}))

    assert result["success"] is False
    assert "CST 未连接" in result["message"]
    assert all(call[0] != "run_solver" for call in agent.cst.calls)


def test_do_execute_tool_reports_unknown_tool():
    agent = FakeAgent()

    result = json.loads(do_execute_tool(agent, "unknown_tool", {}))

    assert result["success"] is False
    assert "未知工具" in result["message"]
    assert agent.last_tool_message == "未知工具：unknown_tool"
