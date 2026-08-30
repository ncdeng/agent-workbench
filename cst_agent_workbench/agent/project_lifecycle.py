"""State coordination for safe CST project lifecycle tool handlers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from cst_agent_workbench.agent.memory import MemoryManager, project_scope_from_path
from cst_agent_workbench.cst.primitives import reset_created_objects


def validate_cst_project_path(
    raw_path: str,
    *,
    must_exist: bool,
    output_must_be_on_d_drive: bool = False,
) -> str:
    """Return a normalized absolute ``.cst`` path or raise ``ValueError``."""

    text = str(raw_path or "").strip().strip('"')
    if not text:
        raise ValueError("CST 工程路径不能为空")
    path = Path(text)
    if not path.is_absolute():
        raise ValueError("CST 工程路径必须是绝对路径")
    if path.suffix.lower() != ".cst":
        raise ValueError("CST 工程路径必须以 .cst 结尾")
    normalized = os.path.normpath(str(path))
    drive = os.path.splitdrive(normalized)[0].upper()
    if output_must_be_on_d_drive and drive != "D:":
        raise ValueError("新建和另存为工程必须位于 D 盘")
    exists = os.path.isfile(normalized)
    if must_exist and not exists:
        raise ValueError(f"CST 工程文件不存在: {normalized}")
    if not must_exist and os.path.exists(normalized):
        raise ValueError(f"目标工程已存在，拒绝覆盖: {normalized}")
    return normalized


def synchronize_project_transition(agent: Any, project_path: str, *, reason: str) -> None:
    """Atomically retire project-scoped cached state after new/open/close."""

    reset_created_objects()
    session = agent.session
    # A raw-VBA approval is a capability over the active CST project.  Even an
    # identical script must be reviewed again after the project identity changes.
    session.clear_tool_approvals()
    artifacts = session.artifacts
    artifacts.last_results = {}
    artifacts.last_farfield_results = {}
    artifacts.last_optimizer_result = {}
    artifacts.tool_results = {}
    artifacts.results_invalidated = True
    artifacts.results_invalidated_reason = reason

    optimization = getattr(session, "optimization_state", None)
    reset_optimization = getattr(optimization, "reset", None)
    if callable(reset_optimization):
        reset_optimization()

    normalized_path = str(project_path or "")
    MemoryManager.update_workspace(
        session.memory,
        project_path=normalized_path,
        model_summary={},
        parameter_summary={},
        last_results_summary={},
    )
    metadata = session.metadata
    memory_scope = metadata.setdefault("memory_scope", {})
    memory_scope["project_scope"] = project_scope_from_path(normalized_path)
    metadata["current_design_signature"] = ""
    metadata["project_transition"] = {
        "project_path": normalized_path,
        "reason": str(reason),
    }
