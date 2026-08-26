"""Tests for RemoteClient using httpx.MockTransport, mirroring test_client.py."""

import json

import httpx
import pytest

from lydia.llm.client import OllamaError
from lydia.llm.remote_client import RemoteAuthError, RemoteClient, RemoteConnectionError
from lydia.llm.types import Message


def make_remote(handler, api_key: str | None = "secret-token") -> RemoteClient:
    client = RemoteClient(base_url="https://server.example", api_key=api_key)
    client._client = httpx.Client(
        base_url=client.base_url, headers=client._client.headers, transport=httpx.MockTransport(handler)
    )
    return client


def ndjson(*objects: dict) -> bytes:
    return ("\n".join(json.dumps(o) for o in objects) + "\n").encode()


def test_bearer_token_sent_on_every_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer secret-token"
        return httpx.Response(200, json={"models": []})

    make_remote(handler).list_models()


def test_no_auth_header_when_no_api_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"models": []})

    make_remote(handler, api_key=None).list_models()


def test_list_models_hits_v1_models() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json={"models": [{"name": "qwen3.5:9b", "size": 100, "modified_at": "t"}]})

    models = make_remote(handler).list_models()
    assert models[0].name == "qwen3.5:9b"


def test_embed_hits_v1_embed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/embed"
        payload = json.loads(request.content)
        assert payload == {"model": "nomic-embed-text", "input": ["hello"]}
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2]]})

    result = make_remote(handler).embed("nomic-embed-text", ["hello"])
    assert result == [[0.1, 0.2]]


def test_chat_stream_hits_v1_chat_and_parses_like_ollama() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat"
        return httpx.Response(200, content=ndjson(
            {"message": {"content": "Hel"}, "done": False},
            {"message": {"content": "lo!"}, "done": False},
            {"message": {"content": ""}, "done": True, "eval_count": 3},
        ))

    chunks = list(make_remote(handler).chat_stream("m", [Message("user", "hi")]))
    assert "".join(c.content for c in chunks) == "Hello!"
    assert chunks[-1].done is True


def test_401_raises_remote_auth_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid token"})

    with pytest.raises(RemoteAuthError):
        make_remote(handler).list_models()


def test_403_raises_remote_auth_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "forbidden"})

    with pytest.raises(RemoteAuthError):
        make_remote(handler).embed("m", ["x"])


def test_other_error_status_raises_ollama_error_not_auth_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "server exploded"})

    with pytest.raises(OllamaError) as exc_info:
        make_remote(handler).list_models()
    assert not isinstance(exc_info.value, RemoteAuthError)
    assert "server exploded" in str(exc_info.value)


def test_connection_error_raises_remote_connection_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(RemoteConnectionError):
        make_remote(handler).list_models()


def test_is_alive_false_on_connection_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    assert make_remote(handler).is_alive() is False


def test_is_alive_true_on_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/health"
        return httpx.Response(200)

    assert make_remote(handler).is_alive() is True


def test_embed_empty_input_short_circuits() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not make a request for empty input")

    assert make_remote(handler).embed("m", []) == []


def test_stream_cut_off_before_done_raises() -> None:
    """Same guarantee as OllamaClient: a server that drops mid-reply must
    not look like a short but finished answer."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=ndjson({"message": {"content": "Hel"}, "done": False}))

    with pytest.raises(OllamaError, match="before the reply finished"):
        list(make_remote(handler).chat_stream("m", [Message("user", "hi")]))
