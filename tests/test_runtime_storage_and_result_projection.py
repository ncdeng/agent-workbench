from pathlib import Path
from types import SimpleNamespace

import pytest

from cst_agent_workbench import config
from cst_agent_workbench.agent.agent import CSTAgent
from cst_agent_workbench.agent.runtime_state import build_runtime_snapshot
from cst_agent_workbench.agent.session import AgentSession
from cst_agent_workbench.cst import controller as controller_mod
from cst_agent_workbench.cst.controller import CSTController
from cst_agent_workbench.cst.primitives import register_parameter
from cst_agent_workbench.optimization.state import OptimizationState
from cst_agent_workbench.web_api import _summarize_results, build_dashboard_snapshot
from fakes import FakeCSTController


pytestmark = pytest.mark.usefixtures("reset_primitives")


class _ConnectedCST:
    project_path = r"D:\models\demo.cst"
    offline_mode = False

    def is_connected(self):
        return True


def _dashboard_state(*, target_freq=0.0, mode="at_f0", chat_mode="fast_path", brain="native"):
    opt = OptimizationState()
    opt.target_mode = mode
    opt.target_freq = target_freq
    session = AgentSession(session_id="projection")
    session.bind_optimization_state(opt)
    session.metadata["agent_brain"] = brain
    agent = SimpleNamespace(
        session=session,
        opt_state=opt,
        last_chat_status={"mode": chat_mode},
        last_execution_mode=chat_mode,
    )
    return SimpleNamespace(
        cst=_ConnectedCST(),
        session=session,
        agent=agent,
        opt_settings={"mode": mode, "target_freq": target_freq, "target_db": -10.0},
    )


def test_fast_path_runtime_root_defaults_to_d_drive():
    assert Path(config.CST_RUNTIME_ROOT).drive.upper() == "D:"
    assert Path(config.CST_FAST_PATH_DIR).drive.upper() == "D:"
    assert Path(config.CST_TEMP_DIR).drive.upper() == "D:"


def test_runtime_snapshot_uses_configured_fast_path_root(monkeypatch, tmp_path):
    root = tmp_path / "runtime" / "projects" / "fast_path"
    project = root / "fast_patch_1.cst"
    monkeypatch.setattr(config, "CST_FAST_PATH_DIR", str(root))
    cst = FakeCSTController(project_path=str(project), connected=True, offline_mode=False)
    agent = CSTAgent(cst)

    snapshot = build_runtime_snapshot(agent)

    assert snapshot["project"]["is_temp_fast_path_project"] is True


def test_dashboard_curve_downsampling_preserves_frequency_ends_and_global_notch():
    points = [
        {"freq": 7.0 + index * 0.005, "s_db": -2.0}
        for index in range(1001)
    ]
    points[100]["s_db"] = -31.0

    projected = _summarize_results({"plot_data": points}, 9.4)

    assert projected["points"] == 1001
    assert len(projected["plotData"]) == 500
    assert projected["plotData"][0]["freq"] == points[0]["freq"]
    assert projected["plotData"][-1]["freq"] == points[-1]["freq"]
    assert points[100] in projected["plotData"]
    assert projected["minS11Db"] == -31.0


def test_dashboard_resolves_unset_target_from_model_f0():
    register_parameter("f0", "9.4")
    state = _dashboard_state(target_freq=0.0)
    state.session.artifacts.last_results = {
        "plot_data": [
            {"freq": 9.3, "s_db": -8.0},
            {"freq": 9.4, "s_db": -15.0},
            {"freq": 9.5, "s_db": -7.0},
        ]
    }

    snapshot = build_dashboard_snapshot(state)

    assert snapshot["optimization"]["targetFreqGhz"] == 9.4
    assert snapshot["results"]["targetS11Db"] == -15.0


@pytest.mark.parametrize(
    ("brain", "mode", "strategy"),
    [
        ("native", "fast_path", "fast_path"),
        ("native", "llm_path", "native_loop"),
        ("pi", "pi_harness", "pi_harness"),
    ],
)
def test_dashboard_projects_brain_and_execution_strategy(brain, mode, strategy):
    snapshot = build_dashboard_snapshot(_dashboard_state(chat_mode=mode, brain=brain))

    assert snapshot["execution"] == {
        "agentBrain": brain,
        "executionStrategy": strategy,
    }


def test_fast_path_farfield_export_is_promoted_to_session_cut(monkeypatch, tmp_path):
    cst = FakeCSTController(project_path=str(tmp_path / "fast_patch.cst"))
    cst.queue_response(
        "list_farfield_tree_items",
        {
            "success": True,
            "items": [
                r"Farfields\farfield (f=8.0)",
                r"Farfields\farfield (f=9.4)",
            ],
        },
    )
    agent = CSTAgent(cst)
    expected = {
        "success": True,
        "result_kind": "farfield_cut",
        "plot_data": [
            {"angle_deg": 0.0, "gain_dbi": 7.0},
            {"angle_deg": 90.0, "gain_dbi": -2.0},
        ],
    }
    monkeypatch.setattr(
        "cst_agent_workbench.results.service._build_exported_farfield_result",
        lambda item_path, cut_type, cut_value_deg, export_result: dict(expected),
    )

    result = agent._export_farfield_online_after_solver(
        cst.project_path,
        r"Farfields\farfield (f=9.4)",
    )

    assert result == expected
    assert agent.last_farfield_results == expected
    assert cst.calls["export_farfield_ascii"][0]["item_path"] == r"Farfields\farfield (f=9.4)"
    assert agent.tool_events[-1]["tool_name"] == "read_farfield_cut"
    assert agent.tool_events[-1]["success"] is True


def test_probe_farfield_vba_uses_configured_d_drive_temp(monkeypatch, tmp_path):
    temp_root = tmp_path / "bridge"
    output = tmp_path / "farfield-tree.txt"
    controller = CSTController()
    controller.connected = True
    controller.offline_mode = False
    controller.project_path = str(tmp_path / "model.cst")
    captured = {}
    monkeypatch.setattr(controller_mod.config, "CST_TEMP_DIR", str(temp_root))
    monkeypatch.setattr(controller, "_query_best_effort_project_path", lambda: "")
    monkeypatch.setattr(controller, "_resolve_project_for_command", lambda: controller.project_path)

    def fake_run(args, timeout):
        captured["vba_file"] = args[2]
        return {"success": True, "message": "ok", "project_file": controller.project_path}

    monkeypatch.setattr(controller, "_run_com_script", fake_run)

    result = controller.probe_farfield_tree_items(str(output))

    assert result["success"] is True
    assert Path(captured["vba_file"]).parent == temp_root
    assert Path(captured["vba_file"]).drive.upper() == "D:"
