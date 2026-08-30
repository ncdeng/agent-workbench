"""Owned subprocess lifecycle for Agent Harness sidecars.

The process wrapper knows nothing about Pi, CST, sessions or tools.  It owns
stdio threads, JSONL framing, timeout/error projection and deterministic
shutdown so Harness adapters do not duplicate process-management code.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
from collections import deque
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

from cst_agent_workbench.agent.error_model import (
    AgentLayerError,
    ErrorEnvelope,
    ErrorLayer,
    HarnessErrorCode,
)


class SidecarState(str, Enum):
    NEW = "new"
    STARTING = "starting"
    RUNNING = "running"
    FAILED = "failed"
    STOPPING = "stopping"
    STOPPED = "stopped"


class SidecarProcessError(AgentLayerError):
    pass


def _process_error(
    code: HarnessErrorCode,
    message: str,
    *,
    retryable: bool = False,
    details: Mapping[str, Any] | None = None,
) -> SidecarProcessError:
    return SidecarProcessError(
        ErrorEnvelope(
            layer=ErrorLayer.HARNESS,
            code=code.value,
            message=message,
            retryable=retryable,
            cause_type="SidecarProcessError",
            details=dict(details or {}),
        )
    )


class SidecarProcess:
    """Own one line-delimited JSON subprocess and all of its resources."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        stderr_limit: int = 80,
        shutdown_timeout_sec: float = 2.0,
    ) -> None:
        if not command or not str(command[0]).strip():
            raise ValueError("sidecar command must not be empty")
        self.command = tuple(str(item) for item in command)
        self.cwd = Path(cwd).resolve() if cwd is not None else None
        self.env = dict(env) if env is not None else None
        self.shutdown_timeout_sec = max(0.05, float(shutdown_timeout_sec))
        self._stderr_lines: deque[str] = deque(maxlen=max(1, int(stderr_limit)))
        self._stdout_events: queue.Queue[str | None] = queue.Queue()
        self._process: subprocess.Popen[str] | None = None
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._state = SidecarState.NEW
        self._state_lock = threading.RLock()
        self._write_lock = threading.Lock()

    @property
    def state(self) -> SidecarState:
        with self._state_lock:
            return self._state

    @property
    def pid(self) -> int | None:
        process = self._process
        return process.pid if process is not None else None

    @property
    def stderr_tail(self) -> tuple[str, ...]:
        return tuple(self._stderr_lines)

    def start(self) -> None:
        with self._state_lock:
            if self._state not in {SidecarState.NEW, SidecarState.STOPPED}:
                raise _process_error(
                    HarnessErrorCode.PROTOCOL_ERROR,
                    f"cannot start sidecar while state is {self._state.value}",
                    details={"state": self._state.value},
                )
            self._state = SidecarState.STARTING
            self._stderr_lines.clear()
            self._stdout_events = queue.Queue()
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            process = subprocess.Popen(
                list(self.command),
                cwd=str(self.cwd) if self.cwd is not None else None,
                env=self.env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
        except Exception as exc:
            with self._state_lock:
                self._state = SidecarState.FAILED
            raise _process_error(
                HarnessErrorCode.SIDECAR_UNAVAILABLE,
                f"failed to start sidecar: {exc}",
                retryable=False,
                details={"command": list(self.command), "cause_type": type(exc).__name__},
            ) from exc
        self._process = process

        def read_stdout() -> None:
            assert process.stdout is not None
            try:
                for line in process.stdout:
                    self._stdout_events.put(line)
            finally:
                self._stdout_events.put(None)

        def read_stderr() -> None:
            assert process.stderr is not None
            for line in process.stderr:
                self._stderr_lines.append(line.rstrip())

        self._stdout_thread = threading.Thread(
            target=read_stdout,
            name=f"sidecar-{process.pid}-stdout",
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=read_stderr,
            name=f"sidecar-{process.pid}-stderr",
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()
        with self._state_lock:
            self._state = SidecarState.RUNNING

    def send_json(self, payload: Mapping[str, Any]) -> None:
        process = self._require_running()
        if process.stdin is None:
            self._mark_failed()
            raise _process_error(HarnessErrorCode.SIDECAR_EXITED, "sidecar stdin is unavailable")
        encoded = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            with self._write_lock:
                process.stdin.write(encoded)
                process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self._mark_failed()
            raise _process_error(
                HarnessErrorCode.SIDECAR_EXITED,
                f"failed to write to sidecar: {exc}",
                details=self._exit_details(),
            ) from exc

    def receive_json(self, *, timeout_sec: float) -> dict[str, Any]:
        self._require_running()
        try:
            raw_line = self._stdout_events.get(timeout=max(0.001, float(timeout_sec)))
        except queue.Empty as exc:
            raise _process_error(
                HarnessErrorCode.SIDECAR_TIMEOUT,
                f"sidecar did not produce an event within {float(timeout_sec):.3f}s",
                retryable=False,
                details={"timeout_sec": float(timeout_sec), "pid": self.pid},
            ) from exc
        if raw_line is None:
            self._mark_failed()
            process = self._process
            if process is not None and process.poll() is None:
                try:
                    process.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    pass
            if self._stderr_thread is not None:
                self._stderr_thread.join(timeout=0.1)
            raise _process_error(
                HarnessErrorCode.SIDECAR_EXITED,
                "sidecar closed stdout before producing the expected event",
                details=self._exit_details(),
            )
        try:
            value = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            self._mark_failed()
            raise _process_error(
                HarnessErrorCode.PROTOCOL_ERROR,
                f"sidecar emitted invalid JSONL: {exc}",
                details={"line_preview": raw_line[:300], "pid": self.pid},
            ) from exc
        if not isinstance(value, dict):
            self._mark_failed()
            raise _process_error(
                HarnessErrorCode.PROTOCOL_ERROR,
                "sidecar JSONL event must be an object",
                details={"value_type": type(value).__name__, "pid": self.pid},
            )
        return value

    def terminate(self) -> None:
        process = self._process
        with self._state_lock:
            if process is None or self._state == SidecarState.STOPPED:
                self._state = SidecarState.STOPPED
                return
            self._state = SidecarState.STOPPING
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=self.shutdown_timeout_sec)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=self.shutdown_timeout_sec)
                except subprocess.TimeoutExpired:
                    pass
            except OSError:
                pass
        for thread in (self._stdout_thread, self._stderr_thread):
            if thread is not None:
                thread.join(timeout=min(1.0, self.shutdown_timeout_sec))
        with self._state_lock:
            self._state = SidecarState.STOPPED

    def _require_running(self) -> subprocess.Popen[str]:
        process = self._process
        if process is None or self.state != SidecarState.RUNNING or process.poll() is not None:
            if process is not None and process.poll() is not None:
                self._mark_failed()
            raise _process_error(
                HarnessErrorCode.SIDECAR_EXITED,
                "sidecar is not running",
                details=self._exit_details(),
            )
        return process

    def _mark_failed(self) -> None:
        with self._state_lock:
            self._state = SidecarState.FAILED

    def _exit_details(self) -> dict[str, Any]:
        process = self._process
        return {
            "pid": process.pid if process is not None else None,
            "return_code": process.poll() if process is not None else None,
            "stderr_tail": list(self._stderr_lines),
            "command": list(self.command),
        }

    def __enter__(self) -> "SidecarProcess":
        self.start()
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        self.terminate()
