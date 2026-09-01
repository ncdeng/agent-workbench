import os
import tempfile
from pathlib import Path

import pytest

from benchmarks.cst_complex_geometry_smoke import EXPECTED_FINAL_SOLIDS, run_smoke

pytestmark = pytest.mark.windows_d_drive
from cst_agent_workbench.agent.tools import TOOLS


class _FakeController:
    cst_python_command = ["D:/fake/python.exe"]

    def __init__(self):
        self.project_path = ""
        self.offline_mode = False
        self.connected = True
        self._solids = set()
        self._saved_solids = set()

    def new_project(self, path, timeout=120):
        target = Path(path)
        target.write_bytes(b"fake-cst")
        self.project_path = str(target)
        return {"success": True, "message": "created", "project_file": str(target)}

    def execute_vba(self, vba, label="", timeout=60):
        if label.startswith("define extruded polygon: complex:body"):
            self._solids.add("complex:body")
        elif label.startswith("define extruded polygon: complex:tab"):
            self._solids.add("complex:tab")
        elif label == "boolean add":
            self._solids.discard("complex:tab")
        elif label.startswith("define cylinder: complex:hole"):
            self._solids.add("complex:hole")
        elif label == "boolean subtract":
            self._solids.discard("complex:hole")
        elif label.startswith("define brick: complex:array"):
            self._solids.add("complex:array")
        return {
            "success": True,
            "message": label,
            "executed": True,
            "verification": "history_accepted",
        }

    def list_solids(self, timeout=60):
        return {"success": True, "message": "inventory", "solids": sorted(self._solids)}

    def save_project(self, include_results=False, timeout=90):
        self._saved_solids = set(self._solids)
        return {"success": True, "message": "saved", "project_file": self.project_path}

    def close_project(self, path, timeout=60):
        self.project_path = ""
        self._solids.clear()
        return {"success": True, "message": "closed", "project_file": path}

    def open_project(self, path, timeout=120):
        self.project_path = path
        self._solids = set(self._saved_solids)
        return {"success": True, "message": "opened", "project_file": path}

    def is_connected(self):
        return True

    def get_status(self):
        return "connected"


def test_complex_geometry_smoke_report_contract(monkeypatch, tmp_path):
    output_root = Path("D:/cst_agent_rag_data/tmp") / tmp_path.name
    monkeypatch.setattr(
        "benchmarks.cst_complex_geometry_smoke.CSTController",
        _FakeController,
    )

    previous_temp = tempfile.tempdir
    previous_env = {key: os.environ.get(key) for key in ("TEMP", "TMP", "CST_TEMP_DIR", "AGENT_MEMORY_DIR")}

    report, report_path = run_smoke(output_root)

    assert report_path.is_file()
    assert report["success"] is True
    assert set(report["canonical_tools_exercised"]).issubset(
        {tool["function"]["name"] for tool in TOOLS}
    )
    assert set(report["inventory_before_save"]) == EXPECTED_FINAL_SOLIDS
    assert set(report["inventory_after_reopen"]) == EXPECTED_FINAL_SOLIDS
    assert report["controller_final_project_path"] == ""
    assert tempfile.tempdir == previous_temp
    assert {key: os.environ.get(key) for key in previous_env} == previous_env
