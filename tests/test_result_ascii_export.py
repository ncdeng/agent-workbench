from __future__ import annotations

from pathlib import Path

import pytest

from cst_agent_workbench.results.contracts import ResultKind
from cst_agent_workbench.results.service import export_project_result_ascii, validate_ascii_export_path


class _FakeCST:
    project_path = "D:/projects/solved.cst"

    def __init__(self):
        self.calls = []

    def export_result_ascii(self, item_path: str, output_path: str, timeout: int = 120):
        self.calls.append((item_path, output_path, timeout))
        path = Path(output_path)
        path.write_text("x y\n1 2\n", encoding="utf-8")
        return {"success": True, "message": "ok"}


@pytest.mark.parametrize("path", [
    "relative.txt",
    "C:/tmp/result.txt",
    "D:/tmp/result.bin",
])
def test_ascii_export_path_rejects_unsafe_target(path):
    with pytest.raises(ValueError):
        validate_ascii_export_path(path)


@pytest.mark.windows_d_drive
def test_ascii_export_path_refuses_overwrite(monkeypatch, tmp_path):
    target = Path("D:/cst_agent_rag_data/tmp/result-contract-existing.txt")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("existing", encoding="utf-8")
    try:
        with pytest.raises(ValueError, match="拒绝覆盖"):
            validate_ascii_export_path(str(target))
    finally:
        target.unlink(missing_ok=True)


@pytest.mark.windows_d_drive
def test_export_project_result_ascii_returns_artifact_metadata():
    target = Path("D:/cst_agent_rag_data/tmp/result-contract-new.txt")
    target.unlink(missing_ok=True)
    cst = _FakeCST()
    try:
        result = export_project_result_ascii(
            cst,
            r"1D Results\S-Parameters\S1,1",
            str(target),
            timeout=45,
        )
        assert result["success"] is True
        assert result["result_kind"] == ResultKind.ARTIFACT_REF.value
        assert result["artifact"]["bytes"] > 0
        assert result["artifact"]["format"] == "txt"
        assert cst.calls == [(r"1D Results\S-Parameters\S1,1", str(target), 45)]
    finally:
        target.unlink(missing_ok=True)
