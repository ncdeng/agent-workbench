"""Protocol adapters for the CST Agent's upstream language model.

The rest of the Python agent intentionally keeps one canonical, OpenAI-style
tool-loop contract.  This module is the only place that knows how to translate
that contract to Chat Completions, Responses, or Anthropic Messages.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Iterable, Mapping, Sequence


CHAT_COMPLETIONS = "chat_completions"
RESPONSES = "responses"
ANTHROPIC_MESSAGES = "anthropic_messages"

_PROTOCOL_ALIASES = {
    "chat": CHAT_COMPLETIONS,
    "chat_completions": CHAT_COMPLETIONS,
    "chat-completions": CHAT_COMPLETIONS,
    "openai-completions": CHAT_COMPLETIONS,
    "openai_chat": CHAT_COMPLETIONS,
    "response": RESPONSES,
    "responses": RESPONSES,
    "openai-responses": RESPONSES,
    "message": ANTHROPIC_MESSAGES,
    "messages": ANTHROPIC_MESSAGES,
    "anthropic": ANTHROPIC_MESSAGES,
    "anthropic-messages": ANTHROPIC_MESSAGES,
    "anthropic_messages": ANTHROPIC_MESSAGES,
}


def normalize_model_protocol(value: str | None) -> str:
    normalized = str(value or CHAT_COMPLETIONS).strip().lower()
    protocol = _PROTOCOL_ALIASES.get(normalized)
    if protocol is None:
        supported = ", ".join((CHAT_COMPLETIONS, RESPONSES, ANTHROPIC_MESSAGES))
        raise ValueError(f"Unsupported model API protocol {value!r}; expected one of: {supported}")
    return protocol


def pi_api_name(protocol: str) -> str:
    return {
        CHAT_COMPLETIONS: "openai-completions",
        RESPONSES: "openai-responses",
        ANTHROPIC_MESSAGES: "anthropic-messages",
    }[normalize_model_protocol(protocol)]


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _json_arguments(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value if value is not None else {}, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class CanonicalToolCall:
    id: str
    function: Any
    type: str = "function"

    @classmethod
    def build(cls, *, call_id: Any, name: Any, arguments: Any) -> "CanonicalToolCall":
        return cls(
            id=str(call_id or ""),
            function=SimpleNamespace(name=str(name or ""), arguments=_json_arguments(arguments)),
        )

    def model_dump(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "function": {
                "name": self.function.name,
                "arguments": self.function.arguments,
            },
        }


@dataclass(frozen=True)
class CanonicalUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0


def _canonical_response(
    *,
    content: str = "",
    tool_calls: Sequence[CanonicalToolCall] | None = None,
    usage: CanonicalUsage | None = None,
    finish_reason: str = "stop",
    protocol: str,
) -> Any:
    message = SimpleNamespace(content=content or "", tool_calls=list(tool_calls or []))
    return SimpleNamespace(
        choices=[SimpleNamespace(index=0, message=message, finish_reason=finish_reason)],
        usage=usage or CanonicalUsage(),
        protocol=protocol,
    )


def _usage_from_openai(value: Any, *, input_prefix: str = "prompt") -> CanonicalUsage:
    prompt = _integer(_field(value, f"{input_prefix}_tokens", 0))
    completion_field = "output_tokens" if input_prefix == "input" else "completion_tokens"
    completion = _integer(_field(value, completion_field, 0))
    details = _field(value, f"{input_prefix}_tokens_details", None)
    cached = _integer(_field(details, "cached_tokens", 0))
    cache_write = _integer(
        _field(value, "cache_write_tokens", _field(details, "cache_write_tokens", 0))
    )
    total = _integer(_field(value, "total_tokens", prompt + completion))
    return CanonicalUsage(prompt, completion, total or prompt + completion, cached, cache_write)


def _usage_from_anthropic(value: Any) -> CanonicalUsage:
    uncached = _integer(_field(value, "input_tokens", 0))
    cache_read = _integer(_field(value, "cache_read_input_tokens", 0))
    cache_write = _integer(_field(value, "cache_creation_input_tokens", 0))
    prompt = uncached + cache_read + cache_write
    completion = _integer(_field(value, "output_tokens", 0))
    return CanonicalUsage(prompt, completion, prompt + completion, cache_read, cache_write)


def _text_from_openai_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content if isinstance(content, Sequence) else []:
        if isinstance(block, str):
            parts.append(block)
            continue
        text = _field(block, "text", "")
        if text:
            parts.append(str(text))
    return "".join(parts)


def _normalize_chat_response(response: Any) -> Any:
    choices = list(_field(response, "choices", []) or [])
    if not choices:
        raise ValueError("Chat Completions response did not contain choices")
    choice = choices[0]
    message = _field(choice, "message", None)
    if message is None:
        raise ValueError("Chat Completions response did not contain a message")
    calls = []
    for call in list(_field(message, "tool_calls", []) or []):
        function = _field(call, "function", None)
        calls.append(
            CanonicalToolCall.build(
                call_id=_field(call, "id", ""),
                name=_field(function, "name", ""),
                arguments=_field(function, "arguments", "{}"),
            )
        )
    return _canonical_response(
        content=_text_from_openai_content(_field(message, "content", "")),
        tool_calls=calls,
        usage=_usage_from_openai(_field(response, "usage", None)),
        finish_reason=str(_field(choice, "finish_reason", "stop") or "stop"),
        protocol=CHAT_COMPLETIONS,
    )


def _responses_message_content(content: Any) -> Any:
    if not isinstance(content, Sequence) or isinstance(content, (str, bytes)):
        return content
    converted: list[dict[str, Any]] = []
    for part in content:
        part_type = str(_field(part, "type", ""))
        if part_type == "text":
            converted.append({"type": "input_text", "text": str(_field(part, "text", ""))})
        elif part_type == "image_url":
            image = _field(part, "image_url", {})
            converted.append({"type": "input_image", "image_url": str(_field(image, "url", image) or "")})
    return converted


def _responses_input(messages: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = message.get("content", "")
        if role in {"system", "developer", "user"}:
            items.append({"role": role, "content": _responses_message_content(content)})
            continue
        if role == "assistant":
            if content:
                items.append({"role": "assistant", "content": _responses_message_content(content)})
            for call in message.get("tool_calls") or []:
                function = _field(call, "function", None)
                items.append(
                    {
                        "type": "function_call",
                        "call_id": str(_field(call, "id", "")),
                        "name": str(_field(function, "name", "")),
                        "arguments": _json_arguments(_field(function, "arguments", {})),
                    }
                )
            continue
        if role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": str(message.get("tool_call_id") or ""),
                    "output": str(content or ""),
                }
            )
    return items


def _responses_tools(tools: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    converted = []
    for tool in tools:
        function = _field(tool, "function", tool)
        converted.append(
            {
                "type": "function",
                "name": str(_field(function, "name", "")),
                "description": str(_field(function, "description", "")),
                "parameters": _field(function, "parameters", {"type": "object", "properties": {}}),
            }
        )
    return converted


def _normalize_responses_response(response: Any) -> Any:
    text_parts: list[str] = []
    calls: list[CanonicalToolCall] = []
    for item in list(_field(response, "output", []) or []):
        item_type = str(_field(item, "type", ""))
        if item_type == "function_call":
            calls.append(
                CanonicalToolCall.build(
                    call_id=_field(item, "call_id", _field(item, "id", "")),
                    name=_field(item, "name", ""),
                    arguments=_field(item, "arguments", "{}"),
                )
            )
        elif item_type == "message":
            for block in list(_field(item, "content", []) or []):
                if str(_field(block, "type", "")) in {"output_text", "text"}:
                    text_parts.append(str(_field(block, "text", "")))
    if not text_parts:
        output_text = _field(response, "output_text", "")
        if output_text:
            text_parts.append(str(output_text))
    finish_reason = "tool_calls" if calls else "stop"
    status = str(_field(response, "status", "") or "")
    if status == "incomplete":
        finish_reason = "length"
    return _canonical_response(
        content="".join(text_parts),
        tool_calls=calls,
        usage=_usage_from_openai(_field(response, "usage", None), input_prefix="input"),
        finish_reason=finish_reason,
        protocol=RESPONSES,
    )


def _anthropic_content(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    blocks: list[dict[str, Any]] = []
    for part in content if isinstance(content, Sequence) else []:
        if isinstance(part, str):
            blocks.append({"type": "text", "text": part})
            continue
        part_type = str(_field(part, "type", ""))
        if part_type == "text":
            blocks.append({"type": "text", "text": str(_field(part, "text", ""))})
        elif part_type == "image_url":
            image = _field(part, "image_url", {})
            url = str(_field(image, "url", image) or "")
            if url.startswith("data:") and ";base64," in url:
                header, data = url.split(",", 1)
                media_type = header[5:].split(";", 1)[0]
                blocks.append(
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}
                )
            elif url:
                blocks.append({"type": "image", "source": {"type": "url", "url": url}})
    return blocks


def _append_anthropic_message(messages: list[dict[str, Any]], role: str, blocks: list[dict[str, Any]]) -> None:
    if not blocks:
        return
    if messages and messages[-1]["role"] == role:
        messages[-1]["content"].extend(blocks)
    else:
        messages.append({"role": role, "content": blocks})


def _anthropic_messages(messages: Iterable[Mapping[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    system_parts: list[str] = []
    converted: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        if role in {"system", "developer"}:
            text = _text_from_openai_content(message.get("content"))
            if text:
                system_parts.append(text)
            continue
        if role == "user":
            _append_anthropic_message(converted, "user", _anthropic_content(message.get("content", "")))
            continue
        if role == "assistant":
            blocks = _anthropic_content(message.get("content", ""))
            for call in message.get("tool_calls") or []:
                function = _field(call, "function", None)
                arguments = _field(function, "arguments", {})
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments or "{}")
                    except json.JSONDecodeError:
                        arguments = {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": str(_field(call, "id", "")),
                        "name": str(_field(function, "name", "")),
                        "input": arguments or {},
                    }
                )
            _append_anthropic_message(converted, "assistant", blocks)
            continue
        if role == "tool":
            _append_anthropic_message(
                converted,
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": str(message.get("tool_call_id") or ""),
                        "content": str(message.get("content") or ""),
                    }
                ],
            )
    return "\n\n".join(system_parts), converted


def _anthropic_tools(tools: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    converted = []
    for tool in tools:
        function = _field(tool, "function", tool)
        converted.append(
            {
                "name": str(_field(function, "name", "")),
                "description": str(_field(function, "description", "")),
                "input_schema": _field(function, "parameters", {"type": "object", "properties": {}}),
            }
        )
    return converted


def _normalize_anthropic_response(response: Any) -> Any:
    text_parts: list[str] = []
    calls: list[CanonicalToolCall] = []
    for block in list(_field(response, "content", []) or []):
        block_type = str(_field(block, "type", ""))
        if block_type == "text":
            text_parts.append(str(_field(block, "text", "")))
        elif block_type == "tool_use":
            calls.append(
                CanonicalToolCall.build(
                    call_id=_field(block, "id", ""),
                    name=_field(block, "name", ""),
                    arguments=_field(block, "input", {}),
                )
            )
    stop_reason = {
        "tool_use": "tool_calls",
        "end_turn": "stop",
        "stop_sequence": "stop",
        "max_tokens": "length",
    }.get(str(_field(response, "stop_reason", "") or ""), "stop")
    return _canonical_response(
        content="".join(text_parts),
        tool_calls=calls,
        usage=_usage_from_anthropic(_field(response, "usage", None)),
        finish_reason=stop_reason,
        protocol=ANTHROPIC_MESSAGES,
    )


class _CompletionEndpoint:
    def __init__(self, owner: "ModelProviderClient") -> None:
        self._owner = owner

    def create(self, **kwargs: Any) -> Any:
        return self._owner._create(**kwargs)


class _ChatEndpoint:
    def __init__(self, owner: "ModelProviderClient") -> None:
        self.completions = _CompletionEndpoint(owner)


class ModelProviderClient:
    """Expose one canonical client while dispatching to three provider APIs."""

    def __init__(
        self,
        *,
        protocol: str,
        api_key: str,
        base_url: str = "",
        max_output_tokens: int = 8192,
        prompt_cache_enabled: bool = True,
        prompt_cache_key: str = "cst-agent",
        prompt_cache_ttl: str = "5m",
        sdk_client: Any = None,
    ) -> None:
        self.protocol = normalize_model_protocol(protocol)
        self.max_output_tokens = max(1, int(max_output_tokens))
        self.prompt_cache_enabled = bool(prompt_cache_enabled)
        self.prompt_cache_key = str(prompt_cache_key or "").strip()
        self.prompt_cache_ttl = str(prompt_cache_ttl or "5m").strip().lower()
        self._sdk_client = sdk_client or self._build_sdk_client(api_key=str(api_key or ""), base_url=base_url)
        self.chat = _ChatEndpoint(self)

    @property
    def embeddings(self) -> Any:
        """Preserve the independent OpenAI embedding surface when available."""
        endpoint = getattr(self._sdk_client, "embeddings", None)
        if endpoint is None:
            raise AttributeError(
                "The selected generation provider has no embeddings endpoint; configure EMBEDDING_API_KEY/BASE_URL."
            )
        return endpoint

    def _build_sdk_client(self, *, api_key: str, base_url: str) -> Any:
        if self.protocol == ANTHROPIC_MESSAGES:
            try:
                from anthropic import Anthropic
            except ImportError as exc:
                raise RuntimeError(
                    "Anthropic Messages requires the 'anthropic' dependency; reinstall the project dependencies."
                ) from exc
            kwargs: dict[str, Any] = {"api_key": api_key}
            if str(base_url or "").strip():
                kwargs["base_url"] = str(base_url).strip()
            return Anthropic(**kwargs)
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("OpenAI Chat/Responses requires the 'openai' dependency.") from exc
        kwargs = {"api_key": api_key}
        if str(base_url or "").strip():
            kwargs["base_url"] = str(base_url).strip()
        return OpenAI(**kwargs)

    def _create(self, **kwargs: Any) -> Any:
        if self.protocol == CHAT_COMPLETIONS:
            return self._create_chat_completions(kwargs)
        if self.protocol == RESPONSES:
            return self._create_responses(kwargs)
        return self._create_anthropic_messages(kwargs)

    def _openai_cache_options(self, payload: dict[str, Any]) -> None:
        if not self.prompt_cache_enabled or not self.prompt_cache_key:
            return
        payload["prompt_cache_key"] = self.prompt_cache_key
        payload["prompt_cache_retention"] = "24h" if self.prompt_cache_ttl == "24h" else "in-memory"

    def _create_chat_completions(self, kwargs: Mapping[str, Any]) -> Any:
        payload = dict(kwargs)
        self._openai_cache_options(payload)
        response = self._sdk_client.chat.completions.create(**payload)
        return _normalize_chat_response(response)

    def _create_responses(self, kwargs: Mapping[str, Any]) -> Any:
        payload: dict[str, Any] = {
            "model": kwargs.get("model"),
            "input": _responses_input(kwargs.get("messages") or []),
        }
        if kwargs.get("tools"):
            payload["tools"] = _responses_tools(kwargs.get("tools") or [])
        for key in ("tool_choice", "temperature", "timeout"):
            if kwargs.get(key) is not None:
                payload[key] = kwargs[key]
        max_tokens = kwargs.get("max_tokens")
        payload["max_output_tokens"] = int(max_tokens or self.max_output_tokens)
        response_format = kwargs.get("response_format")
        if isinstance(response_format, Mapping):
            payload["text"] = {"format": dict(response_format)}
        self._openai_cache_options(payload)
        response = self._sdk_client.responses.create(**payload)
        return _normalize_responses_response(response)

    def _create_anthropic_messages(self, kwargs: Mapping[str, Any]) -> Any:
        system, messages = _anthropic_messages(kwargs.get("messages") or [])
        payload: dict[str, Any] = {
            "model": kwargs.get("model"),
            "messages": messages,
            "max_tokens": int(kwargs.get("max_tokens") or self.max_output_tokens),
        }
        if system:
            payload["system"] = system
        if kwargs.get("tools"):
            payload["tools"] = _anthropic_tools(kwargs.get("tools") or [])
        tool_choice = kwargs.get("tool_choice")
        if tool_choice and tool_choice != "none":
            payload["tool_choice"] = {"type": "auto"} if tool_choice == "auto" else tool_choice
        for key in ("temperature", "timeout"):
            if kwargs.get(key) is not None:
                payload[key] = kwargs[key]
        if self.prompt_cache_enabled:
            cache_control: dict[str, str] = {"type": "ephemeral"}
            cache_control["ttl"] = "1h" if self.prompt_cache_ttl == "1h" else "5m"
            payload["cache_control"] = cache_control
        response = self._sdk_client.messages.create(**payload)
        return _normalize_anthropic_response(response)


def create_model_provider_client(**kwargs: Any) -> ModelProviderClient:
    return ModelProviderClient(**kwargs)


__all__ = [
    "ANTHROPIC_MESSAGES",
    "CHAT_COMPLETIONS",
    "RESPONSES",
    "CanonicalToolCall",
    "CanonicalUsage",
    "ModelProviderClient",
    "create_model_provider_client",
    "normalize_model_protocol",
    "pi_api_name",
]
