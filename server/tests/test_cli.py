"""Tests for the lydia-server-token CLI. Same lru_cache caveat as
test_settings.py — get_settings() is process-wide cached, so every test
clears it before/after."""

import io

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


def _revoke_via_stdin(token: str, monkeypatch, capsys) -> int:
    """`revoke` deliberately doesn't take the token as a CLI argument (see
    cli.py's module docstring) — tests feed it the same way a real piped
    invocation would. io.StringIO.isatty() is False by default, which is
    exactly the "piped, not interactive" branch _read_token_securely()
    is meant to take."""
    monkeypatch.setattr("sys.stdin", io.StringIO(token + "\n"))
    exit_code = cli.main(["revoke"])
    return exit_code


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


def test_revoke_reads_the_token_from_stdin_not_argv(capsys, monkeypatch) -> None:
    cli.main(["add", "alice"])
    token = capsys.readouterr().out.splitlines()[1]

    exit_code = _revoke_via_stdin(token, monkeypatch, capsys)
    assert exit_code == 0
    assert "Revoked" in capsys.readouterr().out
    assert get_settings().tokens.get(token) is None


def test_revoke_never_accepts_the_token_as_a_positional_argument() -> None:
    """The whole point of the stdin-based design — a raw token must never
    be an argv element (visible via ps/proc/shell history). Passing it
    positionally should fail argument parsing, not silently work."""
    with pytest.raises(SystemExit):
        cli.main(["revoke", "some-token"])


def test_revoke_unknown_token_fails_with_nonzero_exit(capsys, monkeypatch) -> None:
    exit_code = _revoke_via_stdin("not-a-real-token", monkeypatch, capsys)
    assert exit_code == 1
    assert "No matching" in capsys.readouterr().err


def test_revoke_with_empty_stdin_fails_cleanly(capsys, monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    exit_code = cli.main(["revoke"])
    assert exit_code == 1
    assert "No token provided" in capsys.readouterr().err


def test_revoke_user_revokes_every_active_token_for_that_user(capsys) -> None:
    """The practical admin path: revoking someone's access by user_id,
    since whoever ran `add` almost never retains the raw token afterward —
    it's shown exactly once."""
    cli.main(["add", "alice"])
    token_1 = capsys.readouterr().out.splitlines()[1]
    cli.main(["add", "alice"])
    token_2 = capsys.readouterr().out.splitlines()[1]

    exit_code = cli.main(["revoke-user", "alice"])
    assert exit_code == 0
    assert "Revoked 2" in capsys.readouterr().out
    assert get_settings().tokens.get(token_1) is None
    assert get_settings().tokens.get(token_2) is None


def test_revoke_user_with_no_active_tokens_fails_with_nonzero_exit(capsys) -> None:
    exit_code = cli.main(["revoke-user", "nobody"])
    assert exit_code == 1
    assert "No active tokens" in capsys.readouterr().err


def test_list_with_no_tokens(capsys) -> None:
    exit_code = cli.main(["list"])
    assert exit_code == 0
    assert "No tokens stored" in capsys.readouterr().out


def test_list_shows_users_and_status_but_never_the_raw_token(capsys, monkeypatch) -> None:
    cli.main(["add", "alice"])
    token = capsys.readouterr().out.splitlines()[1]
    _revoke_via_stdin(token, monkeypatch, capsys)
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
