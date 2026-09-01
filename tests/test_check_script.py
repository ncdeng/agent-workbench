from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path


def _load_check_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "check.py"
    spec = importlib.util.spec_from_file_location("check_script", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _args(**overrides):
    data = {
        "level": "all",
        "python_only": False,
        "frontend_only": False,
        "skip_frontend": False,
        "include_cst": False,
    }
    data.update(overrides)
    return argparse.Namespace(**data)


def test_check_script_all_level_defines_layered_gate():
    check = _load_check_module()

    checks = check.build_checks(_args(level="all"))

    assert [item.name for item in checks] == [
        "privacy-tests",
        "privacy",
        "python-smoke",
        "python-core",
        "python-offline",
        "ruff",
        "frontend-test",
        "frontend-e2e",
        "frontend-build",
    ]
    assert "live-cst" not in [item.name for item in checks]
    assert "live-cst-solver" not in [item.name for item in checks]


def test_check_script_python_only_keeps_all_python_gates():
    check = _load_check_module()

    checks = check.build_checks(_args(level="all", python_only=True))

    assert [item.name for item in checks] == [
        "privacy-tests",
        "privacy",
        "python-smoke",
        "python-core",
        "python-offline",
        "ruff",
    ]


def test_check_script_frontend_only_keeps_test_and_build():
    check = _load_check_module()

    checks = check.build_checks(_args(frontend_only=True))

    assert [item.name for item in checks] == ["frontend-test", "frontend-e2e", "frontend-build"]
    assert checks[-1].cleanup == check.FRONTEND_DIST_CHECK


def test_check_script_core_gate_covers_runtime_and_api():
    check = _load_check_module()

    checks = check.build_checks(_args(level="core"))

    command = checks[0].command
    assert "tests/test_agent_runtime.py" in command
    assert "tests/test_agent_tool_runtime.py" in command
    assert "tests/test_web_api.py" in command


def test_check_script_eval_gate_is_offline_and_covers_evidence_contracts():
    check = _load_check_module()

    checks = check.build_checks(_args(level="eval"))

    assert [item.name for item in checks] == ["python-eval"]
    command = checks[0].command
    assert "tests/test_rag_official_eval.py" in command
    assert "tests/test_agent_rag_groundedness_revalidate.py" in command
    assert "tests/test_agent_e2e_ablation.py" in command
    assert "tests/test_tool_use_memory_semantic_revalidate.py" in command
    assert "tests/test_sealed_eval_handoff.py" in command
    assert "tests/live_cst/test_solver_smoke.py" not in command


def test_check_script_privacy_gate_tests_and_scans_the_worktree():
    check = _load_check_module()

    checks = check.build_checks(_args(level="privacy"))

    assert [item.name for item in checks] == ["privacy-tests", "privacy"]
    assert "tests/test_privacy_scan.py" in checks[0].command
    assert checks[1].command[-2:] == ["--scope", "worktree"]


def test_check_script_referenced_test_files_exist():
    """check.py 里写死的测试文件路径必须真实存在。

    ADR-001 删掉 agent/nodes.py 时，tests/test_agent_nodes.py 一并消失，但
    PYTHON_SMOKE_FILES / PYTHON_CORE_FILES 仍引用它，导致 README 里记录的
    `--level smoke` / `core` / `all` 三个门禁长期直接报错退出（exit code 4），
    而本测试文件当时只断言 check 的名字，没人发现。
    """
    check = _load_check_module()
    root = Path(__file__).resolve().parents[1]

    referenced = [
        *check.PYTHON_SMOKE_FILES,
        *check.PYTHON_CORE_FILES,
        *check.PYTHON_EVAL_FILES,
        *check.PYTHON_PRIVACY_FILES,
    ]
    missing = [path for path in referenced if not (root / path).is_file()]

    assert missing == [], f"check.py references non-existent test files: {missing}"


def test_check_script_offline_gates_always_exclude_live_cst():
    check = _load_check_module()

    offline_checks = check.build_checks(_args(level="offline", include_cst=True))
    all_checks = check.build_checks(_args(level="all", include_cst=True))

    assert offline_checks[0].command[-2:] == ["-m", "not cst"]
    offline_from_all = next(item for item in all_checks if item.name == "python-offline")
    assert offline_from_all.command[-2:] == ["-m", "not cst"]


def test_check_script_live_cst_gate_is_explicit_and_not_part_of_all():
    check = _load_check_module()

    checks = check.build_checks(_args(level="live-cst"))

    assert [item.name for item in checks] == ["live-cst"]
    command = checks[0].command
    assert "-m" in command
    assert "cst" in command
    assert "--run-live-cst" in command
    assert "tests/live_cst/test_connect_smoke.py" in command


def test_check_script_live_cst_solver_gate_is_separate_from_connection_smoke():
    check = _load_check_module()

    checks = check.build_checks(_args(level="live-cst-solver"))

    assert [item.name for item in checks] == ["live-cst-solver"]
    command = checks[0].command
    assert "-m" in command
    assert "cst" in command
    assert "--run-live-cst" in command
    assert "tests/live_cst/test_solver_smoke.py" in command


def test_check_script_live_cst_gate_requires_env(monkeypatch, tmp_path):
    check = _load_check_module()
    args = _args(level="live-cst")
    checks = check.build_checks(args)

    monkeypatch.delenv("RUN_LIVE_CST", raising=False)
    assert check.validate_requested_checks(args, checks) == 2

    monkeypatch.setenv("RUN_LIVE_CST", "1")
    assert check.validate_requested_checks(args, checks) == 2

    project_copy = tmp_path / "live_status_copy.cst"
    project_copy.write_bytes(b"test project fixture")
    monkeypatch.setenv("CST_LIVE_PROJECT_COPY", str(project_copy))
    expected = 0 if (sys.platform == "win32" and project_copy.resolve().drive.upper() == "D:") else 2
    assert check.validate_requested_checks(args, checks) == expected


def test_check_script_live_cst_solver_gate_requires_explicit_copy_and_mutation_gates(monkeypatch, tmp_path):
    check = _load_check_module()
    args = _args(level="live-cst-solver")
    checks = check.build_checks(args)

    monkeypatch.delenv("RUN_LIVE_CST", raising=False)
    monkeypatch.delenv("RUN_LIVE_CST_SOLVER", raising=False)
    monkeypatch.delenv("RUN_LIVE_CST_MUTATING", raising=False)
    assert check.validate_requested_checks(args, checks) == 2

    project_copy = tmp_path / "live_solver_copy.cst"
    project_copy.write_bytes(b"test project fixture")
    monkeypatch.setenv("CST_LIVE_PROJECT_COPY", str(project_copy))
    monkeypatch.setenv("RUN_LIVE_CST", "1")
    assert check.validate_requested_checks(args, checks) == 2

    monkeypatch.setenv("RUN_LIVE_CST_SOLVER", "1")
    assert check.validate_requested_checks(args, checks) == 2

    monkeypatch.setenv("RUN_LIVE_CST_MUTATING", "1")
    expected = 0 if (sys.platform == "win32" and project_copy.resolve().drive.upper() == "D:") else 2
    assert check.validate_requested_checks(args, checks) == expected


def test_check_script_rejects_deprecated_include_cst():
    check = _load_check_module()
    args = _args(level="offline", include_cst=True)
    checks = check.build_checks(args)

    assert check.validate_requested_checks(args, checks) == 2


def test_check_script_frontend_dependencies_are_required(monkeypatch):
    check = _load_check_module()
    args = _args(level="frontend")
    checks = check.build_checks(args)

    monkeypatch.setattr(check, "frontend_dependencies_ready", lambda: False)

    assert check.validate_requested_checks(args, checks) == 2
