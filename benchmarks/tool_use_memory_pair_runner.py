"""Cold-start -> learn -> cross-instance reuse mechanism evaluation.

This runner exercises the production Tool Runtime write path and ToolUseMemory
persistence/recall/rerank path.  It is deliberately a deterministic mechanism
evaluation, not evidence that an LLM's task-success rate improved.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.fake_cst_agent_adapter import FakeCSTAgentAdapter  # noqa: E402
from cst_agent_workbench import config  # noqa: E402
from cst_agent_workbench.agent.agent import CSTAgent  # noqa: E402
from cst_agent_workbench.agent.tool_runtime import execute_tool  # noqa: E402
from cst_agent_workbench.agent.tool_use_memory import (  # noqa: E402
    build_tool_use_memory_guidance,
    rerank_safe_tools,
)


def _make_offline_agent(*, memory_root: Path, project_path: Path) -> CSTAgent:
    config.AGENT_MEMORY_DIR = str(memory_root)
    cst = FakeCSTAgentAdapter(project_path=str(project_path), connected=True)
    import cst_agent_workbench.agent.agent as agent_module

    openai_class = agent_module.OpenAI
    agent_module.OpenAI = None
    try:
        agent = CSTAgent(cst)
    finally:
        agent_module.OpenAI = openai_class
    agent._refresh_session_memory_from_runtime()
    return agent


def _tool(name: str) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": name,
            "parameters": {"type": "object", "properties": {}},
        },
    }


def _tool_names(tools: list[dict[str, Any]]) -> list[str]:
    return [str((tool.get("function") or {}).get("name") or "") for tool in tools]


def run_memory_pair(*, artifact_root: Path) -> dict[str, Any]:
    run_id = uuid.uuid4().hex[:12]
    run_root = artifact_root / run_id
    learned_memory_root = run_root / "learned" / "memory"
    project_path = run_root / "shared_project" / "memory_eval.cst"
    isolated_project_path = run_root / "other_project" / "memory_eval.cst"
    original_memory_dir = config.AGENT_MEMORY_DIR
    safe_tools = [_tool("execute_vba_script"), _tool("check_cst_status")]
    try:
        cold_agent = _make_offline_agent(
            memory_root=learned_memory_root,
            project_path=project_path,
        )
        cold_start_empty = not cold_agent.session.tool_use_memory.records
        cold_agent._failure_recovery_engine = None
        cold_agent.cst.queue_response(
            "execute_vba",
            {
                "success": False,
                "message": "VBA is unavailable and unnecessary for this read-only status audit.",
            },
        )
        execute_tool(
            cold_agent,
            "execute_vba_script",
            {"vba_code": "' memory evaluation probe", "description": "read-only audit probe"},
        )
        write_metadata = dict(cold_agent.session.metadata.get("last_tool_use_memory_write") or {})
        memory_path = Path(str(cold_agent.session.metadata.get("tool_use_memory_path") or ""))

        reuse_agent = _make_offline_agent(
            memory_root=learned_memory_root,
            project_path=project_path,
        )
        guidance, recalled = build_tool_use_memory_guidance(
            reuse_agent.session,
            "execute_vba_script failed during a read-only status audit",
            allowed_tool_names=_tool_names(safe_tools),
        )
        learned_order = _tool_names(rerank_safe_tools(safe_tools, recalled))
        no_memory_order = _tool_names(rerank_safe_tools(safe_tools, []))

        isolated_agent = _make_offline_agent(
            memory_root=learned_memory_root,
            project_path=isolated_project_path,
        )
        _, isolated_recall = build_tool_use_memory_guidance(
            isolated_agent.session,
            "execute_vba_script failed during a read-only status audit",
            allowed_tool_names=_tool_names(safe_tools),
        )
    finally:
        config.AGENT_MEMORY_DIR = original_memory_dir

    checks = {
        "cold_start_empty": cold_start_empty,
        "production_failure_write_recorded": bool(write_metadata),
        "memory_file_persisted": memory_path.exists(),
        "cross_instance_record_loaded": bool(reuse_agent.session.tool_use_memory.records),
        "related_failure_recalled": bool(recalled),
        "guidance_injected": bool(guidance),
        "learned_arm_changed_top1": learned_order != no_memory_order,
        "failed_tool_demoted": learned_order[-1:] == ["execute_vba_script"],
        "safe_tool_set_preserved": set(learned_order) == set(no_memory_order),
        "project_scope_isolated": not isolated_recall,
    }
    return {
        "schema_version": "tool-use-memory-pair-report-v1",
        "run": {
            "run_id": run_id,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "artifact_root": str(run_root.resolve()),
            "memory_path": str(memory_path.resolve()),
            "secrets_recorded": False,
        },
        "arms": {
            "no_memory": {"safe_tool_order": no_memory_order, "top1": no_memory_order[0]},
            "learned": {
                "safe_tool_order": learned_order,
                "top1": learned_order[0],
                "recalled_record_count": len(recalled),
                "guidance": guidance,
            },
        },
        "checks": checks,
        "all_checks_passed": all(checks.values()),
        "limitations": [
            "This deterministic paired run proves production write, persistence, scoped recall, and safe reranking contracts.",
            "It does not prove that ToolUseMemory improves an LLM's held-out task-success rate.",
            "The learn phase intentionally creates a final unrecovered tool failure so the production write gate stores it.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=Path("D:/cst_agent_rag_data/agent_eval/tool_use_memory"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--assert-checks", action="store_true")
    args = parser.parse_args()
    report = run_memory_pair(artifact_root=args.artifact_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.assert_checks and not report["all_checks_passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
