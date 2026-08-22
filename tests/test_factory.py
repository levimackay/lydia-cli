"""Tests for llm/factory.py::build_client selecting the right client type."""

import pytest

from lydia.config import secrets
from lydia.config.settings import LydiaConfig
from lydia.llm.client import OllamaClient
from lydia.llm.factory import build_client
from lydia.llm.gemini_client import GeminiAuthError, GeminiClient
from lydia.llm.remote_client import RemoteClient


def test_build_client_defaults_to_local_ollama() -> None:
    client = build_client(LydiaConfig())
    assert isinstance(client, OllamaClient)
    client.close()


def test_build_client_uses_remote_when_server_url_set() -> None:
    client = build_client(LydiaConfig(server_url="https://gaming-pc.example:8000", api_key="tok"))
    assert isinstance(client, RemoteClient)
    assert client.base_url == "https://gaming-pc.example:8000"
    client.close()


def test_build_client_local_uses_configured_host() -> None:
    client = build_client(LydiaConfig(ollama_host="http://10.0.0.5:11434"))
    assert isinstance(client, OllamaClient)
    assert client.host == "http://10.0.0.5:11434"
    client.close()


def test_build_client_uses_gemini_when_provider_set(fake_keyring) -> None:
    secrets.set_secret(secrets.GEMINI_API_KEY, "test-key-123")
    client = build_client(LydiaConfig(provider="gemini"))
    assert isinstance(client, GeminiClient)
    client.close()


def test_build_client_gemini_without_a_key_raises_a_clear_error(fake_keyring) -> None:
    """No key set at all — never silently falls back to Ollama, since that
    would be surprising (you asked for Gemini, you should hear why it
    didn't happen, not get local behavior with no explanation)."""
    with pytest.raises(GeminiAuthError):
        build_client(LydiaConfig(provider="gemini"))


def test_provider_defaults_to_ollama_not_gemini() -> None:
    """The "no API keys required" premise depends on this never flipping
    on its own."""
    assert LydiaConfig().provider == "ollama"
