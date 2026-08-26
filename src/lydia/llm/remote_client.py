"""HTTP client for a remote Lydia Server (see server/ — Milestone: client/server split).

Deliberately mirrors `OllamaClient`'s method signatures exactly (both
satisfy `ModelClient`, see llm/protocol.py) and reuses its NDJSON parsing
and payload-building (`parse_chat_line`, `build_chat_payload`) since the
server's /v1/chat and /v1/embed are thin, same-shape proxies onto its own
Ollama daemon. The only real differences from OllamaClient: requests carry
a bearer token, hit /v1/* instead of /api/*, and auth failures raise a
distinct, catchable error.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx

from lydia.llm.client import (
    OllamaConnectionError,
    OllamaError,
    build_chat_payload,
    extract_error,
    parse_chat_stream,
)
from lydia.llm.types import ChatChunk, Message, ModelInfo


class RemoteConnectionError(OllamaError):
    """Could not reach the Lydia Server at all."""

    def __init__(self, base_url: str) -> None:
        super().__init__(
            f"Cannot reach the Lydia Server at {base_url}.\n"
            "Check the server is running and reachable (e.g. over Tailscale)."
        )
        self.base_url = base_url


class RemoteAuthError(OllamaError):
    """The server rejected the request's credentials."""

    def __init__(self, base_url: str) -> None:
        super().__init__(
            f"Authentication failed against the Lydia Server at {base_url}.\n"
            "Check `lydia config show` — is api_key set and correct?"
        )
        self.base_url = base_url


class RemoteClient:
    """Talks to a Lydia Server over HTTPS instead of a local Ollama daemon."""

    def __init__(self, base_url: str, api_key: str | None = None, timeout: float = 300.0) -> None:
        self.base_url = base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.Client(
            base_url=self.base_url,
            headers=headers,
            timeout=httpx.Timeout(timeout, connect=5.0),
        )

    # -- health -------------------------------------------------------------

    def is_alive(self) -> bool:
        try:
            return self._client.get("/v1/health").status_code == 200
        except httpx.HTTPError:
            return False

    # -- models ---------------------------------------------------------------

    def list_models(self) -> list[ModelInfo]:
        response = self._get("/v1/models")
        models = response.json().get("models", [])
        return [
            ModelInfo(
                name=m.get("name", ""),
                size_bytes=m.get("size", 0),
                modified_at=m.get("modified_at", ""),
            )
            for m in models
        ]

    def has_model(self, name: str) -> bool:
        return any(m.name == name or m.name.split(":")[0] == name for m in self.list_models())

    # -- embeddings -----------------------------------------------------------

    def embed(self, model: str, inputs: list[str]) -> list[list[float]]:
        if not inputs:
            return []
        response = self._post("/v1/embed", {"model": model, "input": inputs})
        embeddings = response.json().get("embeddings")
        if embeddings is None:
            raise OllamaError("Lydia Server returned no embeddings.")
        return embeddings

    # -- chat -------------------------------------------------------------

    def chat_stream(
        self,
        model: str,
        messages: list[Message],
        temperature: float = 0.7,
        num_ctx: int = 8192,
        think: bool | None = None,
        tools: list[dict] | None = None,
        keep_alive: str | None = None,
    ) -> Iterator[ChatChunk]:
        payload = build_chat_payload(model, messages, temperature, num_ctx, think, tools, keep_alive)
        try:
            with self._client.stream("POST", "/v1/chat", json=payload) as response:
                self._raise_for_status(response.status_code, lambda: response.read().decode("utf-8", errors="replace"))
                yield from parse_chat_stream(response.iter_lines())
        except httpx.ConnectError as exc:
            raise RemoteConnectionError(self.base_url) from exc
        except httpx.HTTPError as exc:
            raise OllamaError(f"Chat request failed: {exc}") from exc

    # -- plumbing -----------------------------------------------------------

    def _get(self, path: str) -> httpx.Response:
        try:
            response = self._client.get(path)
        except httpx.ConnectError as exc:
            raise RemoteConnectionError(self.base_url) from exc
        except httpx.HTTPError as exc:
            raise OllamaError(f"Request to {path} failed: {exc}") from exc
        self._raise_for_status(response.status_code, lambda: response.text)
        return response

    def _post(self, path: str, json_body: dict) -> httpx.Response:
        try:
            response = self._client.post(path, json=json_body)
        except httpx.ConnectError as exc:
            raise RemoteConnectionError(self.base_url) from exc
        except httpx.HTTPError as exc:
            raise OllamaError(f"Request to {path} failed: {exc}") from exc
        self._raise_for_status(response.status_code, lambda: response.text)
        return response

    def _raise_for_status(self, status_code: int, body_fn) -> None:
        if status_code in (401, 403):
            raise RemoteAuthError(self.base_url)
        if status_code != 200:
            raise OllamaError(extract_error(body_fn(), status_code))

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "RemoteClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
