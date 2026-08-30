"""Provider-call normalization and bounded retry policy.

The policy is intentionally independent of OpenAI client classes.  Native,
Planner and Pi adapters can pass any zero-argument operation while tests inject
their own sleeper and random source.  Tool recovery and task replanning do not
belong here.
"""

from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass, replace
from typing import Any, Callable, TypeVar

from cst_agent_workbench.agent.error_model import (
    ErrorEnvelope,
    ErrorLayer,
    ProviderCallError,
    ProviderErrorCode,
)

T = TypeVar("T")


@dataclass(frozen=True)
class ProviderRetryPolicy:
    """Bounded exponential-backoff configuration.

    ``max_attempts`` includes the initial call.  Jitter is expressed as a
    fraction around the exponential delay; ``0`` makes the policy deterministic.
    """

    max_attempts: int = 3
    initial_delay_seconds: float = 0.5
    multiplier: float = 2.0
    max_delay_seconds: float = 4.0
    jitter_ratio: float = 0.1

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.initial_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError("retry delays must be non-negative")
        if self.multiplier < 1:
            raise ValueError("multiplier must be at least 1")
        if not 0 <= self.jitter_ratio <= 1:
            raise ValueError("jitter_ratio must be between 0 and 1")

    def delay_for_retry(self, retry_index: int, random_value: float) -> float:
        """Return delay before retry ``retry_index`` (first retry is 1)."""

        base = min(
            self.max_delay_seconds,
            self.initial_delay_seconds * (self.multiplier ** max(0, retry_index - 1)),
        )
        jitter = 1.0 + self.jitter_ratio * (2.0 * min(1.0, max(0.0, random_value)) - 1.0)
        return max(0.0, base * jitter)


@dataclass(frozen=True)
class ProviderRetryEvent:
    attempt: int
    next_attempt: int
    delay_seconds: float
    error: ErrorEnvelope


def _coerce_status_code(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _response_headers(exc: Exception) -> Any:
    response = getattr(exc, "response", None)
    return getattr(response, "headers", None) or {}


def _header(headers: Any, name: str) -> str | None:
    if not headers:
        return None
    try:
        value = headers.get(name) or headers.get(name.lower()) or headers.get(name.upper())
    except AttributeError:
        return None
    return str(value).strip() if value is not None and str(value).strip() else None


def _retry_after_seconds(exc: Exception) -> float | None:
    value = _header(_response_headers(exc), "retry-after")
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return max(0.0, parsed)


def normalize_provider_exception(exc: Exception) -> ErrorEnvelope:
    """Map SDK-specific exceptions to a stable provider error envelope."""

    response = getattr(exc, "response", None)
    status_code = _coerce_status_code(
        getattr(exc, "status_code", None) or getattr(response, "status_code", None)
    )
    headers = _response_headers(exc)
    request_id = (
        getattr(exc, "request_id", None)
        or _header(headers, "x-request-id")
        or _header(headers, "request-id")
    )
    cause_type = type(exc).__name__
    text = str(exc) or cause_type
    lowered = f"{cause_type} {text}".lower()
    if status_code is None:
        status_match = re.search(
            r"(?:error\s+code|status(?:\s+code)?|http)\s*[:=]?\s*(\d{3})\b",
            lowered,
        )
        if status_match:
            status_code = int(status_match.group(1))

    if status_code == 408 or "timeout" in lowered or "timed out" in lowered:
        code = ProviderErrorCode.TIMEOUT
        retryable = True
    elif status_code == 429 or "ratelimit" in lowered or "rate limit" in lowered:
        code = ProviderErrorCode.RATE_LIMIT
        retryable = True
    elif status_code in {401, 403} or any(
        marker in lowered
        for marker in ("authentication", "unauthorized", "forbidden", "permissiondenied", "api key")
    ):
        code = ProviderErrorCode.AUTH
        retryable = False
    elif status_code is not None and status_code >= 500 or any(
        marker in lowered for marker in ("service temporarily unavailable", "upstream service unavailable")
    ):
        code = ProviderErrorCode.SERVER
        retryable = True
    elif status_code is not None and 400 <= status_code < 500:
        code = ProviderErrorCode.BAD_REQUEST
        retryable = False
    elif isinstance(exc, (ConnectionError, OSError)) or any(
        marker in lowered for marker in ("connection", "network", "dns", "socket", "transport")
    ):
        code = ProviderErrorCode.NETWORK
        retryable = True
    elif isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError)) or any(
        marker in lowered for marker in ("invalid response", "malformed response", "decode response")
    ):
        code = ProviderErrorCode.INVALID_RESPONSE
        retryable = False
    else:
        code = ProviderErrorCode.UNKNOWN
        retryable = False

    details: dict[str, Any] = {}
    retry_after = _retry_after_seconds(exc)
    if retry_after is not None:
        details["retry_after_seconds"] = retry_after
    return ErrorEnvelope(
        layer=ErrorLayer.PROVIDER,
        code=code.value,
        message=text,
        retryable=retryable,
        status_code=status_code,
        request_id=str(request_id) if request_id else None,
        cause_type=cause_type,
        details=details,
    )


def call_provider(
    operation: Callable[[], T],
    *,
    policy: ProviderRetryPolicy | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    random_value: Callable[[], float] = random.random,
    on_retry: Callable[[ProviderRetryEvent], None] | None = None,
) -> T:
    """Execute a provider operation with bounded retry for transient failures."""

    effective_policy = policy or ProviderRetryPolicy()
    attempt = 1
    while True:
        try:
            return operation()
        except Exception as exc:
            envelope = normalize_provider_exception(exc)
            if not envelope.retryable or attempt >= effective_policy.max_attempts:
                details = dict(envelope.details)
                details.update({"attempts": attempt, "max_attempts": effective_policy.max_attempts})
                raise ProviderCallError(replace(envelope, details=details)) from exc

            retry_after = envelope.details.get("retry_after_seconds")
            if isinstance(retry_after, (int, float)):
                delay = min(effective_policy.max_delay_seconds, max(0.0, float(retry_after)))
            else:
                delay = effective_policy.delay_for_retry(attempt, random_value())
            event = ProviderRetryEvent(
                attempt=attempt,
                next_attempt=attempt + 1,
                delay_seconds=delay,
                error=envelope,
            )
            if on_retry is not None:
                on_retry(event)
            sleeper(delay)
            attempt += 1
