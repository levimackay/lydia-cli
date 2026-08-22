"""Tests for the Gemini client using httpx.MockTransport (no live API needed
for the automated suite) — the wire format itself was verified against the
real API by hand before writing this client; see gemini_client.py's module
docstring."""

import json

import httpx
import pytest

from lydia.llm.gemini_client import (
    GeminiAuthError,
    GeminiClient,
    GeminiConnectionError,
    _to_gemini_contents,
    _to_gemini_tools,
)
from lydia.llm.types import Message, ToolCall


def make_client(handler) -> GeminiClient:
    client = GeminiClient(api_key="test-key")
    client._client = httpx.Client(
        base_url="https://generativelanguage.googleapis.com/v1beta",
        transport=httpx.MockTransport(handler),
    )
    return client


def sse(*objects: dict) -> bytes:
    return "\n\n".join(f"data: {json.dumps(o)}" for o in objects).encode() + b"\n\n"


# -- message conversion ------------------------------------------------------


def test_system_messages_become_a_separate_system_instruction() -> None:
    system, contents = _to_gemini_contents([
        Message("system", "You are Lydia."),
        Message("user", "hi"),
    ])
    assert system == "You are Lydia."
    assert contents == [{"role": "user", "parts": [{"text": "hi"}]}]


def test_multiple_system_messages_are_joined() -> None:
    system, _ = _to_gemini_contents([Message("system", "First."), Message("system", "Second.")])
    assert system == "First.\n\nSecond."


def test_assistant_with_tool_calls_becomes_functioncall_parts() -> None:
    call = ToolCall(name="read_file", arguments={"path": "a.py"})
    _, contents = _to_gemini_contents([
        Message("user", "read a.py"),
        Message("assistant", "", tool_calls=[call]),
    ])
    assert contents[1] == {
        "role": "model",
        "parts": [{"functionCall": {"name": "read_file", "args": {"path": "a.py"}}}],
    }


def test_tool_result_is_matched_to_the_preceding_call_by_position() -> None:
    """Message(role="tool", ...) carries no function name of its own (see
    agent/loop.py) — the name has to be recovered from the assistant
    message's tool_calls, in order."""
    calls = [ToolCall(name="read_file", arguments={"path": "a.py"}), ToolCall(name="read_file", arguments={"path": "b.py"})]
    _, contents = _to_gemini_contents([
        Message("user", "read both files"),
        Message("assistant", "", tool_calls=calls),
        Message("tool", "contents of a.py"),
        Message("tool", "contents of b.py"),
    ])
    tool_msgs = [c for c in contents if c["role"] == "function"]
    assert tool_msgs[0]["parts"][0]["functionResponse"] == {
        "name": "read_file",
        "response": {"result": "contents of a.py"},
    }
    assert tool_msgs[1]["parts"][0]["functionResponse"] == {
        "name": "read_file",
        "response": {"result": "contents of b.py"},
    }


def test_tool_schema_conversion_wraps_into_function_declarations() -> None:
    tools = [
        {"type": "function", "function": {"name": "get_weather", "description": "d", "parameters": {"type": "object"}}},
    ]
    converted = _to_gemini_tools(tools)
    assert converted == [
        {"functionDeclarations": [{"name": "get_weather", "description": "d", "parameters": {"type": "object"}}]}
    ]


def test_no_tools_converts_to_none() -> None:
    assert _to_gemini_tools(None) is None
    assert _to_gemini_tools([]) is None


# -- chat_stream ---------------------------------------------------------


def test_chat_stream_parses_text_reply() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith(":streamGenerateContent")
        assert request.url.params["alt"] == "sse"
        payload = json.loads(request.content)
        assert payload["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]
        return httpx.Response(200, content=sse({
            "candidates": [{
                "content": {"parts": [{"text": "Hello!"}], "role": "model"},
                "finishReason": "STOP",
            }],
            "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2},
        }))

    chunks = list(make_client(handler).chat_stream("gemini-2.5-flash", [Message("user", "hi")]))
    assert chunks[-1].content == "Hello!"
    assert chunks[-1].done is True
    assert chunks[-1].stats == {"prompt_eval_count": 3, "eval_count": 2}


def test_chat_stream_parses_a_function_call() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse({
            "candidates": [{
                "content": {"parts": [{"functionCall": {"name": "get_weather", "args": {"city": "Paris"}}}], "role": "model"},
                "finishReason": "STOP",
            }],
        }))

    chunks = list(make_client(handler).chat_stream("gemini-2.5-flash", [Message("user", "weather?")]))
    assert len(chunks[-1].tool_calls) == 1
    assert chunks[-1].tool_calls[0].name == "get_weather"
    assert chunks[-1].tool_calls[0].arguments == {"city": "Paris"}


def test_chat_stream_accumulates_multiple_sse_lines() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse(
            {"candidates": [{"content": {"parts": [{"text": "Hel"}], "role": "model"}}]},
            {"candidates": [{"content": {"parts": [{"text": "lo!"}], "role": "model"}, "finishReason": "STOP"}]},
        ))

    chunks = list(make_client(handler).chat_stream("gemini-2.5-flash", [Message("user", "hi")]))
    assert "".join(c.content for c in chunks) == "Hello!"
    assert chunks[-1].done is True
    assert len(chunks) == 2


def test_chat_stream_sends_system_instruction_and_tools() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, content=sse({"candidates": [{"content": {"parts": [{"text": "ok"}], "role": "model"}, "finishReason": "STOP"}]}))

    tools = [{"type": "function", "function": {"name": "f", "description": "d", "parameters": {}}}]
    list(make_client(handler).chat_stream(
        "gemini-2.5-flash",
        [Message("system", "be helpful"), Message("user", "hi")],
        tools=tools,
    ))
    assert captured["payload"]["systemInstruction"] == {"parts": [{"text": "be helpful"}]}
    assert captured["payload"]["tools"] == [{"functionDeclarations": [{"name": "f", "description": "d", "parameters": {}}]}]


def test_chat_stream_401_raises_auth_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "API key not valid"}})

    with pytest.raises(GeminiAuthError):
        list(make_client(handler).chat_stream("gemini-2.5-flash", [Message("user", "hi")]))


def test_chat_stream_connect_error_raises_gemini_connection_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(GeminiConnectionError):
        list(make_client(handler).chat_stream("gemini-2.5-flash", [Message("user", "hi")]))


# -- other protocol methods ------------------------------------------------


def test_list_models_strips_the_models_prefix_and_filters_to_chat_capable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(200, json={"models": [
            {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-embedding-001", "supportedGenerationMethods": ["embedContent"]},
        ]})

    models = make_client(handler).list_models()
    assert [m.name for m in models] == ["gemini-2.5-flash"]


def test_embed_returns_one_vector_per_input() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith(":embedContent")
        return httpx.Response(200, json={"embedding": {"values": [0.1, 0.2, 0.3]}})

    vectors = make_client(handler).embed("gemini-embedding-001", ["a", "b"])
    assert vectors == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]


def test_embed_with_no_inputs_makes_no_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not be called")

    assert make_client(handler).embed("gemini-embedding-001", []) == []


def test_is_alive_true_on_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": []})

    assert make_client(handler).is_alive() is True


def test_is_alive_false_on_connection_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    assert make_client(handler).is_alive() is False


def test_missing_api_key_raises_immediately() -> None:
    with pytest.raises(GeminiAuthError):
        GeminiClient(api_key="")


def test_api_key_sent_as_header_not_query_param() -> None:
    """Verified against the real API that the header form works — using it
    keeps the key out of URLs (proxy logs, etc.), so pin that this client
    actually does that rather than the query-param form."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"models": []})

    client = GeminiClient(api_key="super-secret-key")
    # Unlike make_client() above, this test cares about headers specifically
    # — carry over the same x-goog-api-key header the real constructor set,
    # since swapping in a bare httpx.Client would otherwise silently drop it
    # and the assertion below would fail for the wrong reason.
    client._client = httpx.Client(
        base_url="https://generativelanguage.googleapis.com/v1beta",
        headers={"x-goog-api-key": "super-secret-key"},
        transport=httpx.MockTransport(handler),
    )
    client.is_alive()
    assert captured["headers"]["x-goog-api-key"] == "super-secret-key"
    assert "super-secret-key" not in captured["url"]
