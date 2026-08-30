"""Resolve developer-only CST diagnostic inputs without personal paths."""

from __future__ import annotations

import os
from pathlib import Path


def sample_project_path() -> str:
    raw = os.environ.get("CST_AGENT_SAMPLE_PROJECT", "").strip()
    if not raw:
        raise SystemExit(
            "Set CST_AGENT_SAMPLE_PROJECT to an existing disposable .cst file on D:."
        )
    path = Path(raw).expanduser().resolve()
    _require_d_drive(path, label="CST_AGENT_SAMPLE_PROJECT")
    if path.suffix.lower() != ".cst" or not path.is_file():
        raise SystemExit("CST_AGENT_SAMPLE_PROJECT must point to an existing .cst file.")
    return str(path)


def scratch_directory() -> Path:
    raw = os.environ.get(
        "CST_AGENT_SCRATCH_DIR",
        r"D:\cst_agent_rag_data\diagnostic_scratch",
    ).strip()
    path = Path(raw).expanduser().resolve()
    _require_d_drive(path, label="CST_AGENT_SCRATCH_DIR")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _require_d_drive(path: Path, *, label: str) -> None:
    if os.name == "nt" and path.drive.upper() != "D:":
        raise SystemExit(f"{label} must stay on D: to protect C: disk space.")
