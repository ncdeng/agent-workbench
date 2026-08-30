"""Run the raw-VBA approval flow through Agent chat and the Web control plane."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from benchmarks.fake_cst_agent_adapter import DeterministicAgentClient, FakeCSTAgentAdapter
from cst_agent_workbench.agent.tool_approval import canonical_arguments_sha256
from cst_agent_workbench.agent.tool_contracts import apply_declared_argument_defaults
from cst_agent_workbench.agent.tool_runtime import execute_tool
from cst_agent_workbench.bootstrap import get_app_state, reset_app_state
from cst_agent_workbench.web_api import create_app


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = Path(
    r"D:\cst_agent_rag_data\agent_eval\raw_vba_approval_e2e\report.json"
)
TOOL_NAME = "execute_vba_script"
ARGUMENTS = {
    "vba_code": "Sub Main()\nEnd Sub",
    "description": "raw VBA approval E2E",
}
CHANGED_ARGUMENTS = {
    "vba_code": "Sub Main()\n' changed\nEnd Sub",
    "description": "raw VBA approval E2E",
}


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check(name: str, passed: bool, evidence: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "evidence": evidence}


def run_evaluation() -> dict[str, Any]:
    """Exercise chat request -> approval API -> exact execution -> replay rejection."""
    reset_app_state()
    try:
        # This benchmark evaluates the Agent/control-plane path, not RAG.
        # Prevent local model or vector-store loading so repeated runs remain
        # offline, fast, and free of incidental cache writes.
        from cst_agent_workbench.rag import knowledge_base

        original_rule_retriever = knowledge_base.retrieve_antenna_rules
        original_document_retriever = knowledge_base.retrieve_official_document_hits
        knowledge_base.retrieve_antenna_rules = lambda *_args, **_kwargs: []
        knowledge_base.retrieve_official_document_hits = lambda *_args, **_kwargs: []
        app = create_app(dry_run=True)
        state = get_app_state(dry_run=True)
        fake_cst = FakeCSTAgentAdapter(
            project_path=r"D:\cst_agent_rag_data\agent_eval\raw_vba_approval_e2e\fake_project.cst",
            connected=True,
            offline_mode=False,
        )
        provider = DeterministicAgentClient()
        provider.set_turn(
            {
                "content": "执行一段无副作用 VBA 连通性脚本；高风险操作必须先请求审批。",
                "tool_name": TOOL_NAME,
                "tool_arguments": dict(ARGUMENTS),
            }
        )
        state.cst = fake_cst
        state.agent.cst = fake_cst
        state.agent.client = provider

        normalized_arguments = apply_declared_argument_defaults(TOOL_NAME, ARGUMENTS)
        expected_hash = canonical_arguments_sha256(normalized_arguments)
        client = TestClient(app)

        chat_response = client.post(
            "/api/chat",
            json={"message": provider.current_turn["content"]},
        )
        approval_events = [
            event
            for event in state.session.tool_events
            if event.get("tool_name") == TOOL_NAME
            and event.get("error_type") == "approval_required"
        ]
        first_event = approval_events[-1] if approval_events else {}
        approval_request = dict(first_event.get("approval_request") or {})
        request_id = str(approval_request.get("request_id") or "")

        pending_response = client.get("/api/approvals/pending")
        pending_payload = pending_response.json()
        pending_requests = list(pending_payload.get("requests") or [])
        pending_request = next(
            (item for item in pending_requests if item.get("request_id") == request_id),
            {},
        )

        approve_response = client.post(
            f"/api/approvals/{request_id}/approve",
            json={"ttl_seconds": 120},
        )
        approve_payload = approve_response.json()
        cst_calls_after_approval = list(fake_cst.calls.get("execute_vba") or [])

        replay_payload = json.loads(execute_tool(state.agent, TOOL_NAME, ARGUMENTS))
        changed_payload = json.loads(execute_tool(state.agent, TOOL_NAME, CHANGED_ARGUMENTS))
        changed_request = dict(changed_payload.get("approval_request") or {})

        checks = [
            _check("chat_route_returned", chat_response.status_code == 200, chat_response.json()),
            _check(
                "chat_created_parameter_bound_request",
                bool(request_id)
                and approval_request.get("tool_name") == TOOL_NAME
                and approval_request.get("arguments_sha256") == expected_hash,
                approval_request,
            ),
            _check(
                "unapproved_call_not_dispatched",
                not cst_calls_after_approval[:-1],
                {"calls_before_approval": max(0, len(cst_calls_after_approval) - 1)},
            ),
            _check(
                "pending_projection_hides_arguments",
                pending_response.status_code == 200
                and bool(pending_request)
                and "arguments" not in pending_request,
                pending_request,
            ),
            _check(
                "approval_executed_server_owned_call",
                approve_response.status_code == 200
                and approve_payload.get("approved") is True
                and approve_payload.get("executed") is True
                and (approve_payload.get("result") or {}).get("success") is True,
                approve_payload,
            ),
            _check(
                "exact_normalized_arguments_dispatched_once",
                len(cst_calls_after_approval) == 1
                and cst_calls_after_approval[0].get("vba_code")
                == normalized_arguments["vba_code"],
                cst_calls_after_approval,
            ),
            _check(
                "grant_consumed_before_handler_return",
                bool((approve_payload.get("grant") or {}).get("consumed")),
                approve_payload.get("grant") or {},
            ),
            _check(
                "same_arguments_require_new_approval",
                replay_payload.get("error_type") == "approval_required",
                replay_payload,
            ),
            _check(
                "changed_arguments_get_distinct_binding",
                changed_payload.get("error_type") == "approval_required"
                and changed_request.get("arguments_sha256")
                != approval_request.get("arguments_sha256"),
                changed_request,
            ),
        ]
        return {
            "schema_version": "raw-vba-approval-e2e-report-v1",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "scope": (
                "Agent chat creates the approval request; the Web approval endpoint replays "
                "server-owned exact arguments. This does not resume the original model turn."
            ),
            "environment": {
                "provider": "scripted-deterministic-v1",
                "cst_boundary": "fake-controller",
                "project_path": fake_cst.project_path,
            },
            "bindings": {
                "runner_path": "benchmarks/raw_vba_approval_e2e.py",
                "runner_sha256": _sha256_file(Path(__file__)),
                "tool_name": TOOL_NAME,
                "normalized_arguments_sha256": expected_hash,
            },
            "summary": {
                "checks_passed": sum(item["passed"] for item in checks),
                "checks_total": len(checks),
                "all_passed": all(item["passed"] for item in checks),
            },
            "checks": checks,
            "raw": {
                "chat_response": chat_response.json(),
                "first_tool_event": first_event,
                "pending_response": pending_payload,
                "approve_response": approve_payload,
                "controller_calls": cst_calls_after_approval,
                "same_argument_replay": replay_payload,
                "changed_argument_replay": changed_payload,
            },
            "limitations": [
                "The provider and CST controller are deterministic test doubles.",
                "Approval execution is a Web control-plane replay, not suspended-turn resume.",
                "This evaluates safety/control-flow mechanics, not model reasoning quality.",
            ],
        }
    finally:
        if "original_rule_retriever" in locals():
            knowledge_base.retrieve_antenna_rules = original_rule_retriever
            knowledge_base.retrieve_official_document_hits = original_document_retriever
        reset_app_state()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = run_evaluation()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0 if report["summary"]["all_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
