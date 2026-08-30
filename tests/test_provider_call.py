from __future__ import annotations

from types import SimpleNamespace

import pytest

from cst_agent_workbench.agent.error_model import ErrorLayer, ProviderCallError, ProviderErrorCode
from cst_agent_workbench.agent.provider_call import (
    ProviderRetryPolicy,
    call_provider,
    normalize_provider_exception,
)


class _ProviderError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        request_id: str | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id
        self.response = SimpleNamespace(status_code=status_code, headers=headers or {})


def _deterministic_policy(max_attempts: int = 3) -> ProviderRetryPolicy:
    return ProviderRetryPolicy(
        max_attempts=max_attempts,
        initial_delay_seconds=0.5,
        multiplier=2.0,
        max_delay_seconds=5.0,
        jitter_ratio=0.0,
    )


def test_timeout_retries_until_success():
    calls = 0
    sleeps: list[float] = []

    def operation():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise TimeoutError("provider request timed out")
        return "ok"

    result = call_provider(operation, policy=_deterministic_policy(), sleeper=sleeps.append)

    assert result == "ok"
    assert calls == 3
    assert sleeps == [0.5, 1.0]


def test_server_error_preserves_retry_events_and_request_id():
    calls = 0
    events = []

    def operation():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _ProviderError("temporarily unavailable", status_code=503, request_id="req-503")
        return "recovered"

    result = call_provider(
        operation,
        policy=_deterministic_policy(),
        sleeper=lambda _delay: None,
        on_retry=events.append,
    )

    assert result == "recovered"
    assert len(events) == 1
    assert events[0].error.layer == ErrorLayer.PROVIDER
    assert events[0].error.code == ProviderErrorCode.SERVER.value
    assert events[0].error.request_id == "req-503"
    assert events[0].next_attempt == 2


def test_rate_limit_retry_after_header_takes_precedence():
    calls = 0
    sleeps: list[float] = []

    def operation():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _ProviderError(
                "rate limited",
                status_code=429,
                headers={"retry-after": "1.25", "x-request-id": "req-429"},
            )
        return "ok"

    assert call_provider(operation, policy=_deterministic_policy(), sleeper=sleeps.append) == "ok"
    assert sleeps == [1.25]


@pytest.mark.parametrize("status_code, expected_code", [(400, "bad_request"), (401, "auth"), (403, "auth")])
def test_non_retryable_http_errors_are_called_once(status_code: int, expected_code: str):
    calls = 0

    def operation():
        nonlocal calls
        calls += 1
        raise _ProviderError("request rejected", status_code=status_code)

    with pytest.raises(ProviderCallError) as exc_info:
        call_provider(operation, policy=_deterministic_policy(), sleeper=lambda _delay: None)

    assert calls == 1
    assert exc_info.value.envelope.code == expected_code
    assert exc_info.value.envelope.retryable is False
    assert exc_info.value.envelope.details["attempts"] == 1


def test_exhausted_transient_error_records_attempt_count():
    calls = 0

    def operation():
        nonlocal calls
        calls += 1
        raise _ProviderError("upstream unavailable", status_code=502)

    with pytest.raises(ProviderCallError) as exc_info:
        call_provider(
            operation,
            policy=_deterministic_policy(max_attempts=2),
            sleeper=lambda _delay: None,
        )

    envelope = exc_info.value.envelope
    assert calls == 2
    assert envelope.code == ProviderErrorCode.SERVER.value
    assert envelope.retryable is True
    assert envelope.details["attempts"] == 2
    assert envelope.details["max_attempts"] == 2


def test_invalid_response_is_not_retried():
    envelope = normalize_provider_exception(ValueError("malformed response body"))

    assert envelope.layer == ErrorLayer.PROVIDER
    assert envelope.code == ProviderErrorCode.INVALID_RESPONSE.value
    assert envelope.retryable is False


def test_error_envelope_is_json_ready():
    envelope = normalize_provider_exception(
        _ProviderError(
            "service unavailable",
            status_code=503,
            headers={"x-request-id": "trace-1"},
        )
    )

    assert envelope.to_dict() == {
        "layer": "provider",
        "code": "server",
        "message": "service unavailable",
        "retryable": True,
        "status_code": 503,
        "request_id": "trace-1",
        "cause_type": "_ProviderError",
    }


def test_retry_policy_rejects_invalid_values():
    with pytest.raises(ValueError):
        ProviderRetryPolicy(max_attempts=0)
    with pytest.raises(ValueError):
        ProviderRetryPolicy(multiplier=0.5)
    with pytest.raises(ValueError):
        ProviderRetryPolicy(jitter_ratio=1.5)
