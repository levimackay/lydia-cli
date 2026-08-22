"""CLI tests for `lydia auth login/status/logout`."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from lydia.cli.main import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("lydia.config.settings.GLOBAL_DIR", tmp_path / "home" / ".lydia")
    return tmp_path


@pytest.fixture(autouse=True)
def _use_fake_keyring(fake_keyring) -> None:
    pass


def test_login_canvas_stores_base_url_and_token() -> None:
    result = runner.invoke(app, [
        "auth", "login", "canvas",
        "--base-url", "https://school.instructure.com", "--token", "tok-123",
    ])
    assert result.exit_code == 0, result.stdout

    from lydia.config import secrets
    from lydia.config.settings import load_config
    assert load_config().canvas_base_url == "https://school.instructure.com"
    assert secrets.get_secret(secrets.CANVAS_TOKEN) == "tok-123"


def test_login_gmail_without_client_secret_file_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # DEFAULT_CLIENT_SECRET_PATH is baked into login()'s default argument at
    # module-import time, so patching settings.GLOBAL_DIR alone (see
    # isolated_cwd above) doesn't affect it — patch the bound default
    # directly so this test can't depend on (or collide with) a real
    # ~/.lydia/gmail_client_secret.json on the machine running it.
    from lydia.connectors.auth import gmail_oauth
    monkeypatch.setattr(gmail_oauth.login, "__defaults__", (tmp_path / "does_not_exist.json",))

    result = runner.invoke(app, ["auth", "login", "gmail"])
    assert result.exit_code == 1
    assert "error" in result.stdout.lower()


def test_login_outlook_invokes_device_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    from lydia.connectors.auth import outlook_oauth

    calls = {}

    def fake_login(client_id, on_code=None, **kwargs):
        calls["client_id"] = client_id
        if on_code:
            on_code("Go to microsoft.com/devicelogin and enter ABC123")

    monkeypatch.setattr(outlook_oauth, "login", fake_login)
    result = runner.invoke(app, ["auth", "login", "outlook", "--client-id", "my-client-id"])
    assert result.exit_code == 0, result.stdout
    assert calls["client_id"] == "my-client-id"
    assert "devicelogin" in result.stdout


def test_status_shows_disconnected_by_default() -> None:
    result = runner.invoke(app, ["auth", "status"])
    assert result.exit_code == 0
    assert "not connected" in result.stdout


def test_status_shows_canvas_connected_after_login() -> None:
    runner.invoke(app, [
        "auth", "login", "canvas",
        "--base-url", "https://school.instructure.com", "--token", "tok-123",
    ])
    result = runner.invoke(app, ["auth", "status"])
    assert "connected" in result.stdout


def test_logout_canvas_clears_token() -> None:
    runner.invoke(app, [
        "auth", "login", "canvas",
        "--base-url", "https://school.instructure.com", "--token", "tok-123",
    ])
    result = runner.invoke(app, ["auth", "logout", "canvas"])
    assert result.exit_code == 0

    from lydia.config import secrets
    assert secrets.get_secret(secrets.CANVAS_TOKEN) is None


def test_login_unknown_provider_fails() -> None:
    result = runner.invoke(app, ["auth", "login", "twitter"])
    assert result.exit_code == 1


def test_status_without_assistant_extra_shows_canvas_ntfy_and_a_hint_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulates the 'assistant' extra genuinely not being installed
    (google-api-python-client/msal absent) — auth_status must degrade
    gracefully (canvas/ntfy still checkable) rather than crash the whole
    command, and the install hint must survive Rich markup parsing intact
    (regression test: "[assistant]" was previously silently stripped by
    Rich, since an unescaped f-string treats [...] as a markup tag)."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "lydia.connectors.auth":
            raise ModuleNotFoundError("No module named 'google_auth_oauthlib'", name="google_auth_oauthlib")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    result = runner.invoke(app, ["auth", "status"])

    assert result.exit_code == 0, result.stdout
    assert "gmail/outlook status unavailable" in result.stdout
    # Rich wraps long lines in CliRunner's captured terminal width, so
    # check the brackets survived rather than exact contiguous text —
    # the bug this pins was Rich's markup parser silently deleting
    # "[assistant]" outright, not line-wrapping it.
    assert "lydia-cli[assistant]" in result.stdout.replace("\n", "")
    assert "not connected" in result.stdout  # canvas/ntfy still rendered
