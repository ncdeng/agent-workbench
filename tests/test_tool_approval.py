from datetime import datetime, timedelta, timezone

import pytest

from cst_agent_workbench.agent.tool_approval import (
    DEFAULT_APPROVAL_ACTOR,
    ToolApprovalStore,
    canonical_arguments_sha256,
    requires_tool_approval,
)
from cst_agent_workbench.agent.tool_contracts import normalize_and_validate_tool_arguments


NOW = datetime(2026, 8, 12, tzinfo=timezone.utc)


def test_argument_hash_is_key_order_independent_but_value_and_array_order_sensitive():
    first = canonical_arguments_sha256({"b": [1, 2], "a": {"x": 1}})

    assert first == canonical_arguments_sha256({"a": {"x": 1}, "b": [1, 2]})
    assert first != canonical_arguments_sha256({"a": {"x": 2}, "b": [1, 2]})
    assert first != canonical_arguments_sha256({"a": {"x": 1}, "b": [2, 1]})


def test_grant_is_actor_bound_parameter_bound_single_use_and_expires():
    store = ToolApprovalStore()
    arguments = {"vba_code": "Sub Main()\nEnd Sub", "description": "safe probe"}
    store.issue(
        "execute_vba_script",
        arguments,
        actor=DEFAULT_APPROVAL_ACTOR,
        now=NOW,
        ttl_seconds=60,
    )

    assert store.consume(
        "execute_vba_script",
        {"description": "safe probe", "vba_code": "Sub Main()\nEnd Sub"},
        actor="another-user",
        now=NOW,
    ) is None
    assert store.consume(
        "execute_vba_script",
        {**arguments, "vba_code": "Sub Main()\nDebug.Print 1\nEnd Sub"},
        actor=DEFAULT_APPROVAL_ACTOR,
        now=NOW,
    ) is None

    grant = store.consume(
        "execute_vba_script",
        arguments,
        actor=DEFAULT_APPROVAL_ACTOR,
        now=NOW + timedelta(seconds=1),
    )
    assert grant is not None
    assert grant.consumed is True
    assert store.consume(
        "execute_vba_script",
        arguments,
        actor=DEFAULT_APPROVAL_ACTOR,
        now=NOW + timedelta(seconds=2),
    ) is None

    store.issue(
        "execute_vba_script",
        arguments,
        actor=DEFAULT_APPROVAL_ACTOR,
        now=NOW,
        ttl_seconds=1,
    )
    assert store.consume(
        "execute_vba_script",
        arguments,
        actor=DEFAULT_APPROVAL_ACTOR,
        now=NOW + timedelta(seconds=1),
    ) is None


def test_pending_request_is_deduplicated_and_approved_grant_keeps_request_identity():
    store = ToolApprovalStore()
    arguments = {"vba_code": "Sub Main()\nEnd Sub", "description": ""}

    first = store.request(
        "execute_vba_script",
        arguments,
        actor=DEFAULT_APPROVAL_ACTOR,
        now=NOW,
    )
    second = store.request(
        "execute_vba_script",
        arguments,
        actor=DEFAULT_APPROVAL_ACTOR,
        now=NOW + timedelta(seconds=1),
    )
    request, grant = store.approve_request(
        first.request_id,
        actor=DEFAULT_APPROVAL_ACTOR,
        now=NOW + timedelta(seconds=2),
    )

    assert first.request_id == second.request_id
    assert request.status == "approved"
    assert grant.request_id == first.request_id
    assert store.projection(now=NOW + timedelta(seconds=3))["pending_count"] == 0


def test_only_explicit_high_risk_tools_require_approval_and_ttl_is_bounded():
    assert requires_tool_approval("execute_vba_script") is True
    assert requires_tool_approval("create_brick") is False
    assert requires_tool_approval("run_solver") is False

    with pytest.raises(ValueError, match="ttl_seconds"):
        ToolApprovalStore().issue(
            "execute_vba_script",
            {"vba_code": "x"},
            actor=DEFAULT_APPROVAL_ACTOR,
            ttl_seconds=601,
        )


def test_canonical_defaults_make_omitted_and_explicit_values_hash_identical():
    omitted = normalize_and_validate_tool_arguments("set_units", {})
    explicit = normalize_and_validate_tool_arguments(
        "set_units",
        {"geometry": "mm", "frequency": "GHz", "time": "ns"},
    )

    assert omitted == explicit
    assert canonical_arguments_sha256(omitted) == canonical_arguments_sha256(explicit)


def test_clear_revokes_pending_requests_and_active_grants():
    store = ToolApprovalStore()
    pending = store.request(
        "execute_vba_script",
        {"vba_code": "Sub Pending()\nEnd Sub", "description": "pending"},
        actor=DEFAULT_APPROVAL_ACTOR,
        now=NOW,
    )
    store.issue(
        "execute_vba_script",
        {"vba_code": "Sub Approved()\nEnd Sub", "description": "approved"},
        actor=DEFAULT_APPROVAL_ACTOR,
        now=NOW,
    )

    store.clear()

    assert store.projection(now=NOW)["pending_count"] == 0
    assert store.projection(now=NOW)["active_grant_count"] == 0
    assert store.get_request(pending.request_id, actor=DEFAULT_APPROVAL_ACTOR, now=NOW) is None
