"""Tests for the Ollama client using httpx.MockTransport (no daemon needed)."""

import json

import httpx
import pytest

from lydia.llm.client import OllamaClient, OllamaError, extract_error, parse_chat_line, serialize_chat_chunk
from lydia.llm.types import ChatChunk, Message, ToolCall


def make_client(handler) -> OllamaClient:
    client = OllamaClient()
    client._client = httpx.Client(
        base_url=client.host, transport=httpx.MockTransport(handler)
    )
    return client


def ndjson(*objects: dict) -> bytes:
    return ("\n".join(json.dumps(o) for o in objects) + "\n").encode()


def test_list_models() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [
            {"name": "qwen3.5:9b", "size": 6_600_000_000, "modified_at": "2026-06-01"},
        ]})

    models = make_client(handler).list_models()
    assert len(models) == 1
    assert models[0].name == "qwen3.5:9b"
    assert models[0].size_human == "6.1 GB"


def test_chat_stream_accumulates_chunks() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        payload = json.loads(request.content)
        assert payload["stream"] is True
        assert payload["messages"][0] == {"role": "user", "content": "hi"}
        return httpx.Response(200, content=ndjson(
            {"message": {"content": "Hel"}, "done": False},
            {"message": {"content": "lo!"}, "done": False},
            {"message": {"content": ""}, "done": True,
             "eval_count": 5, "total_duration": 1_000_000_000},
        ))

    chunks = list(make_client(handler).chat_stream("m", [Message("user", "hi")]))
    assert "".join(c.content for c in chunks) == "Hello!"
    assert chunks[-1].done is True
    assert chunks[-1].stats["eval_count"] == 5


def test_chat_stream_surfaces_ollama_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model 'nope' not found"})

    with pytest.raises(OllamaError, match="not found"):
        list(make_client(handler).chat_stream("nope", [Message("user", "hi")]))


def test_thinking_chunks_are_separated_from_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["think"] is False
        return httpx.Response(200, content=ndjson(
            {"message": {"content": "", "thinking": "hmm, "}, "done": False},
            {"message": {"content": "", "thinking": "let me see"}, "done": False},
            {"message": {"content": "Answer."}, "done": False},
            {"message": {"content": ""}, "done": True},
        ))

    chunks = list(make_client(handler).chat_stream("m", [Message("user", "hi")], think=False))
    assert "".join(c.thinking for c in chunks) == "hmm, let me see"
    assert "".join(c.content for c in chunks) == "Answer."


def test_keep_alive_included_when_set() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["keep_alive"] == "30m"
        return httpx.Response(200, content=ndjson({"message": {"content": "ok"}, "done": True}))

    list(make_client(handler).chat_stream("m", [Message("user", "hi")], keep_alive="30m"))


def test_keep_alive_omitted_when_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert "keep_alive" not in payload
        return httpx.Response(200, content=ndjson({"message": {"content": "ok"}, "done": True}))

    list(make_client(handler).chat_stream("m", [Message("user", "hi")]))


def test_serialize_chat_chunk_round_trips_through_parse_chat_line() -> None:
    """serialize_chat_chunk (server-side) and parse_chat_line (client-side)
    must agree on the wire shape — used by RemoteClient against the real
    Lydia Server, see server/lydia_server/api/v1.py."""
    original = ChatChunk(
        content="hi", thinking="pondering",
        tool_calls=[ToolCall(name="read_file", arguments={"path": "a.py"})],
        done=True, stats={"eval_count": 5, "total_duration": 100},
    )
    line = json.dumps(serialize_chat_chunk(original))
    parsed = parse_chat_line(line)
    assert parsed.content == original.content
    assert parsed.thinking == original.thinking
    assert parsed.done is True
    assert parsed.stats == original.stats
    assert len(parsed.tool_calls) == 1
    assert parsed.tool_calls[0].name == "read_file"
    assert parsed.tool_calls[0].arguments == {"path": "a.py"}


def test_serialize_chat_chunk_intermediate_chunk_round_trip() -> None:
    original = ChatChunk(content="partial", done=False)
    parsed = parse_chat_line(json.dumps(serialize_chat_chunk(original)))
    assert parsed.content == "partial"
    assert parsed.done is False


def test_malformed_stream_lines_are_skipped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = b'{"message": {"content": "ok"}, "done": false}\ngarbage\n' + ndjson(
            {"message": {"content": ""}, "done": True}
        )
        return httpx.Response(200, content=body)

    chunks = list(make_client(handler).chat_stream("m", [Message("user", "hi")]))
    assert "".join(c.content for c in chunks) == "ok"


def test_extract_error_adds_hint_for_unsupported_tools_models() -> None:
    body = json.dumps({"error": "registry.ollama.ai/library/phi3.5:latest does not support tools"})
    message = extract_error(body, 400)
    assert "does not support tools" in message
    assert "ollama pull qwen3.5" in message


def test_extract_error_passes_through_other_errors_unchanged() -> None:
    body = json.dumps({"error": "model 'nope' not found"})
    assert extract_error(body, 404) == "model 'nope' not found"


def test_extract_error_falls_back_for_non_json_body() -> None:
    assert extract_error("not json", 500) == "HTTP 500: not json"


def test_stream_cut_off_before_done_raises() -> None:
    """A stream that ends without a done chunk was truncated (daemon killed,
    connection dropped); it must surface as an error, not a finished reply."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=ndjson({"message": {"content": "Hel"}, "done": False}))

    with pytest.raises(OllamaError, match="before the reply finished"):
        list(make_client(handler).chat_stream("m", [Message("user", "hi")]))


@pytest.mark.parametrize("raw, expected", [
    ({"path": "a.py"}, {"path": "a.py"}),  # Ollama's own shape: a JSON object
    ('{"path": "a.py"}', {"path": "a.py"}),  # OpenAI-style: the object as a JSON string
    (None, {}),  # omitted entirely
    ("not json", "not json"),  # undecodable: passed through for execute_tool to report
])
def test_tool_call_arguments_are_normalised(raw, expected) -> None:
    line = json.dumps({
        "message": {"content": "", "tool_calls": [{"function": {"name": "read_file", "arguments": raw}}]},
        "done": False,
    })
    assert parse_chat_line(line).tool_calls[0].arguments == expected


def test_done_reason_is_kept_in_stats() -> None:
    line = json.dumps({"message": {"content": ""}, "done": True, "done_reason": "length", "eval_count": 3})
    assert parse_chat_line(line).stats == {"eval_count": 3, "done_reason": "length"}
