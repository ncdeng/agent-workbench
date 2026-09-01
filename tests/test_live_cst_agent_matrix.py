from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.live_cst_agent_matrix import CASES, _score_live_case, validate_live_request

pytestmark = pytest.mark.windows_d_drive


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = Path("D:/cst_agent_rag_data/live_cst_agent_evidence")


def test_live_status_requires_explicit_environment_gate():
    project_copy = ROOT / ".pytest_cache" / "live_cst_gate" / "status_project_copy.cst"
    project_copy.parent.mkdir(parents=True, exist_ok=True)
    project_copy.write_bytes(b"test fixture only")
    with pytest.raises(PermissionError, match="RUN_LIVE_CST"):
        validate_live_request(
            case_names=["status_readonly"],
            artifact_root=ARTIFACT_ROOT,
            project_copy=project_copy,
            allow_mutating=False,
            allow_solver=False,
            environment={},
        )

    with pytest.raises(ValueError, match="project-copy on D"):
        validate_live_request(
            case_names=["status_readonly"],
            artifact_root=ARTIFACT_ROOT,
            project_copy=None,
            allow_mutating=False,
            allow_solver=False,
            environment={"RUN_LIVE_CST": "1"},
        )

    validate_live_request(
        case_names=["status_readonly"],
        artifact_root=ARTIFACT_ROOT,
        project_copy=project_copy,
        allow_mutating=False,
        allow_solver=False,
        environment={"RUN_LIVE_CST": "1"},
    )


def test_mutating_and_solver_cases_require_project_copy_and_two_key_gates():
    project_copy = ROOT / ".pytest_cache" / "live_cst_gate" / "project_copy.cst"
    project_copy.parent.mkdir(parents=True, exist_ok=True)
    project_copy.write_bytes(b"test fixture only")
    base_environment = {"RUN_LIVE_CST": "1", "RUN_LIVE_CST_MUTATING": "1"}

    with pytest.raises(PermissionError, match="allow-mutating"):
        validate_live_request(
            case_names=["patch_build_copy"],
            artifact_root=ARTIFACT_ROOT,
            project_copy=project_copy,
            allow_mutating=False,
            allow_solver=False,
            environment=base_environment,
        )

    validate_live_request(
        case_names=["patch_build_copy"],
        artifact_root=ARTIFACT_ROOT,
        project_copy=project_copy,
        allow_mutating=True,
        allow_solver=False,
        environment=base_environment,
    )

    with pytest.raises(PermissionError, match="RUN_LIVE_CST_SOLVER"):
        validate_live_request(
            case_names=["patch_solver_readback_copy"],
            artifact_root=ARTIFACT_ROOT,
            project_copy=project_copy,
            allow_mutating=True,
            allow_solver=True,
            environment=base_environment,
        )


def test_live_solver_score_accepts_complete_fast_path_evidence_and_checks_parameters():
    request = SimpleNamespace(
        f0_ghz=9.4,
        substrate_name="Rogers5880",
        epsilon_r=2.2,
        loss_tangent=0.0009,
        substrate_thickness_mm=0.508,
        feed_strategy="microstrip",
    )
    agent = SimpleNamespace(
        tool_events=[
            {"tool_name": name, "success": True}
            for name in (
                "new_project",
                "fast_patch_setup",
                "fast_patch_geometry",
                "fast_patch_port",
                "run_solver",
            )
        ],
        last_chat_status={"ok": True, "mode": "fast_path"},
        last_results={"success": True, "plot_data": [{"freq": 9.4, "s_db": -12.0}]},
        last_patch_request=request,
        cst=SimpleNamespace(project_path="D:/cst_agent_rag_data/runs/patch.cst"),
        trace_history=[{"status": "completed"}],
    )

    checks = _score_live_case(CASES["patch_solver_readback_copy"], agent, "done")

    assert all(checks.values()), checks

    request.substrate_name = "FR-4 (lossy)"
    mismatched = _score_live_case(CASES["patch_solver_readback_copy"], agent, "done")
    assert mismatched["requested_parameters_satisfied"] is False
