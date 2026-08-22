"""Tests for /v1/models, /v1/embed, /v1/chat behavior against a fake provider."""

from fastapi.testclient import TestClient

from lydia.llm.client import OllamaError, parse_chat_line
from lydia.llm.types import ChatChunk

from tests.conftest import FakeProvider


def test_models_returns_provider_models(api_client: TestClient, auth_headers: dict) -> None:
    response = api_client.get("/v1/models", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["models"] == [{"name": "qwen3.5:9b", "size": 100, "modified_at": "t"}]


def test_embed_passes_through_to_provider(
    api_client: TestClient, auth_headers: dict, fake_provider: FakeProvider
) -> None:
    response = api_client.post("/v1/embed", json={"model": "nomic-embed-text", "input": ["hello"]}, headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {"embeddings": [[0.1, 0.2, 0.3]]}
    assert fake_provider.embed_calls == [("nomic-embed-text", ["hello"])]


def test_embed_upstream_error_becomes_502(
    api_client: TestClient, auth_headers: dict, fake_provider: FakeProvider
) -> None:
    fake_provider.embed = lambda model, inputs: (_ for _ in ()).throw(OllamaError("upstream boom"))
    response = api_client.post("/v1/embed", json={"model": "m", "input": ["x"]}, headers=auth_headers)
    assert response.status_code == 502
    assert "upstream boom" in response.text


def test_chat_streams_ndjson_parseable_by_the_cli_client(
    api_client: TestClient, auth_headers: dict, fake_provider: FakeProvider
) -> None:
    fake_provider.chat_chunks = [
        ChatChunk(content="Hel", done=False),
        ChatChunk(content="lo!", done=True, stats={"eval_count": 2}),
    ]
    response = api_client.post(
        "/v1/chat",
        json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
        headers=auth_headers,
    )
    assert response.status_code == 200
    lines = [line for line in response.text.splitlines() if line.strip()]
    chunks = [parse_chat_line(line) for line in lines]
    assert "".join(c.content for c in chunks if c) == "Hello!"
    assert chunks[-1].done is True


def test_chat_forwards_request_fields_to_provider(
    api_client: TestClient, auth_headers: dict, fake_provider: FakeProvider
) -> None:
    api_client.post(
        "/v1/chat",
        json={
            "model": "qwen3.5:9b",
            "messages": [{"role": "user", "content": "hi"}],
            "options": {"temperature": 0.2, "num_ctx": 4096},
            "think": False,
            "tools": [{"type": "function", "function": {"name": "read_file"}}],
            "keep_alive": "30m",
        },
        headers=auth_headers,
    )
    assert len(fake_provider.chat_calls) == 1
    call = fake_provider.chat_calls[0]
    assert call["model"] == "qwen3.5:9b"
    assert call["temperature"] == 0.2
    assert call["num_ctx"] == 4096
    assert call["think"] is False
    assert call["keep_alive"] == "30m"
    assert call["tools"] == [{"type": "function", "function": {"name": "read_file"}}]


def test_chat_round_trips_tool_calls_in_assistant_messages(
    api_client: TestClient, auth_headers: dict, fake_provider: FakeProvider
) -> None:
    """A second-turn request includes an assistant message carrying tool_calls
    (from an earlier round) — this must survive the Pydantic model → Message
    conversion, not just plain content."""
    api_client.post(
        "/v1/chat",
        json={
            "model": "m",
            "messages": [
                {"role": "user", "content": "read a.py"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "a.py"}}}],
                },
                {"role": "tool", "content": "x = 1"},
            ],
        },
        headers=auth_headers,
    )
    sent_messages = fake_provider.chat_calls[0]["messages"]
    assistant_msg = sent_messages[1]
    assert len(assistant_msg.tool_calls) == 1
    # ToolCall.id is randomly generated per instance, so compare fields directly
    # rather than dataclass equality (which would compare id too).
    assert assistant_msg.tool_calls[0].name == "read_file"
    assert assistant_msg.tool_calls[0].arguments == {"path": "a.py"}


def test_chat_upstream_error_becomes_in_band_error_line(
    api_client: TestClient, auth_headers: dict, fake_provider: FakeProvider
) -> None:
    fake_provider.raise_on_chat = OllamaError("model not found")
    response = api_client.post(
        "/v1/chat",
        json={"model": "missing", "messages": [{"role": "user", "content": "hi"}]},
        headers=auth_headers,
    )
    assert response.status_code == 200  # streaming already started; error is in-band
    assert "model not found" in response.text


def test_chat_does_not_close_provider_after_streaming(
    api_client: TestClient, auth_headers: dict, fake_provider: FakeProvider
) -> None:
    """The provider is shared across requests (see test_ollama_provider.py),
    so a single request must not close it — that would tear down the
    connection every other in-flight or subsequent request also relies on."""
    api_client.post(
        "/v1/chat",
        json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
        headers=auth_headers,
    )
    assert fake_provider.closed is False


def test_models_does_not_close_provider(
    api_client: TestClient, auth_headers: dict, fake_provider: FakeProvider
) -> None:
    api_client.get("/v1/models", headers=auth_headers)
    assert fake_provider.closed is False


def test_embed_does_not_close_provider(
    api_client: TestClient, auth_headers: dict, fake_provider: FakeProvider
) -> None:
    api_client.post("/v1/embed", json={"model": "m", "input": ["x"]}, headers=auth_headers)
    assert fake_provider.closed is False


def test_repeated_requests_share_the_same_provider_instance(
    api_client: TestClient, auth_headers: dict, fake_provider: FakeProvider
) -> None:
    """api_client's override always hands back the same fake_provider
    regardless of how many requests run, but list_models/embed/chat must
    each accept that same instance via the get_provider dependency rather
    than assuming a fresh one — this pins that nothing route-local tries to
    construct its own."""
    api_client.get("/v1/models", headers=auth_headers)
    api_client.post("/v1/embed", json={"model": "m", "input": ["x"]}, headers=auth_headers)
    api_client.post(
        "/v1/chat",
        json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
        headers=auth_headers,
    )
    assert fake_provider.closed is False
    assert len(fake_provider.embed_calls) == 1
    assert len(fake_provider.chat_calls) == 1
