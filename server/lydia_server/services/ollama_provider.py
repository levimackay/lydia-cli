"""The model provider this server proxies to — currently always Ollama.

`lydia.llm.client.OllamaClient` already satisfies everything a provider
needs to (chat_stream, embed, list_models, is_alive) via the same
`ModelClient` protocol the CLI itself is built around — see
`lydia.llm.protocol`. A future provider (OpenAI, Anthropic, Gemini) is
just another class satisfying that same protocol; nothing in api/v1.py
would need to change beyond what `build_provider` constructs.
"""

from __future__ import annotations

from lydia.llm.client import OllamaClient
from lydia.llm.protocol import ModelClient

from lydia_server.config.settings import ServerSettings

# Process-wide, keyed by ollama_host rather than holding one global
# singleton: a test (or a future multi-backend setup) can swap
# settings.ollama_host and get its own isolated client instead of reusing
# one pointed at the wrong host. In normal single-host operation this
# holds exactly one entry.
_shared_providers: dict[str, ModelClient] = {}


def build_provider(settings: ServerSettings) -> ModelClient:
    """Construct a fresh, unshared provider.

    Used by get_shared_provider() below, and directly by anything that
    wants an isolated instance instead of the process-wide shared one
    (e.g. a caller that needs its own connection lifecycle).
    """
    return OllamaClient(host=settings.ollama_host)


def get_shared_provider(settings: ServerSettings) -> ModelClient:
    """Returns one OllamaClient reused across requests for a given host.

    Building a fresh OllamaClient per request also builds a fresh
    httpx.Client — a new TCP connection to Ollama on every single request,
    with no keep-alive reuse, even though most requests in a session hit
    the same host. Reusing one client lets httpx's connection pool actually
    do its job. This only matters once "multiple requests in flight" is
    real; for one person hitting their own local Ollama it's a rounding
    error, but it's free to get right now.
    """
    provider = _shared_providers.get(settings.ollama_host)
    if provider is None:
        provider = build_provider(settings)
        _shared_providers[settings.ollama_host] = provider
    return provider


def close_shared_providers() -> None:
    """Close every pooled provider and forget it. Called on server shutdown —
    never per-request, since per-request closing is exactly what pooling
    exists to avoid."""
    for provider in _shared_providers.values():
        provider.close()
    _shared_providers.clear()
