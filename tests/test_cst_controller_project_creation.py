from __future__ import annotations

from cst_agent_workbench.cst.controller import CSTController
from cst_agent_workbench.cst import controller as controller_module


def _disconnected_controller() -> CSTController:
    controller = CSTController.__new__(CSTController)
    controller.connected = False
    controller.offline_mode = True
    controller.project_path = ""
    controller.last_message = ""
    return controller


def test_new_project_can_bootstrap_connection_when_saved_to_explicit_path(monkeypatch):
    controller = _disconnected_controller()
    calls = []

    def run_script(arguments, timeout):
        calls.append((arguments, timeout))
        return {
            "success": True,
            "message": "project created",
            "project_file": "D:/cst_agent_rag_data/projects/generated.cst",
        }

    monkeypatch.setattr(controller, "_run_com_script", run_script)

    result = controller.new_project(
        "D:/cst_agent_rag_data/projects/generated.cst",
        timeout=120,
    )

    assert result["success"] is True
    assert calls == [
        (["new_project", "D:/cst_agent_rag_data/projects/generated.cst"], 120)
    ]
    assert controller.connected is True
    assert controller.offline_mode is False
    assert controller.project_path == "D:/cst_agent_rag_data/projects/generated.cst"


def test_new_project_refuses_unsaved_bootstrap_that_could_fall_back_to_temp(monkeypatch):
    controller = _disconnected_controller()
    called = False

    def run_script(arguments, timeout):
        nonlocal called
        called = True
        return {"success": True}

    monkeypatch.setattr(controller, "_run_com_script", run_script)

    result = controller.new_project()

    assert result["success"] is False
    assert "明确保存路径" in result["message"]
    assert called is False
    assert controller.connected is False
    assert controller.offline_mode is True


def test_new_project_keeps_offline_state_when_bootstrap_fails(monkeypatch):
    controller = _disconnected_controller()
    monkeypatch.setattr(
        controller,
        "_run_com_script",
        lambda arguments, timeout: {"success": False, "message": "license unavailable"},
    )

    result = controller.new_project("D:/cst_agent_rag_data/projects/generated.cst")

    assert result["success"] is False
    assert controller.connected is False
    assert controller.offline_mode is True
    assert controller.project_path == ""


def test_execute_command_persists_history_before_reporting_success():
    script = controller_module._COM_SCRIPT

    execute_branch = script.split('elif command == "execute":', 1)[1].split(
        'elif command == "execute_immediate":', 1
    )[0]

    assert "mws.add_to_history(label, vba_content)" in execute_branch
    assert "proj.save(project_path)" in execute_branch
    assert '"project_saved": True' in execute_branch


def test_close_failure_keeps_current_project_path(monkeypatch):
    controller = _disconnected_controller()
    controller.connected = True
    controller.offline_mode = False
    controller.project_path = "D:/projects/current.cst"
    monkeypatch.setattr(
        controller,
        "_run_com_script",
        lambda arguments, timeout: {"success": False, "message": "dialog blocked"},
    )

    result = controller.close_project(controller.project_path)

    assert result["success"] is False
    assert controller.project_path == "D:/projects/current.cst"


def test_official_project_save_signature_is_used_by_static_script():
    assert 'proj.save("", include_results, False)' in controller_module._COM_SCRIPT
    assert 'proj.save(result_path, include_results, False)' in controller_module._COM_SCRIPT
