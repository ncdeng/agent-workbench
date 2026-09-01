from __future__ import annotations

from pathlib import Path

import pytest

from cst_agent_workbench.cst import controller as controller_mod

pytestmark = pytest.mark.windows_d_drive


def _make_controller() -> controller_mod.CSTController:
    controller = controller_mod.CSTController.__new__(controller_mod.CSTController)
    controller.connected = True
    controller.offline_mode = False
    controller.project_path = "D:/projects/geometry.cst"
    controller.last_message = ""
    return controller


@pytest.mark.parametrize(
    ("offline_mode", "connected"),
    [(True, True), (False, False)],
)
def test_list_solids_rejects_offline_without_running_com(
    monkeypatch,
    offline_mode,
    connected,
):
    controller = _make_controller()
    controller.offline_mode = offline_mode
    controller.connected = connected
    monkeypatch.setattr(
        controller,
        "_run_com_script",
        lambda *_args, **_kwargs: pytest.fail("offline inventory must not call CST"),
    )

    result = controller.list_solids()

    assert result["success"] is False
    assert "离线模式" in result["message"]


def test_list_solids_rejects_non_d_drive_temp_dir_before_side_effect(monkeypatch):
    controller = _make_controller()
    monkeypatch.setattr(controller_mod.config, "CST_TEMP_DIR", r"C:\cst-inventory")
    monkeypatch.setattr(controller, "_resolve_project_for_command", lambda: controller.project_path)
    monkeypatch.setattr(
        controller,
        "_run_com_script",
        lambda *_args, **_kwargs: pytest.fail("C-drive inventory must not call CST"),
    )

    result = controller.list_solids()

    assert result["success"] is False
    assert "D 盘" in result["message"]


def test_list_solids_passes_through_result_and_removes_artifact(monkeypatch):
    controller = _make_controller()
    temp_root = Path(r"D:\cst_agent_rag_data\tmp\list-solids-unit")
    observed = {}
    sentinel = {
        "success": True,
        "message": "inventory read",
        "project_file": controller.project_path,
        "solids": ["component:a", "component:b"],
        "extra": "preserved",
    }
    monkeypatch.setattr(controller_mod.config, "CST_TEMP_DIR", str(temp_root))
    monkeypatch.setattr(controller, "_resolve_project_for_command", lambda: controller.project_path)
    monkeypatch.setattr(
        controller,
        "_update_project_path_from_result",
        lambda result, project: observed.update(updated=(result, project)),
    )

    def fake_run(args, timeout):
        observed["args"] = args
        observed["timeout"] = timeout
        Path(args[4]).write_text("component:a\ncomponent:b\n", encoding="utf-8")
        return sentinel

    monkeypatch.setattr(controller, "_run_com_script", fake_run)

    result = controller.list_solids(timeout=37)

    assert result is sentinel
    assert observed["args"][:4] == ["list_solids", controller.project_path, "", ""]
    assert Path(observed["args"][4]).drive.upper() == "D:"
    assert not Path(observed["args"][4]).exists()
    assert observed["timeout"] == 37
    assert observed["updated"] == (sentinel, controller.project_path)
    assert controller.last_message == "inventory read"


def test_list_solids_removes_artifact_when_com_runner_raises(monkeypatch):
    controller = _make_controller()
    temp_root = Path(r"D:\cst_agent_rag_data\tmp\list-solids-exception")
    observed = {}
    monkeypatch.setattr(controller_mod.config, "CST_TEMP_DIR", str(temp_root))
    monkeypatch.setattr(controller, "_resolve_project_for_command", lambda: controller.project_path)

    def fake_run(args, timeout):
        assert timeout == 60
        artifact = Path(args[4])
        observed["artifact"] = artifact
        artifact.write_text("component:a\n", encoding="utf-8")
        raise RuntimeError("bridge failed")

    monkeypatch.setattr(controller, "_run_com_script", fake_run)

    with pytest.raises(RuntimeError, match="bridge failed"):
        controller.list_solids()

    assert not observed["artifact"].exists()
