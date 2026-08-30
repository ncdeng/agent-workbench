"""Parameter-bound human approval for high-risk CST Agent tool calls."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping


DEFAULT_APPROVAL_ACTOR = "local-desktop-user"
APPROVAL_REQUEST_TTL_SECONDS = 600
MAX_APPROVAL_GRANT_TTL_SECONDS = 600

# Keep this deliberately small. Typed CST operations remain autonomous; raw
# code execution crosses the safety boundary because it can bypass every typed
# contract and controller guard.
APPROVAL_REQUIRED_TOOLS = frozenset({"execute_vba_script"})


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def canonical_arguments_json(arguments: Mapping[str, Any]) -> str:
    """Serialize normalized arguments deterministically without changing arrays."""

    return json.dumps(
        dict(arguments),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_arguments_sha256(arguments: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_arguments_json(arguments).encode("utf-8")).hexdigest()


def requires_tool_approval(tool_name: str) -> bool:
    return str(tool_name) in APPROVAL_REQUIRED_TOOLS


def _validate_actor(actor: str) -> str:
    normalized = str(actor or "").strip()
    if not normalized:
        raise ValueError("approval actor must not be empty")
    return normalized


def _validate_ttl(ttl_seconds: int, *, maximum: int) -> int:
    ttl = int(ttl_seconds)
    if ttl < 1 or ttl > maximum:
        raise ValueError(f"approval ttl_seconds must be between 1 and {maximum}")
    return ttl


@dataclass
class ToolApprovalRequest:
    request_id: str
    tool_name: str
    normalized_arguments: dict[str, Any]
    arguments_sha256: str
    actor: str
    created_at: datetime
    expires_at: datetime
    status: str = "pending"
    grant_id: str = ""

    def to_public_dict(self, *, include_arguments: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "request_id": self.request_id,
            "tool_name": self.tool_name,
            "arguments_sha256": self.arguments_sha256,
            "created_at": _iso(self.created_at),
            "expires_at": _iso(self.expires_at),
            "status": self.status,
            "preview": _arguments_preview(self.tool_name, self.normalized_arguments),
        }
        if include_arguments:
            payload["arguments"] = dict(self.normalized_arguments)
        return payload


@dataclass
class ToolApprovalGrant:
    approval_id: str
    request_id: str
    tool_name: str
    arguments_sha256: str
    actor: str
    issued_at: datetime
    expires_at: datetime
    consumed_at: datetime | None = None

    @property
    def consumed(self) -> bool:
        return self.consumed_at is not None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "request_id": self.request_id,
            "tool_name": self.tool_name,
            "arguments_sha256": self.arguments_sha256,
            "issued_at": _iso(self.issued_at),
            "expires_at": _iso(self.expires_at),
            "consumed_at": _iso(self.consumed_at) if self.consumed_at else None,
            "consumed": self.consumed,
        }


def _arguments_preview(tool_name: str, arguments: Mapping[str, Any]) -> str:
    if tool_name == "execute_vba_script":
        text = str(arguments.get("vba_code") or "").strip()
        return text[:500] + ("…" if len(text) > 500 else "")
    text = canonical_arguments_json(arguments)
    return text[:500] + ("…" if len(text) > 500 else "")


@dataclass
class ToolApprovalStore:
    """Session-owned, in-memory requests and single-use grants."""

    requests: dict[str, ToolApprovalRequest] = field(default_factory=dict)
    grants: dict[str, ToolApprovalGrant] = field(default_factory=dict)
    retention_limit: int = 50

    def request(
        self,
        tool_name: str,
        normalized_arguments: Mapping[str, Any],
        *,
        actor: str,
        now: datetime | None = None,
        ttl_seconds: int = APPROVAL_REQUEST_TTL_SECONDS,
    ) -> ToolApprovalRequest:
        current = now or _utc_now()
        normalized_actor = _validate_actor(actor)
        ttl = _validate_ttl(ttl_seconds, maximum=APPROVAL_REQUEST_TTL_SECONDS)
        arguments = dict(normalized_arguments)
        digest = canonical_arguments_sha256(arguments)
        for request in reversed(list(self.requests.values())):
            if (
                request.status == "pending"
                and request.tool_name == tool_name
                and request.arguments_sha256 == digest
                and request.actor == normalized_actor
                and request.expires_at > current
            ):
                return request
        request = ToolApprovalRequest(
            request_id=uuid.uuid4().hex[:16],
            tool_name=str(tool_name),
            normalized_arguments=arguments,
            arguments_sha256=digest,
            actor=normalized_actor,
            created_at=current,
            expires_at=current + timedelta(seconds=ttl),
        )
        self.requests[request.request_id] = request
        self._enforce_retention()
        return request

    def get_request(
        self,
        request_id: str,
        *,
        actor: str,
        now: datetime | None = None,
    ) -> ToolApprovalRequest | None:
        current = now or _utc_now()
        request = self.requests.get(str(request_id))
        if request is None or request.actor != _validate_actor(actor):
            return None
        if request.expires_at <= current and request.status == "pending":
            request.status = "expired"
        return request

    def approve_request(
        self,
        request_id: str,
        *,
        actor: str,
        now: datetime | None = None,
        ttl_seconds: int = 120,
    ) -> tuple[ToolApprovalRequest, ToolApprovalGrant]:
        current = now or _utc_now()
        request = self.get_request(request_id, actor=actor, now=current)
        if request is None:
            raise ValueError("approval request not found for actor")
        if request.status != "pending" or request.expires_at <= current:
            raise ValueError(f"approval request is {request.status}")
        grant = self.issue(
            request.tool_name,
            request.normalized_arguments,
            actor=actor,
            request_id=request.request_id,
            now=current,
            ttl_seconds=ttl_seconds,
        )
        request.status = "approved"
        request.grant_id = grant.approval_id
        return request, grant

    def issue(
        self,
        tool_name: str,
        normalized_arguments: Mapping[str, Any],
        *,
        actor: str,
        request_id: str = "",
        now: datetime | None = None,
        ttl_seconds: int = 120,
    ) -> ToolApprovalGrant:
        current = now or _utc_now()
        ttl = _validate_ttl(ttl_seconds, maximum=MAX_APPROVAL_GRANT_TTL_SECONDS)
        grant = ToolApprovalGrant(
            approval_id=uuid.uuid4().hex[:16],
            request_id=str(request_id),
            tool_name=str(tool_name),
            arguments_sha256=canonical_arguments_sha256(normalized_arguments),
            actor=_validate_actor(actor),
            issued_at=current,
            expires_at=current + timedelta(seconds=ttl),
        )
        self.grants[grant.approval_id] = grant
        self._enforce_retention()
        return grant

    def consume(
        self,
        tool_name: str,
        normalized_arguments: Mapping[str, Any],
        *,
        actor: str,
        now: datetime | None = None,
    ) -> ToolApprovalGrant | None:
        current = now or _utc_now()
        normalized_actor = _validate_actor(actor)
        digest = canonical_arguments_sha256(normalized_arguments)
        for grant in reversed(list(self.grants.values())):
            if (
                not grant.consumed
                and grant.tool_name == tool_name
                and grant.arguments_sha256 == digest
                and grant.actor == normalized_actor
                and grant.expires_at > current
            ):
                grant.consumed_at = current
                if grant.request_id in self.requests:
                    self.requests[grant.request_id].status = "consumed"
                return grant
        return None

    def revoke(self, approval_id: str) -> None:
        self.grants.pop(str(approval_id), None)

    def clear(self) -> None:
        """Revoke every pending request and grant owned by this session.

        Approval capabilities must not survive a user-visible session clear or
        a CST project transition.  Dropping both mappings also guarantees that
        an old request id cannot be approved after either boundary changes.
        """

        self.requests.clear()
        self.grants.clear()

    def projection(self, *, now: datetime | None = None) -> dict[str, Any]:
        current = now or _utc_now()
        pending = [
            request.to_public_dict()
            for request in self.requests.values()
            if request.status == "pending" and request.expires_at > current
        ]
        active = [
            grant.to_public_dict()
            for grant in self.grants.values()
            if not grant.consumed and grant.expires_at > current
        ]
        return {
            "pending_count": len(pending),
            "active_grant_count": len(active),
            "pending_requests": pending[-10:],
        }

    def _enforce_retention(self) -> None:
        while len(self.requests) > self.retention_limit:
            self.requests.pop(next(iter(self.requests)))
        while len(self.grants) > self.retention_limit:
            self.grants.pop(next(iter(self.grants)))
