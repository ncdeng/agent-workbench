"""Run an opt-in CST Windows batch smoke against a D-drive project copy.

This exercises the documented ``CST DESIGN ENVIRONMENT.exe`` batch contract.
It is deliberately separate from Agent E2E evaluation: a successful process
exit proves unattended CST execution, not Agent quality or EM correctness.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


EXIT_CODES = {
    0: "success",
    1: "failed",
    2: "aborted_by_user",
    3: "no_license",
    4: "failed_to_open",
}

MODE_FLAGS: dict[str, list[str]] = {
    "active_solver": ["-as"],
    "time_domain": ["-m", "-r"],
    "frequency_domain": ["-m", "-f"],
}

ProcessRunner = Callable[..., subprocess.CompletedProcess[str]]


def _assert_d_drive(path: Path, label: str) -> Path:
    resolved = path.resolve()
    if resolved.drive.upper() != "D:":
        raise ValueError(f"{label} must be on D:; got {resolved}")
    return resolved


def _snapshot(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def validate_batch_request(
    *,
    executable: Path,
    project_copy: Path,
    artifact_root: Path,
    mode: str,
    environment: Mapping[str, str] | None = None,
) -> tuple[Path, Path, Path]:
    env = environment if environment is not None else os.environ
    if env.get("RUN_LIVE_CST") != "1" or env.get("RUN_LIVE_CST_SOLVER") != "1":
        raise PermissionError(
            "batch solver requires RUN_LIVE_CST=1 and RUN_LIVE_CST_SOLVER=1"
        )
    if env.get("RUN_LIVE_CST_BATCH") != "1":
        raise PermissionError("batch solver requires RUN_LIVE_CST_BATCH=1")
    if mode not in MODE_FLAGS:
        raise ValueError(f"unsupported batch mode: {mode}")

    resolved_executable = executable.resolve()
    if not resolved_executable.is_file():
        raise ValueError(f"CST executable does not exist: {resolved_executable}")
    if resolved_executable.name.lower() not in {
        "cst design environment.exe",
        "cst design environment_amd64.exe",
    }:
        raise ValueError(
            "executable must be the documented CST Design Environment entry point"
        )

    resolved_project = _assert_d_drive(project_copy, "project_copy")
    if resolved_project.suffix.lower() != ".cst" or not resolved_project.is_file():
        raise ValueError("project_copy must be an existing .cst file on D:")
    resolved_artifacts = _assert_d_drive(artifact_root, "artifact_root")
    return resolved_executable, resolved_project, resolved_artifacts


def build_batch_command(
    *,
    executable: Path,
    project_copy: Path,
    mode: str,
) -> list[str]:
    if mode not in MODE_FLAGS:
        raise ValueError(f"unsupported batch mode: {mode}")
    return [str(executable), *MODE_FLAGS[mode], str(project_copy)]


def run_cst_batch(
    *,
    executable: Path,
    project_copy: Path,
    artifact_root: Path,
    mode: str = "active_solver",
    timeout_seconds: int = 1800,
    environment: Mapping[str, str] | None = None,
    process_runner: ProcessRunner = subprocess.run,
) -> dict[str, Any]:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    source_env = dict(environment if environment is not None else os.environ)
    resolved_executable, resolved_project, resolved_artifacts = validate_batch_request(
        executable=executable,
        project_copy=project_copy,
        artifact_root=artifact_root,
        mode=mode,
        environment=source_env,
    )

    run_id = uuid.uuid4().hex[:12]
    run_root = resolved_artifacts / run_id
    temp_root = run_root / "tmp"
    temp_root.mkdir(parents=True, exist_ok=False)
    command = build_batch_command(
        executable=resolved_executable,
        project_copy=resolved_project,
        mode=mode,
    )
    process_env = dict(source_env)
    process_env["TEMP"] = str(temp_root)
    process_env["TMP"] = str(temp_root)
    project_before = _snapshot(resolved_project)
    started = time.perf_counter()
    return_code: int | None = None
    stdout = ""
    stderr = ""
    timed_out = False

    try:
        completed = process_runner(
            command,
            cwd=str(run_root),
            env=process_env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        return_code = int(completed.returncode)
        stdout = str(completed.stdout or "")
        stderr = str(completed.stderr or "")
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = str(exc.stdout or "")
        stderr = str(exc.stderr or "")

    exit_status = "timeout" if timed_out else EXIT_CODES.get(return_code, "unknown_exit_code")
    normalized_stdout = stdout.casefold()
    solver_started = "start solver <" in normalized_stdout
    solver_finished = any(
        marker in normalized_stdout
        for marker in (
            "solver finished successfully",
            "batch run finished successfully",
        )
    )
    success = return_code == 0 and not timed_out and solver_started and solver_finished
    return {
        "schema_version": "cst-batch-smoke-v1",
        "run": {
            "run_id": run_id,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "platform": platform.platform(),
            "mode": mode,
            "command": command,
            "cwd": str(run_root),
            "temp_root": str(temp_root),
            "timeout_seconds": timeout_seconds,
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "secrets_recorded": False,
        },
        "project_before": project_before,
        "project_after": _snapshot(resolved_project),
        "process": {
            "return_code": return_code,
            "exit_status": exit_status,
            "timed_out": timed_out,
            "solver_started": solver_started,
            "solver_finished": solver_finished,
            "stdout": stdout,
            "stderr": stderr,
        },
        "success": success,
        "honest_boundary": [
            "This uses the documented CST Design Environment Windows batch entry point.",
            "Unattended batch execution does not prove that Windows creates no GUI process or window.",
            "Exit code 0 proves process-level completion only; result-tree checks are still required for EM correctness.",
            "TEMP and TMP are redirected to D:, but CST or license services may still write small vendor-managed profile metadata.",
            "A timeout can terminate the launched process without proving every CST child process or solver job was aborted.",
        ],
    }


def _default_executable() -> Path:
    candidates: Sequence[Path] = (
        Path("D:/Program Files (x86)/CST Studio Suite 2025/CST DESIGN ENVIRONMENT.exe"),
        Path("D:/Program Files (x86)/CST Studio Suite 2025/AMD64/CST DESIGN ENVIRONMENT_AMD64.exe"),
    )
    return next((path for path in candidates if path.is_file()), candidates[0])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-copy", type=Path, required=True)
    parser.add_argument("--executable", type=Path, default=_default_executable())
    parser.add_argument("--artifact-root", type=Path, default=Path("D:/cst_agent_rag_data/cst_batch_evidence"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=sorted(MODE_FLAGS), default="active_solver")
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    args = parser.parse_args()

    output = _assert_d_drive(args.output, "output")
    report = run_cst_batch(
        executable=args.executable,
        project_copy=args.project_copy,
        artifact_root=args.artifact_root,
        mode=args.mode,
        timeout_seconds=args.timeout_seconds,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    process = report["process"]
    print(
        json.dumps(
            {
                "success": report["success"],
                "return_code": process["return_code"],
                "exit_status": process["exit_status"],
                "timed_out": process["timed_out"],
                "solver_started": process["solver_started"],
                "solver_finished": process["solver_finished"],
                "duration_ms": report["run"]["duration_ms"],
                "stdout_chars": len(process["stdout"]),
                "stderr_chars": len(process["stderr"]),
                "report_path": str(output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
