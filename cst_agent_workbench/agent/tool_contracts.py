"""Pure helpers for interpreting and enforcing canonical Agent tool contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator

from cst_agent_workbench.agent.tools import TOOLS


@dataclass(frozen=True)
class ToolArgumentViolation:
    """One stable, JSON-serializable contract violation."""

    path: str
    validator: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "validator": self.validator,
            "message": self.message,
        }


class ToolArgumentValidationError(ValueError):
    """Raised before a model-requested tool can reach a side-effecting handler."""

    def __init__(self, tool_name: str, violations: Sequence[ToolArgumentViolation]):
        self.tool_name = tool_name
        self.violations = tuple(violations)
        summary = "; ".join(
            f"{item.path}: {item.message}" for item in self.violations[:3]
        )
        super().__init__(f"工具参数不符合 {tool_name} 契约: {summary}")


def declared_parameter_schema(
    tool_name: str,
    *,
    tool_catalog: Sequence[Mapping[str, Any]] = TOOLS,
) -> Mapping[str, Any] | None:
    """Return the canonical top-level parameter schema for ``tool_name``."""

    for tool in tool_catalog:
        function = tool.get("function")
        if not isinstance(function, Mapping) or function.get("name") != tool_name:
            continue
        parameters = function.get("parameters")
        return parameters if isinstance(parameters, Mapping) else None
    return None


def declared_argument_defaults(
    tool_name: str,
    *,
    tool_catalog: Sequence[Mapping[str, Any]] = TOOLS,
) -> dict[str, Any]:
    """Return JSON-schema defaults declared by one canonical tool."""

    parameters = declared_parameter_schema(tool_name, tool_catalog=tool_catalog)
    if not isinstance(parameters, Mapping):
        return {}
    properties = parameters.get("properties")
    if not isinstance(properties, Mapping):
        return {}
    return {
        str(name): deepcopy(schema["default"])
        for name, schema in properties.items()
        if isinstance(schema, Mapping) and "default" in schema
    }


def apply_declared_argument_defaults(
    tool_name: str,
    arguments: Mapping[str, Any] | None,
    *,
    tool_catalog: Sequence[Mapping[str, Any]] = TOOLS,
) -> dict[str, Any]:
    """Canonicalize omitted optional arguments without overriding explicit values."""

    normalized = declared_argument_defaults(tool_name, tool_catalog=tool_catalog)
    normalized.update(dict(arguments or {}))
    return normalized


def normalize_and_validate_tool_arguments(
    tool_name: str,
    arguments: Any,
    *,
    tool_catalog: Sequence[Mapping[str, Any]] = TOOLS,
) -> dict[str, Any]:
    """Apply defaults and enforce the exact schema used for model tool selection.

    Unknown tool names remain the execution runtime's responsibility so callers
    still receive the established ``unknown tool`` result. Known tools fail here
    before any CST, filesystem, solver, or session side effect can occur.
    """

    schema = declared_parameter_schema(tool_name, tool_catalog=tool_catalog)
    if schema is None:
        return dict(arguments) if isinstance(arguments, Mapping) else {}
    if not isinstance(arguments, Mapping):
        raise ToolArgumentValidationError(
            tool_name,
            [
                ToolArgumentViolation(
                    path="$",
                    validator="type",
                    message=f"expected object, received {type(arguments).__name__}",
                )
            ],
        )

    normalized = apply_declared_argument_defaults(
        tool_name,
        arguments,
        tool_catalog=tool_catalog,
    )
    errors = sorted(
        Draft202012Validator(schema).iter_errors(normalized),
        key=lambda item: (list(item.absolute_path), item.message),
    )
    if errors:
        violations = []
        for error in errors:
            path = "$"
            for part in error.absolute_path:
                path += f"[{part}]" if isinstance(part, int) else f".{part}"
            violations.append(
                ToolArgumentViolation(
                    path=path,
                    validator=str(error.validator or "schema"),
                    message=error.message,
                )
            )
        raise ToolArgumentValidationError(tool_name, violations)
    return normalized
