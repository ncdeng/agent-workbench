from benchmarks.tool_use_memory_semantic_grader import (
    score_semantic_sample,
    semantic_arm_metrics,
    semantic_case_endpoint,
)


def _case() -> dict:
    return {
        "case_id": "solver-case",
        "expected_tool_calls": [
            {
                "name": "create_discrete_port",
                "arguments": {
                    "port_number": 1,
                    "p2_x": "0",
                    "p2_y": "0",
                    "p2_z": "1.6",
                },
                "argument_match": "exact",
            },
            {"name": "run_solver", "arguments": {}, "argument_match": "exact"},
        ],
    }


def _sample(calls: list[dict], response: str, *, task_success: bool = False) -> dict:
    return {
        "sample_id": "solver-case::repeat_01::learned",
        "case_id": "solver-case",
        "arm": "learned",
        "task_success": task_success,
        "tool_calls_detailed": calls,
        "final_response": response,
    }


def _port_call(**overrides) -> dict:
    arguments = {
        "port_number": 1,
        "p2_x": "0",
        "p2_y": "0",
        "p2_z": "1.6",
    }
    arguments.update(overrides)
    return {"name": "create_discrete_port", "arguments": arguments, "success": True}


def _solver_call(*, success: bool = True) -> dict:
    return {"name": "run_solver", "arguments": {}, "success": success}


def test_semantic_grader_accepts_required_state_transition():
    sample = _sample(
        [_port_call(), _solver_call()],
        "Configured the port and completed the solver run successfully.",
        task_success=True,
    )

    score = score_semantic_sample(
        sample,
        _case(),
        agent_completion_ok=True,
        completion_evidence_source="runtime_result",
    )

    assert score["semantic_task_success"] is True
    assert score["final_response_outcome_consistent"] is True
    assert score["exact_sequence_false_negative"] is False


def test_semantic_grader_allows_successful_read_only_extra_call_as_subsequence():
    sample = _sample(
        [
            {"name": "check_cst_status", "arguments": {}, "success": True},
            _port_call(),
            _solver_call(),
        ],
        "The port was configured and the solver run completed.",
        task_success=False,
    )

    score = score_semantic_sample(
        sample,
        _case(),
        agent_completion_ok=True,
        completion_evidence_source="runtime_result",
    )

    assert score["semantic_task_success"] is True
    assert [item["name"] for item in score["harmless_extra_calls"]] == ["check_cst_status"]
    assert score["exact_sequence_false_negative"] is True


def test_semantic_grader_rejects_wrong_axis_and_flags_unsupported_success_claim():
    sample = _sample(
        [_port_call(p2_y="1.6", p2_z="0"), _solver_call()],
        "Successfully configured the requested port coordinates and completed the solver.",
    )

    score = score_semantic_sample(
        sample,
        _case(),
        agent_completion_ok=True,
        completion_evidence_source="runtime_result",
    )

    assert score["required_calls_passed"] is False
    assert score["semantic_task_success"] is False
    assert score["unsupported_success_claim"] is True
    assert score["final_response_outcome_consistent"] is False


def test_semantic_grader_distinguishes_honest_failure_from_task_success():
    sample = _sample(
        [_port_call(), _solver_call(success=False)],
        "The solver failed, so the requested workflow was not completed.",
    )

    score = score_semantic_sample(
        sample,
        _case(),
        agent_completion_ok=True,
        completion_evidence_source="runtime_result",
    )

    assert score["semantic_task_success"] is False
    assert score["response_outcome_stance"] == "failure"
    assert score["final_response_outcome_consistent"] is True
    assert score["unsupported_success_claim"] is False


def test_semantic_grader_rejects_material_extra_state_transition():
    sample = _sample(
        [
            _port_call(),
            {"name": "execute_vba_script", "arguments": {"vba_code": "x"}, "success": True},
            _solver_call(),
        ],
        "Successfully completed the solver run.",
    )

    score = score_semantic_sample(
        sample,
        _case(),
        agent_completion_ok=True,
        completion_evidence_source="runtime_result",
    )

    assert score["semantic_task_success"] is False
    assert score["disallowed_extra_calls"][0]["name"] == "execute_vba_script"


def test_semantic_grader_marks_missing_completion_evidence_invalid():
    score = score_semantic_sample(
        _sample([_port_call(), _solver_call()], "Completed successfully."),
        _case(),
        agent_completion_ok=None,
        completion_evidence_source="missing",
    )

    assert score["score_valid"] is False
    assert score["semantic_task_success"] is False


def test_semantic_aggregates_report_denominators_and_case_level_pair():
    samples = []
    for arm in ("no_memory", "learned"):
        for repeat_index in (1, 2, 3):
            sample = _sample(
                [_port_call(), _solver_call()],
                "Completed successfully.",
                task_success=True,
            )
            sample.update(
                {
                    "sample_id": f"solver-case::repeat_{repeat_index:02d}::{arm}",
                    "arm": arm,
                    "repeat_index": repeat_index,
                }
            )
            sample["deterministic_semantic"] = score_semantic_sample(
                sample,
                _case(),
                agent_completion_ok=True,
                completion_evidence_source="runtime_result",
            )
            samples.append(sample)

    arms = semantic_arm_metrics(samples)
    case_endpoint = semantic_case_endpoint(samples, endpoint="all_repeats")

    assert arms["no_memory"]["valid_scores"] == 3
    assert arms["learned"]["semantic_task_success"]["rate"] == 1.0
    assert case_endpoint["valid"] is True
    assert case_endpoint["no_memory"]["successes"] == 1
    assert case_endpoint["learned"]["successes"] == 1
