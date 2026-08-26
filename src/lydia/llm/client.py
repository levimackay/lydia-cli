"""HTTP client for the local Ollama daemon.

Uses the native Ollama REST API (http://localhost:11434) directly so there
is no dependency on the `ollama` Python package — just httpx and NDJSON.

Endpoints used:
    GET  /api/version  — health check
    GET  /api/tags     — installed models
    POST /api/chat     — streaming chat completions
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Iterator
from typing import Any

import httpx

from lydia.llm.types import ChatChunk, Message, ModelInfo, ToolCall

logger = logging.getLogger(__name__)


class OllamaError(Exception):
    """The Ollama daemon returned an error."""


class OllamaConnectionError(OllamaError):
    """Could not reach the Ollama daemon at all."""

    def __init__(self, host: str) -> None:
        super().__init__(
            f"Cannot reach Ollama at {host}.\n"
            "Start it with `ollama serve` or by opening the Ollama app."
        )
        self.host = host


class OllamaClient:
    """Thin, synchronous client for a local Ollama daemon."""

    def __init__(self, host: str = "http://localhost:11434", timeout: float = 300.0) -> None:
        self.host = host.rstrip("/")
        # Generous read timeout: local models can pause while loading layers.
        self._client = httpx.Client(
            base_url=self.host,
            timeout=httpx.Timeout(timeout, connect=5.0),
        )

    # -- health -----------------------------------------------------------

    def is_alive(self) -> bool:
        try:
            return self._client.get("/api/version").status_code == 200
        except httpx.HTTPError:
            return False

    # -- models -----------------------------------------------------------

    def list_models(self) -> list[ModelInfo]:
        try:
            response = self._client.get("/api/tags")
            response.raise_for_status()
        except httpx.ConnectError as exc:
            raise OllamaConnectionError(self.host) from exc
        except httpx.HTTPError as exc:
            raise OllamaError(f"Failed to list models: {exc}") from exc
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

    # -- embeddings ---------------------------------------------------------

    def embed(self, model: str, inputs: list[str]) -> list[list[float]]:
        """Return one embedding vector per input string, via /api/embed (batched)."""
        if not inputs:
            return []
        try:
            response = self._client.post("/api/embed", json={"model": model, "input": inputs})
        except httpx.ConnectError as exc:
            raise OllamaConnectionError(self.host) from exc
        except httpx.HTTPError as exc:
            raise OllamaError(f"Embedding request failed: {exc}") from exc
        if response.status_code != 200:
            raise OllamaError(extract_error(response.text, response.status_code))
        embeddings = response.json().get("embeddings")
        if embeddings is None:
            raise OllamaError("Ollama returned no embeddings.")
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
        """Stream a chat completion as it is generated.

        Yields ChatChunk objects; the final chunk has done=True and carries
        generation stats (token counts, duration). A model that decides to
        call a tool sends the whole call in one non-final chunk rather than
        streaming it token by token.

        *think*: force reasoning on/off for thinking-capable models.
        None leaves the model's default behaviour untouched (safe for
        models that don't support the parameter at all).
        *tools*: JSON-schema tool definitions (Ollama/OpenAI function-calling
        format) to offer the model this turn.
        *keep_alive*: how long Ollama keeps this model loaded after the
        request (e.g. "30m"); avoids a multi-second reload on the next
        message. None leaves Ollama's own default (5 minutes) in place.
        """
        payload = build_chat_payload(model, messages, temperature, num_ctx, think, tools, keep_alive)
        try:
            with self._client.stream("POST", "/api/chat", json=payload) as response:
                if response.status_code != 200:
                    body = response.read().decode("utf-8", errors="replace")
                    raise OllamaError(extract_error(body, response.status_code))
                yield from parse_chat_stream(response.iter_lines())
        except httpx.ConnectError as exc:
            raise OllamaConnectionError(self.host) from exc
        except httpx.HTTPError as exc:
            raise OllamaError(f"Chat request failed: {exc}") from exc

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "OllamaClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def extract_error(body: str, status: int) -> str:
    try:
        message = json.loads(body).get("error", body)
    except json.JSONDecodeError:
        return f"HTTP {status}: {body[:200]}"
    if isinstance(message, str) and "does not support tools" in message:
        message += (
            " (this model's Ollama chat template has no tool-calling support at all; "
            "pull one that does, e.g. `ollama pull qwen3.5` or a llama3.1+ model, on "
            "whichever Ollama instance is actually handling the request)"
        )
    return message


def build_chat_payload(
    model: str,
    messages: list[Message],
    temperature: float = 0.7,
    num_ctx: int = 8192,
    think: bool | None = None,
    tools: list[dict] | None = None,
    keep_alive: str | None = None,
) -> dict:
    """Build an Ollama-shaped /api/chat request body.

    Shared between `OllamaClient` (sends this to Ollama directly) and
    `RemoteClient` (sends this to a Lydia Server, which forwards it to its
    own Ollama largely as-is) so both stay byte-for-byte consistent.
    """
    payload: dict = {
        "model": model,
        "messages": [m.to_dict() for m in messages],
        "stream": True,
        "options": {"temperature": temperature, "num_ctx": num_ctx},
    }
    if think is not None:
        payload["think"] = think
    if keep_alive is not None:
        payload["keep_alive"] = keep_alive
    if tools:
        payload["tools"] = tools
    return payload


def serialize_chat_chunk(chunk: ChatChunk) -> dict:
    """The inverse of `parse_chat_line`: a ChatChunk back into an
    Ollama-shaped NDJSON line (as a dict, ready for `json.dumps`).

    Used by the Lydia Server (server/lydia_server/api/v1.py) to re-emit
    what it gets back from its own Ollama provider in the same wire shape
    `RemoteClient` expects — keeping exactly one chunk format across the
    whole system regardless of which provider produced it.
    """
    message: dict = {"content": chunk.content}
    if chunk.thinking:
        message["thinking"] = chunk.thinking
    if chunk.tool_calls:
        message["tool_calls"] = [tc.to_dict() for tc in chunk.tool_calls]
    line: dict = {"message": message, "done": chunk.done}
    if chunk.done:
        line.update(chunk.stats)
    return line


def parse_chat_stream(lines: Iterable[str]) -> Iterator[ChatChunk]:
    """Turn a stream of NDJSON lines into ChatChunks, stopping at the done chunk.

    Shared by `OllamaClient` and `RemoteClient`. A stream that ends before
    Ollama sent `done: true` was cut off (daemon killed, proxy dropped the
    connection, the server's generator died) and must not be passed off as
    a finished reply: the caller would show a truncated answer, or a tool
    call that never arrived, with no sign anything went wrong.
    """
    for line in lines:
        if not line.strip():
            continue
        chunk = parse_chat_line(line)
        if chunk is not None:
            yield chunk
            if chunk.done:
                return
    raise OllamaError("The response stream ended before the reply finished (connection dropped?).")


def _tool_arguments(raw: Any) -> Any:
    """Ollama sends tool arguments as a JSON object, but an OpenAI-style
    backend behind a Lydia Server sends them as a JSON *string*; decode
    that so tools see a dict either way. Anything undecodable is passed
    through as-is so `execute_tool` can tell the model exactly what it got
    instead of failing on a KeyError deep inside a handler."""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw or {}


def parse_chat_line(line: str) -> ChatChunk | None:
    """Parse one NDJSON line from Ollama's /api/chat streaming shape.

    Shared between `OllamaClient` (talking to Ollama directly) and
    `RemoteClient` (talking to a Lydia Server, which passes this exact
    shape through from its own Ollama) so the parsing logic exists once.
    """
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        logger.warning("Skipping malformed stream line: %.120s", line)
        return None
    if "error" in data:
        raise OllamaError(data["error"])
    message = data.get("message", {})
    tool_calls = [
        ToolCall(
            name=tc.get("function", {}).get("name", ""),
            arguments=_tool_arguments(tc.get("function", {}).get("arguments")),
        )
        for tc in message.get("tool_calls") or []
    ]
    if data.get("done"):
        # done_reason is "length" when the reply hit the token limit, i.e.
        # the answer was cut off mid-thought; keep it so callers can say so.
        stats = {
            k: data[k]
            for k in ("total_duration", "eval_count", "prompt_eval_count", "done_reason")
            if k in data
        }
        return ChatChunk(
            content=message.get("content", ""),
            thinking=message.get("thinking", ""),
            tool_calls=tool_calls,
            done=True,
            stats=stats,
        )
    return ChatChunk(
        content=message.get("content", ""),
        thinking=message.get("thinking", ""),
        tool_calls=tool_calls,
    )
