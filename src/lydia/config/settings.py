"""Configuration management for Lydia.

Two layers of configuration, merged in order (later wins):

1. Global:  ~/.lydia/config.json         — user-wide defaults
2. Project: <project>/.lydia/config.json — per-repository overrides

Both are plain JSON so they are easy to inspect and edit by hand.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

GLOBAL_DIR = Path.home() / ".lydia"
PROJECT_DIR_NAME = ".lydia"
CONFIG_FILE_NAME = "config.json"


@dataclass
class LydiaConfig:
    """All tunable settings for Lydia."""

    model: str | None = None  # None = auto-detect best installed model
    temperature: float = 0.7
    num_ctx: int = 8192
    ollama_host: str = "http://localhost:11434"
    # Reasoning for thinking-capable models (qwen3, deepseek-r1):
    # auto = model default, on/off = force. "off" gives much faster replies.
    think: str = "auto"
    # Session mode: ask (confirm every mutating action), auto (skip
    # confirmation for routine/non-dangerous actions), plan (research only —
    # mutating tools aren't even offered to the model). See
    # agent/tools.py::filter_for_mode and _confirm_or_auto.
    mode: str = "ask"
    # How long Ollama keeps the model loaded in memory after a request, so a
    # session doesn't pay the multi-second reload cost on every message.
    # Ollama duration string ("30m", "1h") or "-1" to never unload.
    keep_alive: str = "30m"
    # If set, talk to a remote Lydia Server at this URL instead of a local
    # Ollama daemon (see llm/factory.py::build_client). None = local-only,
    # today's behavior, unchanged.
    server_url: str | None = None
    api_key: str | None = None  # bearer token for server_url, if set
    # "ollama" (default, local-only, no API key) or "gemini" (opt-in,
    # bring-your-own-key — see llm/gemini_client.py and llm/factory.py).
    # Never auto-selected; the "no API keys required" premise only holds
    # for anyone who doesn't explicitly opt into this. The actual key
    # lives in the OS keychain (config/secrets.py::GEMINI_API_KEY), not
    # here — this field only says which provider is active.
    provider: str = "ollama"
    # Shell command to check the project still works, e.g. "pytest -q". If
    # set, the system prompt tells the model to run it (via run_command)
    # after making code changes and fix any failures before finishing.
    # `lydia init` suggests one based on the project's manifest files.
    verify_command: str | None = None
    # Personal-assistant settings. Actual credentials (OAuth tokens, the
    # Canvas access token) never live here — see config/secrets.py — only
    # non-sensitive companion values do.
    canvas_base_url: str | None = None  # e.g. "https://school.instructure.com"
    briefing_schedule_enabled: bool = False
    briefing_schedule_time: str = "08:00"  # HH:MM, 24-hour, local time
    # Voice mode (see voice/). Wake word is an openWakeWord model name.
    voice_wake_word: str = "hey_jarvis"
    voice_stt_model: str = "base.en"
    voice_tts_voice: str | None = None  # None = system default `say` voice
    # e.g. "Mountain Home, Idaho". None = auto-detect from IP (works while traveling).
    weather_location: str | None = None
    # Model used for voice turns only. None = same resolution as chat. A small
    # tool-calling model (qwen3.5:4b) keeps spoken replies fast.
    voice_model: str | None = None

    @property
    def think_flag(self) -> bool | None:
        return {"on": True, "off": False}.get(self.think)

    def merged_with(self, overrides: dict[str, Any]) -> "LydiaConfig":
        """Return a new config with known keys from *overrides* applied."""
        known = {f.name for f in fields(self)}
        data = asdict(self)
        for key, value in overrides.items():
            if key in known:
                data[key] = value
            else:
                logger.warning("Ignoring unknown config key: %s", key)
        return LydiaConfig(**data)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read config %s: %s", path, exc)
        return {}


def find_project_root(start: Path | None = None) -> Path | None:
    """Walk upward from *start* looking for a .lydia/ or .git/ directory."""
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / PROJECT_DIR_NAME).is_dir() or (candidate / ".git").is_dir():
            return candidate
    return None


def global_config_path() -> Path:
    return GLOBAL_DIR / CONFIG_FILE_NAME


def project_config_path(project_root: Path) -> Path:
    return project_root / PROJECT_DIR_NAME / CONFIG_FILE_NAME


def load_config(project_root: Path | None = None) -> LydiaConfig:
    """Load defaults, then apply global config, then project config."""
    config = LydiaConfig()
    config = config.merged_with(_read_json(global_config_path()))
    root = project_root if project_root is not None else find_project_root()
    if root is not None:
        config = config.merged_with(_read_json(project_config_path(root)))
    return config


def save_config_value(key: str, value: Any, path: Path) -> None:
    """Set a single key in the JSON config file at *path*, creating it if needed."""
    known = {f.name for f in fields(LydiaConfig)}
    if key not in known:
        raise KeyError(f"Unknown config key '{key}'. Valid keys: {', '.join(sorted(known))}")
    data = _read_json(path)
    data[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def coerce_value(key: str, raw: str) -> Any:
    """Convert a CLI string into the appropriate type for the given config key."""
    for f in fields(LydiaConfig):
        if f.name != key:
            continue

        if f.type in ("float", float):
            return float(raw)

        if f.type in ("int", int):
            return int(raw)

        if f.type in ("bool", bool):
            value = raw.strip().lower()
            if value in ("true", "1", "yes", "on"):
                return True
            if value in ("false", "0", "no", "off"):
                return False
            raise ValueError(f"Invalid boolean value: {raw!r}")

        return raw

    return raw
