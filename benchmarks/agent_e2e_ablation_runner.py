"""Full-agent E2E ablation runner over a frozen Fake-CST dataset.

Unlike ``agent_ablation_runner.py`` (optimization-proposal evaluation), this
runner always enters through ``CSTAgent.chat`` and therefore exercises the
production planner, context builder, tool loop, recovery engine, memory and
trace lifecycle.  The deterministic provider is a mechanism smoke test; use
``openai_compatible`` for model-quality claims.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import random
import statistics
import subprocess
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPORT_SCHEMA = ROOT / "benchmarks" / "agent_e2e_report.schema.json"
CASE_SCHEMA = ROOT / "benchmarks" / "agent_e2e_case.schema.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.fake_cst_agent_adapter import (  # noqa: E402
    DeterministicAgentClient,
    FakeCSTAgentAdapter,
    RecordingClient,
)
from benchmarks.eval_statistics import exact_paired_binary_test  # noqa: E402
from cst_agent_workbench import config  # noqa: E402
from cst_agent_workbench.agent.agent import CSTAgent  # noqa: E402
from cst_agent_workbench.agent.memory import MemoryManager, StructuredMemory  # noqa: E402
from cst_agent_workbench.agent.tool_contracts import (  # noqa: E402
    apply_declared_argument_defaults,
)
from cst_agent_workbench.agent.tool_use_memory import (  # noqa: E402
    ToolUseMemoryRecord,
    ToolUseMemoryStore,
)
from cst_agent_workbench.cst.primitives import reset_created_objects  # noqa: E402


GROUPS: dict[str, dict[str, bool]] = {
    "full": {"memory": True, "context": True, "planner": True, "recovery": True, "tool_use_memory": True},
    "no_memory": {"memory": False, "context": True, "planner": True, "recovery": True, "tool_use_memory": True},
    "no_context": {"memory": True, "context": False, "planner": True, "recovery": True, "tool_use_memory": True},
    "no_planner": {"memory": True, "context": True, "planner": False, "recovery": True, "tool_use_memory": True},
    "no_recovery": {"memory": True, "context": True, "planner": True, "recovery": False, "tool_use_memory": True},
    "no_tool_use_memory": {"memory": True, "context": True, "planner": True, "recovery": True, "tool_use_memory": False},
}


def _schema_errors(instance: dict[str, Any], schema_path: Path) -> list[str]:
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:  # pragma: no cover - dependency contract
        raise RuntimeError("jsonschema is required; install the test or dev extra") from exc
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    return [
        f"{'.'.join(map(str, item.path)) or '<root>'}: {item.message}"
        for item in sorted(
            Draft202012Validator(schema).iter_errors(instance),
            key=lambda item: list(item.path),
        )
    ]


def validate_dataset_schema(dataset: dict[str, Any], schema_path: Path = CASE_SCHEMA) -> None:
    errors = _schema_errors(dataset, schema_path)
    if errors:
        raise ValueError(f"agent E2E dataset schema validation failed: {'; '.join(errors[:10])}")
    case_ids = [str(case.get("case_id") or "") for case in dataset["cases"]]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("agent E2E dataset contains duplicate case_id values")
    seeds = [int(case.get("seed", 0) or 0) for case in dataset["cases"]]
    if len(seeds) != len(set(seeds)):
        raise ValueError("agent E2E dataset contains duplicate case seeds")
    for case in dataset["cases"]:
        oracle = dict(case.get("oracle") or {})
        allowed = {str(name) for name in oracle.get("allowed_tools") or []}
        required = {str(name) for name in oracle.get("required_tools_all") or []}
        forbidden = {str(name) for name in oracle.get("forbidden_tools") or []}
        max_calls = int(oracle.get("max_tool_calls", 0) or 0)
        sequences = [list(map(str, sequence)) for sequence in oracle.get("valid_tool_sequences") or []]
        if not required <= allowed:
            raise ValueError(f"{case['case_id']}: required tools must be included in allowed_tools")
        if allowed & forbidden:
            raise ValueError(f"{case['case_id']}: allowed_tools and forbidden_tools must be disjoint")
        for sequence in sequences:
            if not set(sequence) <= allowed:
                raise ValueError(f"{case['case_id']}: valid tool sequence exceeds allowed_tools")
            if len(sequence) > max_calls:
                raise ValueError(f"{case['case_id']}: valid tool sequence exceeds max_tool_calls")
        if max_calls == 0 and sequences != [[]]:
            raise ValueError(f"{case['case_id']}: zero-tool oracle must declare exactly [[]]")
        for expected in oracle.get("expected_tool_arguments") or []:
            if str(expected.get("tool_name") or "") not in allowed:
                raise ValueError(
                    f"{case['case_id']}: argument oracle tool must be included in allowed_tools"
                )
        for injected in (case.get("fixture") or {}).get("injected_failures") or []:
            method = str(injected.get("method") or "")
            if not method or not hasattr(FakeCSTAgentAdapter, method):
                raise ValueError(f"{case['case_id']}: unsupported injected failure method {method!r}")
    if dataset.get("split") in {"held_out", "frozen_evaluation"}:
        required_metadata = ("dataset_role", "frozen_at", "annotation_policy", "generation_provenance")
        missing = [name for name in required_metadata if not dataset.get(name)]
        if missing:
            raise ValueError(f"frozen dataset is missing metadata: {', '.join(missing)}")
        family_fields = ("scenario_family", "design_family", "failure_family")
        incomplete = [
            str(case["case_id"])
            for case in dataset["cases"]
            if any(not case.get(name) for name in family_fields)
        ]
        if incomplete:
            raise ValueError(f"frozen dataset cases are missing family metadata: {incomplete[:5]}")
        if not 30 <= len(dataset["cases"]) <= 50:
            raise ValueError("frozen Agent E2E dataset must contain 30 to 50 unique cases")
        coverage_fields = {
            "category": 3,
            "scenario_family": 3,
            "design_family": 3,
            "failure_family": 2,
        }
        for field, minimum_distinct in coverage_fields.items():
            counts = Counter(str(case.get(field) or "") for case in dataset["cases"])
            if len(counts) < minimum_distinct or min(counts.values()) < 2:
                raise ValueError(
                    f"frozen dataset {field} coverage requires at least "
                    f"{minimum_distinct} families and two cases per family"
                )


def _load_dataset(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    validate_dataset_schema(data)
    return data, hashlib.sha256(raw).hexdigest()


def _portable_path(path: Path) -> str:
    """Record repository-relative paths so reports stay machine-independent.

    Absolute paths embed the developer's Windows user name and directory layout,
    which then ship inside published evidence artifacts. Anything outside the
    repository is still recorded verbatim because it carries no portable form.
    """
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return str(resolved)


def _verify_dataset_manifest(
    *,
    dataset_path: Path,
    dataset: dict[str, Any],
    dataset_sha256: str,
    manifest_path: Path | None,
) -> dict[str, Any]:
    frozen = dataset.get("split") in {"held_out", "frozen_evaluation"}
    if manifest_path is None:
        if frozen:
            raise ValueError("frozen Agent E2E evaluation requires a manifest")
        return {
            "path": None,
            "sha256": None,
            "verified": False,
            "case_ids": [],
        }
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {
        "dataset_id": dataset["dataset_id"],
        "dataset_split": dataset["split"],
        "dataset_role": dataset.get("dataset_role", "unspecified"),
        "dataset_sha256": dataset_sha256,
        "case_count": len(dataset["cases"]),
        "case_ids": [str(case["case_id"]) for case in dataset["cases"]],
    }
    mismatches = [name for name, value in expected.items() if manifest.get(name) != value]
    if mismatches:
        raise ValueError(
            "Agent E2E manifest mismatch for "
            f"{dataset_path.resolve()}: {', '.join(mismatches)}"
        )
    return {
        "path": _portable_path(manifest_path),
        "sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "verified": True,
        "case_ids": list(expected["case_ids"]),
    }


def validate_report_schema(report: dict[str, Any], schema_path: Path = REPORT_SCHEMA) -> None:
    """Fail the benchmark when its machine-readable report drifts from the schema."""
    errors = _schema_errors(report, schema_path)
    if errors:
        raise ValueError(f"agent E2E report schema validation failed: {'; '.join(errors[:10])}")


def _git_revision() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _harness_identity(agent_brain: str) -> dict[str, Any]:
    relative_paths = [
        "cst_agent_workbench/agent/agent.py",
        "cst_agent_workbench/agent/runtime.py",
        "cst_agent_workbench/agent/tool_runtime.py",
    ]
    if agent_brain == "pi":
        relative_paths.extend(
            [
                "cst_agent_workbench/agent/pi_brain.py",
                "integrations/pi_agent_core/sidecar.mjs",
                "integrations/pi_agent_core/package.json",
                "integrations/pi_agent_core/package-lock.json",
            ]
        )
    files: dict[str, str] = {}
    combined = hashlib.sha256()
    for relative_path in relative_paths:
        path = ROOT / relative_path
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        files[relative_path] = digest
        combined.update(relative_path.encode("utf-8"))
        combined.update(b"\0")
        combined.update(digest.encode("ascii"))
        combined.update(b"\n")
    return {
        "agent_brain": agent_brain,
        "files": files,
        "combined_sha256": combined.hexdigest(),
    }


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * percentile)))
    return ordered[index]


def _token_snapshot(agent: CSTAgent) -> dict[str, int]:
    stats = getattr(agent, "token_stats", {}) or {}
    prompt = int(stats.get("prompt", 0) or 0)
    completion = int(stats.get("completion", 0) or 0)
    return {"prompt": prompt, "completion": completion, "total": prompt + completion, "calls": int(stats.get("calls", 0) or 0)}


def _reset_structured_memory(agent: CSTAgent) -> None:
    agent.session.memory = StructuredMemory()


def _clear_conversation_context(agent: CSTAgent) -> None:
    agent.history.clear()
    conversation = agent.session.memory.conversation
    conversation.user_goal = ""
    conversation.constraints = []
    conversation.constraint_records = []
    conversation.recent_summary = ""
    conversation.pending_questions = []
    agent.session.active_plan = None
    agent.session.metadata.pop("active_plan_user_message", None)


def _seed_structured_memory(agent: CSTAgent, fixture: dict[str, Any]) -> None:
    agent._refresh_session_memory_from_runtime()
    project_scope = str((agent.session.metadata.get("memory_scope") or {}).get("project_scope") or "")
    for index, lesson in enumerate(fixture.get("structured_lessons") or [], start=1):
        MemoryManager.update_decisions(
            agent.session.memory,
            strategy_entry={
                "round": index,
                "lesson": str(lesson),
                "effective_action": str(lesson),
                "reuse_condition": "CST status safety audit",
            },
            project_scope=project_scope,
        )


def _seed_tool_use_memory(agent: CSTAgent, fixture: dict[str, Any]) -> None:
    project_scope = str((agent.session.metadata.get("memory_scope") or {}).get("project_scope") or "")
    store = ToolUseMemoryStore()
    for item in fixture.get("tool_use_memory") or []:
        metadata = dict(item.get("metadata") or {})
        metadata.setdefault("project_scope", project_scope)
        metadata.setdefault("design_signature", "")
        store.add(
            ToolUseMemoryRecord(
                task_signature=str(item.get("task_signature") or ""),
                selected_tools=tuple(str(name) for name in item.get("selected_tools") or []),
                success=bool(item.get("success")),
                failure_reason=str(item.get("failure_reason") or ""),
                corrective_hint=str(item.get("corrective_hint") or ""),
                confidence=float(item.get("confidence", 0.8)),
                metadata=metadata,
            )
        )
    agent.session.tool_use_memory = store


def _inject_failures(cst: FakeCSTAgentAdapter, fixture: dict[str, Any]) -> None:
    for injected in fixture.get("injected_failures") or []:
        method = str(injected.get("method") or "")
        for response in injected.get("responses") or []:
            cst.queue_response(method, dict(response))


def _make_agent(
    *,
    case: dict[str, Any],
    group_name: str,
    group: dict[str, bool],
    provider: str,
    agent_brain: str,
    model: str,
    artifact_root: Path,
    run_index: int = 1,
) -> tuple[CSTAgent, Any, FakeCSTAgentAdapter]:
    case_root = artifact_root / group_name / str(case["case_id"]) / f"repeat_{run_index:02d}"
    case_root.mkdir(parents=True, exist_ok=True)
    config.AGENT_MEMORY_DIR = str(case_root / "memory")
    config.AGENT_BRAIN = agent_brain
    cst = FakeCSTAgentAdapter(
        project_path=str(case_root / "fake_project.cst"),
        connected=bool((case.get("fixture") or {}).get("connected", True)),
    )
    _inject_failures(cst, case.get("fixture") or {})
    if provider == "deterministic_proxy":
        # CSTAgent normally constructs an HTTP client before the benchmark
        # replaces it. Avoid that irrelevant socket/client setup in the offline
        # mechanism smoke while preserving the same agent implementation.
        import cst_agent_workbench.agent.agent as agent_module

        openai_class = agent_module.OpenAI
        agent_module.OpenAI = None
        try:
            agent = CSTAgent(cst)
        finally:
            agent_module.OpenAI = openai_class
    else:
        agent = CSTAgent(cst)
    agent.model = model
    if agent_brain == "pi":
        from cst_agent_workbench.agent.pi_brain import PiAgentBrain

        agent._pi_brain = PiAgentBrain(
            node_executable=config.PI_NODE_EXECUTABLE,
            sidecar_path=config.PI_SIDECAR_PATH,
            timeout_sec=config.PI_SIDECAR_TIMEOUT_SEC,
            api_key=config.PI_API_KEY,
            base_url=config.PI_BASE_URL,
            model=model,
            context_window=config.PI_CONTEXT_WINDOW,
            max_output_tokens=config.PI_MAX_OUTPUT_TOKENS,
            thinking_level=config.PI_THINKING_LEVEL,
        )

    if provider == "deterministic_proxy":
        client = DeterministicAgentClient()
    else:
        if not config.OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is required for provider=openai_compatible")
        if agent.client is None:
            raise RuntimeError("OpenAI client dependency is unavailable")
        client = RecordingClient(agent.client)
    agent.client = client

    fixture = case.get("fixture") or {}
    if group["memory"]:
        _seed_structured_memory(agent, fixture)
    else:
        _reset_structured_memory(agent)
    if group["tool_use_memory"]:
        _seed_tool_use_memory(agent, fixture)
    else:
        agent.session.tool_use_memory = ToolUseMemoryStore()
    if not group["recovery"]:
        agent._failure_recovery_engine = None
    return agent, client, cst


def _flatten_request_text(calls: list[dict[str, Any]]) -> str:
    return json.dumps(calls, ensure_ascii=False, default=str)


def _case_path_proof(
    *,
    case: dict[str, Any],
    agent: CSTAgent,
    client: Any,
) -> dict[str, Any]:
    calls = list(getattr(client, "calls", []) or [])
    executor_calls = [call for call in calls if not call.get("is_planner")]
    trace_turns = [
        turn
        for trace in list(getattr(agent, "trace_history", []) or [])
        for turn in list(trace.get("turns") or [])
        if isinstance(turn, dict)
    ]
    first_turn = str((case.get("turns") or [{}])[0].get("content") or "")
    final_executor_text = (
        _flatten_request_text(
            [((trace_turns[-1].get("request") or {}).get("messages_full") or [])]
        )
        if trace_turns
        else _flatten_request_text(executor_calls[-1:])
    )
    recovery_attempts = agent.session.metadata.get("failure_recovery_attempts") or {}
    return {
        "planner_call_count": sum(1 for call in calls if call.get("is_planner")),
        "planner_fallback_count": int(
            agent.session.metadata.get("planner_llm_fallbacks", 0) or 0
        ),
        "executor_call_count": len(trace_turns) if trace_turns else len(executor_calls),
        "memory_recall_count": len(agent.session.metadata.get("planner_memory_recall") or []),
        "context_prior_turn_visible": (
            first_turn in final_executor_text if len(case.get("turns") or []) > 1 else None
        ),
        "recovery_attempt_count": sum(int(value or 0) for value in recovery_attempts.values()),
        "recovery_attempts": dict(recovery_attempts),
        "tool_memory_recall_count": len(agent.session.metadata.get("tool_use_memory_recall") or []),
        "tool_memory_reranked_tools": list(agent.session.metadata.get("tool_use_memory_reranked_tools") or []),
        "context_budget_event_count": len(agent.session.metadata.get("context_budget_events") or []),
    }


def _evaluate_tool_oracle(oracle: dict[str, Any], tool_names: list[str]) -> dict[str, Any]:
    """Evaluate tool constraints without confusing an empty value with a missing rule."""
    allowed_declared = "allowed_tools" in oracle
    allowed_tools = {str(name) for name in oracle.get("allowed_tools") or []}
    required_tools = [str(name) for name in oracle.get("required_tools_all") or []]
    forbidden_tools = [str(name) for name in oracle.get("forbidden_tools") or []]
    sequence_options = [list(map(str, sequence)) for sequence in oracle.get("valid_tool_sequences") or []]

    required_ok = all(name in tool_names for name in required_tools)
    allowlist_ok = not allowed_declared or all(name in allowed_tools for name in tool_names)
    sequence_ok = not sequence_options or tool_names in sequence_options
    max_calls_declared = "max_tool_calls" in oracle
    max_tool_calls = int(oracle.get("max_tool_calls", 0) or 0)

    constraint_checks = [
        {"name": "allowed_tools", "passed": allowlist_ok},
        {"name": "required_tools_all", "passed": required_ok},
        {"name": "valid_tool_sequence", "passed": sequence_ok},
        *[
            {"name": f"forbid:{name}", "passed": name not in tool_names}
            for name in forbidden_tools
        ],
    ]
    if max_calls_declared:
        constraint_checks.append(
            {"name": "max_tool_calls", "passed": len(tool_names) <= max_tool_calls}
        )

    first_tool_options = {sequence[0] for sequence in sequence_options if sequence}
    return {
        "constraint_checks": constraint_checks,
        "hard_constraints_ok": all(item["passed"] for item in constraint_checks),
        "tool_sequence_ok": sequence_ok,
        "first_tool_ok": (
            not first_tool_options
            or (bool(tool_names) and tool_names[0] in first_tool_options)
        ),
        "invalid_calls": [
            name
            for name in tool_names
            if (allowed_declared and name not in allowed_tools) or name in forbidden_tools
        ],
    }


def _argument_value_matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        if "$contains" in expected:
            needles = expected["$contains"]
            values = needles if isinstance(needles, list) else [needles]
            actual_text = str(actual or "").casefold()
            if not all(str(value).casefold() in actual_text for value in values):
                return False
        if "$not_contains" in expected:
            needles = expected["$not_contains"]
            values = needles if isinstance(needles, list) else [needles]
            actual_text = str(actual or "").casefold()
            if any(str(value).casefold() in actual_text for value in values):
                return False
        if "$any_of" in expected:
            return any(
                _argument_value_matches(actual, candidate)
                for candidate in expected["$any_of"]
            )
        operator_names = {"$contains", "$not_contains", "$any_of"}
        expected_fields = {
            name: value for name, value in expected.items() if name not in operator_names
        }
        if not expected_fields:
            return True
        return isinstance(actual, dict) and all(
            name in actual and _argument_value_matches(actual[name], value)
            for name, value in expected_fields.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(_argument_value_matches(a, e) for a, e in zip(actual, expected))
        )
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        try:
            return abs(float(actual) - float(expected)) <= 1e-9
        except (TypeError, ValueError):
            return False
    return actual == expected


def _evaluate_argument_oracle(
    oracle: dict[str, Any],
    tool_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    checks = []
    for expected in oracle.get("expected_tool_arguments") or []:
        tool_name = str(expected.get("tool_name") or "")
        occurrence = int(expected.get("occurrence", 1) or 1)
        matching_events = [
            event for event in tool_events if str(event.get("tool_name") or "") == tool_name
        ]
        actual_arguments = (
            apply_declared_argument_defaults(
                tool_name,
                matching_events[occurrence - 1].get("arguments"),
            )
            if 0 < occurrence <= len(matching_events)
            else None
        )
        checks.append(
            {
                "name": f"arguments:{tool_name}:{occurrence}",
                "passed": actual_arguments is not None
                and _argument_value_matches(
                    actual_arguments,
                    dict(expected.get("arguments") or {}),
                ),
                "expected": dict(expected.get("arguments") or {}),
                "actual": actual_arguments,
            }
        )
    return checks


def _score_case(
    *,
    case: dict[str, Any],
    agent: CSTAgent,
    responses: list[str],
    latency_ms: float,
    token_before: dict[str, int],
    token_after: dict[str, int],
    client: Any,
    errors: list[str],
    run_index: int = 1,
    effective_seed: int | None = None,
    cst: FakeCSTAgentAdapter | None = None,
) -> dict[str, Any]:
    oracle = case.get("oracle") or {}
    tool_events = [dict(item) for item in agent.tool_events]
    tool_names = [str(item.get("tool_name") or "") for item in tool_events]
    final_response = responses[-1] if responses else ""
    tool_oracle = _evaluate_tool_oracle(oracle, tool_names)
    constraint_checks = list(tool_oracle["constraint_checks"])
    constraint_checks.extend(_evaluate_argument_oracle(oracle, tool_events))
    pending_failures = cst.pending_injected_responses() if cst is not None else {}
    if (case.get("fixture") or {}).get("injected_failures"):
        constraint_checks.append(
            {
                "name": "injected_failures_consumed",
                "passed": not pending_failures,
                "pending": pending_failures,
            }
        )

    recovery_events = [
        dict(item.get("recovery_result") or {})
        for item in tool_events
        if item.get("recovery_result")
    ]
    expected_recovery = oracle.get("expected_recovery")
    recovery_ok = True
    if expected_recovery:
        recovery_ok = any(
            item.get("action_name") == expected_recovery
            and bool(item.get("recovered"))
            and bool(item.get("retry_success"))
            for item in recovery_events
        )

    grounding_checks = []
    for fact in oracle.get("required_grounding_facts") or []:
        if isinstance(fact, dict):
            conditions = fact.get("all_of") or []
            condition_matches = []
            for condition in conditions:
                alternatives = (
                    [str(item) for item in condition]
                    if isinstance(condition, list)
                    else [str(condition)]
                )
                condition_matches.append(
                    any(item.lower() in final_response.lower() for item in alternatives)
                )
            grounding_checks.append(
                {
                    "fact": str(fact.get("label") or "structured_fact"),
                    "supported": bool(condition_matches) and all(condition_matches),
                    "condition_matches": condition_matches,
                }
            )
            continue
        alternatives = [str(item) for item in fact] if isinstance(fact, list) else [str(fact)]
        grounding_checks.append(
            {
                "fact": " | ".join(alternatives),
                "supported": any(item.lower() in final_response.lower() for item in alternatives),
            }
        )
    grounding_ok = all(item["supported"] for item in grounding_checks)
    tool_success_ok = all(bool(item.get("success")) for item in tool_events)
    execution_success = bool(
        not errors
        and final_response
        and agent.last_chat_status.get("ok")
        and all(item["passed"] for item in constraint_checks)
        and tool_success_ok
        and recovery_ok
    )
    strict_grounded_success = execution_success and grounding_ok
    latest_trace = agent.trace_history[-1] if agent.trace_history else {}
    provider_diagnostics = [
        {
            "is_planner": bool(item.get("is_planner")),
            "call_kind": str(item.get("call_kind") or ("planner" if item.get("is_planner") else "executor")),
            "response_content": str(item.get("response_content") or ""),
            "response_tool_names": list(item.get("response_tool_names") or []),
            "usage": dict(item.get("usage") or {}),
            "error": str(item.get("error") or ""),
        }
        for item in getattr(client, "calls", [])
        if item.get("response_content") or item.get("response_tool_names") or item.get("error")
    ]
    failed_constraints = [
        str(item["name"]) for item in constraint_checks if not item["passed"]
    ]
    failure_labels: list[str] = []
    if errors:
        failure_labels.append("runtime_exception")
    if not final_response:
        failure_labels.append("empty_final_response")
    if tool_oracle["invalid_calls"]:
        failure_labels.append("invalid_tool_call")
    if failed_constraints:
        failure_labels.append("hard_constraint_violation")
    if not tool_oracle["tool_sequence_ok"]:
        failure_labels.append("wrong_tool_sequence")
    if any(not bool(item.get("success")) for item in tool_events):
        failure_labels.append("tool_execution_failure")
    if expected_recovery and not recovery_ok:
        failure_labels.append("recovery_failure")
    if execution_success and not grounding_ok:
        failure_labels.append("grounding_or_context_failure")
    if latest_trace.get("status", "missing") != "completed":
        failure_labels.append("trace_incomplete")

    return {
        "case_id": case["case_id"],
        "sample_id": f"{case['case_id']}::repeat_{run_index:02d}",
        "run_index": run_index,
        "category": case.get("category", ""),
        "scenario_family": case.get("scenario_family", "unspecified"),
        "design_family": case.get("design_family", "unspecified"),
        "failure_family": case.get("failure_family", "unspecified"),
        "seed": int(effective_seed if effective_seed is not None else case.get("seed", 0) or 0),
        "valid": not errors,
        "task_success": execution_success,
        "execution_success": execution_success,
        "strict_grounded_success": strict_grounded_success,
        "constraint_checks": constraint_checks,
        "tool_calls": tool_names,
        "tool_events": tool_events,
        "tool_sequence_valid": tool_oracle["tool_sequence_ok"],
        "first_tool_valid": tool_oracle["first_tool_ok"],
        "invalid_calls": tool_oracle["invalid_calls"],
        "failed_constraints": failed_constraints,
        "pending_injected_failures": pending_failures,
        "failure_labels": failure_labels,
        "recovery_events": recovery_events,
        "recovery_success": recovery_ok if expected_recovery else None,
        "token_usage": {
            key: int(token_after.get(key, 0)) - int(token_before.get(key, 0))
            for key in ("prompt", "completion", "total", "calls")
        },
        "latency_ms": round(latency_ms, 3),
        "grounding_checks": grounding_checks,
        "final_response": final_response,
        "final_response_sha256": hashlib.sha256(final_response.encode("utf-8")).hexdigest(),
        "all_responses": responses,
        "trace_run_id": latest_trace.get("run_id", ""),
        "trace_status": latest_trace.get("status", "missing"),
        "path_proof": _case_path_proof(case=case, agent=agent, client=client),
        "provider_diagnostics": provider_diagnostics,
        "errors": errors,
    }


def _run_case(
    *,
    case: dict[str, Any],
    group_name: str,
    group: dict[str, bool],
    provider: str,
    agent_brain: str,
    model: str,
    artifact_root: Path,
    run_index: int = 1,
) -> dict[str, Any]:
    random_state = random.getstate()
    effective_seed = int(case.get("seed", 0) or 0) + max(0, run_index - 1)
    random.seed(effective_seed)
    reset_created_objects()
    try:
        agent, client, cst = _make_agent(
            case=case,
            group_name=group_name,
            group=group,
            provider=provider,
            agent_brain=agent_brain,
            model=model,
            artifact_root=artifact_root,
            run_index=run_index,
        )
        responses: list[str] = []
        errors: list[str] = []
        token_before = _token_snapshot(agent)
        started = time.perf_counter()
        for index, turn in enumerate(case.get("turns") or []):
            if index > 0 and not group["context"]:
                _clear_conversation_context(agent)
            if index > 0 and not group["memory"]:
                _reset_structured_memory(agent)
            if hasattr(client, "set_turn"):
                client.set_turn(turn)
            try:
                responses.append(
                    agent.chat(
                        str(turn.get("content") or ""),
                        skip_plan=not group["planner"],
                    )
                )
            except Exception as exc:  # the batch must preserve the failing case
                errors.append(f"{type(exc).__name__}: {exc}")
                break
        latency_ms = (time.perf_counter() - started) * 1000
        return _score_case(
            case=case,
            agent=agent,
            responses=responses,
            latency_ms=latency_ms,
            token_before=token_before,
            token_after=_token_snapshot(agent),
            client=client,
            errors=errors,
            run_index=run_index,
            effective_seed=effective_seed,
            cst=cst,
        )
    finally:
        random.setstate(random_state)
        reset_created_objects()


def _aggregate_path_proof(group_name: str, cases: list[dict[str, Any]]) -> dict[str, Any]:
    proof = {
        "planner_call_count": sum(item["path_proof"]["planner_call_count"] for item in cases),
        "planner_fallback_count": sum(item["path_proof"]["planner_fallback_count"] for item in cases),
        "memory_recall_count": sum(item["path_proof"]["memory_recall_count"] for item in cases),
        "recovery_attempt_count": sum(item["path_proof"]["recovery_attempt_count"] for item in cases),
        "tool_memory_recall_count": sum(item["path_proof"]["tool_memory_recall_count"] for item in cases),
        "context_budget_event_count": sum(item["path_proof"]["context_budget_event_count"] for item in cases),
        "invalid_ablation_cases": [],
    }
    context_cases = [item for item in cases if item.get("category") == "context"]
    if group_name == "no_planner" and proof["planner_call_count"] != 0:
        proof["invalid_ablation_cases"].append("planner_was_called")
    if group_name != "no_planner" and proof["planner_call_count"] == 0:
        proof["invalid_ablation_cases"].append("planner_was_not_called")
    if group_name == "no_recovery" and proof["recovery_attempt_count"] != 0:
        proof["invalid_ablation_cases"].append("recovery_was_called")
    if group_name == "no_context" and any(
        item["path_proof"]["context_prior_turn_visible"] is True for item in context_cases
    ):
        proof["invalid_ablation_cases"].append("prior_turn_leaked_into_context")
    if group_name not in {"no_context", "no_planner"} and context_cases and not any(
        item["path_proof"]["context_prior_turn_visible"] is True for item in context_cases
    ):
        proof["invalid_ablation_cases"].append("prior_turn_missing_from_context")
    if group_name == "no_tool_use_memory" and proof["tool_memory_recall_count"] != 0:
        proof["invalid_ablation_cases"].append("tool_memory_was_recalled")
    return proof


def _family_breakdown(cases: list[dict[str, Any]], field: str) -> dict[str, Any]:
    families: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        families.setdefault(str(case.get(field) or "unspecified"), []).append(case)
    result: dict[str, Any] = {}
    for family, family_cases in sorted(families.items()):
        calls = [name for case in family_cases for name in case["tool_calls"]]
        invalid = [name for case in family_cases for name in case["invalid_calls"]]
        result[family] = {
            "sample_count": len(family_cases),
            "unique_case_count": len({case["case_id"] for case in family_cases}),
            "task_success_rate": sum(bool(case["task_success"]) for case in family_cases) / len(family_cases),
            "strict_grounded_success_rate": sum(
                bool(case["strict_grounded_success"]) for case in family_cases
            ) / len(family_cases),
            "hard_constraint_case_rate": sum(
                all(check["passed"] for check in case["constraint_checks"])
                for case in family_cases
            ) / len(family_cases),
            "invalid_call_rate": len(invalid) / len(calls) if calls else 0.0,
        }
    return result


def _wilson_interval(successes: int, trials: int, z: float = 1.959963984540054) -> dict[str, Any]:
    if trials <= 0:
        return {"successes": 0, "trials": 0, "rate": None, "low": None, "high": None, "method": "wilson_95"}
    rate = successes / trials
    denominator = 1.0 + (z * z / trials)
    center = (rate + z * z / (2.0 * trials)) / denominator
    margin = (
        z
        * ((rate * (1.0 - rate) / trials + z * z / (4.0 * trials * trials)) ** 0.5)
        / denominator
    )
    return {
        "successes": successes,
        "trials": trials,
        "rate": rate,
        "low": max(0.0, center - margin),
        "high": min(1.0, center + margin),
        "method": "wilson_95",
    }


def _repeat_stability(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize repeated samples without pretending they are independent cases."""
    by_case: dict[str, list[dict[str, Any]]] = {}
    for item in cases:
        by_case.setdefault(str(item["case_id"]), []).append(item)

    per_case: list[dict[str, Any]] = []
    for case_id, samples in sorted(by_case.items()):
        task_successes = sum(bool(item["task_success"]) for item in samples)
        strict_successes = sum(bool(item["strict_grounded_success"]) for item in samples)
        tool_sequences = Counter(tuple(item["tool_calls"]) for item in samples)
        response_hashes = {str(item["final_response_sha256"]) for item in samples}
        sample_count = len(samples)
        per_case.append(
            {
                "case_id": case_id,
                "sample_count": sample_count,
                "task_success_rate": task_successes / sample_count,
                "strict_grounded_success_rate": strict_successes / sample_count,
                "all_repeats_task_success": task_successes == sample_count,
                "majority_task_success": task_successes > sample_count / 2,
                "task_success_unstable": 0 < task_successes < sample_count,
                "strict_success_unstable": 0 < strict_successes < sample_count,
                "unique_tool_sequence_count": len(tool_sequences),
                "dominant_tool_sequence_rate": max(tool_sequences.values()) / sample_count,
                "unique_final_response_count": len(response_hashes),
            }
        )

    unique_case_count = len(per_case)
    all_repeat_successes = sum(bool(item["all_repeats_task_success"]) for item in per_case)
    majority_successes = sum(bool(item["majority_task_success"]) for item in per_case)
    task_unstable = [str(item["case_id"]) for item in per_case if item["task_success_unstable"]]
    strict_unstable = [str(item["case_id"]) for item in per_case if item["strict_success_unstable"]]
    tool_unstable = [
        str(item["case_id"])
        for item in per_case
        if int(item["unique_tool_sequence_count"]) > 1
    ]
    return {
        "unique_case_count": unique_case_count,
        "repeat_count_min": min(int(item["sample_count"]) for item in per_case),
        "repeat_count_max": max(int(item["sample_count"]) for item in per_case),
        "case_all_repeats_task_success_rate": all_repeat_successes / unique_case_count,
        "case_majority_task_success_rate": majority_successes / unique_case_count,
        "task_success_unstable_case_rate": len(task_unstable) / unique_case_count,
        "strict_success_unstable_case_rate": len(strict_unstable) / unique_case_count,
        "tool_sequence_unstable_case_rate": len(tool_unstable) / unique_case_count,
        "task_success_unstable_case_ids": task_unstable,
        "strict_success_unstable_case_ids": strict_unstable,
        "tool_sequence_unstable_case_ids": tool_unstable,
        "case_level_confidence_intervals": {
            "all_repeats_task_success_rate": _wilson_interval(
                all_repeat_successes,
                unique_case_count,
            ),
            "majority_task_success_rate": _wilson_interval(
                majority_successes,
                unique_case_count,
            ),
        },
        "per_case": per_case,
        "interpretation": (
            "Case-level rates treat each unique task as one unit. Sample-level Wilson intervals "
            "remain descriptive because repeated provider calls for the same task are correlated."
        ),
    }


def _summarize_group(group_name: str, config_flags: dict[str, bool], cases: list[dict[str, Any]]) -> dict[str, Any]:
    case_count = len(cases)
    constraint_checks = [check for item in cases for check in item["constraint_checks"]]
    all_calls = [name for item in cases for name in item["tool_calls"]]
    invalid_calls = [name for item in cases for name in item["invalid_calls"]]
    recovery_cases = [item for item in cases if item["recovery_success"] is not None]
    grounding = [check for item in cases for check in item["grounding_checks"]]
    totals = [item["token_usage"]["total"] for item in cases]
    prompts = [item["token_usage"]["prompt"] for item in cases]
    completions = [item["token_usage"]["completion"] for item in cases]
    latencies = [item["latency_ms"] for item in cases]
    lexical_fact_match_rate = (
        sum(bool(item["supported"]) for item in grounding) / len(grounding)
        if grounding else None
    )
    binary_counts = {
        "task_success_rate": sum(bool(item["task_success"]) for item in cases),
        "strict_grounded_success_rate": sum(bool(item["strict_grounded_success"]) for item in cases),
        "hard_constraint_case_rate": sum(
            all(check["passed"] for check in item["constraint_checks"]) for item in cases
        ),
        "exact_tool_sequence_match_rate": sum(bool(item["tool_sequence_valid"]) for item in cases),
        "first_tool_accuracy": sum(bool(item["first_tool_valid"]) for item in cases),
    }
    error_taxonomy: dict[str, int] = {}
    for item in cases:
        for label in item.get("failure_labels") or []:
            error_taxonomy[label] = error_taxonomy.get(label, 0) + 1
    return {
        "config": dict(config_flags),
        "path_proof": _aggregate_path_proof(group_name, cases),
        "family_metrics": {
            field: _family_breakdown(cases, field)
            for field in ("scenario_family", "design_family", "failure_family")
        },
        "error_taxonomy": dict(sorted(error_taxonomy.items())),
        "metrics": {
            "task_success_rate": sum(bool(item["task_success"]) for item in cases) / case_count,
            "strict_grounded_success_rate": sum(
                bool(item["strict_grounded_success"]) for item in cases
            ) / case_count,
            "constraint_adherence_rate": (
                sum(bool(item["passed"]) for item in constraint_checks) / len(constraint_checks)
                if constraint_checks else 1.0
            ),
            "hard_constraint_case_rate": sum(
                all(check["passed"] for check in item["constraint_checks"]) for item in cases
            ) / case_count,
            "exact_tool_sequence_match_rate": sum(
                bool(item["tool_sequence_valid"]) for item in cases
            ) / case_count,
            "first_tool_accuracy": sum(bool(item["first_tool_valid"]) for item in cases) / case_count,
            # Backward-compatible alias. New reports should cite the exact name above.
            "tool_selection_accuracy": sum(bool(item["tool_sequence_valid"]) for item in cases) / case_count,
            "invalid_call_rate": len(invalid_calls) / len(all_calls) if all_calls else 0.0,
            "recovery_rate": (
                sum(bool(item["recovery_success"]) for item in recovery_cases) / len(recovery_cases)
                if recovery_cases else None
            ),
            "planner": {
                "call_count": sum(item["path_proof"]["planner_call_count"] for item in cases),
                "fallback_count": sum(item["path_proof"]["planner_fallback_count"] for item in cases),
                "fallback_rate": (
                    sum(item["path_proof"]["planner_fallback_count"] for item in cases)
                    / sum(item["path_proof"]["planner_call_count"] for item in cases)
                    if sum(item["path_proof"]["planner_call_count"] for item in cases) else None
                ),
            },
            "tokens": {
                "prompt_mean": statistics.fmean(prompts),
                "completion_mean": statistics.fmean(completions),
                "total_mean": statistics.fmean(totals),
                "total_p95": _percentile(totals, 0.95),
                "estimated": False,
            },
            "latency_ms": {
                "mean": statistics.fmean(latencies),
                "median": statistics.median(latencies),
                "p95": _percentile(latencies, 0.95),
            },
            "repeat_stability": _repeat_stability(cases),
            "groundedness": {
                "claim_precision": None,
                "required_fact_recall": lexical_fact_match_rate,
                "lexical_fact_match_rate": lexical_fact_match_rate,
                "matching_method": "case_authored_lexical_alternatives",
            },
            "confidence_intervals": {
                name: _wilson_interval(successes, case_count)
                for name, successes in binary_counts.items()
            } | {
                "recovery_rate": _wilson_interval(
                    sum(bool(item["recovery_success"]) for item in recovery_cases),
                    len(recovery_cases),
                )
            },
        },
        "cases": cases,
    }


def _case_level_paired_endpoint(
    *,
    full_cases: list[dict[str, Any]],
    ablation_cases: list[dict[str, Any]],
    metric: str,
    endpoint: str,
) -> dict[str, Any]:
    def collect(cases: list[dict[str, Any]]) -> dict[str, list[bool]]:
        by_case: dict[str, list[bool]] = {}
        for item in cases:
            by_case.setdefault(str(item["case_id"]), []).append(bool(item[metric]))
        return by_case

    full_by_case = collect(full_cases)
    ablation_by_case = collect(ablation_cases)
    if set(full_by_case) != set(ablation_by_case):
        raise ValueError("paired Agent comparison has different case IDs across arms")

    def reduce(values: list[bool]) -> bool:
        return all(values) if endpoint == "all_repeats" else sum(values) > len(values) / 2

    paired = [
        (reduce(ablation_by_case[case_id]), reduce(full_by_case[case_id]))
        for case_id in sorted(full_by_case)
    ]
    return {
        "endpoint": endpoint,
        "metric": metric,
        **exact_paired_binary_test(
            paired,
            baseline_label="ablation",
            treatment_label="full",
        ),
    }


def _comparisons(groups: dict[str, Any]) -> dict[str, Any]:
    full = groups.get("full")
    if not full:
        return {}
    full_by_sample = {item["sample_id"]: item for item in full["cases"]}
    result = {}
    for name, group in groups.items():
        if name == "full":
            continue
        wins = losses = ties = 0
        strict_wins = strict_losses = strict_ties = 0
        for item in group["cases"]:
            baseline = full_by_sample[item["sample_id"]]["task_success"]
            candidate = item["task_success"]
            if baseline and not candidate:
                wins += 1
            elif candidate and not baseline:
                losses += 1
            else:
                ties += 1
            strict_baseline = full_by_sample[item["sample_id"]]["strict_grounded_success"]
            strict_candidate = item["strict_grounded_success"]
            if strict_baseline and not strict_candidate:
                strict_wins += 1
            elif strict_candidate and not strict_baseline:
                strict_losses += 1
            else:
                strict_ties += 1
        result[f"{name}_vs_full"] = {
            "task_success_delta": group["metrics"]["task_success_rate"] - full["metrics"]["task_success_rate"],
            "strict_grounded_success_delta": (
                group["metrics"]["strict_grounded_success_rate"]
                - full["metrics"]["strict_grounded_success_rate"]
            ),
            "full_wins": wins,
            "full_losses": losses,
            "ties": ties,
            "strict_full_wins": strict_wins,
            "strict_full_losses": strict_losses,
            "strict_ties": strict_ties,
            "sample_level_interpretation": (
                "Sample wins/losses are descriptive when one case has repeated provider calls."
            ),
            "case_level_paired_tests": {
                "task_success_majority": _case_level_paired_endpoint(
                    full_cases=full["cases"],
                    ablation_cases=group["cases"],
                    metric="task_success",
                    endpoint="majority",
                ),
                "task_success_all_repeats": _case_level_paired_endpoint(
                    full_cases=full["cases"],
                    ablation_cases=group["cases"],
                    metric="task_success",
                    endpoint="all_repeats",
                ),
                "strict_success_majority": _case_level_paired_endpoint(
                    full_cases=full["cases"],
                    ablation_cases=group["cases"],
                    metric="strict_grounded_success",
                    endpoint="majority",
                ),
            },
        }
    return result


def _thresholds(groups: dict[str, Any]) -> list[dict[str, Any]]:
    full = groups.get("full")
    if not full:
        return []
    metrics = full["metrics"]
    checks = [
        ("full_task_success_rate", metrics["task_success_rate"] >= 0.8, metrics["task_success_rate"], ">= 0.8"),
        ("full_hard_constraint_case_rate", metrics["hard_constraint_case_rate"] == 1.0, metrics["hard_constraint_case_rate"], "== 1.0"),
        ("full_exact_tool_sequence_match_rate", metrics["exact_tool_sequence_match_rate"] >= 0.8, metrics["exact_tool_sequence_match_rate"], ">= 0.8"),
        ("full_invalid_call_rate", metrics["invalid_call_rate"] <= 0.1, metrics["invalid_call_rate"], "<= 0.1"),
        ("full_lexical_required_fact_recall", (metrics["groundedness"]["required_fact_recall"] or 0.0) >= 0.9, metrics["groundedness"]["required_fact_recall"], ">= 0.9"),
        ("full_planner_fallback_count", metrics["planner"]["fallback_count"] == 0, metrics["planner"]["fallback_count"], "== 0"),
        ("full_ablation_path_valid", not full["path_proof"]["invalid_ablation_cases"], full["path_proof"]["invalid_ablation_cases"], "empty"),
    ]
    if metrics["recovery_rate"] is not None:
        checks.append(("full_recovery_rate", metrics["recovery_rate"] == 1.0, metrics["recovery_rate"], "== 1.0"))
    for name, group in groups.items():
        checks.append((f"{name}_path_valid", not group["path_proof"]["invalid_ablation_cases"], group["path_proof"]["invalid_ablation_cases"], "empty"))
    return [
        {"name": name, "passed": bool(passed), "value": value, "threshold": threshold}
        for name, passed, value, threshold in checks
    ]


def _checkpoint_fingerprint(
    *,
    dataset_sha256: str,
    provider: str,
    agent_brain: str,
    harness_sha256: str,
    model: str,
    group_names: list[str],
    case_ids: list[str],
    repeat_count: int,
) -> str:
    payload = {
        "dataset_sha256": dataset_sha256,
        "provider": provider,
        "agent_brain": agent_brain,
        "harness_sha256": harness_sha256,
        "model": model,
        "group_names": group_names,
        "case_ids": case_ids,
        "repeat_count": repeat_count,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_checkpoint(path: Path, checkpoint: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def run_ablation(
    *,
    dataset_path: Path,
    provider: str,
    agent_brain: str = "native",
    model: str,
    group_names: list[str],
    artifact_root: Path,
    case_ids: list[str] | None = None,
    repeat_count: int = 1,
    categories: list[str] | None = None,
    scenario_families: list[str] | None = None,
    design_families: list[str] | None = None,
    failure_families: list[str] | None = None,
    manifest_path: Path | None = None,
    full_frozen_eval: bool = False,
    checkpoint_path: Path | None = None,
    resume_checkpoint: bool = False,
) -> dict[str, Any]:
    if repeat_count < 1:
        raise ValueError("repeat_count must be >= 1")
    if agent_brain not in {"native", "pi"}:
        raise ValueError("agent_brain must be 'native' or 'pi'")
    if agent_brain == "pi" and provider == "deterministic_proxy":
        raise ValueError(
            "agent_brain=pi requires provider=openai_compatible; the Python deterministic "
            "client cannot drive the Node Pi loop. Use Pi Faux smoke tests for offline mechanics."
        )
    dataset, dataset_sha = _load_dataset(dataset_path)
    manifest_identity = _verify_dataset_manifest(
        dataset_path=dataset_path,
        dataset=dataset,
        dataset_sha256=dataset_sha,
        manifest_path=manifest_path,
    )
    available_case_ids = [str(case["case_id"]) for case in dataset["cases"]]
    unknown_case_ids = sorted(set(case_ids or []) - set(available_case_ids))
    if unknown_case_ids:
        raise ValueError(f"unknown Agent E2E case IDs: {unknown_case_ids}")
    filters_active = any(
        values
        for values in (
            case_ids,
            categories,
            scenario_families,
            design_families,
            failure_families,
        )
    )
    frozen = dataset.get("split") in {"held_out", "frozen_evaluation"}
    if full_frozen_eval and not frozen:
        raise ValueError("full_frozen_eval is only valid for held_out/frozen_evaluation datasets")
    if full_frozen_eval and filters_active:
        raise ValueError("full frozen evaluation cannot use case or family filters")
    effective_model = "scripted-deterministic-v1" if provider == "deterministic_proxy" else model
    harness_identity = _harness_identity(agent_brain)
    selected_cases = [
        case for case in dataset["cases"]
        if (not case_ids or str(case.get("case_id")) in set(case_ids))
        and (not categories or str(case.get("category")) in set(categories))
        and (
            not scenario_families
            or str(case.get("scenario_family")) in set(scenario_families)
        )
        and (
            not design_families
            or str(case.get("design_family")) in set(design_families)
        )
        and (
            not failure_families
            or str(case.get("failure_family")) in set(failure_families)
        )
    ]
    if not selected_cases:
        raise ValueError(f"no cases matched: {case_ids}")
    selected_case_ids = [str(case["case_id"]) for case in selected_cases]
    if full_frozen_eval and selected_case_ids != manifest_identity["case_ids"]:
        raise ValueError("full frozen evaluation must select every manifest case in order")
    if resume_checkpoint and checkpoint_path is None:
        raise ValueError("resume_checkpoint requires checkpoint_path")
    checkpoint_fingerprint = _checkpoint_fingerprint(
        dataset_sha256=dataset_sha,
        provider=provider,
        agent_brain=agent_brain,
        harness_sha256=str(harness_identity["combined_sha256"]),
        model=effective_model,
        group_names=group_names,
        case_ids=selected_case_ids,
        repeat_count=repeat_count,
    )
    checkpoint: dict[str, Any]
    if resume_checkpoint:
        if not checkpoint_path or not checkpoint_path.exists():
            raise ValueError("resume checkpoint does not exist")
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if checkpoint.get("schema_version") != "agent-e2e-checkpoint-v1":
            raise ValueError("unsupported Agent E2E checkpoint schema")
        if checkpoint.get("fingerprint") != checkpoint_fingerprint:
            raise ValueError("Agent E2E checkpoint fingerprint mismatch")
        if not isinstance(checkpoint.get("samples"), dict):
            raise ValueError("Agent E2E checkpoint samples must be an object")
    else:
        checkpoint = {
            "schema_version": "agent-e2e-checkpoint-v1",
            "fingerprint": checkpoint_fingerprint,
            "run_id": uuid.uuid4().hex[:12],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "samples": {},
        }
        if checkpoint_path is not None:
            _write_checkpoint(checkpoint_path, checkpoint)
    run_id = str(checkpoint["run_id"])
    run_artifact_root = artifact_root / run_id
    evaluation_scope = (
        "full_frozen_evaluation"
        if full_frozen_eval
        else "filtered_debug"
        if frozen
        else "development_regression"
    )
    run_artifact_root.mkdir(parents=True, exist_ok=True)
    cache_directories = {
        "HF_HOME": artifact_root / "_shared_cache" / "huggingface",
        "TRANSFORMERS_CACHE": artifact_root / "_shared_cache" / "huggingface" / "transformers",
        "SENTENCE_TRANSFORMERS_HOME": artifact_root / "_shared_cache" / "huggingface" / "sentence_transformers",
        "TMP": run_artifact_root / "tmp",
        "TEMP": run_artifact_root / "tmp",
    }
    original_environment = {name: os.environ.get(name) for name in cache_directories}
    original_config = {
        "CHROMA_PERSIST_DIR": config.CHROMA_PERSIST_DIR,
        "RAG_QUERY_REWRITE": config.RAG_QUERY_REWRITE,
        "RAG_DOCUMENT_QUERY_TRANSLATION": config.RAG_DOCUMENT_QUERY_TRANSLATION,
        "AGENT_MEMORY_DIR": config.AGENT_MEMORY_DIR,
        "AGENT_BRAIN": config.AGENT_BRAIN,
    }
    for variable, directory in cache_directories.items():
        directory.mkdir(parents=True, exist_ok=True)
        os.environ[variable] = str(directory)
    knowledge_base_module = None
    original_knowledge_functions: dict[str, Any] = {}
    if provider == "deterministic_proxy":
        # Agent E2E here targets orchestration, not the separately evaluated RAG
        # model. Point the optional document channel at an empty D-drive store so
        # this smoke test cannot download/load reranker models or touch C:.
        config.CHROMA_PERSIST_DIR = str(run_artifact_root / "empty_chroma")
        config.RAG_QUERY_REWRITE = False
        config.RAG_DOCUMENT_QUERY_TRANSLATION = False
        from cst_agent_workbench.rag import knowledge_base as knowledge_base_module

        original_knowledge_functions = {
            "retrieve_antenna_rules": knowledge_base_module.retrieve_antenna_rules,
            "retrieve_official_document_hits": knowledge_base_module.retrieve_official_document_hits,
            "embed_texts": knowledge_base_module.embed_texts,
        }
        knowledge_base_module.retrieve_antenna_rules = lambda *args, **kwargs: []
        knowledge_base_module.retrieve_official_document_hits = lambda *args, **kwargs: []
        def _disabled_deterministic_embedding(*args, **kwargs):
            raise RuntimeError("semantic embedding disabled in deterministic mechanism smoke")

        knowledge_base_module.embed_texts = _disabled_deterministic_embedding

    groups: dict[str, Any] = {}
    resumed_sample_count = 0
    executed_sample_count = 0
    try:
        for group_name in group_names:
            flags = GROUPS[group_name]
            case_results: list[dict[str, Any]] = []
            for run_index in range(1, repeat_count + 1):
                for case in selected_cases:
                    sample_key = f"{group_name}/{case['case_id']}/repeat_{run_index:02d}"
                    cached = (checkpoint.get("samples") or {}).get(sample_key)
                    if cached is not None:
                        case_results.append(dict(cached))
                        resumed_sample_count += 1
                        continue
                    result = _run_case(
                        case=case,
                        group_name=group_name,
                        group=flags,
                        provider=provider,
                        agent_brain=agent_brain,
                        model=effective_model,
                        artifact_root=run_artifact_root,
                        run_index=run_index,
                    )
                    case_results.append(result)
                    executed_sample_count += 1
                    if checkpoint_path is not None:
                        checkpoint["samples"][sample_key] = result
                        checkpoint["updated_at"] = datetime.now(timezone.utc).isoformat()
                        _write_checkpoint(checkpoint_path, checkpoint)
            groups[group_name] = _summarize_group(group_name, flags, case_results)
            groups[group_name]["metrics"]["tokens"]["estimated"] = provider == "deterministic_proxy"
    finally:
        if knowledge_base_module is not None:
            for name, value in original_knowledge_functions.items():
                setattr(knowledge_base_module, name, value)
        for name, value in original_config.items():
            setattr(config, name, value)
        for name, value in original_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    limitations = [
        "Fake CST validates orchestration and recovery contracts, not CST electromagnetic fidelity.",
        "The deterministic provider is a mechanism smoke test, bypasses RAG retrieval, uses lexical memory fallback, and must not be cited as LLM quality evidence.",
        f"{len(selected_cases)} unique {dataset.get('split', 'unspecified')} cases across {repeat_count} repeat(s) are not automatically a statistically powered benchmark.",
        "Sample-level confidence intervals are descriptive because repeated provider calls for one task are correlated; use repeat_stability for unique-case conclusions.",
        "Required-fact recall uses case-authored lexical alternatives; task_success reports execution success separately, while strict_grounded_success additionally requires lexical fact matches.",
        "Claim precision and semantic entailment remain unevaluated until a blinded manual or calibrated claim-level review is attached.",
    ]
    if agent_brain == "pi":
        limitations.append(
            "Pi is evaluated as an executor Harness only: the existing Python planner, Tool Runtime, Recovery, Session and Trace remain shared with Native."
        )
    if dataset.get("split") == "development_regression":
        limitations.append(
            "The development_regression split and its lexical oracle were iterated during development; it is not a blinded held-out set."
        )
    elif "blinded" not in str(dataset.get("dataset_role") or "").lower():
        limitations.append(
            "This frozen set was visible to developers and must not be described as blinded held-out."
        )
    if frozen and not full_frozen_eval:
        limitations.append(
            "This is a filtered/debug frozen-set run and is not release-eligible; use full_frozen_eval with no filters for canonical thresholds."
        )

    report = {
        "schema_version": "agent-e2e-report-v1",
        "run": {
            "run_id": run_id,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "dataset_id": dataset["dataset_id"],
            "dataset_split": str(dataset.get("split") or "unspecified"),
            "dataset_role": str(dataset.get("dataset_role") or "unspecified"),
            "dataset_frozen_at": dataset.get("frozen_at"),
            "annotation_policy": dataset.get("annotation_policy"),
            "generation_provenance": dataset.get("generation_provenance"),
            "dataset_path": _portable_path(dataset_path),
            "dataset_sha256": dataset_sha,
            "manifest_path": manifest_identity["path"],
            "manifest_sha256": manifest_identity["sha256"],
            "manifest_verified": manifest_identity["verified"],
            "evaluation_scope": evaluation_scope,
            "release_eligible": bool(
                full_frozen_eval
                and manifest_identity["verified"]
                and selected_case_ids == manifest_identity["case_ids"]
                and "full" in group_names
            ),
            "code_revision": _git_revision(),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "provider": provider,
            "agent_brain": agent_brain,
            "harness_identity": harness_identity,
            "model": effective_model,
            "case_count": len(selected_cases),
            "sample_count": len(selected_cases) * repeat_count,
            "repeat_count": repeat_count,
            "checkpoint": {
                "path": str(checkpoint_path.resolve()) if checkpoint_path is not None else None,
                "fingerprint": checkpoint_fingerprint,
                "resumed_sample_count": resumed_sample_count,
                "executed_sample_count": executed_sample_count,
            },
            "filters": {
                "case_ids": list(case_ids or []),
                "categories": list(categories or []),
                "scenario_families": list(scenario_families or []),
                "design_families": list(design_families or []),
                "failure_families": list(failure_families or []),
            },
            "seed_control": {
                "python_random_per_case": True,
                "provider_sampling_seed": False,
                "note": "The case seed controls local Python randomness only; the OpenAI-compatible provider is not claimed deterministic.",
            },
            "groups": group_names,
            "large_artifact_root": str(run_artifact_root.resolve()),
            "secrets_recorded": False,
        },
        "groups": groups,
        "comparisons": _comparisons(groups),
        "threshold_results": _thresholds(groups),
        "limitations": limitations,
    }
    if frozen:
        report["threshold_results"].extend(
            [
                {
                    "name": "frozen_manifest_verified",
                    "passed": bool(report["run"]["manifest_verified"]),
                    "value": report["run"]["manifest_verified"],
                    "threshold": "true",
                },
                {
                    "name": "frozen_release_scope",
                    "passed": bool(report["run"]["release_eligible"]),
                    "value": report["run"]["evaluation_scope"],
                    "threshold": "full_frozen_evaluation",
                },
            ]
        )
    validate_report_schema(report)
    return report


def _write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# CST-Agent Full E2E Ablation",
        "",
        f"- Dataset: `{report['run']['dataset_id']}` ({report['run']['case_count']} unique cases; {report['run']['sample_count']} samples)",
        f"- Repeats per case: `{report['run']['repeat_count']}`",
        f"- Split: `{report['run'].get('dataset_split', 'unspecified')}`",
        f"- Provider/model: `{report['run']['provider']}` / `{report['run']['model']}`",
        f"- Agent Harness: `{report['run'].get('agent_brain', 'native')}`",
        f"- Harness SHA256: `{(report['run'].get('harness_identity') or {}).get('combined_sha256', 'missing')}`",
        f"- Dataset SHA256: `{report['run']['dataset_sha256']}`",
        "",
        "| Group | Execution success | Strict lexical-grounded | Constraints | Exact tool sequence | First tool | Invalid calls | Recovery | Planner fallback | Required facts | Tokens mean | p95 latency ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, group in report["groups"].items():
        metrics = group["metrics"]
        recovery = metrics["recovery_rate"]
        groundedness = metrics["groundedness"]["required_fact_recall"]
        lines.append(
            f"| {name} | {metrics['task_success_rate']:.3f} | {metrics['strict_grounded_success_rate']:.3f} | "
            f"{metrics['constraint_adherence_rate']:.3f} | {metrics['exact_tool_sequence_match_rate']:.3f} | "
            f"{metrics['first_tool_accuracy']:.3f} | {metrics['invalid_call_rate']:.3f} | "
            f"{'n/a' if recovery is None else f'{recovery:.3f}'} | "
            f"{metrics['planner']['fallback_count']} | "
            f"{'n/a' if groundedness is None else f'{groundedness:.3f}'} | "
            f"{metrics['tokens']['total_mean']:.1f} | {metrics['latency_ms']['p95']:.1f} |"
        )
    lines.extend(["", "## Sample-level 95% Wilson intervals", ""])
    for name, group in report["groups"].items():
        intervals = group["metrics"]["confidence_intervals"]
        execution = intervals["task_success_rate"]
        strict = intervals["strict_grounded_success_rate"]
        lines.append(
            f"- {name}: execution `{execution['rate']:.3f}` "
            f"[{execution['low']:.3f}, {execution['high']:.3f}]; "
            f"strict-grounded `{strict['rate']:.3f}` [{strict['low']:.3f}, {strict['high']:.3f}]"
        )
    lines.extend(["", "## Repeat stability by unique case", ""])
    for name, group in report["groups"].items():
        stability = group["metrics"]["repeat_stability"]
        lines.append(
            f"- {name}: all-repeat success `{stability['case_all_repeats_task_success_rate']:.3f}`; "
            f"majority success `{stability['case_majority_task_success_rate']:.3f}`; "
            f"task instability `{stability['task_success_unstable_case_rate']:.3f}`; "
            f"tool-sequence instability `{stability['tool_sequence_unstable_case_rate']:.3f}`"
        )
        if stability["task_success_unstable_case_ids"]:
            lines.append(
                f"  - unstable task cases: {', '.join(stability['task_success_unstable_case_ids'])}"
            )
    lines.extend(["", "## Error taxonomy", ""])
    for name, group in report["groups"].items():
        taxonomy = group.get("error_taxonomy") or {}
        rendered = ", ".join(f"{label}={count}" for label, count in taxonomy.items()) or "none"
        lines.append(f"- {name}: {rendered}")
    lines.extend(["", "## Thresholds", ""])
    for item in report["threshold_results"]:
        lines.append(f"- [{'x' if item['passed'] else ' '}] {item['name']}: `{item['value']}` ({item['threshold']})")
    lines.extend(["", "## Honest boundary", ""])
    lines.extend(f"- {item}" for item in report["limitations"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "benchmarks" / "agent_e2e_cases.json")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--full-frozen-eval",
        action="store_true",
        help="Require the complete manifest case set with no filters and enable release gates.",
    )
    parser.add_argument("--provider", choices=["deterministic_proxy", "openai_compatible"], default="deterministic_proxy")
    parser.add_argument(
        "--agent-brain",
        choices=["native", "pi"],
        default="native",
        help="Executor Harness. Pi requires provider=openai_compatible; Native remains the baseline.",
    )
    parser.add_argument("--model", default=config.OPENAI_MODEL or "gpt-4o-mini")
    parser.add_argument("--group", action="append", choices=sorted(GROUPS), dest="groups")
    parser.add_argument("--case", action="append", dest="cases", help="Run only the named frozen case (repeatable).")
    parser.add_argument("--repeat", type=int, default=1, help="Repeat each selected case independently.")
    parser.add_argument("--category", action="append", dest="categories")
    parser.add_argument("--scenario-family", action="append", dest="scenario_families")
    parser.add_argument("--design-family", action="append", dest="design_families")
    parser.add_argument("--failure-family", action="append", dest="failure_families")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary-md", type=Path)
    parser.add_argument("--artifact-root", type=Path, default=ROOT / "benchmarks" / "reports" / ".agent_e2e_artifacts")
    parser.add_argument(
        "--checkpoint",
        type=Path,
        help="Atomically persist each completed sample so an interrupted run can resume",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from --checkpoint after validating the dataset/provider/model fingerprint",
    )
    parser.add_argument("--assert-thresholds", action="store_true")
    args = parser.parse_args()

    group_names = args.groups or list(GROUPS)
    report = run_ablation(
        dataset_path=args.dataset,
        provider=args.provider,
        agent_brain=args.agent_brain,
        model=args.model,
        group_names=group_names,
        artifact_root=args.artifact_root,
        case_ids=args.cases,
        repeat_count=args.repeat,
        categories=args.categories,
        scenario_families=args.scenario_families,
        design_families=args.design_families,
        failure_families=args.failure_families,
        manifest_path=args.manifest,
        full_frozen_eval=args.full_frozen_eval,
        checkpoint_path=args.checkpoint,
        resume_checkpoint=args.resume,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.summary_md:
        _write_markdown(report, args.summary_md)
    print(json.dumps({name: group["metrics"] for name, group in report["groups"].items()}, ensure_ascii=False, indent=2))
    if args.assert_thresholds and any(not item["passed"] for item in report["threshold_results"]):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
