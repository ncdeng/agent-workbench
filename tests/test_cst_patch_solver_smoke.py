from __future__ import annotations

import json
from pathlib import Path

from benchmarks.cst_patch_solver_smoke import run_smoke


class _FakeController:
    def __init__(self):
        self.project_path = ""
        self.connected = True
        self.offline_mode = False

    def new_project(self, project_path, timeout=120):
        path = Path(project_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake-cst")
        self.project_path = str(path)
        return {"success": True, "message": "created", "project_file": str(path)}

    def execute_vba(self, _vba, label="", timeout=120):
        return {"success": True, "message": label, "executed": True, "verification": "history_accepted"}

    def run_solver(self, timeout=360):
        return {"success": True, "message": "solved", "project_file": self.project_path}

    def close_project(self, project_path, timeout=60):
        # 与真实 CSTController.close_project 一致：只有关闭的是当前工程时才清空
        # project_path。_cleanup_old_fast_path_projects 会关闭别的旧工程文件，
        # 无条件清空会让求解预检误判"没有工程路径"。
        if str(project_path) == self.project_path:
            self.project_path = ""
        return {"success": True, "message": "closed", "project_file": project_path}

    def set_mesh_by_frequency(self, f0_ghz, epsilon_r=1.0, steps=15, timeout=30, project_path=""):
        return {"success": True, "message": "mesh set", "executed": True}

    def is_connected(self):
        return True

    def get_status(self):
        return "connected"


class _FakeResults:
    def __init__(self, **_kwargs):
        pass

    def open(self, project_path):
        return {"success": bool(project_path), "message": "opened"}

    def get_s_parameter(self, port_i, port_j, *, max_points=None):
        return {
            "success": True,
            "item": f"S{port_i},{port_j}",
            "plot_data": [
                {"freq": 9.3, "s_db": -8.0},
                {"freq": 9.4, "s_db": -12.0},
                {"freq": 9.5, "s_db": -7.0},
            ],
        }


def test_patch_solver_smoke_report_contract(monkeypatch, tmp_path):
    output_root = tmp_path / "patch"
    monkeypatch.setattr("benchmarks.cst_patch_solver_smoke.CSTController", _FakeController)
    monkeypatch.setattr("cst_agent_workbench.agent.agent.ResultsReader", _FakeResults)
    monkeypatch.setattr("benchmarks.cst_patch_solver_smoke.bound_sources_match_commit", lambda *_args, **_kwargs: True)

    report, report_path = run_smoke(output_root)

    assert report["success"] is True
    assert report["target_met"] is True
    assert report_path.is_file()
    assert report["solver_event_success"] is True
    assert report["s11"]["success"] is True
    assert report["s11_point_count"] == 3
    assert Path(report["project_file"]).drive.upper() == "D:"
    assert Path(report["s11_curve_file"]).drive.upper() == "D:"
    assert len(json.loads(Path(report["s11_curve_file"]).read_text(encoding="utf-8"))) == 3
    assert report["controller_final_project_path"] == ""


def test_patch_solver_smoke_requires_nonempty_s11(monkeypatch, tmp_path):
    class _EmptyResults(_FakeResults):
        def get_s_parameter(self, port_i, port_j, *, max_points=None):
            return {"success": True, "message": "empty", "item": "S1,1", "plot_data": []}

    output_root = tmp_path / "empty-patch"
    monkeypatch.setattr("benchmarks.cst_patch_solver_smoke.CSTController", _FakeController)
    monkeypatch.setattr("cst_agent_workbench.agent.agent.ResultsReader", _EmptyResults)
    monkeypatch.setattr("benchmarks.cst_patch_solver_smoke.bound_sources_match_commit", lambda *_args, **_kwargs: True)

    report, _ = run_smoke(output_root)

    assert report["success"] is False
    assert report["target_eligible"] is False
    assert report["target_met"] is False
    assert report["s11_point_count"] == 0
