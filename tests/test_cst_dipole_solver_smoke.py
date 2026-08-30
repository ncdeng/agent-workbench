from __future__ import annotations

import json
from pathlib import Path

from benchmarks.cst_dipole_solver_smoke import run_smoke


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

    def run_solver(self, timeout=300):
        return {"success": True, "message": "solved", "project_file": self.project_path}

    def close_project(self, project_path, timeout=60):
        self.project_path = ""
        return {"success": True, "message": "closed", "project_file": project_path}

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
                {"freq": 2.3, "s_db": -8.0},
                {"freq": 2.4, "s_db": -12.0},
                {"freq": 2.5, "s_db": -7.0},
            ],
        }


def test_dipole_solver_smoke_report_contract(monkeypatch, tmp_path):
    output_root = Path("D:/cst_agent_rag_data/tmp") / f"dipole-{tmp_path.name}"
    monkeypatch.setattr("benchmarks.cst_dipole_solver_smoke.CSTController", _FakeController)
    monkeypatch.setattr("cst_agent_workbench.agent.agent.ResultsReader", _FakeResults)
    monkeypatch.setattr("benchmarks.cst_dipole_solver_smoke.bound_sources_match_commit", lambda *_args, **_kwargs: True)

    report, report_path = run_smoke(output_root, f0_ghz=2.4)

    assert report["success"] is True
    assert report_path.is_file()
    assert report["preflight"]["success"] is True
    assert report["solver"]["success"] is True
    assert report["s11"]["success"] is True
    assert report["s11_point_count"] == 3
    assert Path(report["s11_curve_file"]).drive.upper() == "D:"
    curve = json.loads(Path(report["s11_curve_file"]).read_text(encoding="utf-8"))
    assert len(curve) == 3
    assert report["controller_final_project_path"] == ""
