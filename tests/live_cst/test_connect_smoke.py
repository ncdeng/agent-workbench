from __future__ import annotations

import os
from pathlib import Path

import pytest

from cst_agent_workbench.cst.controller import CSTController


pytestmark = pytest.mark.cst


def test_live_cst_connect_smoke(live_cst_project_copy: Path):
    """Opt-in CST connection smoke.

    This intentionally does not run the solver. It only proves that the local
    workstation can reach CST through the configured COM/Python bridge.
    """
    if os.environ.get("RUN_LIVE_CST") != "1":
        pytest.skip("set RUN_LIVE_CST=1 and pass --run-live-cst to connect to real CST")

    controller = CSTController()
    result = controller.connect()

    assert result["success"] is True, result
    assert controller.is_connected()
    assert Path(controller.project_path).resolve() == live_cst_project_copy
