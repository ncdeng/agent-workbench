from pathlib import Path

from benchmarks.cst_monitor_mesh_smoke import run_smoke


class _FakeController:
    cst_python_command = ["D:/fake/python.exe"]

    def __init__(self):
        self.project_path = ""
        self.offline_mode = False
        self.connected = True

    def new_project(self, path, timeout=120):
        target = Path(path)
        target.write_bytes(b"fake-cst")
        self.project_path = str(target)
        return {"success": True, "message": "created", "project_file": str(target)}

    def execute_vba(self, _vba, label="", timeout=60):
        return {"success": True, "message": label, "executed": True}

    def set_mesh_by_frequency(self, _f0, epsilon_r=1.0, steps=15, timeout=30, project_path=""):
        return {"success": True, "message": f"mesh {epsilon_r} {steps}"}

    def set_global_hexahedral_mesh(self, lines_per_wavelength=15, minimum_step_number=5, timeout=30, project_path=""):
        return {"success": True, "message": f"mesh {lines_per_wavelength} {minimum_step_number}"}

    def save_project(self, include_results=False, timeout=90):
        return {"success": True, "message": "saved", "project_file": self.project_path}

    def close_project(self, path, timeout=60):
        self.project_path = ""
        return {"success": True, "message": "closed", "project_file": path}

    def open_project(self, path, timeout=120):
        self.project_path = path
        return {"success": True, "message": "opened", "project_file": path}

    def is_connected(self):
        return True

    def get_status(self):
        return "connected"


def test_monitor_mesh_smoke_report_is_reproducible(monkeypatch, tmp_path):
    output_root = Path("D:/cst_agent_rag_data/tmp") / tmp_path.name
    monkeypatch.setattr("benchmarks.cst_monitor_mesh_smoke.CSTController", _FakeController)

    report, report_path = run_smoke(output_root)

    assert report["success"] is True
    assert report_path.is_file()
    assert report["canonical_tool_count"] == 42
    assert report["trace"]["tool_call_count"] == 5
    assert all(
        stage["verification"] == "history_accepted"
        for stage in report["stages"]
        if stage["name"] in report["canonical_tools_exercised"]
    )
