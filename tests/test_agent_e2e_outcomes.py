from benchmarks.agent_e2e_outcomes import SampleOutcome, classify_sample, summarize_outcomes


def _case(**overrides):
    value = {
        "task_success": False,
        "strict_grounded_success": False,
        "tool_calls": [],
        "trace_status": "failed",
        "token_usage": {"prompt": 0, "completion": 0, "total": 0, "calls": 1},
        "provider_diagnostics": [],
        "final_response": "",
    }
    value.update(overrides)
    return value


def _diagnostic(error: str):
    return {"call_kind": "executor", "error": error}


def test_terminal_503_is_excluded_from_agent_metrics():
    result = classify_sample(
        _case(
            provider_diagnostics=[
                _diagnostic(
                    "InternalServerError: Error code: 503 - Service temporarily unavailable"
                )
            ],
            final_response='503: {"message":"Service temporarily unavailable","type":"api_error"}',
        )
    )

    assert result.outcome == SampleOutcome.PROVIDER_FAILURE
    assert result.eligible_for_agent_metrics is False
    assert result.provider_errors[0].code == "server"
    assert result.provider_errors[0].status_code == 503


def test_terminal_timeout_after_partial_execution_is_provider_failure_with_audit_flag():
    result = classify_sample(
        _case(
            tool_calls=["check_cst_status"],
            provider_diagnostics=[_diagnostic("APITimeoutError: Request timed out.")],
            final_response="Provider request timed out.",
        )
    )

    assert result.outcome == SampleOutcome.PROVIDER_FAILURE
    assert result.partial_execution is True
    assert result.eligible_for_agent_metrics is False


def test_auxiliary_timeout_does_not_invalidate_successful_task():
    result = classify_sample(
        _case(
            task_success=True,
            strict_grounded_success=True,
            trace_status="completed",
            tool_calls=["execute_vba_script"],
            token_usage={"prompt": 100, "completion": 20, "total": 120, "calls": 2},
            provider_diagnostics=[
                {"call_kind": "auxiliary", "error": "APITimeoutError: Request timed out."}
            ],
            final_response="Recovery completed successfully.",
        )
    )

    assert result.outcome == SampleOutcome.TASK_SUCCESS
    assert result.eligible_for_agent_metrics is True
    assert result.provider_degraded is True


def test_wrong_tool_without_provider_error_is_agent_failure():
    result = classify_sample(
        _case(
            trace_status="completed",
            tool_calls=["run_solver"],
            token_usage={"prompt": 100, "completion": 20, "total": 120, "calls": 1},
            final_response="Done.",
        )
    )

    assert result.outcome == SampleOutcome.TASK_FAILURE
    assert result.eligible_for_agent_metrics is True
    assert result.provider_degraded is False


def test_outcome_summary_separates_capability_from_availability():
    cases = [
        _case(
            task_success=True,
            strict_grounded_success=True,
            trace_status="completed",
            token_usage={"prompt": 10, "completion": 2, "total": 12, "calls": 1},
        ),
        _case(
            trace_status="completed",
            tool_calls=["wrong_tool"],
            token_usage={"prompt": 10, "completion": 2, "total": 12, "calls": 1},
            final_response="wrong",
        ),
        _case(
            provider_diagnostics=[_diagnostic("Error code: 502 - upstream unavailable")],
            final_response="Upstream service temporarily unavailable",
        ),
    ]

    assert summarize_outcomes(cases) == {
        "total_sample_count": 3,
        "eligible_sample_count": 2,
        "agent_success_count": 1,
        "agent_failure_count": 1,
        "provider_failure_count": 1,
        "provider_degraded_eligible_count": 0,
        "eligible_task_success_rate": 0.5,
        "eligible_strict_grounded_success_rate": 0.5,
        "provider_failure_rate": 1 / 3,
        "provider_availability_rate": 2 / 3,
        "end_to_end_task_success_rate": 1 / 3,
    }
