from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.cst_typed_results_validation import _require_d_drive


def test_live_results_runner_rejects_non_d_drive():
    with pytest.raises(ValueError, match="must be on D"):
        _require_d_drive(Path("C:/tmp/project.cst"), kind="project")
