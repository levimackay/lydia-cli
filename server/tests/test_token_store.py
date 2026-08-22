"""Unit tests for the SQLite-backed TokenStore — no server/app involved."""

import time

import pytest

from lydia_server.database.tokens import TokenStore


@pytest.fixture
def store(tmp_path) -> TokenStore:
    s = TokenStore(tmp_path / "tokens.sqlite3")
    yield s
    s.close()


def test_unknown_token_returns_none(store: TokenStore) -> None:
    assert store.get("never-added") is None


def test_add_then_get_returns_user_id(store: TokenStore) -> None:
    store.add("tok-1", "alice")
    assert store.get("tok-1") == "alice"


def test_tokens_are_never_stored_in_plaintext(store: TokenStore) -> None:
    store.add("super-secret-token", "alice")
    raw_rows = store._conn.execute("SELECT token_hash FROM tokens").fetchall()  # noqa: SLF001
    assert all("super-secret-token" not in str(row) for row in raw_rows)


def test_expired_token_returns_none(store: TokenStore) -> None:
    store.add("tok-1", "alice", expires_in_seconds=-1)  # already in the past
    assert store.get("tok-1") is None


def test_token_with_no_expiry_never_expires(store: TokenStore) -> None:
    store.add("tok-1", "alice", expires_in_seconds=None)
    assert store.get("tok-1") == "alice"


def test_future_expiry_is_still_valid(store: TokenStore) -> None:
    store.add("tok-1", "alice", expires_in_seconds=3600)
    assert store.get("tok-1") == "alice"


def test_revoke_makes_the_token_invalid(store: TokenStore) -> None:
    store.add("tok-1", "alice")
    assert store.revoke("tok-1") is True
    assert store.get("tok-1") is None


def test_revoking_an_unknown_or_already_revoked_token_returns_false(store: TokenStore) -> None:
    assert store.revoke("never-added") is False
    store.add("tok-1", "alice")
    store.revoke("tok-1")
    assert store.revoke("tok-1") is False  # already revoked, not "found and revoked again"


def test_re_adding_a_revoked_token_reactivates_it(store: TokenStore) -> None:
    """_load_tokens() re-adds the same env-var token on every startup — if
    someone revoked it and then restarted with the same env var still set,
    it should come back, not stay silently dead."""
    store.add("tok-1", "alice")
    store.revoke("tok-1")
    store.add("tok-1", "alice")
    assert store.get("tok-1") == "alice"


def test_list_tokens_reflects_state_and_never_includes_raw_tokens(store: TokenStore) -> None:
    store.add("tok-1", "alice")
    store.add("tok-2", "bob", expires_in_seconds=3600)
    store.revoke("tok-1")

    infos = store.list_tokens()
    by_user = {i.user_id: i for i in infos}

    assert by_user["alice"].revoked is True
    assert by_user["bob"].revoked is False
    assert by_user["bob"].expires_at is not None
    assert by_user["bob"].expires_at > time.time()
    # TokenInfo has no field that could leak the raw token or its hash —
    # this is really a type-level guarantee, but pin it in case that ever
    # changes without someone reading this comment first.
    assert not hasattr(infos[0], "token") and not hasattr(infos[0], "token_hash")


def test_len_counts_all_stored_tokens_including_revoked(store: TokenStore) -> None:
    """main.py checks `if not settings.tokens` at startup to refuse running
    unconfigured — __len__ has to make that keep working the way it did
    for a plain dict."""
    assert len(store) == 0
    store.add("tok-1", "alice")
    assert len(store) == 1
    store.add("tok-2", "bob")
    store.revoke("tok-2")
    assert len(store) == 2  # still "configured", just one revoked


def test_store_persists_across_reopening_the_same_file(tmp_path) -> None:
    db_path = tmp_path / "tokens.sqlite3"
    first = TokenStore(db_path)
    first.add("tok-1", "alice")
    first.close()

    second = TokenStore(db_path)
    try:
        assert second.get("tok-1") == "alice"
    finally:
        second.close()
