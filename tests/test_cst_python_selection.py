from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from cst_agent_workbench.cst import controller as controller_mod


def test_resolve_cst_python_command_uses_supported_current_python(monkeypatch):
    monkeypatch.setattr(controller_mod.config, "CST_PYTHON_EXECUTABLE", "")
    monkeypatch.setattr(controller_mod.sys, "version_info", SimpleNamespace(major=3, minor=12))
    monkeypatch.setattr(controller_mod.sys, "executable", "current-python")

    assert controller_mod._resolve_cst_python_command() == ["current-python"]


def test_resolve_cst_python_command_prefers_configured_executable(monkeypatch):
    monkeypatch.setattr(controller_mod.config, "CST_PYTHON_EXECUTABLE", r"C:\Python312\python.exe")
    monkeypatch.setattr(controller_mod.sys, "version_info", SimpleNamespace(major=3, minor=14))

    assert controller_mod._resolve_cst_python_command() == [r"C:\Python312\python.exe"]


def test_resolve_cst_python_command_probes_launcher_when_current_is_unsupported(monkeypatch):
    probed = []

    def fake_candidate_supports(command):
        probed.append(command)
        return command == ["py", "-3.12"]

    monkeypatch.setattr(controller_mod.config, "CST_PYTHON_EXECUTABLE", "")
    monkeypatch.setattr(controller_mod.sys, "version_info", SimpleNamespace(major=3, minor=14))
    monkeypatch.setattr(controller_mod, "_cst_python_candidates", lambda: [["py", "-3.11"], ["py", "-3.12"]])
    monkeypatch.setattr(controller_mod, "_candidate_supports_cst_python", fake_candidate_supports)

    assert controller_mod._resolve_cst_python_command() == ["py", "-3.12"]
    assert probed == [["py", "-3.11"], ["py", "-3.12"]]


def test_resolve_cst_python_command_returns_empty_when_no_candidate_works(monkeypatch):
    monkeypatch.setattr(controller_mod.config, "CST_PYTHON_EXECUTABLE", "")
    monkeypatch.setattr(controller_mod.sys, "version_info", SimpleNamespace(major=3, minor=14))
    monkeypatch.setattr(controller_mod, "_cst_python_candidates", lambda: [["py", "-3.12"]])
    monkeypatch.setattr(controller_mod, "_candidate_supports_cst_python", lambda command: False)

    assert controller_mod._resolve_cst_python_command() == []


def test_run_com_script_uses_resolved_cst_python_command(monkeypatch):
    calls = []

    class Completed:
        returncode = 0
        stdout = 'warning before json\n{"success": true, "message": "ok"}\n'
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Completed()

    controller = controller_mod.CSTController.__new__(controller_mod.CSTController)
    controller.cst_python_command = ["py", "-3.12"]
    monkeypatch.setattr(controller_mod.config, "CST_TEMP_DIR", r"D:\cst_agent_rag_data\tmp\controller-test")
    monkeypatch.setattr(controller_mod.subprocess, "run", fake_run)

    result = controller._run_com_script(["connect", ""], timeout=7)

    assert result == {"success": True, "message": "ok"}
    command, kwargs = calls[0]
    assert command[:2] == ["py", "-3.12"]
    assert command[3:] == ["connect", ""]
    assert kwargs["timeout"] == 7
    assert Path(command[2]).drive.upper() == "D:"
    assert Path(kwargs["env"]["TEMP"]).drive.upper() == "D:"


def test_run_com_script_reports_missing_compatible_python(monkeypatch):
    def fail_run(*args, **kwargs):
        raise AssertionError("subprocess.run should not be called")

    controller = controller_mod.CSTController.__new__(controller_mod.CSTController)
    controller.cst_python_command = []
    monkeypatch.setattr(controller_mod.subprocess, "run", fail_run)

    result = controller._run_com_script(["connect", ""], timeout=7)

    assert result["success"] is False
    assert "Python 3.8-3.12" in result["message"]
