"""Tests for _load_tokens()'s env-var-seeds-the-store behavior.

get_settings() is @lru_cache'd process-wide — every test here must clear
that cache both before (so its own env vars actually take effect) and
after (so it doesn't leak a stale TokenStore, pointed at a tmp_path that
no longer exists, into whichever test runs next).
"""

import pytest

from lydia_server.config.settings import get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_single_token_env_var_is_seeded(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LYDIA_SERVER_TOKENS_DB", str(tmp_path / "tokens.sqlite3"))
    monkeypatch.setenv("LYDIA_SERVER_TOKEN", "solo-token")
    monkeypatch.delenv("LYDIA_SERVER_TOKENS", raising=False)

    settings = get_settings()

    assert settings.tokens.get("solo-token") == "default"


def test_multi_token_env_var_is_seeded(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LYDIA_SERVER_TOKENS_DB", str(tmp_path / "tokens.sqlite3"))
    monkeypatch.delenv("LYDIA_SERVER_TOKEN", raising=False)
    monkeypatch.setenv("LYDIA_SERVER_TOKENS", "tok-a:alice,tok-b:bob")

    settings = get_settings()

    assert settings.tokens.get("tok-a") == "alice"
    assert settings.tokens.get("tok-b") == "bob"


def test_both_single_and_multi_env_vars_can_be_set_at_once(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LYDIA_SERVER_TOKENS_DB", str(tmp_path / "tokens.sqlite3"))
    monkeypatch.setenv("LYDIA_SERVER_TOKEN", "solo-token")
    monkeypatch.setenv("LYDIA_SERVER_TOKENS", "tok-a:alice")

    settings = get_settings()

    assert settings.tokens.get("solo-token") == "default"
    assert settings.tokens.get("tok-a") == "alice"


def test_no_token_env_vars_means_an_empty_but_usable_store(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LYDIA_SERVER_TOKENS_DB", str(tmp_path / "tokens.sqlite3"))
    monkeypatch.delenv("LYDIA_SERVER_TOKEN", raising=False)
    monkeypatch.delenv("LYDIA_SERVER_TOKENS", raising=False)

    settings = get_settings()

    assert len(settings.tokens) == 0
    assert settings.tokens.get("anything") is None


def test_a_token_added_at_runtime_survives_restart_without_being_named_in_env_vars(
    tmp_path, monkeypatch
) -> None:
    """The whole point: a token added via the CLI (or directly against the
    store) between two server startups is still there on the second
    startup, even with no matching env var — env vars only ever add,
    never remove or gate what's already in the file."""
    db_path = tmp_path / "tokens.sqlite3"
    monkeypatch.setenv("LYDIA_SERVER_TOKENS_DB", str(db_path))
    monkeypatch.delenv("LYDIA_SERVER_TOKEN", raising=False)
    monkeypatch.delenv("LYDIA_SERVER_TOKENS", raising=False)

    first_run_settings = get_settings()
    first_run_settings.tokens.add("runtime-token", "carol")

    get_settings.cache_clear()  # simulate a process restart
    second_run_settings = get_settings()

    assert second_run_settings.tokens.get("runtime-token") == "carol"
