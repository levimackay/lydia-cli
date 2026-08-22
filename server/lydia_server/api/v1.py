"""The /v1/* API: health, models, chat, embed.

No route here ever touches a filesystem, git, or a shell — this server is
purely an inference proxy (see the migration plan's resolved design fork:
tool execution stays entirely client-side). Every route except /v1/health
requires a valid bearer token.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from lydia.llm.client import OllamaError, serialize_chat_chunk
from lydia.llm.protocol import ModelClient
from lydia.llm.types import Message, ToolCall

from lydia_server import __version__
from lydia_server.auth.bearer import verify_token
from lydia_server.config.settings import ServerSettings, get_settings
from lydia_server.models.chat import (
    ChatMessage,
    ChatRequest,
    EmbedRequest,
    EmbedResponse,
    HealthResponse,
    ModelEntry,
    ModelsResponse,
)
from lydia_server.services.ollama_provider import get_shared_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1")


def get_provider(settings: ServerSettings = Depends(get_settings)) -> ModelClient:
    """The process-wide shared provider (see services/ollama_provider.py) —
    NOT a fresh instance per request. Routes must not close what this
    returns; its lifecycle is owned by the app (closed on shutdown, see
    main.py's lifespan), not by any single request.

    This is deliberately NOT a yield-dependency either way: for the
    streaming /v1/chat route, FastAPI runs yield-dependency teardown as
    soon as the endpoint function returns the Response object — which for
    StreamingResponse is *before* the body has actually been streamed. A
    yield-dependency here would tear down state before the response body
    is done using it, regardless of what that teardown did.
    """
    return get_shared_provider(settings)


def _to_message(m: ChatMessage) -> Message:
    tool_calls = [
        ToolCall(name=tc.get("function", {}).get("name", ""), arguments=tc.get("function", {}).get("arguments", {}) or {})
        for tc in (m.tool_calls or [])
    ]
    return Message(role=m.role, content=m.content, tool_calls=tool_calls)


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(version=__version__)


@router.get("/models", response_model=ModelsResponse)
def list_models(
    user: str = Depends(verify_token),
    provider: ModelClient = Depends(get_provider),
) -> ModelsResponse:
    try:
        models = provider.list_models()
    except OllamaError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return ModelsResponse(models=[ModelEntry(name=m.name, size=m.size_bytes, modified_at=m.modified_at) for m in models])


@router.post("/embed", response_model=EmbedResponse)
def embed(
    body: EmbedRequest,
    user: str = Depends(verify_token),
    provider: ModelClient = Depends(get_provider),
) -> EmbedResponse:
    try:
        vectors = provider.embed(body.model, body.input)
    except OllamaError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return EmbedResponse(embeddings=vectors)


@router.post("/chat")
def chat(
    body: ChatRequest,
    user: str = Depends(verify_token),
    provider: ModelClient = Depends(get_provider),
) -> StreamingResponse:
    messages = [_to_message(m) for m in body.messages]

    def stream() -> Iterator[str]:
        try:
            for chunk in provider.chat_stream(
                model=body.model,
                messages=messages,
                temperature=body.options.temperature,
                num_ctx=body.options.num_ctx,
                think=body.think,
                tools=body.tools,
                keep_alive=body.keep_alive,
            ):
                yield json.dumps(serialize_chat_chunk(chunk)) + "\n"
        except OllamaError as exc:
            # In-band error line, same convention Ollama itself uses —
            # RemoteClient's parse_chat_line already expects this shape.
            logger.warning("chat_stream error for user=%s: %s", user, exc)
            yield json.dumps({"error": str(exc)}) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")
