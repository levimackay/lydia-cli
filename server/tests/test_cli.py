"""Tests for the lydia-server-token CLI. Same lru_cache caveat as
test_settings.py — get_settings() is process-wide cached, so every test
clears it before/after."""

import pytest

from lydia_server import cli
from lydia_server.config.settings import get_settings


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("LYDIA_SERVER_TOKENS_DB", str(tmp_path / "tokens.sqlite3"))
    monkeypatch.delenv("LYDIA_SERVER_TOKEN", raising=False)
    monkeypatch.delenv("LYDIA_SERVER_TOKENS", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_add_prints_a_token_that_actually_works(capsys) -> None:
    exit_code = cli.main(["add", "alice"])
    assert exit_code == 0

    output = capsys.readouterr().out
    lines = [line for line in output.splitlines() if line.strip()]
    # "Token for 'alice':" then the token on its own line
    token = lines[1]

    assert get_settings().tokens.get(token) == "alice"


def test_add_with_expiry_stores_an_expiring_token(capsys) -> None:
    cli.main(["add", "bob", "--expires-in", "3600"])
    token = capsys.readouterr().out.splitlines()[1]

    infos = get_settings().tokens.list_tokens()
    bob = next(i for i in infos if i.user_id == "bob")
    assert bob.expires_at is not None
    assert get_settings().tokens.get(token) == "bob"


def test_revoke_an_existing_token(capsys) -> None:
    cli.main(["add", "alice"])
    token = capsys.readouterr().out.splitlines()[1]

    exit_code = cli.main(["revoke", token])
    assert exit_code == 0
    assert "Revoked" in capsys.readouterr().out
    assert get_settings().tokens.get(token) is None


def test_revoke_unknown_token_fails_with_nonzero_exit(capsys) -> None:
    exit_code = cli.main(["revoke", "not-a-real-token"])
    assert exit_code == 1
    assert "No matching" in capsys.readouterr().err


def test_list_with_no_tokens(capsys) -> None:
    exit_code = cli.main(["list"])
    assert exit_code == 0
    assert "No tokens stored" in capsys.readouterr().out


def test_list_shows_users_and_status_but_never_the_raw_token(capsys) -> None:
    cli.main(["add", "alice"])
    token = capsys.readouterr().out.splitlines()[1]
    cli.main(["revoke", token])
    capsys.readouterr()  # drain the revoke output

    cli.main(["list"])
    output = capsys.readouterr().out

    assert "alice" in output
    assert "revoked" in output
    assert token not in output


def test_no_command_exits_nonzero_via_argparse() -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main([])
    assert exc_info.value.code != 0
