from __future__ import annotations

from pathlib import Path

from cst_agent_workbench.cst import controller as controller_mod


def _make_controller():
    controller = controller_mod.CSTController.__new__(controller_mod.CSTController)
    controller.connected = True
    controller.offline_mode = False
    controller.project_path = "D:/projects/solved.cst"
    controller.last_message = ""
    return controller


def test_export_result_ascii_uses_official_selection_sequence(monkeypatch):
    target = Path("D:/cst_agent_rag_data/tmp/controller-result-export.txt")
    target.unlink(missing_ok=True)
    observed = {}
    controller = _make_controller()
    monkeypatch.setattr(controller_mod.config, "CST_TEMP_DIR", r"D:\cst_agent_rag_data\tmp\controller-test")
    monkeypatch.setattr(controller, "_resolve_project_for_command", lambda: controller.project_path)
    monkeypatch.setattr(controller, "_update_project_path_from_result", lambda *_args: None)

    def fake_run(args, timeout):
        observed["args"] = args
        observed["timeout"] = timeout
        observed["vba"] = Path(args[2]).read_text(encoding="utf-8")
        target.write_text("x y\n1 2\n", encoding="utf-8")
        return {"success": True, "message": "ok"}

    monkeypatch.setattr(controller, "_run_com_script", fake_run)
    try:
        result = controller.export_result_ascii(
            r"1D Results\S-Parameters\S1,1",
            str(target),
            timeout=33,
        )
        assert result["success"] is True
        assert observed["args"][0] == "export_result_ascii"
        assert observed["timeout"] == 33
        assert 'SelectTreeItem("1D Results\\S-Parameters\\S1,1")' in observed["vba"]
        assert "With ASCIIExport" in observed["vba"]
        assert ".Reset" in observed["vba"]
        assert f'.FileName "{target}"' in observed["vba"]
        assert ".Execute" in observed["vba"]
        assert Path(observed["args"][2]).drive.upper() == "D:"
    finally:
        target.unlink(missing_ok=True)
