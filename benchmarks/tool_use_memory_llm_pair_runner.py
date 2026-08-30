"""Paired real-model evaluation of production ToolUseMemory behavior."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchmarks.eval_statistics import exact_paired_binary_test, wilson_interval
from benchmarks.fake_cst_agent_adapter import FakeCSTAgentAdapter, RecordingClient
from benchmarks import tool_use_memory_semantic_grader as semantic_grader
from cst_agent_workbench import config
from cst_agent_workbench.agent.agent import CSTAgent
from cst_agent_workbench.agent.runtime import run_chat_completion_loop
from cst_agent_workbench.agent.tool_runtime import execute_tool
from cst_agent_workbench.cst.primitives import reset_created_objects
from cst_agent_workbench.agent.tool_use_memory import (
    build_tool_use_memory_guidance,
    rerank_safe_tools,
)


DEFAULT_DATASET_PATH = Path(__file__).with_name("tool_use_memory_llm_pair_dev_v1.json")
DEFAULT_SCHEMA_PATH = Path(__file__).with_name("tool_use_memory_llm_pair_dataset.schema.json")
SYSTEM_PROMPT = (
    "You control a CST session. Select only tools needed to answer the request, and ground the "
    "final answer in tool results. Tool presentation order is not a recommendation."
)


class EvaluationPaused(RuntimeError):
    """Raised after a requested number of new samples is safely checkpointed."""

    def __init__(self, *, executed_samples: int, completed_samples: int, total_samples: int) -> None:
        super().__init__("ToolUseMemory evaluation paused at a sample boundary")
        self.progress = {
            "executed_samples_this_process": executed_samples,
            "completed_samples": completed_samples,
            "total_samples": total_samples,
            "remaining_samples": total_samples - completed_samples,
        }


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_json(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return _sha256_bytes(encoded)


def _validate_dataset(dataset: dict[str, Any], *, schema_path: Path = DEFAULT_SCHEMA_PATH) -> None:
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:  # pragma: no cover - declared project dependency
        raise RuntimeError("jsonschema is required to validate the Memory evaluation dataset") from exc
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(dataset), key=lambda item: list(item.path))
    if errors:
        detail = "; ".join(f"{list(item.path)}: {item.message}" for item in errors[:8])
        raise ValueError(f"invalid ToolUseMemory dataset: {detail}")

    fixtures = {str(item["fixture_id"]): item for item in dataset["learn_fixtures"]}
    if len(fixtures) != len(dataset["learn_fixtures"]):
        raise ValueError("duplicate learn fixture IDs")
    case_ids = [str(case["case_id"]) for case in dataset["cases"]]
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("duplicate ToolUseMemory case IDs")
    for case in dataset["cases"]:
        fixture_id = str(case["learn_fixture_id"])
        if fixture_id not in fixtures:
            raise ValueError(f"case {case['case_id']} references unknown fixture {fixture_id}")
        if str(case["failure_family"]) != str(fixtures[fixture_id]["failure_family"]):
            raise ValueError(f"case {case['case_id']} and fixture failure families differ")
        allowed = set(str(name) for name in case["allowed_tools"])
        expected = [str(name) for name in case["expected_tool_sequence"]]
        if not set(expected).issubset(allowed):
            raise ValueError(f"case {case['case_id']} expects a tool outside its allowlist")
        expected_calls = list(case.get("expected_tool_calls") or [])
        if expected_calls and [str(item["name"]) for item in expected_calls] != expected:
            raise ValueError(f"case {case['case_id']} expected call names differ from its sequence")
        if str(fixtures[fixture_id]["tool_name"]) not in allowed:
            raise ValueError(f"case {case['case_id']} does not expose its learned failure tool")


def _load_dataset(
    dataset_path: Path,
    *,
    manifest_path: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    dataset_bytes = dataset_path.read_bytes()
    dataset = json.loads(dataset_bytes.decode("utf-8"))
    _validate_dataset(dataset)
    dataset_sha = _sha256_bytes(dataset_bytes)
    resolved_manifest = manifest_path or dataset_path.with_suffix(".manifest.json")
    if not resolved_manifest.exists():
        raise ValueError("ToolUseMemory dataset manifest is required")
    manifest_bytes = resolved_manifest.read_bytes()
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    expected = {
        "dataset_id": dataset["dataset_id"],
        "dataset_sha256": dataset_sha,
        "case_count": len(dataset["cases"]),
        "case_ids": [str(case["case_id"]) for case in dataset["cases"]],
        "split": dataset["split"],
        "role": dataset["role"],
        "blinded": bool(dataset["blinded"]),
    }
    mismatches = [key for key, value in expected.items() if manifest.get(key) != value]
    if mismatches:
        raise ValueError(f"ToolUseMemory manifest mismatch: {mismatches}")
    return dataset, {
        **expected,
        "manifest_path": str(resolved_manifest.resolve()),
        "manifest_sha256": _sha256_bytes(manifest_bytes),
        "manifest_verified": True,
    }


def _make_agent(
    *,
    memory_root: Path,
    project_path: Path,
    model: str,
    require_client: bool = True,
) -> CSTAgent:
    config.AGENT_MEMORY_DIR = str(memory_root)
    agent = CSTAgent(FakeCSTAgentAdapter(project_path=str(project_path), connected=True))
    if require_client and (not config.OPENAI_API_KEY or agent.client is None):
        raise RuntimeError("OPENAI_API_KEY and the OpenAI client are required")
    agent.model = model
    if agent.client is not None:
        agent.client = RecordingClient(agent.client)
    agent._refresh_session_memory_from_runtime()
    return agent


def _selected_tools(agent: CSTAgent, allowed_tool_names: list[str]) -> list[dict[str, Any]]:
    by_name = {
        str((tool.get("function") or {}).get("name") or ""): tool
        for tool in agent.tools
    }
    missing = [name for name in allowed_tool_names if name not in by_name]
    if missing:
        raise ValueError(f"unknown tools in Memory evaluation allowlist: {missing}")
    return [by_name[name] for name in allowed_tool_names]


def _seed_production_failure(
    *,
    fixture: dict[str, Any],
    memory_root: Path,
    project_path: Path,
    model: str,
    require_client: bool = True,
) -> dict[str, Any]:
    agent = _make_agent(
        memory_root=memory_root,
        project_path=project_path,
        model=model,
        require_client=require_client,
    )
    agent._failure_recovery_engine = None
    setup_evidence = _run_setup_tools(agent, list(fixture.get("setup_tools") or []))
    injection = fixture.get("fake_injection")
    if injection:
        agent.cst.queue_response(str(injection["method"]), dict(injection["response"]))
    execute_tool(agent, str(fixture["tool_name"]), dict(fixture["arguments"]))
    evidence = dict(agent.session.metadata.get("last_tool_use_memory_write") or {})
    return {
        "fixture_id": fixture["fixture_id"],
        "setup_evidence": setup_evidence,
        "write_evidence": evidence,
    }


def _run_setup_tools(agent: CSTAgent, setup_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for call in setup_tools:
        result_text = execute_tool(agent, str(call["name"]), dict(call["arguments"]))
        try:
            result = json.loads(result_text)
        except (json.JSONDecodeError, TypeError):
            result = {"success": False, "message": str(result_text)}
        evidence.append(
            {
                "name": call["name"],
                "arguments": dict(call["arguments"]),
                "success": bool(result.get("success")),
                "message": str(result.get("message") or ""),
            }
        )
        if not result.get("success"):
            raise RuntimeError(f"Memory evaluation setup tool failed: {call['name']}: {result}")
    return evidence


def _executor_request_record(agent: CSTAgent) -> dict[str, Any]:
    calls = list(getattr(agent.client, "calls", []) or [])
    return next((dict(call) for call in calls if call.get("call_kind") == "executor"), {})


def _first_executor_memory_evidence(
    executor_request: dict[str, Any],
    *,
    allowed_tools: list[str],
) -> dict[str, Any]:
    guidance_blocks = [
        str(message.get("content") or "")
        for message in (executor_request.get("messages") or [])
        if message.get("role") == "system"
        and "[工具使用经验；仅用于安全白名单内的排序与参数提示]" in str(message.get("content") or "")
    ]
    guidance = "\n".join(guidance_blocks)
    presented = [str(name) for name in (executor_request.get("tool_names") or [])]
    recalled_count = sum(
        line.lstrip().startswith(("- failed tools=", "- successful tools="))
        for line in guidance.splitlines()
    )
    return {
        "guidance_presented": bool(guidance),
        "guidance_sha256": _sha256_bytes(guidance.encode("utf-8")),
        "recalled_record_count": recalled_count,
        "tool_order_changed_from_manifest": presented != list(allowed_tools),
        "memory_applied": bool(guidance or presented != list(allowed_tools)),
    }


def _argument_value_matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _argument_value_matches(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            _argument_value_matches(left, right) for left, right in zip(actual, expected)
        )
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return isinstance(actual, (int, float)) and not isinstance(actual, bool) and float(actual) == float(expected)
    return actual == expected


def _score_expected_calls(
    actual_calls: list[dict[str, Any]],
    expected_calls: list[dict[str, Any]],
) -> tuple[bool, list[dict[str, Any]]]:
    if not expected_calls:
        return True, []
    checks: list[dict[str, Any]] = []
    for index, expected in enumerate(expected_calls):
        actual = actual_calls[index] if index < len(actual_calls) else {}
        mode = str(expected.get("argument_match") or "subset")
        actual_arguments = dict(actual.get("arguments") or {})
        expected_arguments = dict(expected.get("arguments") or {})
        name_matches = str(actual.get("name") or "") == str(expected["name"])
        if mode == "exact":
            arguments_match = actual_arguments == expected_arguments
        else:
            arguments_match = _argument_value_matches(actual_arguments, expected_arguments)
        checks.append(
            {
                "index": index,
                "expected_name": expected["name"],
                "actual_name": actual.get("name"),
                "argument_match": mode,
                "expected_arguments": expected_arguments,
                "actual_arguments": actual_arguments,
                "name_matches": name_matches,
                "arguments_match": arguments_match,
                "passed": bool(name_matches and arguments_match),
            }
        )
    passed = len(actual_calls) == len(expected_calls) and all(item["passed"] for item in checks)
    return passed, checks


def _run_sample(
    *,
    arm: str,
    case: dict[str, Any],
    fixture: dict[str, Any],
    repeat_index: int,
    memory_root: Path,
    project_path: Path,
    model: str,
) -> dict[str, Any]:
    agent = _make_agent(memory_root=memory_root, project_path=project_path, model=model)
    setup_evidence = _run_setup_tools(agent, list(case.get("setup_tools") or []))
    agent.tool_events.clear()
    if arm == "no_memory":
        agent.session.tool_use_memory.records.clear()
    query = str(case["query"])
    working_messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": query},
    ]
    pending_history = [{"role": "user", "content": query}]
    token_before = dict(agent.token_stats)
    started = time.perf_counter()
    result = run_chat_completion_loop(
        client=agent.client,
        model=model,
        tools=_selected_tools(agent, list(case["allowed_tools"])),
        working_messages=working_messages,
        pending_history=pending_history,
        execute_tool=lambda name, arguments: execute_tool(agent, name, arguments),
        token_stats=agent.token_stats,
        offline_notice="",
        optimization_mode=False,
        max_tool_iterations=int(case.get("max_tool_iterations") or 3),
        session=agent.session,
        tool_memory_query=query,
    )
    latency_ms = (time.perf_counter() - started) * 1000
    runtime_tool_events = [dict(event) for event in agent.tool_events]
    model_tool_events = [event for event in runtime_tool_events if "arguments" in event]
    tool_calls = [
        {
            "name": str(event.get("tool_name") or ""),
            "arguments": dict(event.get("arguments") or {}),
            "success": bool(event.get("success")),
        }
        for event in model_tool_events
    ]
    tool_names = [str(item["name"]) for item in tool_calls]
    expected = [str(name) for name in case["expected_tool_sequence"]]
    allowed = {str(name) for name in case["allowed_tools"]}
    exact_sequence = tool_names == expected
    arguments_valid, argument_checks = _score_expected_calls(
        tool_calls,
        list(case.get("expected_tool_calls") or []),
    )
    prompt_delta = int(agent.token_stats.get("prompt", 0)) - int(token_before.get("prompt", 0))
    completion_delta = int(agent.token_stats.get("completion", 0)) - int(
        token_before.get("completion", 0)
    )
    executor_request = _executor_request_record(agent)
    first_memory_evidence = _first_executor_memory_evidence(
        executor_request,
        allowed_tools=list(case["allowed_tools"]),
    )
    recall = list(agent.session.metadata.get("tool_use_memory_recall") or [])
    guidance = str(agent.session.metadata.get("tool_use_memory_guidance") or "")
    pair_id = f"{case['case_id']}::repeat_{repeat_index:02d}"
    exact_task_success = bool(exact_sequence and arguments_valid and result.ok)
    deterministic_semantic = semantic_grader.score_semantic_sample(
        {
            "task_success": exact_task_success,
            "tool_calls_detailed": tool_calls,
            "final_response": result.final_text,
        },
        case,
        agent_completion_ok=bool(result.ok),
        completion_evidence_source="runtime_loop_result",
    )
    return {
        "sample_id": f"{pair_id}::{arm}",
        "pair_id": pair_id,
        "case_id": case["case_id"],
        "failure_family": case["failure_family"],
        "repeat_index": repeat_index,
        "arm": arm,
        "query": query,
        "setup_evidence": setup_evidence,
        "allowed_tools": list(case["allowed_tools"]),
        "presented_tool_order": list(executor_request.get("tool_names") or []),
        "first_executor_memory_evidence": first_memory_evidence,
        "expected_tool_sequence": expected,
        "tool_calls": tool_names,
        "tool_calls_detailed": tool_calls,
        "runtime_tool_events": runtime_tool_events,
        "first_tool": tool_names[0] if tool_names else None,
        "exact_tool_sequence": exact_sequence,
        "arguments_valid": arguments_valid,
        "argument_checks": argument_checks,
        "agent_completion_ok": bool(result.ok),
        "task_success": exact_task_success,
        "deterministic_semantic": deterministic_semantic,
        "repeated_failed_tool": bool(tool_names and tool_names[0] == fixture["tool_name"]),
        "invalid_call": any(name not in allowed for name in tool_names),
        "recalled_memory_count": len(recall),
        "final_iteration_recalled_memory_count": len(recall),
        "recalled_memory_sha256": _sha256_json(recall),
        "memory_guidance_sha256": _sha256_bytes(guidance.encode("utf-8")),
        "final_response": result.final_text,
        "final_response_sha256": _sha256_bytes(result.final_text.encode("utf-8")),
        "token_usage": {
            "prompt": prompt_delta,
            "completion": completion_delta,
            "total": prompt_delta + completion_delta,
            "calls": int(agent.token_stats.get("calls", 0)) - int(token_before.get("calls", 0)),
        },
        "latency_ms": round(latency_ms, 3),
    }


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * percentile)))
    return float(ordered[index])


def _arm_metrics(samples: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(samples)
    if count == 0:
        raise ValueError("cannot aggregate an empty Memory arm")
    successes = sum(bool(sample["task_success"]) for sample in samples)
    latencies = [float(sample["latency_ms"]) for sample in samples]
    return {
        "samples": count,
        "task_success_rate": successes / count,
        "repeat_error_rate": sum(bool(sample["repeated_failed_tool"]) for sample in samples) / count,
        "invalid_call_rate": sum(bool(sample["invalid_call"]) for sample in samples) / count,
        "memory_application_rate": sum(
            bool((sample.get("first_executor_memory_evidence") or {}).get("memory_applied"))
            for sample in samples
        )
        / count,
        "mean_tokens": sum(int(sample["token_usage"]["total"]) for sample in samples) / count,
        "mean_latency_ms": sum(latencies) / count,
        "p95_latency_ms": _percentile(latencies, 0.95),
    }


def _family_breakdown(samples: list[dict[str, Any]]) -> dict[str, Any]:
    families = sorted({str(sample["failure_family"]) for sample in samples})
    return {
        family: {
            arm: _arm_metrics(
                [
                    sample
                    for sample in samples
                    if sample["failure_family"] == family and sample["arm"] == arm
                ]
            )
            for arm in ("no_memory", "learned")
        }
        for family in families
    }


def _case_level_endpoint(
    samples: list[dict[str, Any]],
    *,
    endpoint: str,
) -> dict[str, Any]:
    by_case: dict[str, dict[str, list[bool]]] = {}
    for sample in samples:
        by_case.setdefault(str(sample["case_id"]), {}).setdefault(str(sample["arm"]), []).append(
            bool(sample["task_success"])
        )
    paired: list[tuple[bool, bool]] = []
    per_case: list[dict[str, Any]] = []
    for case_id in sorted(by_case):
        arms = by_case[case_id]
        if set(arms) != {"no_memory", "learned"}:
            raise ValueError(f"incomplete paired samples for {case_id}")

        def reduce(values: list[bool]) -> bool:
            return all(values) if endpoint == "all_repeats" else sum(values) > len(values) / 2

        cold = reduce(arms["no_memory"])
        learned = reduce(arms["learned"])
        paired.append((cold, learned))
        per_case.append(
            {
                "case_id": case_id,
                "no_memory_success": cold,
                "learned_success": learned,
                "no_memory_sample_successes": sum(arms["no_memory"]),
                "learned_sample_successes": sum(arms["learned"]),
                "repeats_per_arm": len(arms["no_memory"]),
            }
        )
    cold_successes = sum(cold for cold, _ in paired)
    learned_successes = sum(learned for _, learned in paired)
    return {
        "endpoint": endpoint,
        "unit": "independent_case",
        "no_memory": wilson_interval(cold_successes, len(paired)),
        "learned": wilson_interval(learned_successes, len(paired)),
        "rate_delta": (learned_successes - cold_successes) / len(paired),
        "paired_test": exact_paired_binary_test(
            paired,
            baseline_label="no_memory",
            treatment_label="learned",
        ),
        "per_case": per_case,
    }


def _arm_schedule(cases: list[dict[str, Any]], repeat_count: int, dataset_sha256: str) -> list[dict[str, Any]]:
    pairs = [
        {
            "pair_id": f"{case['case_id']}::repeat_{repeat_index:02d}",
            "case_id": str(case["case_id"]),
            "repeat_index": repeat_index,
        }
        for repeat_index in range(1, repeat_count + 1)
        for case in cases
    ]
    pairs.sort(key=lambda item: _sha256_bytes(f"{dataset_sha256}:{item['pair_id']}".encode("utf-8")))
    for index, pair in enumerate(pairs):
        pair["arm_order"] = ["no_memory", "learned"] if index % 2 == 0 else ["learned", "no_memory"]
    return pairs


def _checkpoint_fingerprint(
    *,
    dataset_identity: dict[str, Any],
    model: str,
    repeat_count: int,
    schedule: list[dict[str, Any]],
    tool_catalog_sha256: str,
) -> str:
    payload = {
        "dataset_sha256": dataset_identity["dataset_sha256"],
        "manifest_sha256": dataset_identity["manifest_sha256"],
        "model": model,
        "repeat_count": repeat_count,
        "schedule": schedule,
        "tool_catalog_sha256": tool_catalog_sha256,
        "system_prompt_sha256": _sha256_bytes(SYSTEM_PROMPT.encode("utf-8")),
        "runner_sha256": _sha256_bytes(Path(__file__).read_bytes()),
        "semantic_grader_version": semantic_grader.GRADER_VERSION,
        "semantic_grader_sha256": _sha256_bytes(Path(semantic_grader.__file__).read_bytes()),
    }
    return _sha256_json(payload)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _load_or_create_checkpoint(
    *,
    checkpoint_path: Path | None,
    resume: bool,
    fingerprint: str,
) -> dict[str, Any]:
    if resume:
        if checkpoint_path is None or not checkpoint_path.exists():
            raise ValueError("resume requires an existing checkpoint")
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if checkpoint.get("schema_version") != "tool-use-memory-pair-checkpoint-v1":
            raise ValueError("unsupported ToolUseMemory checkpoint schema")
        if checkpoint.get("fingerprint") != fingerprint:
            raise ValueError("ToolUseMemory checkpoint fingerprint mismatch")
        if not isinstance(checkpoint.get("samples"), dict):
            raise ValueError("ToolUseMemory checkpoint samples must be an object")
        return checkpoint
    checkpoint = {
        "schema_version": "tool-use-memory-pair-checkpoint-v1",
        "fingerprint": fingerprint,
        "run_id": uuid.uuid4().hex[:12],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "learn_phase": {"completed": False, "write_evidence": []},
        "samples": {},
    }
    if checkpoint_path is not None:
        _write_json_atomic(checkpoint_path, checkpoint)
    return checkpoint


def validate_dataset_oracles(
    *,
    dataset_path: Path,
    artifact_root: Path,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    dataset, dataset_identity = _load_dataset(dataset_path, manifest_path=manifest_path)
    original_memory_dir = config.AGENT_MEMORY_DIR
    cases: list[dict[str, Any]] = []
    try:
        for case in dataset["cases"]:
            reset_created_objects()
            case_root = artifact_root / "oracle_validation" / str(case["case_id"])
            config.AGENT_MEMORY_DIR = str(case_root / "memory")
            agent = CSTAgent(
                FakeCSTAgentAdapter(
                    project_path=str(case_root / "project.cst"),
                    connected=True,
                )
            )
            setup = _run_setup_tools(agent, list(case.get("setup_tools") or []))
            expected_calls = list(case.get("expected_tool_calls") or [])
            if not expected_calls:
                raise ValueError(f"case {case['case_id']} has no argument-level oracle")
            target = _run_setup_tools(
                agent,
                [
                    {"name": call["name"], "arguments": dict(call["arguments"])}
                    for call in expected_calls
                ],
            )
            cases.append(
                {
                    "case_id": case["case_id"],
                    "failure_family": case["failure_family"],
                    "setup_tools": setup,
                    "expected_tool_calls": target,
                    "oracle_executable": all(
                        item["success"] for item in [*setup, *target]
                    ),
                }
            )
    finally:
        reset_created_objects()
        config.AGENT_MEMORY_DIR = original_memory_dir
    return {
        "schema_version": "tool-use-memory-oracle-validation-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": dataset_identity,
        "artifact_root": str(artifact_root.resolve()),
        "case_count": len(cases),
        "all_oracles_executable": all(item["oracle_executable"] for item in cases),
        "cases": cases,
        "limitations": [
            "This validates that authored oracle calls execute through Tool Runtime against Fake-CST.",
            "It does not measure model tool selection, real CST effects, or unseen generalization.",
        ],
    }


def validate_learning_fixtures(
    *,
    dataset_path: Path,
    artifact_root: Path,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    dataset, dataset_identity = _load_dataset(dataset_path, manifest_path=manifest_path)
    original_memory_dir = config.AGENT_MEMORY_DIR
    cases_by_fixture: dict[str, list[dict[str, Any]]] = {}
    for case in dataset["cases"]:
        cases_by_fixture.setdefault(str(case["learn_fixture_id"]), []).append(case)
    rows: list[dict[str, Any]] = []
    try:
        for fixture in dataset["learn_fixtures"]:
            reset_created_objects()
            fixture_id = str(fixture["fixture_id"])
            fixture_root = artifact_root / "learning_validation" / fixture_id
            memory_root = fixture_root / "memory"
            project_path = fixture_root / "project.cst"
            write = _seed_production_failure(
                fixture=fixture,
                memory_root=memory_root,
                project_path=project_path,
                model="fixture-validation-no-provider",
                require_client=False,
            )
            reset_created_objects()
            recall_agent = _make_agent(
                memory_root=memory_root,
                project_path=project_path,
                model="fixture-validation-no-provider",
                require_client=False,
            )
            case = cases_by_fixture[fixture_id][0]
            original_tools = _selected_tools(recall_agent, list(case["allowed_tools"]))
            guidance, records = build_tool_use_memory_guidance(
                recall_agent.session,
                str(case["query"]),
                allowed_tool_names=list(case["allowed_tools"]),
            )
            reranked_tools = rerank_safe_tools(original_tools, records)
            original_names = [str((item.get("function") or {}).get("name") or "") for item in original_tools]
            reranked_names = [str((item.get("function") or {}).get("name") or "") for item in reranked_tools]
            failed_tool = str(fixture["tool_name"])
            demoted = (
                failed_tool in original_names
                and failed_tool in reranked_names
                and reranked_names.index(failed_tool) > original_names.index(failed_tool)
            )
            rows.append(
                {
                    "fixture_id": fixture_id,
                    "failure_family": fixture["failure_family"],
                    "production_write_recorded": bool(write["write_evidence"]),
                    "cross_instance_recall_count": len(records),
                    "guidance_sha256": _sha256_bytes(guidance.encode("utf-8")),
                    "original_tool_order": original_names,
                    "reranked_tool_order": reranked_names,
                    "failed_tool_demoted": demoted,
                    "project_scope_shared": True,
                }
            )
    finally:
        reset_created_objects()
        config.AGENT_MEMORY_DIR = original_memory_dir
    return {
        "schema_version": "tool-use-memory-learning-validation-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": dataset_identity,
        "artifact_root": str(artifact_root.resolve()),
        "fixture_count": len(rows),
        "all_production_writes_recorded": all(item["production_write_recorded"] for item in rows),
        "all_cross_instance_recalls_nonempty": all(item["cross_instance_recall_count"] > 0 for item in rows),
        "all_failed_tools_demoted": all(item["failed_tool_demoted"] for item in rows),
        "all_checks_passed": all(
            item["production_write_recorded"]
            and item["cross_instance_recall_count"] > 0
            and item["failed_tool_demoted"]
            for item in rows
        ),
        "fixtures": rows,
        "limitations": [
            "This validates production write, persistence, scoped recall, and safe reranking without an LLM.",
            "It does not prove that a real model follows recalled guidance or improves task success.",
        ],
    }


def run_pair(
    *,
    artifact_root: Path,
    model: str,
    repeat_count: int,
    dataset_path: Path = DEFAULT_DATASET_PATH,
    manifest_path: Path | None = None,
    checkpoint_path: Path | None = None,
    resume: bool = False,
    max_new_samples: int | None = None,
) -> dict[str, Any]:
    if repeat_count < 1:
        raise ValueError("repeat_count must be >= 1")
    if max_new_samples is not None and max_new_samples < 1:
        raise ValueError("max_new_samples must be >= 1")
    if max_new_samples is not None and checkpoint_path is None:
        raise ValueError("max_new_samples requires a checkpoint path")
    dataset, dataset_identity = _load_dataset(dataset_path, manifest_path=manifest_path)
    cases = list(dataset["cases"])
    fixtures = {str(item["fixture_id"]): item for item in dataset["learn_fixtures"]}
    schedule = _arm_schedule(cases, repeat_count, dataset_identity["dataset_sha256"])

    original_memory_dir = config.AGENT_MEMORY_DIR
    probe_root = artifact_root / ".fingerprint_probe"
    try:
        probe_agent = _make_agent(
            memory_root=probe_root / "memory",
            project_path=probe_root / "project.cst",
            model=model,
        )
        all_allowed_names = list(
            dict.fromkeys(name for case in cases for name in case["allowed_tools"])
        )
        tool_catalog = _selected_tools(probe_agent, all_allowed_names)
        tool_catalog_sha = _sha256_json(tool_catalog)
    finally:
        config.AGENT_MEMORY_DIR = original_memory_dir
    fingerprint = _checkpoint_fingerprint(
        dataset_identity=dataset_identity,
        model=model,
        repeat_count=repeat_count,
        schedule=schedule,
        tool_catalog_sha256=tool_catalog_sha,
    )
    checkpoint = _load_or_create_checkpoint(
        checkpoint_path=checkpoint_path,
        resume=resume,
        fingerprint=fingerprint,
    )
    run_id = str(checkpoint["run_id"])
    run_root = artifact_root / run_id
    learned_root = run_root / "learned_memory"
    cold_root = run_root / "cold_memory"
    cache_directories = {
        "HF_HOME": artifact_root / "_shared_cache" / "huggingface",
        "TRANSFORMERS_CACHE": artifact_root / "_shared_cache" / "huggingface" / "transformers",
        "SENTENCE_TRANSFORMERS_HOME": artifact_root / "_shared_cache" / "huggingface" / "sentence_transformers",
        "TMP": run_root / "tmp",
        "TEMP": run_root / "tmp",
    }
    original_environment = {name: os.environ.get(name) for name in cache_directories}
    for name, directory in cache_directories.items():
        directory.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(directory)

    resumed_sample_count = 0
    executed_sample_count = 0
    try:
        if resume:
            if not checkpoint.get("learn_phase", {}).get("completed") or not learned_root.exists():
                raise ValueError("resume checkpoint has no complete persisted learn phase")
        else:
            write_evidence = []
            for fixture_id, fixture in fixtures.items():
                reset_created_objects()
                write_evidence.append(
                    _seed_production_failure(
                        fixture=fixture,
                        memory_root=learned_root,
                        project_path=run_root / "fixture_projects" / f"{fixture_id}.cst",
                        model=model,
                    )
                )
            checkpoint["learn_phase"] = {"completed": True, "write_evidence": write_evidence}
            if checkpoint_path is not None:
                checkpoint["updated_at"] = datetime.now(timezone.utc).isoformat()
                _write_json_atomic(checkpoint_path, checkpoint)

        case_by_id = {str(case["case_id"]): case for case in cases}
        samples: list[dict[str, Any]] = []
        for pair in schedule:
            case = case_by_id[pair["case_id"]]
            fixture = fixtures[str(case["learn_fixture_id"])]
            project_path = run_root / "fixture_projects" / f"{case['learn_fixture_id']}.cst"
            for arm in pair["arm_order"]:
                sample_id = f"{pair['pair_id']}::{arm}"
                cached = checkpoint["samples"].get(sample_id)
                if cached is not None:
                    samples.append(dict(cached))
                    resumed_sample_count += 1
                    continue
                reset_created_objects()
                sample = _run_sample(
                    arm=arm,
                    case=case,
                    fixture=fixture,
                    repeat_index=int(pair["repeat_index"]),
                    memory_root=learned_root if arm == "learned" else cold_root,
                    project_path=project_path,
                    model=model,
                )
                samples.append(sample)
                executed_sample_count += 1
                checkpoint["samples"][sample_id] = sample
                if checkpoint_path is not None:
                    checkpoint["updated_at"] = datetime.now(timezone.utc).isoformat()
                    _write_json_atomic(checkpoint_path, checkpoint)
                if max_new_samples is not None and executed_sample_count >= max_new_samples:
                    raise EvaluationPaused(
                        executed_samples=executed_sample_count,
                        completed_samples=len(checkpoint["samples"]),
                        total_samples=len(schedule) * 2,
                    )
    finally:
        reset_created_objects()
        config.AGENT_MEMORY_DIR = original_memory_dir
        for name, value in original_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    arms = {
        arm: _arm_metrics([sample for sample in samples if sample["arm"] == arm])
        for arm in ("no_memory", "learned")
    }
    sample_pairs: dict[str, dict[str, dict[str, Any]]] = {}
    for sample in samples:
        sample_pairs.setdefault(str(sample["pair_id"]), {})[str(sample["arm"])] = sample
    sample_outcomes = [
        (
            bool(pair["no_memory"]["task_success"]),
            bool(pair["learned"]["task_success"]),
        )
        for pair in sample_pairs.values()
    ]
    semantic_arms = semantic_grader.semantic_arm_metrics(samples)
    return {
        "schema_version": "tool-use-memory-llm-pair-v3",
        "dataset": dataset_identity,
        "run": {
            "run_id": run_id,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "unique_cases": len(cases),
            "repeat_count": repeat_count,
            "pair_count": len(sample_pairs),
            "artifact_root": str(run_root.resolve()),
            "fingerprint": fingerprint,
            "tool_catalog_sha256": tool_catalog_sha,
            "system_prompt_sha256": _sha256_bytes(SYSTEM_PROMPT.encode("utf-8")),
            "semantic_grader_version": semantic_grader.GRADER_VERSION,
            "semantic_grader_sha256": _sha256_bytes(
                Path(semantic_grader.__file__).read_bytes()
            ),
            "checkpoint": {
                "enabled": checkpoint_path is not None,
                "resumed": resume,
                "resumed_sample_count": resumed_sample_count,
                "executed_sample_count": executed_sample_count,
            },
        },
        "learn_phase": checkpoint["learn_phase"],
        "arm_schedule": schedule,
        "arms": arms,
        "failure_family_breakdown": _family_breakdown(samples),
        "cost_delta_learned_minus_no_memory": {
            "mean_tokens": arms["learned"]["mean_tokens"] - arms["no_memory"]["mean_tokens"],
            "mean_latency_ms": arms["learned"]["mean_latency_ms"] - arms["no_memory"]["mean_latency_ms"],
        },
        "paired": {
            "sample_level_descriptive": {
                **exact_paired_binary_test(
                    sample_outcomes,
                    baseline_label="no_memory",
                    treatment_label="learned",
                ),
                "unit": "correlated_provider_call",
                "interpretation": "Descriptive only: repeats of one task are not independent cases.",
            },
            "case_majority": _case_level_endpoint(samples, endpoint="majority"),
            "case_all_repeats": _case_level_endpoint(samples, endpoint="all_repeats"),
        },
        "deterministic_semantic": {
            "grader_version": semantic_grader.GRADER_VERSION,
            "harmless_extra_tools": list(semantic_grader.DEFAULT_HARMLESS_EXTRA_TOOLS),
            "arms": semantic_arms,
            "case_majority": semantic_grader.semantic_case_endpoint(
                samples, endpoint="majority"
            ),
            "case_all_repeats": semantic_grader.semantic_case_endpoint(
                samples, endpoint="all_repeats"
            ),
            "metric_boundary": (
                "Deterministic required-state-transition and final outcome consistency; "
                "not claim-level natural-language groundedness."
            ),
        },
        "samples": samples,
        "limitations": [
            "This is an Executor-only paired evaluation, not full Planner-to-Executor Agent E2E.",
            "The bundled v1 dataset is developer-visible and targets one known failure family.",
            "A matching manifest SHA proves immutability, not blinded authoring.",
            "A null lift is retained and interpreted as ceiling effect or behavioral ineffectiveness.",
            "Case-level endpoints, not repeated provider calls, are the independent statistical units.",
            "The deterministic semantic grader allows only an explicit harmless read-only extra-tool list.",
            "Final-response outcome consistency is coarser than claim-level groundedness.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", type=Path, default=Path("D:/cst_agent_rag_data/agent_eval/tool_use_memory_llm"))
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--validate-oracles-only", action="store_true")
    parser.add_argument("--validate-learning-only", action="store_true")
    parser.add_argument("--max-new-samples", type=int)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.validate_oracles_only and args.validate_learning_only:
        parser.error("choose only one validation-only mode")
    if args.validate_oracles_only:
        if args.resume or args.checkpoint:
            parser.error("oracle-only validation does not use checkpoint/resume")
        report = validate_dataset_oracles(
            dataset_path=args.dataset,
            manifest_path=args.manifest,
            artifact_root=args.artifact_root,
        )
    elif args.validate_learning_only:
        if args.resume or args.checkpoint:
            parser.error("learning-only validation does not use checkpoint/resume")
        report = validate_learning_fixtures(
            dataset_path=args.dataset,
            manifest_path=args.manifest,
            artifact_root=args.artifact_root,
        )
    else:
        try:
            report = run_pair(
                artifact_root=args.artifact_root,
                model=args.model,
                repeat_count=args.repeat,
                dataset_path=args.dataset,
                manifest_path=args.manifest,
                checkpoint_path=args.checkpoint,
                resume=args.resume,
                max_new_samples=args.max_new_samples,
            )
        except EvaluationPaused as exc:
            print(json.dumps({"status": "paused", **exc.progress}, ensure_ascii=False, indent=2))
            return 75
    _write_json_atomic(args.output, report)
    summary = (
        {"all_oracles_executable": report["all_oracles_executable"], "case_count": report["case_count"]}
        if args.validate_oracles_only
        else {
            "all_checks_passed": report["all_checks_passed"],
            "fixture_count": report["fixture_count"],
        }
        if args.validate_learning_only
        else {"arms": report["arms"], "paired": report["paired"]}
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
