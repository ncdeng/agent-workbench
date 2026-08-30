from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
FRONTEND_DIST_CHECK = ROOT / "tmp" / "frontend-dist-check"


PYTHON_SMOKE_FILES = [
    "tests/test_errors.py",
    "tests/test_results_summary.py",
    "tests/test_agent_planner.py",
    "tests/test_web_api.py",
]

PYTHON_CORE_FILES = [
    "tests/test_agent_planner.py",
    "tests/test_agent_runtime.py",
    "tests/test_agent_tool_runtime.py",
    "tests/test_web_api.py",
]

PYTHON_EVAL_FILES = [
    "tests/test_rag_official_eval.py",
    "tests/test_rag_official_ablation_matrix.py",
    "tests/test_agent_rag_groundedness_eval.py",
    "tests/test_agent_rag_groundedness_revalidate.py",
    "tests/test_agent_rag_claim_review.py",
    "tests/test_agent_e2e_ablation.py",
    "tests/test_agent_e2e_semantic_audit.py",
    "tests/test_agent_e2e_reviewer_calibration.py",
    "tests/test_tool_use_memory_llm_pair_runner.py",
    "tests/test_tool_use_memory_llm_pair_semantic_audit.py",
    "tests/test_tool_use_memory_semantic_grader.py",
    "tests/test_tool_use_memory_semantic_revalidate.py",
    "tests/test_sealed_eval_handoff.py",
]

PYTHON_PRIVACY_FILES = ["tests/test_privacy_scan.py"]

FRONTEND_TEST_ARGS = [
    "--run",
    "--configLoader",
    "native",
    "--pool",
    "threads",
    "--maxWorkers=1",
    "--no-fileParallelism",
    "--no-color",
]

FRONTEND_BUILD_ARGS = [
    "--configLoader",
    "native",
    "--outDir",
    "../tmp/frontend-dist-check",
    "--emptyOutDir",
    "--logLevel",
    "info",
]


@dataclass(frozen=True)
class Check:
    name: str
    command: list[str]
    cwd: Path = ROOT
    cleanup: Path | None = None


def run(command: list[str], *, cwd: Path = ROOT) -> int:
    print(f"\n$ {' '.join(command)}")
    return subprocess.run(command, cwd=str(cwd)).returncode


def run_required(check: Check) -> int:
    code = run(check.command, cwd=check.cwd)
    if check.cleanup is not None and check.cleanup.exists():
        shutil.rmtree(check.cleanup)
    if code != 0:
        print(f"Check failed with exit code {code}: {check.name}")
    return code


def frontend_dependencies_ready() -> bool:
    return (FRONTEND / "node_modules").is_dir()


def npm_command() -> str:
    return "npm.cmd" if os.name == "nt" else "npm"


def make_python_smoke() -> Check:
    return Check(
        "python-smoke",
        [sys.executable, "-m", "pytest", "-q", *PYTHON_SMOKE_FILES],
    )


def make_python_core() -> Check:
    return Check(
        "python-core",
        [sys.executable, "-m", "pytest", "-q", *PYTHON_CORE_FILES],
    )


def make_python_eval() -> Check:
    """Run offline tests for evaluation contracts without CST or model calls."""
    return Check(
        "python-eval",
        [sys.executable, "-m", "pytest", "-q", *PYTHON_EVAL_FILES],
    )


def make_privacy() -> Check:
    return Check(
        "privacy",
        [sys.executable, "scripts/privacy_scan.py", "--scope", "worktree"],
    )


def make_privacy_tests() -> Check:
    return Check(
        "privacy-tests",
        [sys.executable, "-m", "pytest", "-q", *PYTHON_PRIVACY_FILES],
    )


def make_python_offline() -> Check:
    return Check("python-offline", [sys.executable, "-m", "pytest", "-q", "-m", "not cst"])


def make_lint() -> Check:
    # 覆盖 benchmarks/tests/scripts：此前只扫 cst_agent_workbench，
    # benchmark 与测试里的未用导入/未定义名不会被门禁发现。
    return Check(
        "ruff",
        [sys.executable, "-m", "ruff", "check", "cst_agent_workbench", "benchmarks", "tests", "scripts"],
    )


def make_frontend_test() -> Check:
    return Check(
        "frontend-test",
        [npm_command(), "--prefix", "frontend", "test", "--", *FRONTEND_TEST_ARGS],
    )


def make_frontend_build() -> Check:
    return Check(
        "frontend-build",
        [npm_command(), "--prefix", "frontend", "run", "build", "--", *FRONTEND_BUILD_ARGS],
        cleanup=FRONTEND_DIST_CHECK,
    )


def make_frontend_e2e() -> Check:
    return Check(
        "frontend-e2e",
        [npm_command(), "--prefix", "frontend", "run", "e2e"],
    )


def make_live_cst() -> Check:
    return Check(
        "live-cst",
        [sys.executable, "-m", "pytest", "-q", "-m", "cst", "--run-live-cst", "tests/live_cst/test_connect_smoke.py"],
    )


def make_live_cst_solver() -> Check:
    return Check(
        "live-cst-solver",
        [sys.executable, "-m", "pytest", "-q", "-m", "cst", "--run-live-cst", "tests/live_cst/test_solver_smoke.py"],
    )


def build_checks(args: argparse.Namespace) -> list[Check]:
    levels = ["frontend", "e2e", "build"] if args.frontend_only else [args.level]

    checks: list[Check] = []
    for level in levels:
        if level == "smoke":
            checks.append(make_python_smoke())
        elif level == "core":
            checks.append(make_python_core())
        elif level == "eval":
            checks.append(make_python_eval())
        elif level == "offline":
            checks.append(make_python_offline())
        elif level == "lint":
            checks.append(make_lint())
        elif level == "privacy":
            checks.extend([make_privacy_tests(), make_privacy()])
        elif level == "frontend":
            checks.append(make_frontend_test())
        elif level == "e2e":
            checks.append(make_frontend_e2e())
        elif level == "build":
            checks.append(make_frontend_build())
        elif level == "live-cst":
            checks.append(make_live_cst())
        elif level == "live-cst-solver":
            checks.append(make_live_cst_solver())
        elif level == "all":
            checks.extend(
                [
                    make_privacy_tests(),
                    make_privacy(),
                    make_python_smoke(),
                    make_python_core(),
                    make_python_offline(),
                    make_lint(),
                    make_frontend_test(),
                    make_frontend_e2e(),
                    make_frontend_build(),
                ]
            )
        else:
            raise ValueError(f"Unknown check level: {level}")

    if args.skip_frontend:
        checks = [check for check in checks if not check.name.startswith("frontend-")]
    if args.python_only:
        checks = [check for check in checks if not check.name.startswith("frontend-")]
    return checks


def live_cst_enabled() -> bool:
    return os.environ.get("RUN_LIVE_CST") == "1"


def live_cst_solver_enabled() -> bool:
    return os.environ.get("RUN_LIVE_CST_SOLVER") == "1"


def live_cst_project_copy_ready() -> bool:
    raw_path = os.environ.get("CST_LIVE_PROJECT_COPY", "").strip()
    if not raw_path:
        return False
    path = Path(raw_path).resolve()
    return path.drive.upper() == "D:" and path.suffix.lower() == ".cst" and path.is_file()


def validate_requested_checks(args: argparse.Namespace, checks: list[Check]) -> int:
    if args.include_cst:
        print("--include-cst is deprecated because default gates must stay offline.")
        print('Use: $env:RUN_LIVE_CST="1"; python scripts/check.py --level live-cst')
        return 2

    if any(check.name == "live-cst" for check in checks) and not live_cst_enabled():
        print("Refusing to run live-cst without RUN_LIVE_CST=1.")
        print('Use: $env:RUN_LIVE_CST="1"; python scripts/check.py --level live-cst')
        return 2

    if any(check.name in {"live-cst", "live-cst-solver"} for check in checks):
        if not live_cst_project_copy_ready():
            print("Refusing live CST run without CST_LIVE_PROJECT_COPY pointing to an existing .cst copy on D:.")
            return 2

    if any(check.name == "live-cst-solver" for check in checks):
        if (
            not live_cst_enabled()
            or not live_cst_solver_enabled()
            or os.environ.get("RUN_LIVE_CST_MUTATING") != "1"
        ):
            print(
                "Refusing live-cst-solver without RUN_LIVE_CST=1, "
                "RUN_LIVE_CST_MUTATING=1 and RUN_LIVE_CST_SOLVER=1."
            )
            return 2

    frontend_checks = [check for check in checks if check.name.startswith("frontend-")]
    if frontend_checks and not frontend_dependencies_ready():
        print("Frontend checks require frontend/node_modules.")
        print("Run 'npm ci --prefix frontend' first, or pass --skip-frontend / --python-only explicitly.")
        return 2

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run layered CST-Agent checks.")
    parser.add_argument(
        "--level",
        choices=[
            "smoke",
            "core",
            "eval",
            "offline",
            "lint",
            "privacy",
            "frontend",
            "e2e",
            "build",
            "live-cst",
            "live-cst-solver",
            "all",
        ],
        default="all",
        help="Select the test gate to run. Default: all.",
    )
    parser.add_argument("--python-only", action="store_true", help="Run only Python checks.")
    parser.add_argument("--frontend-only", action="store_true", help="Run only frontend test, E2E, and build checks.")
    parser.add_argument("--skip-frontend", action="store_true", help="Skip frontend checks.")
    parser.add_argument(
        "--include-cst",
        action="store_true",
        help="Deprecated. Use --level live-cst with RUN_LIVE_CST=1 for real CST checks.",
    )
    args = parser.parse_args()

    os.chdir(ROOT)
    checks = build_checks(args)
    if not checks:
        print("No checks selected.")
        return 0

    validation_code = validate_requested_checks(args, checks)
    if validation_code != 0:
        return validation_code

    failures = [run_required(check) for check in checks]
    failed = [code for code in failures if code != 0]
    if failed:
        return 1

    print("\nAll requested checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
