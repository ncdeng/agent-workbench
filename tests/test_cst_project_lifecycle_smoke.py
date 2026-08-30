from __future__ import annotations

from pathlib import Path

from benchmarks.cst_project_lifecycle_smoke import run_smoke


class _FakeController:
    def __init__(self):
        self.cst_python_command = ["python-test"]
        self.project_path = ""

    def new_project(self, path, timeout=120):
        Path(path).write_bytes(b"cst-original")
        self.project_path = path
        return {"success": True, "message": "created", "project_file": path}

    def save_project(self, include_results=False, timeout=90):
        return {"success": True, "message": "saved", "project_file": self.project_path}

    def save_project_as(self, path, include_results=False, timeout=120):
        Path(path).write_bytes(b"cst-copy")
        self.project_path = path
        return {"success": True, "message": "saved as", "project_file": path}

    def close_project(self, path, timeout=60):
        self.project_path = ""
        return {"success": True, "message": "closed", "project_file": path}

    def open_project(self, path, timeout=120):
        self.project_path = path
        return {"success": True, "message": "opened", "project_file": path}


def test_smoke_report_requires_all_six_stages(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "benchmarks.cst_project_lifecycle_smoke.CSTController",
        _FakeController,
    )

    report, report_path = run_smoke(tmp_path)

    assert report["success"] is True
    assert [stage["name"] for stage in report["stages"]] == [
        "create",
        "save_without_results",
        "save_as_without_results",
        "close_saved_as",
        "reopen_saved_as",
        "final_close",
    ]
    assert report_path.is_file()

