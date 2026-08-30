import copy
import json
from types import SimpleNamespace

import pytest

from cst_agent_workbench.agent.model_provider import (
    ANTHROPIC_MESSAGES,
    CHAT_COMPLETIONS,
    RESPONSES,
    ModelProviderClient,
    normalize_model_protocol,
    pi_api_name,
)


def _tool():
    return {
        "type": "function",
        "function": {
            "name": "check_cst_status",
            "description": "Read CST status",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def _history():
    return [
        {"role": "system", "content": "Use CST tools."},
        {"role": "user", "content": "Check status."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "check_cst_status", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": '{"success":true}'},
        {"role": "user", "content": "Summarize."},
    ]


class _Recorder:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _sdk_client(*, chat=None, responses=None, messages=None):
    return SimpleNamespace(
        chat=SimpleNamespace(completions=chat),
        responses=responses,
        messages=messages,
    )


@pytest.mark.parametrize(
    ("alias", "expected", "pi_name"),
    [
        ("chat", CHAT_COMPLETIONS, "openai-completions"),
        ("openai-responses", RESPONSES, "openai-responses"),
        ("messages", ANTHROPIC_MESSAGES, "anthropic-messages"),
    ],
)
def test_protocol_aliases_are_strict_and_map_to_pi(alias, expected, pi_name):
    assert normalize_model_protocol(alias) == expected
    assert pi_api_name(alias) == pi_name


def test_unknown_protocol_is_rejected_before_provider_call():
    with pytest.raises(ValueError, match="Unsupported model API protocol"):
        normalize_model_protocol("magic-provider")


def test_openai_embedding_surface_is_preserved_but_not_faked_for_messages_provider():
    embedding_endpoint = SimpleNamespace(create=lambda **_kwargs: "embedding-response")
    openai_sdk = _sdk_client(chat=SimpleNamespace(), responses=SimpleNamespace())
    openai_sdk.embeddings = embedding_endpoint
    openai_client = ModelProviderClient(protocol="chat", api_key="test", sdk_client=openai_sdk)
    assert openai_client.embeddings is embedding_endpoint

    anthropic_client = ModelProviderClient(
        protocol="messages",
        api_key="test",
        sdk_client=_sdk_client(messages=SimpleNamespace()),
    )
    with pytest.raises(AttributeError, match="configure EMBEDDING_API_KEY"):
        _ = anthropic_client.embeddings


def test_chat_completions_normalizes_tool_calls_usage_and_cache_options():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="tool_calls",
                message=SimpleNamespace(
                    content="",
                    tool_calls=[
                        SimpleNamespace(
                            id="call-chat",
                            function=SimpleNamespace(name="check_cst_status", arguments="{}"),
                        )
                    ],
                ),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=120,
            completion_tokens=9,
            total_tokens=129,
            prompt_tokens_details=SimpleNamespace(cached_tokens=80),
        ),
    )
    endpoint = _Recorder(response)
    client = ModelProviderClient(
        protocol="chat",
        api_key="test",
        prompt_cache_enabled=True,
        prompt_cache_key="cst-session",
        prompt_cache_ttl="24h",
        sdk_client=_sdk_client(chat=endpoint),
    )

    canonical = client.chat.completions.create(
        model="test-model",
        messages=[{"role": "user", "content": "Check."}],
        tools=[_tool()],
        tool_choice="auto",
    )

    assert endpoint.calls[0]["prompt_cache_key"] == "cst-session"
    assert endpoint.calls[0]["prompt_cache_retention"] == "24h"
    assert canonical.protocol == CHAT_COMPLETIONS
    assert canonical.choices[0].message.tool_calls[0].model_dump()["function"]["name"] == "check_cst_status"
    assert canonical.usage.prompt_tokens == 120
    assert canonical.usage.cached_tokens == 80
    assert canonical.usage.cache_write_tokens == 0


def test_responses_translates_tool_history_without_mutating_it_and_normalizes_usage():
    response = SimpleNamespace(
        status="completed",
        output=[
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", text="Status is healthy.")],
            ),
            SimpleNamespace(type="function_call", call_id="call-2", name="check_cst_status", arguments="{}"),
        ],
        usage=SimpleNamespace(
            input_tokens=200,
            output_tokens=20,
            total_tokens=220,
            input_tokens_details=SimpleNamespace(cached_tokens=150),
        ),
    )
    endpoint = _Recorder(response)
    client = ModelProviderClient(
        protocol="responses",
        api_key="test",
        max_output_tokens=2048,
        prompt_cache_enabled=True,
        prompt_cache_key="cst-responses",
        sdk_client=_sdk_client(responses=endpoint),
    )
    history = _history()
    original = copy.deepcopy(history)

    canonical = client.chat.completions.create(
        model="gpt-test",
        messages=history,
        tools=[_tool()],
        max_tokens=512,
        response_format={"type": "json_object"},
    )

    payload = endpoint.calls[0]
    assert history == original
    assert payload["max_output_tokens"] == 512
    assert payload["tools"][0]["name"] == "check_cst_status"
    function_call = next(item for item in payload["input"] if item.get("type") == "function_call")
    function_output = next(item for item in payload["input"] if item.get("type") == "function_call_output")
    assert function_call["call_id"] == function_output["call_id"] == "call-1"
    assert payload["text"] == {"format": {"type": "json_object"}}
    assert canonical.choices[0].message.content == "Status is healthy."
    assert canonical.choices[0].message.tool_calls[0].id == "call-2"
    assert canonical.usage.cached_tokens == 150


def test_anthropic_messages_translates_system_tools_and_tool_results_with_cache_usage():
    response = SimpleNamespace(
        stop_reason="tool_use",
        content=[
            SimpleNamespace(type="text", text="Checking."),
            SimpleNamespace(type="tool_use", id="call-a", name="check_cst_status", input={}),
        ],
        usage=SimpleNamespace(
            input_tokens=30,
            output_tokens=8,
            cache_read_input_tokens=70,
            cache_creation_input_tokens=10,
        ),
    )
    endpoint = _Recorder(response)
    client = ModelProviderClient(
        protocol="anthropic",
        api_key="test",
        prompt_cache_enabled=True,
        prompt_cache_ttl="1h",
        sdk_client=_sdk_client(messages=endpoint),
    )

    canonical = client.chat.completions.create(
        model="claude-test",
        messages=_history(),
        tools=[_tool()],
        tool_choice="auto",
        max_tokens=1024,
    )

    payload = endpoint.calls[0]
    assert payload["system"] == "Use CST tools."
    assert payload["tools"][0]["input_schema"]["type"] == "object"
    assert payload["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    tool_use = next(
        block
        for message in payload["messages"]
        for block in message["content"]
        if block["type"] == "tool_use"
    )
    tool_result = next(
        block
        for message in payload["messages"]
        for block in message["content"]
        if block["type"] == "tool_result"
    )
    assert tool_use["id"] == tool_result["tool_use_id"] == "call-1"
    assert canonical.choices[0].finish_reason == "tool_calls"
    assert json.loads(canonical.choices[0].message.tool_calls[0].function.arguments) == {}
    assert canonical.usage.prompt_tokens == 110
    assert canonical.usage.cached_tokens == 70
    assert canonical.usage.cache_write_tokens == 10
    assert canonical.usage.total_tokens == 118


@pytest.mark.parametrize("protocol", [CHAT_COMPLETIONS, RESPONSES, ANTHROPIC_MESSAGES])
def test_prompt_cache_can_be_disabled(protocol):
    if protocol == CHAT_COMPLETIONS:
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=[]), finish_reason="stop")],
            usage=None,
        )
        endpoint = _Recorder(response)
        sdk = _sdk_client(chat=endpoint)
    elif protocol == RESPONSES:
        endpoint = _Recorder(SimpleNamespace(output_text="ok", output=[], status="completed", usage=None))
        sdk = _sdk_client(responses=endpoint)
    else:
        endpoint = _Recorder(
            SimpleNamespace(
                content=[SimpleNamespace(type="text", text="ok")],
                stop_reason="end_turn",
                usage=None,
            )
        )
        sdk = _sdk_client(messages=endpoint)
    client = ModelProviderClient(
        protocol=protocol,
        api_key="test",
        prompt_cache_enabled=False,
        sdk_client=sdk,
    )

    result = client.chat.completions.create(
        model="test",
        messages=[{"role": "user", "content": "hello"}],
    )

    assert result.choices[0].message.content == "ok"
    assert "prompt_cache_key" not in endpoint.calls[0]
    assert "cache_control" not in endpoint.calls[0]
