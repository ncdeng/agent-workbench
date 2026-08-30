import os
from pathlib import Path

from cst_agent_workbench.results.service import read_farfield_result


class _FakeReader:
    def __init__(self, farfield_items):
        self._farfield_items = farfield_items

    def open(self, project_path):
        return {"success": True, "message": f"opened {project_path}"}

    def list_farfield_results(self):
        return {
            "success": True,
            "items": list(self._farfield_items),
            "plot_items": [],
            "raw_monitor_items": list(self._farfield_items),
        }

    def read_result(self, item_path):
        raise AssertionError("read_result should not be used in export fallback test")

    def build_farfield_cut_result(self, item_path, read_result, cut_type="phi", cut_value_deg=0.0):
        raise AssertionError("build_farfield_cut_result should not be used in export fallback test")


class _FakeCST:
    def __init__(self, project_path: str, export_text: str, numeric_payload: dict | None = None):
        self.project_path = project_path
        self._export_text = export_text
        self._numeric_payload = numeric_payload or {}
        self.calls = []
        self.numeric_calls = []

    def get_farfield_numeric(self, **kwargs):
        self.numeric_calls.append(kwargs)
        if self._numeric_payload:
            payload = dict(self._numeric_payload)
            payload.setdefault("success", True)
            payload.setdefault("output_path", kwargs["output_path"])
            return payload
        return {"success": False, "message": "no numeric payload"}

    def export_farfield_ascii(self, **kwargs):
        self.calls.append(kwargs)
        output_path = kwargs["output_path"]
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(self._export_text, encoding="utf-8")
        return {
            "success": True,
            "message": "exported",
            "output_path": output_path,
            "item_path": kwargs.get("item_path"),
        }


def test_read_farfield_result_uses_template_exported_file_first(tmp_path):
    project_path = str(tmp_path / "demo_project.cst")
    project_dir = str(tmp_path / "demo_project")
    farfield_dir = os.path.join(project_dir, "Export", "Farfield")
    os.makedirs(farfield_dir, exist_ok=True)
    Path(project_path).write_text("stub", encoding="utf-8")
    export_file = os.path.join(farfield_dir, "farfield (f=9.4) [1].txt")
    Path(export_file).write_text(
        "0 0 1.0\n45 0 3.5\n90 0 2.0\n0 90 0.5\n45 90 0.8\n90 90 0.2\n",
        encoding="utf-8",
    )

    reader = _FakeReader([r"Farfields\farfield (f=9.4) [1]"])
    cst = _FakeCST(project_path, export_text="should not be used")

    result = read_farfield_result(
        reader,
        project_path,
        item_path=r"Farfields\farfield (f=9.4) [1]",
        cut_type="phi",
        cut_value_deg=0.0,
        cst=cst,
    )

    assert result["success"] is True
    assert result["export_backend"] == "cst_template_post_processing"
    assert result["result_kind"] == "farfield_cut"
    assert len(cst.numeric_calls) == 0
    assert len(cst.calls) == 0


def test_read_farfield_result_prefers_getlist_numeric_extraction(tmp_path):
    project_path = str(tmp_path / "demo_project.cst")
    Path(project_path).write_text("stub", encoding="utf-8")

    reader = _FakeReader([r"Farfields\farfield (f=9.4)"])
    cst = _FakeCST(
        project_path,
        export_text="should not be used",
        numeric_payload={
            "theta": [0.0, 45.0, 90.0, 0.0, 45.0, 90.0],
            "phi": [0.0, 0.0, 0.0, 90.0, 90.0, 90.0],
            "value": [1.0, 3.5, 2.0, 0.5, 0.8, 0.2],
        },
    )

    result = read_farfield_result(
        reader,
        project_path,
        item_path=r"Farfields\farfield (f=9.4)",
        cut_type="phi",
        cut_value_deg=0.0,
        cst=cst,
    )

    assert result["success"] is True
    assert result["result_kind"] == "farfield_cut"
    assert result["export_backend"] == "cst_farfield_get_list"
    assert result["plot_data"] == [
        {"angle_deg": 0.0, "gain_dbi": 1.0},
        {"angle_deg": 45.0, "gain_dbi": 3.5},
        {"angle_deg": 90.0, "gain_dbi": 2.0},
    ]
    assert len(cst.numeric_calls) == 1
    assert len(cst.calls) == 0


def test_read_farfield_result_falls_back_to_online_ascii_export(tmp_path):
    project_path = str(tmp_path / "demo_project.cst")
    Path(project_path).write_text("stub", encoding="utf-8")

    reader = _FakeReader([r"Farfields\farfield (f=9.4)"])
    export_text = """0 0 1.0\n45 0 3.5\n90 0 2.0\n0 90 0.5\n45 90 0.8\n90 90 0.2\n"""
    cst = _FakeCST(project_path, export_text)

    result = read_farfield_result(
        reader,
        project_path,
        item_path=r"Farfields\farfield (f=9.4)",
        cut_type="phi",
        cut_value_deg=0.0,
        cst=cst,
    )

    assert result["success"] is True
    assert result["result_kind"] == "farfield_cut"
    assert result["export_backend"] == "cst_ascii_export"
    assert result["cut_type"] == "phi"
    assert result["cut_value_deg"] == 0.0
    assert result["frequency_ghz"] == 9.4
    assert result["plot_data"] == [
        {"angle_deg": 0.0, "gain_dbi": 1.0},
        {"angle_deg": 45.0, "gain_dbi": 3.5},
        {"angle_deg": 90.0, "gain_dbi": 2.0},
    ]
    assert result["summary"]["peak_gain_dbi"] == 3.5
    assert result["summary"]["peak_angle_deg"] == 45.0
    assert len(cst.numeric_calls) == 1
    assert len(cst.calls) == 1
    assert cst.calls[0]["item_path"] == r"Farfields\farfield (f=9.4)"
    assert os.path.exists(result["export_path"])


def test_read_farfield_result_reports_export_failure_when_online_export_fails(tmp_path):
    project_path = str(tmp_path / "demo_project.cst")
    Path(project_path).write_text("stub", encoding="utf-8")

    reader = _FakeReader([r"Farfields\farfield (f=9.4)"])

    class _FailingCST:
        def __init__(self, project_path: str):
            self.project_path = project_path

        def get_farfield_numeric(self, **kwargs):
            return {"success": False, "message": "getlist boom"}

        def export_farfield_ascii(self, **kwargs):
            return {"success": False, "message": "boom"}

    cst = _FailingCST(project_path)

    result = read_farfield_result(
        reader,
        project_path,
        item_path=r"Farfields\farfield (f=9.4)",
        cut_type="phi",
        cut_value_deg=0.0,
        cst=cst,
    )



def test_read_farfield_result_falls_back_to_ascii_when_numeric_payload_is_invalid(tmp_path):
    project_path = str(tmp_path / "demo_project.cst")
    Path(project_path).write_text("stub", encoding="utf-8")

    reader = _FakeReader([r"Farfields\farfield (f=9.4)"])
    export_text = """0 0 1.0\n45 0 3.5\n90 0 2.0\n"""
    cst = _FakeCST(
        project_path,
        export_text=export_text,
        numeric_payload={
            "theta": [0.0, 45.0],
            "phi": [0.0],
            "value": [1.0, 3.5],
        },
    )

    result = read_farfield_result(
        reader,
        project_path,
        item_path=r"Farfields\farfield (f=9.4)",
        cut_type="phi",
        cut_value_deg=0.0,
        cst=cst,
    )

    assert result["success"] is True
    assert result["export_backend"] == "cst_ascii_export"
    assert len(cst.numeric_calls) == 1
    assert len(cst.calls) == 1
