from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from cst_agent_workbench import config


@pytest.fixture
def live_cst_project_copy(monkeypatch) -> Path:
    raw_project = os.environ.get("CST_LIVE_PROJECT_COPY", "").strip()
    if not raw_project:
        pytest.fail("set CST_LIVE_PROJECT_COPY to an existing disposable .cst copy on D:")
    project = Path(raw_project).resolve()
    if project.drive.upper() != "D:" or project.suffix.lower() != ".cst" or not project.is_file():
        pytest.fail(f"CST_LIVE_PROJECT_COPY must be an existing .cst file on D:; got {project}")

    temp_root = Path("D:/cst_agent_rag_data/live_cst_pytest_tmp") / str(os.getpid())
    temp_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TEMP", str(temp_root))
    monkeypatch.setenv("TMP", str(temp_root))
    monkeypatch.setattr(tempfile, "tempdir", str(temp_root))
    monkeypatch.setattr(config, "CST_DEFAULT_PROJECT", str(project))
    monkeypatch.setattr(config, "AGENT_MEMORY_DIR", str(temp_root / "memory"))
    return project
