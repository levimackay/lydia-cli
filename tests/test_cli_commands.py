"""CLI-level tests for commands that don't need a running Ollama daemon:
analyze, config show/set, init, restore list/apply, and --version."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lydia import __version__
from lydia.cli.main import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def _use_fake_keyring(fake_keyring) -> None:
    # config_show/config_set (gemini_api_key) touch the OS keychain now —
    # every test in this file needs the fake backend, not just the ones
    # that mention it explicitly, or a bare `config show` call ends up
    # hitting the real keychain (works quietly on a dev machine that's
    # already granted access; not something to rely on on a fresh CI
    # runner that's never touched it before).
    pass


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_analyze_on_empty_project(tmp_path: Path) -> None:
    result = runner.invoke(app, ["analyze"])
    assert result.exit_code == 0
    assert "Unknown" in result.stdout


def test_analyze_detects_python_project(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "app.py").write_text("x = 1\n")
    result = runner.invoke(app, ["analyze"])
    assert result.exit_code == 0
    assert "Python" in result.stdout


def test_analyze_missing_directory_fails() -> None:
    result = runner.invoke(app, ["analyze", "does-not-exist"])
    assert result.exit_code == 1


def test_init_creates_project_config(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    config_file = tmp_path / ".lydia" / "config.json"
    assert config_file.exists()
    assert json.loads(config_file.read_text()) == {}
    gitignore = (tmp_path / ".lydia" / ".gitignore").read_text()
    assert "history/" in gitignore
    assert "backups/" in gitignore
    assert "index.sqlite3" in gitignore


def test_init_is_idempotent(tmp_path: Path) -> None:
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def test_init_suggests_verify_command_for_python_project(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert "pytest -q" in result.stdout
    config_file = tmp_path / ".lydia" / "config.json"
    assert json.loads(config_file.read_text()) == {"verify_command": "pytest -q"}


def test_init_no_guess_when_no_manifest_present(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert "No verify_command guessed" in result.stdout


def test_config_show_reports_defaults() -> None:
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert "temperature" in result.stdout


def test_config_set_and_show_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_home = tmp_path / "home"
    monkeypatch.setattr("lydia.config.settings.GLOBAL_DIR", fake_home / ".lydia")
    set_result = runner.invoke(app, ["config", "set", "temperature", "0.3"])
    assert set_result.exit_code == 0
    show_result = runner.invoke(app, ["config", "show"])
    assert "temperature = 0.3" in show_result.stdout


def test_config_set_unknown_key_fails() -> None:
    result = runner.invoke(app, ["config", "set", "not_a_real_key", "value"])
    assert result.exit_code == 1


def test_config_set_project_requires_project_root(tmp_path: Path) -> None:
    result = runner.invoke(app, ["config", "set", "model", "x", "--project"])
    assert result.exit_code == 1
    assert "Not inside a project" in result.stdout


def test_config_set_gemini_api_key_with_value_arg_stores_in_keychain() -> None:
    from lydia.config import secrets

    result = runner.invoke(app, ["config", "set", "gemini_api_key", "test-key-abc"])
    assert result.exit_code == 0
    assert secrets.get_secret(secrets.GEMINI_API_KEY) == "test-key-abc"
    # Never echoed to the terminal, even though it was passed on the CLI —
    # confirmation output must not leak it into scrollback/shell history capture.
    assert "test-key-abc" not in result.stdout


def test_config_set_gemini_api_key_prompts_when_value_omitted() -> None:
    from lydia.config import secrets

    result = runner.invoke(app, ["config", "set", "gemini_api_key"], input="prompted-key-xyz\n")
    assert result.exit_code == 0
    assert secrets.get_secret(secrets.GEMINI_API_KEY) == "prompted-key-xyz"
    assert "prompted-key-xyz" not in result.stdout


def test_config_set_gemini_api_key_rejects_project_flag() -> None:
    result = runner.invoke(app, ["config", "set", "gemini_api_key", "x", "--project"])
    assert result.exit_code == 1
    assert "keychain" in result.stdout.lower()


def test_config_set_gemini_api_key_does_not_touch_config_json() -> None:
    result = runner.invoke(app, ["config", "set", "gemini_api_key", "test-key-abc"])
    assert result.exit_code == 0
    # No config.json should have been created/written for a keychain-only key.
    config_file = Path.cwd() / ".lydia" / "config.json"
    assert not config_file.exists()


def test_config_show_reports_gemini_key_presence_not_value() -> None:
    from lydia.config import secrets

    before = runner.invoke(app, ["config", "show"])
    assert "gemini_api_key = not set" in before.stdout

    secrets.set_secret(secrets.GEMINI_API_KEY, "real-key-value")
    after = runner.invoke(app, ["config", "show"])
    assert "gemini_api_key = set" in after.stdout
    assert "real-key-value" not in after.stdout


def test_index_refuses_when_provider_is_not_ollama(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_home = tmp_path / "home"
    monkeypatch.setattr("lydia.config.settings.GLOBAL_DIR", fake_home / ".lydia")
    runner.invoke(app, ["config", "set", "provider", "gemini"])

    result = runner.invoke(app, ["index"])
    assert result.exit_code == 1
    assert "ollama provider" in result.stdout


def test_restore_list_empty(tmp_path: Path) -> None:
    result = runner.invoke(app, ["restore", "list"])
    assert result.exit_code == 0
    assert "No backups" in result.stdout


def test_restore_apply_invalid_index_fails(tmp_path: Path) -> None:
    result = runner.invoke(app, ["restore", "apply", "1"])
    assert result.exit_code == 1


def test_restore_list_and_apply(tmp_path: Path) -> None:
    from lydia.tools.filesystem import apply_write, propose_write

    (tmp_path / "a.py").write_text("original\n")
    apply_write(tmp_path, propose_write(tmp_path, "a.py", "modified\n"))

    list_result = runner.invoke(app, ["restore", "list"])
    assert "a.py" in list_result.stdout

    apply_result = runner.invoke(app, ["restore", "apply", "1"], input="y\n")
    assert apply_result.exit_code == 0
    assert (tmp_path / "a.py").read_text() == "original\n"
