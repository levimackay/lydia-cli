"""Tests for the app's lifespan wiring — not the pooling logic itself
(see test_ollama_provider.py), just that main.py actually calls it on
shutdown rather than that being true in theory only."""

from fastapi.testclient import TestClient

from lydia_server import main


def test_shutdown_closes_shared_providers(monkeypatch) -> None:
    calls = []
    # main.py does `from ...ollama_provider import close_shared_providers`,
    # which binds that name in main's own namespace — patching the source
    # module's attribute wouldn't affect main's already-bound reference, so
    # patch it where it's actually called from.
    monkeypatch.setattr(main, "close_shared_providers", lambda: calls.append(True))

    app = main.create_app()
    with TestClient(app):
        assert calls == []  # not yet — only on shutdown, not startup

    assert calls == [True]
