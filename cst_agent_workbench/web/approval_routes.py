from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from fastapi import HTTPException
from pydantic import BaseModel, Field

from cst_agent_workbench.agent.tool_approval import DEFAULT_APPROVAL_ACTOR
from cst_agent_workbench.agent.tool_approval import canonical_arguments_sha256
from cst_agent_workbench.agent.tool_runtime import execute_tool, preflight_tool_call


class ApprovalDecisionRequest(BaseModel):
    ttl_seconds: int = Field(default=120, ge=1, le=600)


def register_approval_routes(
    app: Any,
    *,
    dry_run: bool,
    get_app_state: Callable[..., Any],
    operation_lock: asyncio.Lock,
) -> None:
    @app.get("/api/approvals/pending")
    async def pending_approvals():
        async with operation_lock:
            state = get_app_state(dry_run=dry_run)
            requests = []
            for request in state.session.tool_approvals.requests.values():
                current = state.session.tool_approvals.get_request(
                    request.request_id,
                    actor=DEFAULT_APPROVAL_ACTOR,
                )
                if current is not None and current.status == "pending":
                    requests.append(current.to_public_dict(include_arguments=False))
            return {"requests": requests[-20:]}

    @app.post("/api/approvals/{request_id}/approve")
    async def approve_tool_call(request_id: str, req: ApprovalDecisionRequest):
        async with operation_lock:
            state = get_app_state(dry_run=dry_run)
            try:
                request = state.session.tool_approvals.get_request(
                    request_id,
                    actor=DEFAULT_APPROVAL_ACTOR,
                )
                if request is None:
                    raise ValueError("approval request not found for actor")
                if request.status != "pending":
                    raise ValueError(f"approval request is {request.status}")
                normalized, authorization_rejection, validation_error = preflight_tool_call(
                    state.agent,
                    request.tool_name,
                    request.normalized_arguments,
                )
                if authorization_rejection is not None:
                    request.status = "stale"
                    rejection = json.loads(authorization_rejection)
                    raise ValueError(rejection.get("message") or "approval request is no longer authorized")
                if validation_error is not None:
                    request.status = "stale"
                    raise ValueError(f"approval request no longer satisfies tool schema: {validation_error}")
                if canonical_arguments_sha256(normalized) != request.arguments_sha256:
                    request.status = "stale"
                    raise ValueError("approval request arguments changed after canonical normalization")
                request, grant = state.session.tool_approvals.approve_request(
                    request_id,
                    actor=DEFAULT_APPROVAL_ACTOR,
                    ttl_seconds=req.ttl_seconds,
                )
                state.agent._active_tool_call_id = f"approval-{request.request_id}"
                result = json.loads(
                    execute_tool(
                        state.agent,
                        request.tool_name,
                        normalized,
                    )
                )
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except (json.JSONDecodeError, TypeError) as exc:
                raise HTTPException(status_code=500, detail="approved tool returned invalid JSON") from exc
            return {
                "approved": True,
                "executed": bool(result.get("success")),
                "request": request.to_public_dict(),
                "grant": grant.to_public_dict(),
                "result": result,
            }

    @app.post("/api/approvals/{request_id}/reject")
    async def reject_tool_call(request_id: str, req: ApprovalDecisionRequest):
        async with operation_lock:
            state = get_app_state(dry_run=dry_run)
            request = state.session.tool_approvals.get_request(
                request_id,
                actor=DEFAULT_APPROVAL_ACTOR,
            )
            if request is None:
                raise HTTPException(status_code=404, detail="approval request not found for actor")
            if request.status != "pending":
                raise HTTPException(status_code=409, detail=f"approval request is {request.status}")
            request.status = "rejected"
            return {"rejected": True, "request": request.to_public_dict()}
