"""Versioned, JSON-safe context contract for executor Harnesses."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

CONTEXT_ENVELOPE_VERSION = 1


class ContextEnvelopeError(ValueError):
    pass


@dataclass(frozen=True)
class ContextEnvelope:
    messages: tuple[Mapping[str, Any], ...]
    tools: tuple[Mapping[str, Any], ...]
    plan: Mapping[str, Any] | None
    budget: Mapping[str, Any]
    execution: Mapping[str, Any]
    version: int = CONTEXT_ENVELOPE_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "messages": deepcopy(list(self.messages)),
            "tools": deepcopy(list(self.tools)),
            "plan": deepcopy(dict(self.plan)) if self.plan is not None else None,
            "budget": deepcopy(dict(self.budget)),
            "execution": deepcopy(dict(self.execution)),
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ContextEnvelope":
        if not isinstance(raw, Mapping):
            raise ContextEnvelopeError("context envelope must be an object")
        version = raw.get("version")
        if version != CONTEXT_ENVELOPE_VERSION:
            raise ContextEnvelopeError(f"unsupported context envelope version: {version!r}")
        messages = _object_sequence(raw.get("messages"), field_name="messages")
        tools = _object_sequence(raw.get("tools"), field_name="tools")
        plan = raw.get("plan")
        if plan is not None and not isinstance(plan, Mapping):
            raise ContextEnvelopeError("context plan must be an object or null")
        budget = raw.get("budget")
        execution = raw.get("execution")
        if not isinstance(budget, Mapping):
            raise ContextEnvelopeError("context budget must be an object")
        if not isinstance(execution, Mapping):
            raise ContextEnvelopeError("context execution must be an object")
        return cls(
            messages=tuple(deepcopy(messages)),
            tools=tuple(deepcopy(tools)),
            plan=deepcopy(dict(plan)) if plan is not None else None,
            budget=deepcopy(dict(budget)),
            execution=deepcopy(dict(execution)),
            version=CONTEXT_ENVELOPE_VERSION,
        )


def build_context_envelope(
    *,
    messages: Sequence[Mapping[str, Any]],
    tools: Sequence[Mapping[str, Any]],
    active_plan: Mapping[str, Any] | None,
    budget_metrics: Mapping[str, Any],
    max_context_tokens: int,
    continuation_prompt: str,
    optimization_mode: bool,
) -> ContextEnvelope:
    return ContextEnvelope(
        messages=tuple(deepcopy(list(messages))),
        tools=tuple(deepcopy(list(tools))),
        plan=deepcopy(dict(active_plan)) if active_plan is not None else None,
        budget={
            **deepcopy(dict(budget_metrics)),
            "max_context_tokens": int(max_context_tokens),
        },
        execution={
            "continuation_prompt": str(continuation_prompt),
            "optimization_mode": bool(optimization_mode),
        },
    )


def _object_sequence(value: Any, *, field_name: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        raise ContextEnvelopeError(f"context {field_name} must be an array")
    if any(not isinstance(item, Mapping) for item in value):
        raise ContextEnvelopeError(f"context {field_name} entries must be objects")
    return value
