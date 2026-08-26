"""Persistent project facts — curated notes that survive across sessions.

Unlike SessionHistory (a full conversation transcript), this is a short,
curated list of things worth remembering long-term: "this project uses
PostgreSQL", "run tests with `just test`", and so on. Small enough to
always fold into the system prompt, unlike the transcript.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from lydia.config.settings import PROJECT_DIR_NAME

logger = logging.getLogger(__name__)

MEMORY_FILE_NAME = "memory.json"
MAX_FACTS = 100  # keeps the system prompt from growing unbounded


@dataclass
class Fact:
    text: str
    created_at: str

    def to_dict(self) -> dict:
        return {"text": self.text, "created_at": self.created_at}


def memory_path(project_root: Path) -> Path:
    return project_root / PROJECT_DIR_NAME / MEMORY_FILE_NAME


def load_facts(project_root: Path) -> list[Fact]:
    path = memory_path(project_root)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read memory %s: %s", path, exc)
        return []
    if not isinstance(raw, list):
        logger.warning("Ignoring memory %s: expected a JSON list", path)
        return []
    # Tolerate hand-edited entries: this runs at every session start, so a
    # missing timestamp must not turn into a crash before the prompt.
    return [
        Fact(text=d["text"], created_at=d.get("created_at", ""))
        for d in raw
        if isinstance(d, dict) and "text" in d
    ]


def _save(project_root: Path, facts: list[Fact]) -> None:
    path = memory_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([f.to_dict() for f in facts], indent=2) + "\n", encoding="utf-8")


def remember(project_root: Path, text: str) -> Fact:
    text = text.strip()
    if not text:
        raise ValueError("Cannot remember an empty fact.")
    facts = load_facts(project_root)
    fact = Fact(text=text, created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    facts.append(fact)
    facts = facts[-MAX_FACTS:]  # drop the oldest if over the cap
    _save(project_root, facts)
    return fact


def forget(project_root: Path, index: int) -> Fact:
    """Remove a fact by its 1-based position, as shown by load_facts/list."""
    facts = load_facts(project_root)
    if not 1 <= index <= len(facts):
        raise ValueError(f"No fact #{index}. There are {len(facts)} remembered facts.")
    removed = facts.pop(index - 1)
    _save(project_root, facts)
    return removed
