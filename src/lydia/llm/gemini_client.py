"""HTTP client for Google's Gemini API — the first non-Ollama ModelClient.

Opt-in only (`config.provider = "gemini"`, never the default) — the whole
"no API keys required" premise only holds for anyone who doesn't
explicitly turn this on. See config/secrets.py::GEMINI_API_KEY for where
the key itself lives (OS keychain, never plain JSON) and llm/factory.py
for where this gets constructed.

Wire format was verified empirically against the real API before writing
this (same discipline CLAUDE.md documents for Ollama's tool-calling
gotchas), not assumed from docs: functionDeclarations wrapping for tools,
lowercase JSON Schema type names work as-is, SSE streaming via
`alt=sse` returns one full JSON object per `data:` line (not
partial/incremental JSON needing accumulation), and a functionResponse
round-trip works without needing to echo back the `thoughtSignature`
opaque field 2.5-series models attach to function calls (omitted here;
it's for preserving reasoning continuity across turns, a possible future
enhancement, not required for correctness).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

import httpx

from lydia.llm.client import OllamaError, extract_error
from lydia.llm.types import ChatChunk, Message, ModelInfo, ToolCall

logger = logging.getLogger(__name__)

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class GeminiConnectionError(OllamaError):
    """Could not reach the Gemini API at all."""

    def __init__(self) -> None:
        super().__init__(f"Cannot reach the Gemini API ({_BASE_URL}). Check your network connection.")


class GeminiAuthError(OllamaError):
    """The API key is missing, invalid, or lacks access."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Gemini authentication failed: {detail}\n"
            "Set a key with: lydia config set gemini_api_key"
        )


def _to_gemini_contents(messages: list[Message]) -> tuple[str | None, list[dict[str, Any]]]:
    """Splits Lydia's flat message list into Gemini's shape: a separate
    top-level system instruction, plus a `contents` list using Gemini's
    role names (user/model/function instead of user/assistant/tool).

    Tool-result messages carry no function name in Lydia's own Message
    type (Ollama's wire format doesn't need one) — agent/loop.py always
    appends role="tool" messages in the same order as the tool_calls that
    preceded them (one per call, right after the assistant message that
    made them), so the matching function name is recovered positionally:
    track the most recent assistant tool_calls list and consume it in
    order as "tool" messages are converted.
    """
    system_parts: list[str] = []
    contents: list[dict[str, Any]] = []
    pending_calls: list[ToolCall] = []
    call_index = 0

    for message in messages:
        if message.role == "system":
            if message.content:
                system_parts.append(message.content)
            continue

        if message.role == "user":
            contents.append({"role": "user", "parts": [{"text": message.content}]})
            pending_calls, call_index = [], 0
            continue

        if message.role == "assistant":
            parts: list[dict[str, Any]] = []
            if message.content:
                parts.append({"text": message.content})
            for call in message.tool_calls:
                parts.append({"functionCall": {"name": call.name, "args": call.arguments}})
            pending_calls, call_index = list(message.tool_calls), 0
            if not parts:
                # An earlier turn where the model said nothing. Gemini rejects
                # a model turn with no parts, so replaying it would 400 every
                # later message of the session.
                continue
            contents.append({"role": "model", "parts": parts})
            continue

        if message.role == "tool":
            name = pending_calls[call_index].name if call_index < len(pending_calls) else "unknown_function"
            call_index += 1
            contents.append(
                {
                    "role": "function",
                    "parts": [{"functionResponse": {"name": name, "response": {"result": message.content}}}],
                }
            )
            continue

    system_instruction = "\n\n".join(system_parts) if system_parts else None
    return system_instruction, contents


def _to_gemini_tools(tools: list[dict] | None) -> list[dict[str, Any]] | None:
    """Lydia's tool schemas are OpenAI/Ollama-shaped:
    {"type": "function", "function": {"name", "description", "parameters"}}.
    Gemini wants one `tools` entry holding every declaration together:
    [{"functionDeclarations": [{"name", "description", "parameters"}, ...]}]."""
    if not tools:
        return None
    declarations = [t["function"] for t in tools if t.get("type") == "function"]
    if not declarations:
        return None
    return [{"functionDeclarations": declarations}]


def _parse_candidate(data: dict[str, Any]) -> ChatChunk:
    if "error" in data:
        # Gemini reports a mid-stream failure (quota, internal error) as an
        # SSE event on the 200 stream; it must not read as an empty reply.
        error = data["error"]
        raise OllamaError(error.get("message", str(error)) if isinstance(error, dict) else str(error))
    candidates = data.get("candidates") or []
    if not candidates:
        block_reason = (data.get("promptFeedback") or {}).get("blockReason")
        if block_reason:
            raise OllamaError(f"Gemini refused the prompt (blockReason: {block_reason}).")
        # No candidates and no stated reason: nothing to show, nothing to
        # explain, so the caller just sees an empty reply.
        return ChatChunk(content="", done=True)

    candidate = candidates[0]
    parts = candidate.get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts if "text" in p)
    tool_calls = [
        ToolCall(name=p["functionCall"]["name"], arguments=p["functionCall"].get("args", {}) or {})
        for p in parts
        if "functionCall" in p
    ]

    finish_reason = candidate.get("finishReason")
    done = finish_reason is not None
    if done and not parts and finish_reason not in ("STOP", "MAX_TOKENS"):
        # SAFETY, RECITATION, MALFORMED_FUNCTION_CALL, ...: the model produced
        # nothing and Gemini says why. An empty reply would hide that.
        raise OllamaError(f"Gemini stopped without a reply (finishReason: {finish_reason}).")
    stats: dict[str, Any] = {}
    usage = data.get("usageMetadata")
    if done and usage:
        stats = {
            "prompt_eval_count": usage.get("promptTokenCount", 0),
            "eval_count": usage.get("candidatesTokenCount", 0),
        }

    return ChatChunk(content=text, tool_calls=tool_calls, done=done, stats=stats)


class GeminiClient:
    """Talks to Google's Gemini API. Satisfies the same ModelClient protocol
    as OllamaClient/RemoteClient — see llm/protocol.py."""

    def __init__(self, api_key: str, timeout: float = 300.0) -> None:
        if not api_key:
            raise GeminiAuthError("no API key configured")
        self._client = httpx.Client(
            base_url=_BASE_URL,
            headers={"x-goog-api-key": api_key},
            timeout=httpx.Timeout(timeout, connect=5.0),
        )

    # -- health -------------------------------------------------------------

    def is_alive(self) -> bool:
        try:
            return self._client.get("/models", params={"pageSize": 1}).status_code == 200
        except httpx.HTTPError:
            return False

    # -- models ---------------------------------------------------------------

    def list_models(self) -> list[ModelInfo]:
        response = self._request("GET", "/models", params={"pageSize": 100})
        models = response.json().get("models", [])
        return [
            ModelInfo(name=m["name"].removeprefix("models/"))
            for m in models
            if "generateContent" in m.get("supportedGenerationMethods", [])
        ]

    def has_model(self, name: str) -> bool:
        return any(m.name == name for m in self.list_models())

    # -- embeddings -----------------------------------------------------------

    def embed(self, model: str, inputs: list[str]) -> list[list[float]]:
        if not inputs:
            return []
        # Gemini has no batch-in-one-call embed endpoint on the free tier
        # path used here (batchEmbedContents exists but has a stricter
        # quota); one request per input keeps this working on any key.
        vectors = []
        for text in inputs:
            response = self._request(
                "POST", f"/models/{model}:embedContent", json={"content": {"parts": [{"text": text}]}}
            )
            vectors.append(response.json()["embedding"]["values"])
        return vectors

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
        """`num_ctx`, `think`, and `keep_alive` are Ollama-specific concepts
        (context window sizing, forced reasoning toggle, model residency)
        that don't map onto a hosted API — accepted for signature
        compatibility with ModelClient and silently ignored, same as any
        parameter a given provider doesn't support."""
        system_instruction, contents = _to_gemini_contents(messages)
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"temperature": temperature},
        }
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        gemini_tools = _to_gemini_tools(tools)
        if gemini_tools:
            payload["tools"] = gemini_tools

        try:
            with self._client.stream(
                "POST",
                f"/models/{model}:streamGenerateContent",
                params={"alt": "sse"},
                json=payload,
            ) as response:
                if response.status_code != 200:
                    body = response.read().decode("utf-8", errors="replace")
                    self._raise_for_status(response.status_code, body)
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[len("data:"):].strip()
                    if not raw:
                        continue
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        logger.warning("Skipping malformed Gemini stream event: %.120s", raw)
                        continue
                    chunk = _parse_candidate(data)
                    yield chunk
                    if chunk.done:
                        return
                # Same guarantee as parse_chat_stream: a stream that ends
                # without a finishReason was cut off, not finished.
                raise OllamaError("The Gemini response stream ended before the reply finished.")
        except httpx.ConnectError as exc:
            raise GeminiConnectionError() from exc
        except httpx.HTTPError as exc:
            raise OllamaError(f"Gemini chat request failed: {exc}") from exc

    # -- plumbing -----------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.ConnectError as exc:
            raise GeminiConnectionError() from exc
        except httpx.HTTPError as exc:
            raise OllamaError(f"Request to {path} failed: {exc}") from exc
        if response.status_code != 200:
            self._raise_for_status(response.status_code, response.text)
        return response

    def _raise_for_status(self, status_code: int, body: str) -> None:
        if status_code in (401, 403):
            raise GeminiAuthError(extract_error(body, status_code))
        raise OllamaError(extract_error(body, status_code))

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "GeminiClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
