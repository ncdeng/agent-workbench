from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cst_agent_workbench.agent.agent import CSTAgent
from cst_agent_workbench.agent.tool_runtime import execute_tool
from cst_agent_workbench.cst.controller import CSTController


pytestmark = pytest.mark.cst


def test_live_cst_rectangular_patch_solver_smoke(live_cst_project_copy: Path):
    """Opt-in CST solver smoke.

    This test intentionally runs the real CST solver. It is guarded separately
    from the connection smoke so `live-cst` can stay a lightweight COM bridge
    check.
    """
    if os.environ.get("RUN_LIVE_CST") != "1":
        pytest.skip("set RUN_LIVE_CST=1 and pass --run-live-cst to connect to real CST")
    if os.environ.get("RUN_LIVE_CST_SOLVER") != "1":
        pytest.skip("set RUN_LIVE_CST_SOLVER=1 to run the real CST solver smoke")
    if os.environ.get("RUN_LIVE_CST_MUTATING") != "1":
        pytest.skip("set RUN_LIVE_CST_MUTATING=1 for the disposable project copy")

    controller = CSTController()
    connect_result = controller.connect()
    assert connect_result["success"] is True, connect_result

    agent = CSTAgent(controller)
    agent.client = None  # keep this smoke CST-only; do not spend LLM tokens after S11 readback.

    result_text = execute_tool(
        agent,
        "build_rectangular_patch_fast",
        {
            "f0_ghz": 9.4,
            "substrate_name": "Rogers5880",
            "epsilon_r": 2.2,
            "loss_tangent": 0.0009,
            "substrate_thickness_mm": 0.508,
            "conductor_name": "Copper (annealed)",
            "conductor_thickness_mm": 0.035,
            "feed_strategy": "microstrip",
            "run_solver": True,
        },
    )
    result = json.loads(result_text)

    assert result["success"] is True
    assert agent.last_chat_status["ok"] is True
    assert agent.last_chat_status["had_tool_failure"] is False
    active_project = Path(controller.project_path).resolve()
    assert active_project.drive.upper() == "D:"
    assert active_project.suffix.lower() == ".cst"
    assert active_project.is_file()
    assert agent.last_results.get("success") is True
    assert agent.last_results.get("plot_data")
    assert "已完成一次求解" in result.get("message", "")
