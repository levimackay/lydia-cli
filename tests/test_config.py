"""Tests for the layered config system."""

import json
from pathlib import Path

import pytest

from lydia.config import settings
from lydia.config.settings import LydiaConfig, coerce_value, find_project_root, load_config, save_config_value


@pytest.fixture
def isolated_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Point the global config at a temp dir and build a fake project."""
    global_dir = tmp_path / "home" / ".lydia"
    monkeypatch.setattr(settings, "GLOBAL_DIR", global_dir)
    project = tmp_path / "project"
    (project / ".lydia").mkdir(parents=True)
    return global_dir, project


def test_defaults() -> None:
    config = LydiaConfig()
    assert config.model is None
    assert config.temperature == 0.7
    assert config.ollama_host == "http://localhost:11434"


def test_project_overrides_global(isolated_dirs: tuple[Path, Path]) -> None:
    global_dir, project = isolated_dirs
    global_dir.mkdir(parents=True)
    (global_dir / "config.json").write_text(json.dumps({"model": "global-model", "temperature": 0.2}))
    (project / ".lydia" / "config.json").write_text(json.dumps({"model": "project-model"}))

    config = load_config(project_root=project)
    assert config.model == "project-model"  # project wins
    assert config.temperature == 0.2  # global still applies


def test_unknown_keys_ignored(isolated_dirs: tuple[Path, Path]) -> None:
    _, project = isolated_dirs
    (project / ".lydia" / "config.json").write_text(json.dumps({"bogus": 1, "num_ctx": 4096}))
    config = load_config(project_root=project)
    assert config.num_ctx == 4096
    assert not hasattr(config, "bogus")


def test_corrupt_config_falls_back_to_defaults(isolated_dirs: tuple[Path, Path]) -> None:
    _, project = isolated_dirs
    (project / ".lydia" / "config.json").write_text("{not json")
    config = load_config(project_root=project)
    assert config.temperature == 0.7


def test_save_and_coerce(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    save_config_value("temperature", coerce_value("temperature", "0.3"), path)
    save_config_value("model", coerce_value("model", "qwen3.5:9b"), path)
    data = json.loads(path.read_text())
    assert data == {"temperature": 0.3, "model": "qwen3.5:9b"}


def test_save_rejects_unknown_key(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        save_config_value("nope", "x", tmp_path / "config.json")


def test_boolean_coercion(tmp_path: Path) -> None:
    path = tmp_path / "config.json"

    save_config_value(
        "briefing_schedule_enabled",
        coerce_value("briefing_schedule_enabled", "false"),
        path,
    )

    data = json.loads(path.read_text())

    assert data["briefing_schedule_enabled"] is False


def test_global_lydia_dir_does_not_make_home_a_project(isolated_dirs: tuple[Path, Path]) -> None:
    global_dir, _ = isolated_dirs
    global_dir.mkdir(parents=True)
    notes = global_dir.parent / "notes"
    notes.mkdir()
    assert find_project_root(notes) != global_dir.parent

    (notes / ".lydia").mkdir()  # a real project marker still counts
    assert find_project_root(notes) == notes.resolve()


@pytest.mark.parametrize("key, raw, expected", [
    ("num_ctx", "32768", 32768),
    ("temperature", "0.2", 0.2),
    ("briefing_schedule_enabled", "true", True),
])
def test_string_values_in_config_file_are_coerced(isolated_dirs: tuple[Path, Path], key: str, raw: str, expected) -> None:
    _, project = isolated_dirs
    (project / ".lydia" / "config.json").write_text(json.dumps({key: raw}))
    assert getattr(load_config(project_root=project), key) == expected


@pytest.mark.parametrize("body", [
    json.dumps({"num_ctx": "lots"}),  # not an int
    json.dumps({"think": "yes"}),  # not one of the fixed choices
    json.dumps([1, 2, 3]),  # not even an object
])
def test_invalid_config_file_content_falls_back_to_defaults(isolated_dirs: tuple[Path, Path], body: str) -> None:
    _, project = isolated_dirs
    (project / ".lydia" / "config.json").write_text(body)
    config = load_config(project_root=project)
    assert config.num_ctx == LydiaConfig().num_ctx
    assert config.think == "auto"


@pytest.mark.parametrize("raw, expected", [
    ("on", True), ("off", False), ("auto", None),
    (True, True), (False, False),  # JSON booleans from a hand-edited file
])
def test_think_flag(raw, expected) -> None:
    assert LydiaConfig(think=raw).think_flag is expected


@pytest.mark.parametrize("key, raw", [("think", "yes"), ("mode", "yolo"), ("provider", "openai")])
def test_coerce_rejects_values_outside_the_fixed_choices(key: str, raw: str) -> None:
    with pytest.raises(ValueError, match="Use one of"):
        coerce_value(key, raw)
    # and the valid ones still pass through untouched
    assert coerce_value("mode", "plan") == "plan"
