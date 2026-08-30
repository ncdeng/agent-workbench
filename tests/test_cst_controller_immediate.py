from __future__ import annotations

from pathlib import Path

from cst_agent_workbench.cst.controller import CSTController, _COM_SCRIPT
from cst_agent_workbench.cst import controller as controller_mod


def _controller() -> CSTController:
    controller = CSTController.__new__(CSTController)
    controller.connected = True
    controller.offline_mode = False
    controller.last_message = ""
    controller.project_path = "D:/fixture/antenna.cst"
    return controller


def test_static_cst_subprocess_script_compiles():
    compile(_COM_SCRIPT, "<cst-controller-subprocess>", "exec")


def test_execute_vba_immediate_uses_distinct_nonhistory_command(monkeypatch):
    controller = _controller()
    calls = []
    monkeypatch.setattr(controller_mod.config, "CST_TEMP_DIR", r"D:\cst_agent_rag_data\tmp\controller-immediate")
    monkeypatch.setattr(controller, "_query_best_effort_project_path", lambda: "")
    monkeypatch.setattr(
        controller,
        "_run_com_script",
        lambda args, timeout: calls.append((args, timeout))
        or {"success": True, "project_file": controller.project_path},
    )

    result = controller.execute_vba_immediate("With Optimizer\n.Start\nEnd With", 900)

    assert result["success"] is True
    assert result["executed"] is True
    assert "verification" not in result
    assert calls[0][0][0] == "execute_immediate"
    assert calls[0][0][2].upper().startswith("D:")
    assert calls[0][1] == 900


def test_execute_vba_marks_successful_history_execution(monkeypatch):
    controller = _controller()
    calls = []
    monkeypatch.setattr(controller_mod.config, "CST_TEMP_DIR", r"D:\cst_agent_rag_data\tmp\controller-history")
    monkeypatch.setattr(controller, "_query_best_effort_project_path", lambda: "")
    monkeypatch.setattr(
        controller,
        "_run_com_script",
        lambda args, timeout: calls.append((args, timeout))
        or {"success": True, "project_file": controller.project_path},
    )
    monkeypatch.setattr(controller, "_update_project_path_from_result", lambda *_args: None)

    result = controller.execute_vba("With Brick\n.Reset\nEnd With", label="brick", timeout=45)

    assert result["executed"] is True
    assert result["verification"] == "history_accepted"
    assert calls[0][0][0] == "execute"
    assert Path(calls[0][0][2]).drive.upper() == "D:"
    assert calls[0][1] == 45


def test_execute_vba_immediate_never_reports_offline_execution():
    controller = _controller()
    controller.offline_mode = True

    result = controller.execute_vba_immediate("With Optimizer\n.Start\nEnd With")

    assert result["success"] is False
    assert result["executed"] is False
