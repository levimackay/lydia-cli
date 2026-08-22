"""Tests for the shared/pooled provider in services/ollama_provider.py.

Constructing an OllamaClient never makes a network call (httpx.Client's
constructor doesn't connect) — these tests exercise the real class, no
Ollama daemon needed, only real network calls would.
"""

import pytest

from lydia_server.config.settings import ServerSettings
from lydia_server.services.ollama_provider import (
    close_shared_providers,
    get_shared_provider,
)


@pytest.fixture(autouse=True)
def _reset_provider_cache():
    """The provider cache is module-level/process-wide by design (that's
    the whole point — it survives across requests). Tests need it reset
    between runs, or an earlier test's cached client leaks into a later
    one and both its identity-comparison and closed-state assertions
    become order-dependent."""
    close_shared_providers()
    yield
    close_shared_providers()


def test_same_host_returns_the_same_provider_instance() -> None:
    settings = ServerSettings(ollama_host="http://localhost:11434")
    first = get_shared_provider(settings)
    second = get_shared_provider(settings)
    assert first is second


def test_different_host_returns_a_different_provider_instance() -> None:
    a = get_shared_provider(ServerSettings(ollama_host="http://localhost:11434"))
    b = get_shared_provider(ServerSettings(ollama_host="http://other-host:11434"))
    assert a is not b


def test_close_shared_providers_closes_the_underlying_connection() -> None:
    settings = ServerSettings(ollama_host="http://localhost:11434")
    provider = get_shared_provider(settings)
    assert provider._client.is_closed is False  # noqa: SLF001 — asserting real close() effect, not behavior via the public API

    close_shared_providers()

    assert provider._client.is_closed is True  # noqa: SLF001


def test_close_shared_providers_clears_the_cache_so_a_new_instance_is_built() -> None:
    settings = ServerSettings(ollama_host="http://localhost:11434")
    first = get_shared_provider(settings)
    close_shared_providers()
    second = get_shared_provider(settings)
    assert first is not second
    assert second._client.is_closed is False  # noqa: SLF001
