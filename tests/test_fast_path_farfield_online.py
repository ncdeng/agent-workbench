import pytest

from cst_agent_workbench.agent.agent import CSTAgent
from cst_agent_workbench.cst import patch_fast_executor
from cst_agent_workbench.cst.rectangular_patch_fast import RectangularPatchRequest
from fakes import FakeCSTController


pytestmark = pytest.mark.usefixtures("reset_primitives")


def _disable_farfield_template(monkeypatch, tmp_path):
    monkeypatch.setattr(patch_fast_executor.config, "FARFIELD_TEMPLATE_R0D_SOURCE", "")
    monkeypatch.setattr(patch_fast_executor, "_FARFIELD_TEMPLATE_R0D_CONTENT", None)
    monkeypatch.setattr(
        patch_fast_executor,
        "_repo_local_farfield_template_r0d",
        lambda: str(tmp_path / "missing-farfield-template.r0d"),
    )



def test_rectangular_fast_path_builds_without_running_solver_by_default(monkeypatch, tmp_path):
    cst = FakeCSTController(project_path=str(tmp_path / "initial.cst"))
    agent = CSTAgent(cst)
    agent.client = None
    agent._fast_path_project_dir = lambda: str(tmp_path)
    agent._cleanup_old_fast_path_projects = lambda keep_latest=None: None
    agent._collect_s11_summary = lambda target_freq_ghz: {"success": False, "message": "skip results reader"}

    _disable_farfield_template(monkeypatch, tmp_path)

    message = agent._run_rectangular_patch_fast_path_from_request(
        RectangularPatchRequest(
            f0_ghz=9.4,
            substrate_name="Rogers5880",
            epsilon_r=2.2,
            loss_tangent=0.0009,
            substrate_thickness_mm=1.6,
            conductor_name="Copper (annealed)",
            conductor_thickness_mm=0.035,
            feed_strategy="microstrip",
        )
    )

    assert "已跳过" in message
    assert agent.last_chat_status["ok"] is True
    assert len(cst.calls["run_solver"]) == 0
    assert len(cst.calls["run_solver_with_templates"]) == 0
    assert len(cst.calls["list_farfield_tree_items"]) == 0
    assert len(cst.calls["export_farfield_ascii"]) == 0
    port_calls = [call for call in cst.calls["execute_vba"] if call["label"].startswith("fast_patch_port_")]
    assert port_calls
    assert '.Coordinates "Picks"' in port_calls[0]["vba_code"]
    assert "Pick.PickFaceFromPoint" in port_calls[0]["vba_code"]


def test_rectangular_fast_path_runs_solver_but_fails_closed_when_s11_readback_fails(monkeypatch, tmp_path):
    cst = FakeCSTController(project_path=str(tmp_path / "initial.cst"))
    cst.queue_response(
        "list_farfield_tree_items",
        {
            "success": True,
            "message": "found farfield",
            "items": [r"Farfields\farfield (f=9.4)"],
            "project_file": str(tmp_path / "fast_patch.cst"),
        },
    )
    agent = CSTAgent(cst)
    agent.client = None
    agent._fast_path_project_dir = lambda: str(tmp_path)
    agent._cleanup_old_fast_path_projects = lambda keep_latest=None: None
    agent._collect_s11_summary = lambda target_freq_ghz: {"success": False, "message": "skip results reader"}

    _disable_farfield_template(monkeypatch, tmp_path)

    message = agent._run_rectangular_patch_fast_path_from_request(
        RectangularPatchRequest(
            f0_ghz=9.4,
            substrate_name="Rogers5880",
            epsilon_r=2.2,
            loss_tangent=0.0009,
            substrate_thickness_mm=1.6,
            conductor_name="Copper (annealed)",
            conductor_thickness_mm=0.035,
            feed_strategy="microstrip",
        ),
        allow_solver=True,
    )

    assert "远场导出模板安装阶段失败" not in message
    assert agent.last_chat_status["ok"] is False
    assert agent.last_chat_status["had_tool_failure"] is True
    assert agent.last_chat_status["error"] == "skip results reader"
    assert len(cst.calls["run_solver"]) == 1
    assert len(cst.calls["run_solver_with_templates"]) == 0
    assert len(cst.calls["list_farfield_tree_items"]) == 1
    assert len(cst.calls["export_farfield_ascii"]) == 1
    assert cst.calls["export_farfield_ascii"][0]["item_path"] == r"Farfields\farfield (f=9.4)"



def test_probe_rectangular_fast_path_uses_discrete_port(monkeypatch, tmp_path):
    cst = FakeCSTController(project_path=str(tmp_path / "initial.cst"))
    agent = CSTAgent(cst)
    agent.client = None
    agent._fast_path_project_dir = lambda: str(tmp_path)
    agent._cleanup_old_fast_path_projects = lambda keep_latest=None: None
    agent._collect_s11_summary = lambda target_freq_ghz: {"success": False, "message": "skip results reader"}
    _disable_farfield_template(monkeypatch, tmp_path)

    message = agent._run_rectangular_patch_fast_path_from_request(
        RectangularPatchRequest(
            f0_ghz=9.4,
            substrate_name="Rogers5880",
            epsilon_r=2.2,
            loss_tangent=0.0009,
            substrate_thickness_mm=1.6,
            conductor_name="Copper (annealed)",
            conductor_thickness_mm=0.035,
            feed_strategy="probe",
        )
    )

    assert agent.last_chat_status["ok"] is True
    assert "Probe feed" in message
    assert "discrete 50Ω probe port" in message
    port_calls = [call for call in cst.calls["execute_vba"] if call["label"].startswith("fast_patch_port_")]
    assert port_calls
    assert "With DiscretePort" in port_calls[0]["vba_code"]
    assert "MS_WG_Port_1" not in port_calls[0]["vba_code"]
    assert len(cst.calls["run_solver"]) == 0
