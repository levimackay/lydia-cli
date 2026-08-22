"""Picks which ModelClient implementation to construct from config.

If `config.provider == "gemini"`, talk directly to Google's Gemini API
(opt-in, bring-your-own-key — never the default). Otherwise, if
`server_url` is set, talk to a remote Lydia Server; otherwise, a local
Ollama daemon at `ollama_host` — today's original behavior, unchanged.
This is the one place that needs to know every concrete client type;
everything else in the codebase only ever sees the `ModelClient`
protocol. A future OpenAI/Anthropic provider is just another branch here
plus another class satisfying that protocol.
"""

from __future__ import annotations

from lydia.config import secrets
from lydia.config.settings import LydiaConfig
from lydia.llm.client import OllamaClient
from lydia.llm.gemini_client import GeminiAuthError, GeminiClient
from lydia.llm.protocol import ModelClient
from lydia.llm.remote_client import RemoteClient


def build_client(config: LydiaConfig) -> ModelClient:
    if config.provider == "gemini":
        api_key = secrets.get_secret(secrets.GEMINI_API_KEY)
        if not api_key:
            raise GeminiAuthError("no key set")
        return GeminiClient(api_key=api_key)
    if config.server_url:
        return RemoteClient(base_url=config.server_url, api_key=config.api_key)
    return OllamaClient(host=config.ollama_host)
