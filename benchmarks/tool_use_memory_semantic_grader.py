"""Deterministic task-state and response-outcome grading for ToolUseMemory samples."""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from benchmarks.eval_statistics import exact_paired_binary_test, wilson_interval


GRADER_VERSION = "tool-use-memory-deterministic-semantic-v1"
DEFAULT_HARMLESS_EXTRA_TOOLS = ("check_cst_status",)
_SUCCESS_MARKERS = (
    "successfully",
    "completed",
    "configured",
    "created",
    "built",
    "defined",
    "stored",
    "finished",
    "added",
    "ran the solver",
    "solver run",
    "modeling is complete",
)
_FAILURE_MARKERS = (
    "failed",
    "could not",
    "couldn't",
    "unable to",
    "not completed",
    "was not created",
    "was not configured",
    "cannot complete",
    "error",
)


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
        return (
            isinstance(actual, (int, float))
            and not isinstance(actual, bool)
            and float(actual) == float(expected)
        )
    return actual == expected


def _arguments_match(actual: Mapping[str, Any], expected: Mapping[str, Any], mode: str) -> bool:
    if mode == "exact":
        return dict(actual) == dict(expected)
    return _argument_value_matches(dict(actual), dict(expected))


def _response_outcome_stance(final_response: str) -> str:
    normalized = re.sub(r"\s+", " ", final_response.casefold().replace("’", "'")).strip()
    if not normalized:
        return "empty"
    failure = any(marker in normalized for marker in _FAILURE_MARKERS)
    success_text = normalized
    for marker in sorted(_FAILURE_MARKERS, key=len, reverse=True):
        success_text = success_text.replace(marker, " ")
    success = any(marker in success_text for marker in _SUCCESS_MARKERS)
    if success and failure:
        return "ambiguous"
    if failure:
        return "failure"
    if success:
        return "success"
    return "neutral"


def score_semantic_sample(
    sample: Mapping[str, Any],
    case: Mapping[str, Any],
    *,
    agent_completion_ok: bool | None,
    completion_evidence_source: str,
    harmless_extra_tools: Iterable[str] = DEFAULT_HARMLESS_EXTRA_TOOLS,
) -> dict[str, Any]:
    """Grade a required-call subsequence while rejecting material extra state transitions."""

    expected_calls = [dict(item) for item in (case.get("expected_tool_calls") or [])]
    actual_calls = [dict(item) for item in (sample.get("tool_calls_detailed") or [])]
    harmless = set(harmless_extra_tools)
    expected_index = 0
    required_checks: list[dict[str, Any]] = []
    harmless_extras: list[dict[str, Any]] = []
    disallowed_extras: list[dict[str, Any]] = []

    for actual_index, actual in enumerate(actual_calls):
        actual_name = str(actual.get("name") or "")
        if expected_index < len(expected_calls):
            expected = expected_calls[expected_index]
        else:
            expected = None
        if expected is not None and actual_name == str(expected["name"]):
            mode = str(expected.get("argument_match") or "subset")
            arguments_match = _arguments_match(
                dict(actual.get("arguments") or {}),
                dict(expected.get("arguments") or {}),
                mode,
            )
            success = bool(actual.get("success"))
            required_checks.append(
                {
                    "required_index": expected_index,
                    "actual_index": actual_index,
                    "expected_name": expected["name"],
                    "actual_name": actual_name,
                    "argument_match": mode,
                    "arguments_match": arguments_match,
                    "tool_success": success,
                    "passed": bool(arguments_match and success),
                }
            )
            expected_index += 1
            continue
        extra = {
            "actual_index": actual_index,
            "name": actual_name,
            "arguments": dict(actual.get("arguments") or {}),
            "tool_success": bool(actual.get("success")),
        }
        if actual_name in harmless and extra["tool_success"]:
            harmless_extras.append(extra)
        else:
            disallowed_extras.append(extra)

    for missing_index in range(expected_index, len(expected_calls)):
        expected = expected_calls[missing_index]
        required_checks.append(
            {
                "required_index": missing_index,
                "actual_index": None,
                "expected_name": expected["name"],
                "actual_name": None,
                "argument_match": str(expected.get("argument_match") or "subset"),
                "arguments_match": False,
                "tool_success": False,
                "passed": False,
            }
        )

    required_calls_passed = (
        len(required_checks) == len(expected_calls)
        and bool(expected_calls)
        and all(check["passed"] for check in required_checks)
    )
    score_valid = isinstance(agent_completion_ok, bool)
    semantic_task_success = bool(
        score_valid
        and agent_completion_ok
        and required_calls_passed
        and not disallowed_extras
    )
    final_response = str(sample.get("final_response") or "")
    stance = _response_outcome_stance(final_response)
    if not score_valid or stance in {"empty", "ambiguous"}:
        outcome_consistent = False
    elif semantic_task_success:
        outcome_consistent = stance in {"success", "neutral"}
    else:
        outcome_consistent = stance == "failure"
    return {
        "grader_version": GRADER_VERSION,
        "score_valid": score_valid,
        "agent_completion_ok": agent_completion_ok,
        "completion_evidence_source": completion_evidence_source,
        "required_calls_passed": required_calls_passed,
        "required_call_checks": required_checks,
        "harmless_extra_tools": sorted(harmless),
        "harmless_extra_calls": harmless_extras,
        "disallowed_extra_calls": disallowed_extras,
        "semantic_task_success": semantic_task_success,
        "response_outcome_stance": stance,
        "final_response_outcome_consistent": outcome_consistent,
        "exact_sequence_false_negative": bool(
            semantic_task_success and not sample.get("task_success")
        ),
        "unsupported_success_claim": bool(
            not semantic_task_success and stance == "success"
        ),
    }


def _valid_scores(samples: list[dict[str, Any]], arm: str) -> list[dict[str, Any]]:
    return [
        sample["deterministic_semantic"]
        for sample in samples
        if sample.get("arm") == arm
        and isinstance(sample.get("deterministic_semantic"), dict)
        and sample["deterministic_semantic"].get("score_valid") is True
    ]


def semantic_arm_metrics(samples: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for arm in ("no_memory", "learned"):
        arm_samples = [sample for sample in samples if sample.get("arm") == arm]
        scores = _valid_scores(samples, arm)
        valid = len(scores)
        successes = sum(bool(score["semantic_task_success"]) for score in scores)
        result[arm] = {
            "samples": len(arm_samples),
            "valid_scores": valid,
            "semantic_task_success": {
                "successes": successes,
                "trials": valid,
                "rate": successes / valid if valid else None,
                "unit": "correlated_provider_call",
                "interpretation": (
                    "Descriptive only; repeated calls of one case are not independent units."
                ),
            },
            "final_response_outcome_consistency_rate": (
                sum(bool(score["final_response_outcome_consistent"]) for score in scores) / valid
                if valid
                else None
            ),
            "exact_sequence_false_negatives": sum(
                bool(score["exact_sequence_false_negative"]) for score in scores
            ),
            "unsupported_success_claims": sum(
                bool(score["unsupported_success_claim"]) for score in scores
            ),
        }
    return result


def semantic_case_endpoint(
    samples: list[dict[str, Any]],
    *,
    endpoint: str,
) -> dict[str, Any]:
    by_case: dict[str, dict[str, list[bool]]] = {}
    invalid_samples: list[str] = []
    for sample in samples:
        score = sample.get("deterministic_semantic") or {}
        if score.get("score_valid") is not True:
            invalid_samples.append(str(sample.get("sample_id") or ""))
            continue
        by_case.setdefault(str(sample["case_id"]), {}).setdefault(str(sample["arm"]), []).append(
            bool(score["semantic_task_success"])
        )
    if invalid_samples:
        return {
            "endpoint": endpoint,
            "unit": "independent_case",
            "valid": False,
            "invalid_sample_ids": invalid_samples,
        }

    def reduce(values: list[bool]) -> bool:
        return all(values) if endpoint == "all_repeats" else sum(values) > len(values) / 2

    paired: list[tuple[bool, bool]] = []
    per_case: list[dict[str, Any]] = []
    for case_id in sorted(by_case):
        arms = by_case[case_id]
        if set(arms) != {"no_memory", "learned"}:
            raise ValueError(f"incomplete deterministic semantic pair for {case_id}")
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
        "valid": True,
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
