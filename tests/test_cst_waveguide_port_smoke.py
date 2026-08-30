import os
from pathlib import Path

from benchmarks.cst_waveguide_port_smoke import run_smoke
from cst_agent_workbench import config


class _FileBackedFakeController:
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


def test_waveguide_port_smoke_report_contract(monkeypatch, tmp_path, reset_primitives):
    output_root = Path("D:/cst_agent_rag_data/tmp") / f"waveguide-{tmp_path.name}"
    monkeypatch.setattr("benchmarks.cst_waveguide_port_smoke.CSTController", _FileBackedFakeController)
    original_memory_dir = config.AGENT_MEMORY_DIR
    original_temp = os.environ.get("TEMP")
    original_tmp = os.environ.get("TMP")

    report, report_path = run_smoke(output_root)

    assert report["success"] is True
    assert report_path.is_file()
    assert [stage["name"] for stage in report["stages"]] == [
        "create_project",
        "create_waveguide_port_free",
        "create_waveguide_port_full",
        "save_project",
        "close_project",
        "reopen_project",
        "final_close",
    ]
    assert all(stage["success"] for stage in report["stages"])
    assert [
        stage["verification"]
        for stage in report["stages"]
        if stage["name"].startswith("create_waveguide_port_")
    ] == ["history_accepted", "history_accepted"]
    assert report["project_exists"] is True
    assert len(report["project_sha256"]) == 64
    assert set(report["execution_source_sha256"]) == {
        "benchmarks/cst_waveguide_port_smoke.py",
        "cst_agent_workbench/agent/tool_contracts.py",
        "cst_agent_workbench/agent/tool_runtime.py",
        "cst_agent_workbench/agent/tools.py",
        "cst_agent_workbench/cst/primitives.py",
    }
    assert all(len(value) == 64 for value in report["execution_source_sha256"].values())
    assert report["controller_final_project_path"] == ""
    assert report["trace"]["tool_call_count"] == 2
    trace_calls = report["trace"]["turns"][0]["tool_calls"]
    assert [call["tool_name"] for call in trace_calls] == [
        "create_waveguide_port",
        "create_waveguide_port",
    ]
    assert all(call["success"] is True for call in trace_calls)
    memory_path = report["trace"]["exit_snapshot"]["session"]["projection"]["memory_scope"]["session_memory_path"]
    assert Path(memory_path).drive.upper() == "D:"
    assert "Picks" in report["scope"]
    assert "not port-object readback" in report["verification_semantics"]["history_accepted"]
    assert config.AGENT_MEMORY_DIR == original_memory_dir
    assert os.environ.get("TEMP") == original_temp
    assert os.environ.get("TMP") == original_tmp


def test_waveguide_port_smoke_uses_collision_resistant_run_directories(monkeypatch, tmp_path, reset_primitives):
    output_root = Path("D:/cst_agent_rag_data/tmp") / f"waveguide-repeat-{tmp_path.name}"
    monkeypatch.setattr("benchmarks.cst_waveguide_port_smoke.CSTController", _FileBackedFakeController)

    _, first_path = run_smoke(output_root)
    _, second_path = run_smoke(output_root)

    assert first_path.parent != second_path.parent
