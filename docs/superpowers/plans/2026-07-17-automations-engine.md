# Lydia Automations Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Plain-English-created automations (schedules, intervals, event triggers) that run on a launchd heartbeat, execute Lydia's existing connectors + one model turn, and push results to Levi's phone via ntfy.sh.

**Architecture:** New `src/lydia/automations/` package (model → store → parser → runner), sitting at the same layer as `agent/` (may import `agent/`, `connectors/`, `llm/`, `config/`, but NEVER `cli/`). CLI wiring in `cli/main.py` + a shared creation flow in `cli/automate_flow.py`. One new launchd heartbeat plist (`com.lydia.automations`, `StartInterval`) added to `cli/scheduler.py`. Phone push via new `connectors/ntfy.py`.

**Tech Stack:** Python 3.11+, Typer, httpx, keyring, pytest. **No new dependencies.**

**Spec:** `docs/superpowers/specs/2026-07-17-automations-design.md`. Read it first.

## Global Constraints

- Layering (CLAUDE.md): `automations/` must never import `cli/`. `runner.py` uses `confirm=lambda _r: False` (only safe-risk tools run, confirm is never reached).
- Unit tests NEVER hit the network or a live Ollama; fakes and `httpx.MockTransport` only. Run with `.venv/bin/pytest` from repo root (270 existing tests must stay green).
- Pass `keep_alive=config.keep_alive` on every `chat_stream` call (CLAUDE.md gotcha).
- Secrets go through `config/secrets.py` (keychain), never plain JSON.
- **Never add a `Co-Authored-By: Claude` (or any Claude/Anthropic) trailer to commits.**
- Commit messages: plain imperative, e.g. `Add automation recipe model` (match `git log` style).
- Don't touch `~/.lydia/config.json` or Levi's real `~/.lydia/automations/` during testing; tests use `tmp_path` + monkeypatch.
- All state lives under `~/.lydia/automations/` via `store.py`'s single patchable `AUTOMATIONS_DIR` constant; every other path inside store is derived from it via functions so tests patch exactly one attribute.

---

### Task 1: Recipe model in `automations/model.py`

**Files:**
- Create: `src/lydia/automations/__init__.py`
- Create: `src/lydia/automations/model.py`
- Test: `tests/test_automations_model.py`

**Interfaces:**
- Produces: `Trigger`, `Step`, `Notify`, `Automation` dataclasses; `Automation.to_dict() -> dict`; `Automation.from_dict(data) -> Automation` (raises `AutomationError` on malformed input); `validate(auto) -> list[str]`; `describe(auto) -> str`; constants `ALLOWED_STEP_TOOLS`, `EVENT_SOURCES`, `NOTHING_TO_REPORT`; exception `AutomationError`.

- [ ] **Step 1: Write the failing tests**

`tests/test_automations_model.py`:

```python
import pytest

from lydia.automations.model import (
    ALLOWED_STEP_TOOLS, Automation, AutomationError, Notify, Step, Trigger,
    describe, validate,
)


def _valid_automation() -> Automation:
    return Automation(
        name="morning-briefing",
        description="every morning at 8 check email and canvas",
        trigger=Trigger(type="schedule", time="08:00"),
        steps=[
            Step(kind="connector", tool="check_email", args={"account": "personal"}),
            Step(kind="model", instructions="Summarize into a short briefing."),
        ],
        notify=Notify(channel="ntfy", when="always"),
    )


def test_round_trips_through_dict():
    auto = _valid_automation()
    again = Automation.from_dict(auto.to_dict())
    assert again == auto


def test_from_dict_rejects_malformed():
    with pytest.raises(AutomationError):
        Automation.from_dict({"name": "x"})  # missing trigger/steps


def test_valid_automation_has_no_errors():
    assert validate(_valid_automation()) == []


def test_validate_rejects_bad_name():
    auto = _valid_automation()
    auto.name = "Bad Name!"
    assert any("name" in e for e in validate(auto))


def test_validate_rejects_bad_schedule_time():
    auto = _valid_automation()
    auto.trigger = Trigger(type="schedule", time="25:99")
    assert any("time" in e for e in validate(auto))


def test_validate_rejects_unknown_step_tool():
    auto = _valid_automation()
    auto.steps[0] = Step(kind="connector", tool="delete_file")
    assert any("delete_file" in e for e in validate(auto))


def test_validate_rejects_if_important_without_model_step():
    auto = _valid_automation()
    auto.steps = [Step(kind="connector", tool="check_news")]
    auto.notify = Notify(channel="ntfy", when="if_important")
    assert any("if_important" in e for e in validate(auto))


def test_validate_event_trigger_requirements():
    auto = _valid_automation()
    auto.trigger = Trigger(type="event", source="email", account="school",
                           condition="from my professor")
    assert validate(auto) == []
    auto.trigger = Trigger(type="event", source="email")  # no account/condition
    assert validate(auto) != []


def test_describe_mentions_trigger_and_notify():
    text = describe(_valid_automation())
    assert "08:00" in text and "phone" in text.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_automations_model.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'lydia.automations'`)

- [ ] **Step 3: Implement**

`src/lydia/automations/__init__.py`:

```python
"""Automations: plain-English-created recipes run by a launchd heartbeat.

See docs/superpowers/specs/2026-07-17-automations-design.md. Layering: this
package may import agent/, connectors/, llm/, config/ — never cli/.
"""
```

`src/lydia/automations/model.py`:

```python
"""Automation recipes: dataclasses, validation, (de)serialization.

A recipe is one JSON file at ~/.lydia/automations/<name>.json (see store.py).
Kept UI-free and I/O-free so it's trivially unit-testable.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

# Only safe-risk connector tools may appear as steps — an automation must
# never be able to edit files or run shell commands (that's the deferred
# "unattended coding" spec, not this one).
ALLOWED_STEP_TOOLS = {"check_email", "check_canvas", "check_stocks", "check_news"}
EVENT_SOURCES = {"email", "canvas"}
EMAIL_ACCOUNTS = {"personal", "school"}
# A model step ends its reply with this exact marker to suppress an
# `if_important` notification. String check, not a second model call.
NOTHING_TO_REPORT = "NOTHING_TO_REPORT"

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,49}$")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class AutomationError(Exception):
    """A recipe is malformed or could not be loaded/saved."""


@dataclass
class Trigger:
    type: str  # "schedule" | "interval" | "event"
    time: str | None = None        # schedule: 24h "HH:MM", local time
    minutes: int | None = None     # interval
    source: str | None = None      # event: one of EVENT_SOURCES
    account: str | None = None     # event+email: "personal" | "school"
    condition: str | None = None   # event: English, evaluated by the model


@dataclass
class Step:
    kind: str  # "connector" | "model"
    tool: str | None = None                      # connector
    args: dict[str, Any] = field(default_factory=dict)  # connector
    instructions: str | None = None              # model


@dataclass
class Notify:
    channel: str = "ntfy"  # "ntfy" | "mac" | "none"
    when: str = "always"   # "always" | "if_important"


@dataclass
class Automation:
    name: str
    description: str
    trigger: Trigger
    steps: list[Step]
    notify: Notify = field(default_factory=Notify)
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Automation":
        try:
            return cls(
                name=data["name"],
                description=data.get("description", ""),
                trigger=Trigger(**data["trigger"]),
                steps=[Step(**s) for s in data["steps"]],
                notify=Notify(**data.get("notify", {})),
                enabled=bool(data.get("enabled", True)),
            )
        except (KeyError, TypeError) as exc:
            raise AutomationError(f"Malformed automation: {exc}") from exc


def validate(auto: Automation) -> list[str]:
    """Every problem with the recipe, as human/model-readable strings."""
    errors: list[str] = []
    if not _NAME_RE.match(auto.name or ""):
        errors.append("name must be a lowercase-kebab slug, e.g. 'morning-briefing'")

    t = auto.trigger
    if t.type == "schedule":
        if not (t.time and _TIME_RE.match(t.time)):
            errors.append("schedule trigger needs a 24-hour time 'HH:MM', e.g. '08:00'")
    elif t.type == "interval":
        if not (isinstance(t.minutes, int) and t.minutes >= 5):
            errors.append("interval trigger needs integer minutes >= 5")
    elif t.type == "event":
        if t.source not in EVENT_SOURCES:
            errors.append(f"event source must be one of: {', '.join(sorted(EVENT_SOURCES))}")
        if t.source == "email" and t.account not in EMAIL_ACCOUNTS:
            errors.append("event source 'email' needs account 'personal' or 'school'")
        if not (t.condition and t.condition.strip()):
            errors.append("event trigger needs an English condition, e.g. 'from my professor'")
    else:
        errors.append("trigger type must be 'schedule', 'interval', or 'event'")

    if not auto.steps:
        errors.append("at least one step is required")
    for i, step in enumerate(auto.steps, 1):
        if step.kind == "connector":
            if step.tool not in ALLOWED_STEP_TOOLS:
                errors.append(
                    f"step {i}: tool '{step.tool}' is not allowed; "
                    f"choose from {', '.join(sorted(ALLOWED_STEP_TOOLS))}"
                )
        elif step.kind == "model":
            if not (step.instructions and step.instructions.strip()):
                errors.append(f"step {i}: model step needs non-empty instructions")
        else:
            errors.append(f"step {i}: kind must be 'connector' or 'model'")

    if auto.notify.channel not in {"ntfy", "mac", "none"}:
        errors.append("notify channel must be 'ntfy', 'mac', or 'none'")
    if auto.notify.when not in {"always", "if_important"}:
        errors.append("notify when must be 'always' or 'if_important'")
    if auto.notify.when == "if_important" and not any(s.kind == "model" for s in auto.steps):
        errors.append("notify 'if_important' requires at least one model step "
                      "(nothing else can emit the NOTHING_TO_REPORT marker)")
    return errors


def describe(auto: Automation) -> str:
    """Human-readable echo shown before saving, e.g. by `lydia automate`."""
    t = auto.trigger
    if t.type == "schedule":
        when = f"Every day at {t.time}"
    elif t.type == "interval":
        when = f"Every {t.minutes} minutes"
    else:
        where = f"{t.source} ({t.account})" if t.account else t.source
        when = f"When new {where} items match: “{t.condition}”"
    parts = []
    for step in auto.steps:
        if step.kind == "connector":
            parts.append(step.tool or "?")
        else:
            text = (step.instructions or "").strip()
            parts.append(f"model: {text[:60]}" + ("…" if len(text) > 60 else ""))
    if auto.notify.channel == "ntfy":
        dest = "push to phone (ntfy)"
    elif auto.notify.channel == "mac":
        dest = "Mac notification"
    else:
        dest = "no notification"
    if auto.notify.when == "if_important":
        dest += ", only if important"
    return f"[{auto.name}] {when}: {' → '.join(parts)} → {dest}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_automations_model.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/lydia/automations/ tests/test_automations_model.py
git commit -m "Add automation recipe model with validation"
```

---

### Task 2: Persistence in `automations/store.py`

**Files:**
- Create: `src/lydia/automations/store.py`
- Test: `tests/test_automations_store.py`

**Interfaces:**
- Consumes: Task 1's `Automation`, `AutomationError`, `validate`.
- Produces: `AUTOMATIONS_DIR` (module constant, THE patch point for tests); `recipe_path(name) -> Path`; `save_automation(auto) -> Path`; `load_automation(name) -> Automation`; `list_automations() -> list[Automation]`; `delete_automation(name) -> bool`; `load_state() -> dict`; `save_state(state) -> None`; `append_run(record: dict) -> None`; `load_runs() -> list[dict]`; `try_acquire_lock(now_fn=time.time) -> bool`; `release_lock() -> None`; constants `MAX_RUNS = 200`, `MAX_SEEN_IDS = 500`, `LOCK_STALE_SECONDS = 600`.
- State shape: `{"<name>": {"last_run": "<iso>", "seen_ids": [...], "last_failure_notice": "<iso>"}}`. Run record shape (produced by Task 6): `{"name", "started_at", "duration_seconds", "ok", "error", "result_snippet", "notified"}`.

- [ ] **Step 1: Write the failing tests**

`tests/test_automations_store.py`:

```python
import pytest

from lydia.automations import store
from lydia.automations.model import Automation, AutomationError, Notify, Step, Trigger


@pytest.fixture(autouse=True)
def isolated_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "AUTOMATIONS_DIR", tmp_path)


def _auto(name="test-auto") -> Automation:
    return Automation(
        name=name, description="d",
        trigger=Trigger(type="interval", minutes=30),
        steps=[Step(kind="connector", tool="check_news")],
        notify=Notify(),
    )


def test_save_load_round_trip():
    store.save_automation(_auto())
    assert store.load_automation("test-auto") == _auto()


def test_save_rejects_invalid():
    bad = _auto()
    bad.steps = []
    with pytest.raises(AutomationError):
        store.save_automation(bad)


def test_list_sorted_and_skips_garbage(tmp_path):
    store.save_automation(_auto("bbb"))
    store.save_automation(_auto("aaa"))
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    names = [a.name for a in store.list_automations()]
    assert names == ["aaa", "bbb"]


def test_delete():
    store.save_automation(_auto())
    assert store.delete_automation("test-auto") is True
    assert store.delete_automation("test-auto") is False
    with pytest.raises(AutomationError):
        store.load_automation("test-auto")


def test_state_round_trip():
    assert store.load_state() == {}
    store.save_state({"x": {"last_run": "2026-07-17T08:00:00"}})
    assert store.load_state()["x"]["last_run"] == "2026-07-17T08:00:00"


def test_runs_capped():
    for i in range(store.MAX_RUNS + 10):
        store.append_run({"name": "x", "ok": True, "i": i})
    runs = store.load_runs()
    assert len(runs) == store.MAX_RUNS
    assert runs[-1]["i"] == store.MAX_RUNS + 9  # newest kept


def test_lock_blocks_then_goes_stale():
    assert store.try_acquire_lock(now_fn=lambda: 1000.0) is True
    assert store.try_acquire_lock(now_fn=lambda: 1000.0) is False
    # a lock older than LOCK_STALE_SECONDS is broken and re-acquired
    assert store.try_acquire_lock(now_fn=lambda: 1000.0 + store.LOCK_STALE_SECONDS + 1) is True
    store.release_lock()
    assert store.try_acquire_lock(now_fn=lambda: 2000.0) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_automations_store.py -v`
Expected: FAIL (`cannot import name 'store'`)

- [ ] **Step 3: Implement**

`src/lydia/automations/store.py`:

```python
"""Load/save automation recipes and their runtime state under ~/.lydia/automations/.

Follows agent/facts.py's persisted-JSON pattern. Every path is derived from
AUTOMATIONS_DIR inside each function so tests patch exactly one attribute.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Callable

from lydia.automations.model import Automation, AutomationError, validate
from lydia.config.settings import GLOBAL_DIR

logger = logging.getLogger(__name__)

AUTOMATIONS_DIR = GLOBAL_DIR / "automations"
MAX_RUNS = 200
MAX_SEEN_IDS = 500
LOCK_STALE_SECONDS = 600

_RESERVED = {"state", "runs"}  # state.json / runs.json live next to recipes


def recipe_path(name: str) -> Path:
    return AUTOMATIONS_DIR / f"{name}.json"


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def save_automation(auto: Automation) -> Path:
    errors = validate(auto)
    if errors:
        raise AutomationError("; ".join(errors))
    if auto.name in _RESERVED:
        raise AutomationError(f"'{auto.name}' is a reserved name")
    path = recipe_path(auto.name)
    _write_json(path, auto.to_dict())
    return path


def load_automation(name: str) -> Automation:
    path = recipe_path(name)
    if not path.is_file():
        raise AutomationError(f"No automation named '{name}'")
    data = _read_json(path, None)
    if data is None:
        raise AutomationError(f"Could not read {path}")
    return Automation.from_dict(data)


def list_automations() -> list[Automation]:
    if not AUTOMATIONS_DIR.is_dir():
        return []
    autos: list[Automation] = []
    for path in sorted(AUTOMATIONS_DIR.glob("*.json")):
        if path.stem in _RESERVED:
            continue
        data = _read_json(path, None)
        if data is None:
            continue
        try:
            autos.append(Automation.from_dict(data))
        except AutomationError as exc:
            logger.warning("Skipping %s: %s", path, exc)
    return sorted(autos, key=lambda a: a.name)


def delete_automation(name: str) -> bool:
    path = recipe_path(name)
    if not path.is_file():
        return False
    path.unlink()
    return True


def load_state() -> dict:
    return _read_json(AUTOMATIONS_DIR / "state.json", {})


def save_state(state: dict) -> None:
    _write_json(AUTOMATIONS_DIR / "state.json", state)


def load_runs() -> list[dict]:
    return _read_json(AUTOMATIONS_DIR / "runs.json", [])


def append_run(record: dict) -> None:
    runs = load_runs()
    runs.append(record)
    _write_json(AUTOMATIONS_DIR / "runs.json", runs[-MAX_RUNS:])


def try_acquire_lock(now_fn: Callable[[], float] = time.time) -> bool:
    """One tick at a time. A lock older than LOCK_STALE_SECONDS is presumed
    dead (crashed tick) and broken."""
    lock = AUTOMATIONS_DIR / "tick.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    now = now_fn()
    if lock.is_file():
        stamp = _read_json(lock, {"at": 0})
        if now - float(stamp.get("at", 0)) < LOCK_STALE_SECONDS:
            return False
    _write_json(lock, {"at": now})
    return True


def release_lock() -> None:
    (AUTOMATIONS_DIR / "tick.lock").unlink(missing_ok=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_automations_store.py tests/test_automations_model.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/lydia/automations/store.py tests/test_automations_store.py
git commit -m "Add automation store: recipes, state, run log, tick lock"
```

---

### Task 3: ntfy connector + `notify` agent tool

**Files:**
- Create: `src/lydia/connectors/ntfy.py`
- Modify: `src/lydia/config/secrets.py` (add one constant)
- Modify: `src/lydia/agent/tools.py` (new handler + ToolSpec)
- Test: `tests/test_ntfy.py`, extend `tests/test_agent_tools.py` (or create `tests/test_notify_tool.py` if adding there is awkward)

**Interfaces:**
- Produces: `connectors/ntfy.py::send_push(topic: str, title: str, message: str, priority: str = "default", transport: httpx.BaseTransport | None = None) -> None` (raises `ConnectorError`); `NTFY_BASE_URL = "https://ntfy.sh"`; `secrets.NTFY_TOPIC = "ntfy_topic"`; agent tool named `notify` (risk `"safe"`, handler `_send_notification`).

- [ ] **Step 1: Write the failing tests**

`tests/test_ntfy.py`:

```python
import httpx
import pytest

from lydia.connectors import ConnectorError
from lydia.connectors.ntfy import send_push


def test_send_push_posts_to_topic():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["title"] = request.headers.get("Title")
        captured["priority"] = request.headers.get("Priority")
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(200)

    send_push("lydia-abc123", "Lydia · test", "hello phone",
              transport=httpx.MockTransport(handler))
    assert captured["url"] == "https://ntfy.sh/lydia-abc123"
    assert captured["title"] == "Lydia · test"
    assert captured["priority"] == "default"
    assert captured["body"] == "hello phone"


def test_send_push_raises_connector_error_on_http_failure():
    transport = httpx.MockTransport(lambda r: httpx.Response(500))
    with pytest.raises(ConnectorError):
        send_push("t", "x", "y", transport=transport)
```

For the tool, add to the existing agent-tools test file (match its fixture style: it builds a `ToolContext` with a fake config; check `tests/test_agent_tools.py` first and imitate):

```python
def test_notify_tool_without_topic_reports_not_configured(monkeypatch, tool_ctx):
    from lydia.agent import tools as agent_tools
    from lydia.config import secrets
    monkeypatch.setattr(secrets, "get_secret", lambda key: None)
    result = agent_tools._send_notification({"message": "hi"}, tool_ctx)
    assert result.ok is False
    assert "lydia auth login ntfy" in result.content


def test_notify_tool_sends_push(monkeypatch, tool_ctx):
    from lydia.agent import tools as agent_tools
    from lydia.config import secrets
    sent = {}
    monkeypatch.setattr(secrets, "get_secret", lambda key: "topic-x")
    monkeypatch.setattr(
        "lydia.connectors.ntfy.send_push",
        lambda topic, title, message, priority="default", transport=None: sent.update(
            {"topic": topic, "title": title, "message": message}),
    )
    result = agent_tools._send_notification({"message": "hi", "title": "T"}, tool_ctx)
    assert result.ok is True
    assert sent == {"topic": "topic-x", "title": "T", "message": "hi"}
```

(If `tests/test_agent_tools.py` has no reusable `tool_ctx` fixture, create one locally: `ToolContext(root=tmp_path, config=LydiaConfig(), confirm=lambda r: True)`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_ntfy.py -v`
Expected: FAIL (`No module named 'lydia.connectors.ntfy'`)

- [ ] **Step 3: Implement**

`src/lydia/connectors/ntfy.py`:

```python
"""ntfy.sh push connector — send a notification to the user's phone.

The topic name is effectively a password (anyone who knows it can subscribe),
so it lives in the OS keychain (config/secrets.py::NTFY_TOPIC), generated
randomly by `lydia auth login ntfy`. `transport` is injectable for tests,
same pattern as the Canvas connector.
"""

from __future__ import annotations

import httpx

from lydia.connectors import ConnectorError

NTFY_BASE_URL = "https://ntfy.sh"


def send_push(
    topic: str,
    title: str,
    message: str,
    priority: str = "default",
    transport: httpx.BaseTransport | None = None,
) -> None:
    try:
        with httpx.Client(base_url=NTFY_BASE_URL, timeout=10.0, transport=transport) as client:
            response = client.post(
                f"/{topic}",
                content=message.encode("utf-8"),
                headers={"Title": title, "Priority": priority},
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ConnectorError(f"ntfy push failed: {exc}") from exc
```

In `src/lydia/config/secrets.py`, add below `CANVAS_TOKEN`:

```python
NTFY_TOPIC = "ntfy_topic"
```

In `src/lydia/agent/tools.py`, add the handler after `_check_news` (lazy imports, same pattern as the other connector handlers):

```python
def _send_notification(args: dict, ctx: ToolContext) -> ToolResult:
    from lydia.config import secrets
    from lydia.connectors import ntfy

    topic = secrets.get_secret(secrets.NTFY_TOPIC)
    if not topic:
        return ToolResult(
            ok=False,
            content="Phone notifications aren't set up. Tell the user to run `lydia auth login ntfy`.",
            summary="not configured",
        )
    try:
        ntfy.send_push(topic, args.get("title", "Lydia"), args["message"])
    except ConnectorError as exc:
        return ToolResult(ok=False, content=str(exc), summary="error")
    return ToolResult(ok=True, content="Notification sent to the user's phone.",
                      summary="sent push notification")
```

And register in `build_registry()` next to the other personal-assistant ToolSpecs (after `check_news`):

```python
ToolSpec(
    "notify", "Send a push notification to the user's phone. Use for genuinely "
    "important, time-sensitive information — not routine replies.",
    {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "The notification body"},
            "title": {"type": "string", "description": "Short title (optional)"},
        },
        "required": ["message"],
    },
    "safe", _send_notification,
),
```

Note: `_send_notification` calls the *module* (`ntfy.send_push`) rather than importing the function, and the test monkeypatches `lydia.connectors.ntfy.send_push`, and this only works if the handler resolves the attribute at call time, which `from lydia.connectors import ntfy` + `ntfy.send_push(...)` does. Keep it that way.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_ntfy.py tests/test_agent_tools.py -v`
Expected: all PASS (including all pre-existing agent-tools tests)

- [ ] **Step 5: Commit**

```bash
git add src/lydia/connectors/ntfy.py src/lydia/config/secrets.py src/lydia/agent/tools.py tests/
git commit -m "Add ntfy push connector and notify agent tool"
```

---

### Task 4: Stable item IDs on email/Canvas connectors

**Files:**
- Modify: `src/lydia/connectors/email_gmail.py`
- Modify: `src/lydia/connectors/email_outlook.py`
- Modify: `src/lydia/connectors/canvas.py`
- Test: extend the existing connector tests (`tests/test_connectors*.py`; find them with `ls tests/ | grep -i -e connector -e gmail -e canvas` and follow their existing fake/MockTransport style)

**Interfaces:**
- Produces: `email_gmail.EmailSummary.id: str = ""`, `email_outlook.EmailSummary.id: str = ""`, `canvas.Assignment.id: str = ""`, all **appended last with a default** so existing positional constructions keep working. Fetch functions populate them.

- [ ] **Step 1: Write the failing tests.** In each connector's existing test file, extend the existing happy-path test (or add one) to assert IDs are populated. The exact assertion to add in each:

```python
# gmail: the fake service already returns messages with "id" — assert it lands
assert all(s.id for s in summaries)
# outlook: add "id": "msg-1" etc. to the fake Graph response items, then
assert [s.id for s in summaries] == ["msg-1", "msg-2"]
# canvas: add "id": 42 to the fake assignment payloads, then
assert assignments[0].id == "42"
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/ -k "gmail or outlook or canvas" -v`
Expected: FAIL (`EmailSummary has no attribute 'id'`, and similar)

- [ ] **Step 3: Implement** the three small edits:

`email_gmail.py`: add `id: str = ""` as the LAST field of `EmailSummary`; in `get_recent_emails`, add `id=ref["id"],` to the `EmailSummary(...)` construction.

`email_outlook.py`: add `id: str = ""` as the LAST field of `EmailSummary`; change `"$select"` to `"id,from,subject,bodyPreview,isRead"`; add `id=item.get("id", ""),` to the construction.

`canvas.py`: add `id: str = ""` as the LAST field of `Assignment`; in the assignment-building loop add `id=str(item.get("id", "")),` (Canvas IDs are ints on the wire; stored as str so all seen-IDs are uniformly strings).

- [ ] **Step 4: Run the full suite** (these are shared dataclasses, so check nothing else broke)

Run: `.venv/bin/pytest`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/lydia/connectors/ tests/
git commit -m "Carry stable item IDs on email and Canvas summaries"
```

---

### Task 5: English → recipe parser in `automations/parser.py`

**Files:**
- Create: `src/lydia/automations/parser.py`
- Test: `tests/test_automations_parser.py`

**Interfaces:**
- Consumes: Task 1's `Automation`, `validate`, `AutomationError`; `lydia.llm.types.Message`, `ChatChunk`.
- Produces: `complete(client, model, config, messages: list[Message]) -> str` (drains one non-streaming-rendered chat turn; reused by Task 6's runner); `parse_automation(text: str, client, model, config) -> Automation` (one retry on invalid output, then raises `AutomationParseError`); `AutomationParseError(Exception)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_automations_parser.py`:

```python
import json

import pytest

from lydia.automations.parser import AutomationParseError, complete, parse_automation
from lydia.config.settings import LydiaConfig
from lydia.llm.types import ChatChunk

VALID = {
    "name": "morning-briefing",
    "description": "every morning at 8 check email",
    "enabled": True,
    "trigger": {"type": "schedule", "time": "08:00"},
    "steps": [
        {"kind": "connector", "tool": "check_email", "args": {"account": "personal"}},
        {"kind": "model", "instructions": "Summarize."},
    ],
    "notify": {"channel": "ntfy", "when": "always"},
}


class FakeClient:
    """Yields one canned reply per chat_stream call, recording each call."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat_stream(self, **kwargs):
        self.calls.append(kwargs)
        yield ChatChunk(content=self.replies.pop(0), done=True)


def test_complete_concatenates_content():
    client = FakeClient(["hello"])
    assert complete(client, "m", LydiaConfig(), []) == "hello"
    assert client.calls[0]["keep_alive"] == LydiaConfig().keep_alive


def test_parse_valid_json_first_try():
    client = FakeClient([json.dumps(VALID)])
    auto = parse_automation("whatever", client, "m", LydiaConfig())
    assert auto.name == "morning-briefing"
    assert len(client.calls) == 1


def test_parse_strips_code_fences():
    client = FakeClient(["```json\n" + json.dumps(VALID) + "\n```"])
    assert parse_automation("x", client, "m", LydiaConfig()).name == "morning-briefing"


def test_parse_retries_once_with_error_feedback():
    bad = dict(VALID, trigger={"type": "schedule"})  # missing time
    client = FakeClient([json.dumps(bad), json.dumps(VALID)])
    auto = parse_automation("x", client, "m", LydiaConfig())
    assert auto.name == "morning-briefing"
    assert len(client.calls) == 2
    retry_user_msg = client.calls[1]["messages"][-1].content
    assert "HH:MM" in retry_user_msg  # the validation error was fed back


def test_parse_fails_after_retry():
    client = FakeClient(["not json at all", "still not json"])
    with pytest.raises(AutomationParseError):
        parse_automation("x", client, "m", LydiaConfig())
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_automations_parser.py -v`
Expected: FAIL (no module `parser`)

- [ ] **Step 3: Implement**

`src/lydia/automations/parser.py`:

```python
"""Turn an English automation request into a validated recipe via one model turn.

The model only runs at creation time — scheduled runs never re-parse. On
invalid output the validation errors are fed back for exactly one retry
(small local models get JSON wrong sometimes; more than one retry just
burns time on a request that isn't going to work).
"""

from __future__ import annotations

import json

from lydia.automations.model import Automation, AutomationError, validate
from lydia.config.settings import LydiaConfig
from lydia.llm.protocol import ModelClient
from lydia.llm.types import Message

PARSER_SYSTEM_PROMPT = """You convert a user's English request into an automation recipe as JSON.

Reply with ONLY a JSON object — no prose, no markdown fences — in exactly this shape:

{
  "name": "<lowercase-kebab-slug, max 50 chars>",
  "description": "<the user's original request, verbatim>",
  "enabled": true,
  "trigger": <one of:
    {"type": "schedule", "time": "HH:MM"}                              — daily at a fixed 24-hour local time
    {"type": "interval", "minutes": <int >= 5>}                        — repeating
    {"type": "event", "source": "email"|"canvas", "account": "personal"|"school", "condition": "<English>"}
                                                                        — fire only on NEW items matching the condition
  >,
  "steps": [  — executed in order
    {"kind": "connector", "tool": "check_email"|"check_canvas"|"check_stocks"|"check_news", "args": {...}}
    {"kind": "model", "instructions": "<what to do with the gathered data>"}
  ],
  "notify": {"channel": "ntfy"|"mac"|"none", "when": "always"|"if_important"}
}

Rules:
- check_email requires args {"account": "personal"} (Gmail) or {"account": "school"} (Outlook). Other tools take args {}.
- "event" triggers: "account" is only for source "email"; omit it for "canvas".
- If the user wants a summary/briefing/decision, end steps with one "model" step.
- Default notify is {"channel": "ntfy", "when": "always"}. Use "if_important" only when the user says things like "only if", "when something matters".
- "if_important" requires at least one "model" step.

Example — "every morning at 8 check my email and canvas and send me a briefing":
{"name": "morning-briefing", "description": "every morning at 8 check my email and canvas and send me a briefing", "enabled": true,
 "trigger": {"type": "schedule", "time": "08:00"},
 "steps": [{"kind": "connector", "tool": "check_email", "args": {"account": "personal"}},
           {"kind": "connector", "tool": "check_canvas", "args": {}},
           {"kind": "model", "instructions": "Summarize the email and Canvas data into a short morning briefing with a checklist."}],
 "notify": {"channel": "ntfy", "when": "always"}}

Example — "let me know when my professor emails me":
{"name": "professor-email-alert", "description": "let me know when my professor emails me", "enabled": true,
 "trigger": {"type": "event", "source": "email", "account": "school", "condition": "the email is from the user's professor"},
 "steps": [{"kind": "model", "instructions": "Summarize the new matching email(s) in one or two sentences."}],
 "notify": {"channel": "ntfy", "when": "always"}}"""


class AutomationParseError(Exception):
    """The model could not produce a valid recipe from the request."""


def complete(client: ModelClient, model: str, config: LydiaConfig,
             messages: list[Message]) -> str:
    """One silent chat turn: drain the stream, return concatenated content."""
    parts: list[str] = []
    for chunk in client.chat_stream(
        model=model, messages=messages, temperature=0.2,
        num_ctx=config.num_ctx, think=config.think_flag,
        keep_alive=config.keep_alive,
    ):
        if chunk.content:
            parts.append(chunk.content)
    return "".join(parts)


def _extract_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise json.JSONDecodeError("no JSON object found", raw, 0)
    return json.loads(text[start:end + 1])


def parse_automation(text: str, client: ModelClient, model: str,
                     config: LydiaConfig) -> Automation:
    messages = [
        Message(role="system", content=PARSER_SYSTEM_PROMPT),
        Message(role="user", content=text),
    ]
    raw = complete(client, model, config, messages)
    problem = ""
    for attempt in range(2):
        try:
            auto = Automation.from_dict(_extract_json(raw))
            errors = validate(auto)
            if not errors:
                return auto
            problem = "; ".join(errors)
        except (json.JSONDecodeError, AutomationError) as exc:
            problem = str(exc)
        if attempt == 0:
            messages = messages + [
                Message(role="assistant", content=raw),
                Message(role="user", content=(
                    f"That JSON was invalid: {problem}. "
                    "Reply with ONLY the corrected JSON object.")),
            ]
            raw = complete(client, model, config, messages)
    raise AutomationParseError(
        f"Couldn't turn that into an automation ({problem}). "
        "Try rephrasing — e.g. 'every morning at 8, check my email and send me a summary'.")
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_automations_parser.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/lydia/automations/parser.py tests/test_automations_parser.py
git commit -m "Add English-to-recipe automation parser"
```

---

### Task 6: Runner (due-ness, execution, events, tick)

**Files:**
- Create: `src/lydia/automations/runner.py`
- Test: `tests/test_automations_runner.py`

**Interfaces:**
- Consumes: Tasks 1/2/5 (`model`, `store`, `parser.complete`); `agent.tools.build_registry`, `ToolContext`; `connectors` (gmail/outlook/canvas fetchers with `.id` from Task 4, `ntfy.send_push`); `config.secrets`.
- Produces:
  - `is_due(auto: Automation, state_entry: dict, now: datetime) -> bool`: schedule/interval only.
  - `execute(auto, config, client, model, extra_sections: list[tuple[str, str]] | None = None, handlers: dict | None = None) -> str`: runs steps, returns final text.
  - `run_one(auto, config, client, model, now: datetime, state: dict, handlers=None) -> dict`: executes + notifies + updates `state[auto.name]["last_run"]`, returns a run record (shape from Task 2).
  - `poll_new_items(trigger, config) -> list[tuple[str, str]]`: (id, text) for the source's current items; raises `AutomationError` when not configured.
  - `tick(config, client, model, now: datetime | None = None, handlers=None) -> list[dict]` is the heartbeat entry: lock, iterate, dedupe, execute, save state, append runs, failure notices. Returns run records.
  - `FAILURE_NOTICE_INTERVAL_HOURS = 6`.

- [ ] **Step 1: Write the failing tests**

`tests/test_automations_runner.py`:

```python
import json
from datetime import datetime

import pytest

from lydia.automations import runner, store
from lydia.automations.model import Automation, Notify, Step, Trigger
from lydia.config.settings import LydiaConfig
from lydia.llm.types import ChatChunk


@pytest.fixture(autouse=True)
def isolated_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "AUTOMATIONS_DIR", tmp_path)


@pytest.fixture(autouse=True)
def no_real_push(monkeypatch):
    pushes = []
    monkeypatch.setattr(runner, "_send_ntfy",
                        lambda title, message, priority="default": pushes.append((title, message)))
    return pushes


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)

    def chat_stream(self, **kwargs):
        yield ChatChunk(content=self.replies.pop(0), done=True)


def _sched(name="daily", time="08:00") -> Automation:
    return Automation(name=name, description="d",
                      trigger=Trigger(type="schedule", time=time),
                      steps=[Step(kind="connector", tool="check_news"),
                             Step(kind="model", instructions="Summarize.")],
                      notify=Notify(channel="ntfy", when="always"))


FAKE_HANDLERS = {"check_news": lambda args, ctx: type(
    "R", (), {"ok": True, "content": "- headline one"})()}


# -- is_due ------------------------------------------------------------

def test_schedule_not_due_before_time():
    now = datetime(2026, 7, 17, 7, 55)
    assert runner.is_due(_sched(), {}, now) is False


def test_schedule_due_after_time_and_only_once_per_day():
    now = datetime(2026, 7, 17, 8, 3)
    assert runner.is_due(_sched(), {}, now) is True
    ran = {"last_run": datetime(2026, 7, 17, 8, 3).isoformat()}
    assert runner.is_due(_sched(), ran, now) is False
    # catch-up: asleep at 08:00, awake at 14:00, still due
    assert runner.is_due(_sched(), {"last_run": datetime(2026, 7, 16, 8, 0).isoformat()},
                         datetime(2026, 7, 17, 14, 0)) is True


def test_interval_due():
    auto = _sched()
    auto.trigger = Trigger(type="interval", minutes=30)
    now = datetime(2026, 7, 17, 12, 0)
    assert runner.is_due(auto, {}, now) is True
    assert runner.is_due(auto, {"last_run": datetime(2026, 7, 17, 11, 45).isoformat()}, now) is False
    assert runner.is_due(auto, {"last_run": datetime(2026, 7, 17, 11, 25).isoformat()}, now) is True


# -- execute -----------------------------------------------------------

def test_execute_feeds_connector_output_to_model_step():
    client = FakeClient(["the summary"])
    result = runner.execute(_sched(), LydiaConfig(), client, "m", handlers=FAKE_HANDLERS)
    assert result == "the summary"


def test_execute_without_model_step_returns_sections():
    auto = _sched()
    auto.steps = [Step(kind="connector", tool="check_news")]
    auto.notify = Notify(channel="ntfy", when="always")
    result = runner.execute(auto, LydiaConfig(), FakeClient([]), "m", handlers=FAKE_HANDLERS)
    assert "headline one" in result


# -- run_one + notify --------------------------------------------------

def test_run_one_notifies_and_updates_state(no_real_push):
    state = {}
    record = runner.run_one(_sched(), LydiaConfig(), FakeClient(["sum"]), "m",
                            datetime(2026, 7, 17, 8, 3), state, handlers=FAKE_HANDLERS)
    assert record["ok"] is True and record["notified"] is True
    assert state["daily"]["last_run"].startswith("2026-07-17T08:03")
    assert no_real_push and "sum" in no_real_push[0][1]


def test_nothing_to_report_suppresses_push(no_real_push):
    auto = _sched()
    auto.notify = Notify(channel="ntfy", when="if_important")
    record = runner.run_one(auto, LydiaConfig(), FakeClient(["all quiet NOTHING_TO_REPORT"]),
                            "m", datetime(2026, 7, 17, 8, 3), {}, handlers=FAKE_HANDLERS)
    assert record["notified"] is False
    assert no_real_push == []


# -- tick --------------------------------------------------------------

def test_tick_runs_due_automation_and_records(no_real_push):
    store.save_automation(_sched())
    results = runner.tick(LydiaConfig(), FakeClient(["s"]), "m",
                          now=datetime(2026, 7, 17, 9, 0), handlers=FAKE_HANDLERS)
    assert len(results) == 1 and results[0]["ok"] is True
    assert store.load_state()["daily"]["last_run"]
    assert store.load_runs()[-1]["name"] == "daily"
    # second tick same day: nothing due
    assert runner.tick(LydiaConfig(), FakeClient([]), "m",
                       now=datetime(2026, 7, 17, 9, 5), handlers=FAKE_HANDLERS) == []


def test_tick_skips_disabled(no_real_push):
    auto = _sched()
    auto.enabled = False
    store.save_automation(auto)
    assert runner.tick(LydiaConfig(), FakeClient([]), "m",
                       now=datetime(2026, 7, 17, 9, 0), handlers=FAKE_HANDLERS) == []


def test_tick_failure_records_and_rate_limits_notice(no_real_push, monkeypatch):
    store.save_automation(_sched())
    def boom(args, ctx):
        raise RuntimeError("connector exploded")
    bad_handlers = {"check_news": boom}
    results = runner.tick(LydiaConfig(), FakeClient([]), "m",
                          now=datetime(2026, 7, 17, 9, 0), handlers=bad_handlers)
    assert results[0]["ok"] is False and "connector exploded" in results[0]["error"]
    assert len(no_real_push) == 1  # failure notice sent
    # next day, same failure inside 6h window of... reset state so it IS due again
    state = store.load_state()
    state["daily"]["last_run"] = datetime(2026, 7, 16, 9, 0).isoformat()
    store.save_state(state)
    runner.tick(LydiaConfig(), FakeClient([]), "m",
                now=datetime(2026, 7, 17, 11, 0), handlers=bad_handlers)
    assert len(no_real_push) == 1  # still 1: within FAILURE_NOTICE_INTERVAL_HOURS


# -- events ------------------------------------------------------------

def _event_auto() -> Automation:
    return Automation(name="prof-alert", description="d",
                      trigger=Trigger(type="event", source="email", account="school",
                                      condition="from the professor"),
                      steps=[Step(kind="model", instructions="Summarize the new email.")],
                      notify=Notify(channel="ntfy", when="always"))


def test_event_first_poll_seeds_without_firing(no_real_push, monkeypatch):
    store.save_automation(_event_auto())
    monkeypatch.setattr(runner, "poll_new_items",
                        lambda trigger, config: [("id1", "old mail")])
    results = runner.tick(LydiaConfig(), FakeClient([]), "m",
                          now=datetime(2026, 7, 17, 9, 0))
    assert results == []
    assert store.load_state()["prof-alert"]["seen_ids"] == ["id1"]


def test_event_fires_only_on_new_matching_items(no_real_push, monkeypatch):
    store.save_automation(_event_auto())
    store.save_state({"prof-alert": {"seen_ids": ["id1"]}})
    monkeypatch.setattr(runner, "poll_new_items",
                        lambda trigger, config: [("id1", "old"), ("id2", "new prof mail")])
    # reply 1: condition check -> MATCH ; reply 2: the model step summary
    results = runner.tick(LydiaConfig(), FakeClient(["MATCH", "prof emailed you"]), "m",
                          now=datetime(2026, 7, 17, 9, 0))
    assert len(results) == 1 and results[0]["notified"] is True
    assert set(store.load_state()["prof-alert"]["seen_ids"]) == {"id1", "id2"}


def test_event_no_match_updates_seen_without_firing(no_real_push, monkeypatch):
    store.save_automation(_event_auto())
    store.save_state({"prof-alert": {"seen_ids": ["id1"]}})
    monkeypatch.setattr(runner, "poll_new_items",
                        lambda trigger, config: [("id2", "spam")])
    results = runner.tick(LydiaConfig(), FakeClient(["NO_MATCH"]), "m",
                          now=datetime(2026, 7, 17, 9, 0))
    assert results == []
    assert "id2" in store.load_state()["prof-alert"]["seen_ids"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_automations_runner.py -v`
Expected: FAIL (no module `runner`)

- [ ] **Step 3: Implement**

`src/lydia/automations/runner.py`:

```python
"""Execute automations: due-ness, steps, event polling, the heartbeat tick.

The tick is invoked by launchd every ~5 minutes (`lydia automations tick`).
Model/client/now/handlers are all injected so everything here is testable
with fakes. Layering: never import cli/ — notifications go through
connectors/ntfy.py or osascript directly, confirm callbacks always decline
(only safe-risk tools are reachable, so confirm is never actually called).
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from lydia.agent.tools import ToolContext, build_registry
from lydia.automations import store
from lydia.automations.model import (
    ALLOWED_STEP_TOOLS, Automation, AutomationError, NOTHING_TO_REPORT, Trigger,
)
from lydia.automations.parser import complete
from lydia.config.settings import LydiaConfig
from lydia.llm.protocol import ModelClient
from lydia.llm.types import Message

logger = logging.getLogger(__name__)

FAILURE_NOTICE_INTERVAL_HOURS = 6

MODEL_STEP_SYSTEM_PROMPT = (
    "You are Lydia, running an unattended scheduled automation for your user. "
    "Work ONLY from the data sections provided — never invent information. "
    "Be concise: the result may be sent as a phone notification."
)
IF_IMPORTANT_SUFFIX = (
    " If there is nothing worth notifying the user about, end your reply with "
    f"the exact word {NOTHING_TO_REPORT}."
)
CONDITION_PROMPT = (
    "Below are NEW items. Decide whether ANY of them matches this condition: "
    "{condition}\n\nItems:\n{items}\n\nReply with exactly MATCH or NO_MATCH."
)


def _connector_handlers() -> dict[str, Callable]:
    return {spec.name: spec.handler for spec in build_registry()
            if spec.name in ALLOWED_STEP_TOOLS}


def _ctx(config: LydiaConfig) -> ToolContext:
    return ToolContext(root=Path.home(), config=config, confirm=lambda _r: False)


def is_due(auto: Automation, state_entry: dict, now: datetime) -> bool:
    t = auto.trigger
    last_raw = state_entry.get("last_run")
    last = datetime.fromisoformat(last_raw) if last_raw else None
    if t.type == "schedule":
        hour, minute = (int(p) for p in t.time.split(":"))
        if (now.hour, now.minute) < (hour, minute):
            return False
        return last is None or last.date() < now.date()
    if t.type == "interval":
        return last is None or (now - last) >= timedelta(minutes=t.minutes)
    return False  # events are polled every tick, not "due"


def execute(auto: Automation, config: LydiaConfig, client: ModelClient, model: str,
            extra_sections: list[tuple[str, str]] | None = None,
            handlers: dict | None = None) -> str:
    handlers = handlers if handlers is not None else _connector_handlers()
    ctx = _ctx(config)
    sections: list[tuple[str, str]] = list(extra_sections or [])
    result_text = ""
    for step in auto.steps:
        if step.kind == "connector":
            result = handlers[step.tool](step.args, ctx)
            sections.append((step.tool, result.content))
        else:
            system = MODEL_STEP_SYSTEM_PROMPT
            if auto.notify.when == "if_important":
                system += IF_IMPORTANT_SUFFIX
            data = "\n\n".join(f"## {label}\n{content}" for label, content in sections)
            prompt = (f"Data gathered for automation '{auto.name}':\n\n{data}\n\n"
                      f"Instructions: {step.instructions}")
            result_text = complete(client, model, config, [
                Message(role="system", content=system),
                Message(role="user", content=prompt),
            ])
            sections.append(("model", result_text))
    if not result_text:
        result_text = "\n\n".join(f"## {label}\n{content}" for label, content in sections)
    return result_text


def _send_ntfy(title: str, message: str, priority: str = "default") -> None:
    from lydia.config import secrets
    from lydia.connectors import ntfy

    topic = secrets.get_secret(secrets.NTFY_TOPIC)
    if not topic:
        logger.warning("ntfy not configured; run `lydia auth login ntfy`")
        return
    ntfy.send_push(topic, title, message, priority=priority)


def _mac_notify(message: str, subtitle: str) -> None:
    script = (f'display notification {json.dumps(message[:200])} '
              f'with title "Lydia" subtitle {json.dumps(subtitle)}')
    subprocess.run(["osascript", "-e", script], check=False)


def _notify(auto: Automation, result_text: str) -> bool:
    if auto.notify.channel == "none":
        return False
    if auto.notify.when == "if_important" and NOTHING_TO_REPORT in result_text:
        return False
    body = result_text.replace(NOTHING_TO_REPORT, "").strip()[:1000]
    if auto.notify.channel == "ntfy":
        _send_ntfy(f"Lydia · {auto.name}", body)
    else:
        _mac_notify(body, auto.name)
    return True


def run_one(auto: Automation, config: LydiaConfig, client: ModelClient, model: str,
            now: datetime, state: dict,
            extra_sections: list[tuple[str, str]] | None = None,
            handlers: dict | None = None) -> dict:
    started = now.isoformat()
    result_text = execute(auto, config, client, model,
                          extra_sections=extra_sections, handlers=handlers)
    notified = _notify(auto, result_text)
    entry = state.setdefault(auto.name, {})
    entry["last_run"] = now.isoformat()
    return {"name": auto.name, "started_at": started, "duration_seconds": 0.0,
            "ok": True, "error": None,
            "result_snippet": result_text[:200], "notified": notified}


def poll_new_items(trigger: Trigger, config: LydiaConfig) -> list[tuple[str, str]]:
    """(stable_id, one-line text) for every current item at the event source."""
    from lydia.config import secrets

    if trigger.source == "email" and trigger.account == "personal":
        from lydia.connectors.email_gmail import get_recent_emails

        creds = secrets.get_secret(secrets.GMAIL_REFRESH_TOKEN)
        if not creds:
            raise AutomationError("Gmail isn't connected (`lydia auth login gmail`)")
        return [(s.id, f"[{'UNREAD' if s.unread else 'read'}] {s.sender}: {s.subject} — {s.snippet}")
                for s in get_recent_emails(creds)]
    if trigger.source == "email" and trigger.account == "school":
        from lydia.connectors.auth import outlook_oauth
        from lydia.connectors.email_outlook import get_recent_emails

        token = outlook_oauth.get_access_token()
        return [(s.id, f"[{'UNREAD' if s.unread else 'read'}] {s.sender}: {s.subject} — {s.snippet}")
                for s in get_recent_emails(token)]
    if trigger.source == "canvas":
        from lydia.connectors.canvas import get_upcoming_assignments

        base_url = config.canvas_base_url
        token = secrets.get_secret(secrets.CANVAS_TOKEN)
        if not base_url or not token:
            raise AutomationError("Canvas isn't set up (`lydia auth login canvas`)")
        return [(a.id, f"{a.course_name}: {a.name} (due {a.due_at or 'n/a'})")
                for a in get_upcoming_assignments(base_url, token)]
    raise AutomationError(f"Unknown event source '{trigger.source}'")


def _matches(condition: str, items: list[tuple[str, str]],
             config: LydiaConfig, client: ModelClient, model: str) -> bool:
    listing = "\n".join(f"- {text}" for _id, text in items)
    reply = complete(client, model, config, [
        Message(role="user", content=CONDITION_PROMPT.format(condition=condition, items=listing)),
    ])
    return "MATCH" in reply and "NO_MATCH" not in reply


def _record_failure(auto: Automation, exc: Exception, now: datetime, state: dict) -> dict:
    logger.warning("Automation %s failed: %s", auto.name, exc)
    entry = state.setdefault(auto.name, {})
    entry["last_run"] = now.isoformat()
    last_notice_raw = entry.get("last_failure_notice")
    notice_due = (last_notice_raw is None or
                  now - datetime.fromisoformat(last_notice_raw)
                  >= timedelta(hours=FAILURE_NOTICE_INTERVAL_HOURS))
    if notice_due:
        try:
            _send_ntfy(f"Lydia · {auto.name} failed", str(exc)[:500], priority="high")
            entry["last_failure_notice"] = now.isoformat()
        except Exception:  # noqa: BLE001 - a broken push must not kill the tick
            logger.warning("Failure notice for %s could not be sent", auto.name)
    return {"name": auto.name, "started_at": now.isoformat(), "duration_seconds": 0.0,
            "ok": False, "error": str(exc), "result_snippet": "", "notified": False}


def tick(config: LydiaConfig, client: ModelClient, model: str,
         now: datetime | None = None, handlers: dict | None = None) -> list[dict]:
    now = now or datetime.now()
    if not store.try_acquire_lock():
        logger.info("Another tick is running; skipping")
        return []
    results: list[dict] = []
    try:
        state = store.load_state()
        for auto in store.list_automations():
            if not auto.enabled:
                continue
            try:
                if auto.trigger.type == "event":
                    record = _tick_event(auto, config, client, model, now, state)
                elif is_due(auto, state.get(auto.name, {}), now):
                    record = run_one(auto, config, client, model, now, state,
                                     handlers=handlers)
                else:
                    record = None
            except Exception as exc:  # noqa: BLE001 - one bad automation must not stop the rest
                record = _record_failure(auto, exc, now, state)
            if record is not None:
                results.append(record)
                store.append_run(record)
        store.save_state(state)
    finally:
        store.release_lock()
    return results


def _tick_event(auto: Automation, config: LydiaConfig, client: ModelClient,
                model: str, now: datetime, state: dict) -> dict | None:
    items = poll_new_items(auto.trigger, config)
    entry = state.setdefault(auto.name, {})
    if "seen_ids" not in entry:
        # First poll ever: seed silently so creating an automation doesn't
        # alert about everything already in the inbox.
        entry["seen_ids"] = [i for i, _t in items][-store.MAX_SEEN_IDS:]
        entry["last_run"] = now.isoformat()
        return None
    seen = set(entry["seen_ids"])
    new = [(i, t) for i, t in items if i not in seen]
    entry["seen_ids"] = (entry["seen_ids"] + [i for i, _t in new])[-store.MAX_SEEN_IDS:]
    entry["last_run"] = now.isoformat()
    if not new:
        return None
    if not _matches(auto.trigger.condition, new, config, client, model):
        return None
    section = ("new items", "\n".join(f"- {t}" for _i, t in new))
    return run_one(auto, config, client, model, now, state, extra_sections=[section])
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_automations_runner.py -v`
Expected: all PASS. Then `.venv/bin/pytest` for a green full suite.

- [ ] **Step 5: Commit**

```bash
git add src/lydia/automations/runner.py tests/test_automations_runner.py
git commit -m "Add automation runner: due-ness, steps, event dedupe, tick"
```

---

### Task 7: Heartbeat plist via `cli/scheduler.py` generalization

**Files:**
- Modify: `src/lydia/cli/scheduler.py`
- Test: extend `tests/test_scheduler.py` (follow its existing fake-runner pattern; read it first)

**Interfaces:**
- Consumes: existing `Runner`, `ScheduleError`, `_find_lydia_executable`.
- Produces: `AUTOMATIONS_LABEL = "com.lydia.automations"`; `AUTOMATIONS_PLIST_PATH`; `AUTOMATIONS_LOG_PATH = Path.home() / ".lydia" / "automations" / "tick.log"`; `enable_automations(interval_seconds: int = 300, lydia_path: str | None = None, runner: Runner = subprocess.run) -> Path`; `disable_automations(runner: Runner = subprocess.run) -> None`; `automations_enabled() -> bool`. Existing briefing functions unchanged.

- [ ] **Step 1: Write the failing tests** (in `tests/test_scheduler.py`, using its existing fake-runner + monkeypatched-plist-path pattern; monkeypatch `scheduler.AUTOMATIONS_PLIST_PATH` to `tmp_path / "auto.plist"`):

```python
def test_enable_automations_writes_interval_plist(tmp_path, monkeypatch):
    from lydia.cli import scheduler
    monkeypatch.setattr(scheduler, "AUTOMATIONS_PLIST_PATH", tmp_path / "auto.plist")
    calls = []
    def fake_runner(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    path = scheduler.enable_automations(interval_seconds=300, lydia_path="/bin/lydia",
                                        runner=fake_runner)
    text = path.read_text()
    assert "<key>StartInterval</key>" in text and "<integer>300</integer>" in text
    assert "<string>automations</string>" in text and "<string>tick</string>" in text
    assert calls[0][:2] == ["launchctl", "load"]


def test_enable_automations_rejects_silly_interval(tmp_path, monkeypatch):
    from lydia.cli import scheduler
    monkeypatch.setattr(scheduler, "AUTOMATIONS_PLIST_PATH", tmp_path / "auto.plist")
    with pytest.raises(scheduler.ScheduleError):
        scheduler.enable_automations(interval_seconds=10, lydia_path="/bin/lydia",
                                     runner=lambda *a, **k: None)


def test_disable_automations_unloads_and_removes(tmp_path, monkeypatch):
    from lydia.cli import scheduler
    plist = tmp_path / "auto.plist"
    plist.write_text("x")
    monkeypatch.setattr(scheduler, "AUTOMATIONS_PLIST_PATH", plist)
    scheduler.disable_automations(runner=lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, "", ""))
    assert not plist.exists()
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_scheduler.py -v`
Expected: new tests FAIL (`no attribute 'enable_automations'`), old ones PASS.

- [ ] **Step 3: Implement**, appending to `cli/scheduler.py`:

```python
AUTOMATIONS_LABEL = "com.lydia.automations"
AUTOMATIONS_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{AUTOMATIONS_LABEL}.plist"
AUTOMATIONS_LOG_PATH = Path.home() / ".lydia" / "automations" / "tick.log"


def _interval_plist_contents(lydia_path: str, seconds: int) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
\t<key>Label</key>
\t<string>{AUTOMATIONS_LABEL}</string>
\t<key>ProgramArguments</key>
\t<array>
\t\t<string>{lydia_path}</string>
\t\t<string>automations</string>
\t\t<string>tick</string>
\t</array>
\t<key>StartInterval</key>
\t<integer>{seconds}</integer>
\t<key>StandardOutPath</key>
\t<string>{AUTOMATIONS_LOG_PATH}</string>
\t<key>StandardErrorPath</key>
\t<string>{AUTOMATIONS_LOG_PATH}</string>
</dict>
</plist>
"""


def enable_automations(
    interval_seconds: int = 300,
    lydia_path: str | None = None,
    runner: Runner = subprocess.run,
) -> Path:
    """Write and load the automations heartbeat plist. Returns its path."""
    if not 60 <= interval_seconds <= 3600:
        raise ScheduleError("Interval must be between 60 and 3600 seconds.")
    resolved_path = lydia_path or _find_lydia_executable()
    AUTOMATIONS_PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUTOMATIONS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUTOMATIONS_PLIST_PATH.write_text(
        _interval_plist_contents(resolved_path, interval_seconds), encoding="utf-8")
    result = runner(["launchctl", "load", str(AUTOMATIONS_PLIST_PATH)],
                    capture_output=True, text=True)
    if result.returncode != 0:
        raise ScheduleError(f"launchctl load failed: {(result.stderr or result.stdout).strip()}")
    return AUTOMATIONS_PLIST_PATH


def disable_automations(runner: Runner = subprocess.run) -> None:
    if not AUTOMATIONS_PLIST_PATH.is_file():
        return
    runner(["launchctl", "unload", str(AUTOMATIONS_PLIST_PATH)], capture_output=True, text=True)
    AUTOMATIONS_PLIST_PATH.unlink()


def automations_enabled() -> bool:
    return AUTOMATIONS_PLIST_PATH.is_file()
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_scheduler.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/lydia/cli/scheduler.py tests/test_scheduler.py
git commit -m "Add launchd heartbeat for the automations tick"
```

---

### Task 8: CLI wiring for `automate`, the `automations` app, `/automate`, and `auth login ntfy`

**Files:**
- Create: `src/lydia/cli/automate_flow.py`
- Modify: `src/lydia/cli/main.py`
- Modify: `src/lydia/cli/chat.py` (slash command + help table row)
- Test: `tests/test_cli_automations.py`

**Interfaces:**
- Consumes: everything above; `cli/chat.py::resolve_model`; `ui.print_error/print_info/console`; `typer.confirm`.
- Produces: `automate_flow.create_from_english(text, client, model, config) -> bool`; CLI commands `lydia automate "<text>"`, `lydia automations list|show|run|enable|disable|remove|tick`, `lydia automations schedule enable [--interval N]|disable`; `lydia auth login ntfy` (+ status/logout coverage); `/automate <text>` in chat.

- [ ] **Step 1: Write the failing tests**

`tests/test_cli_automations.py` (CliRunner style, copied from `tests/test_cli_commands.py`; read that file first and reuse its runner/fixture conventions):

```python
import pytest
from typer.testing import CliRunner

from lydia.automations import store
from lydia.automations.model import Automation, Notify, Step, Trigger
from lydia.cli.main import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "AUTOMATIONS_DIR", tmp_path)


def _saved(name="daily") -> Automation:
    auto = Automation(name=name, description="d",
                      trigger=Trigger(type="schedule", time="08:00"),
                      steps=[Step(kind="connector", tool="check_news"),
                             Step(kind="model", instructions="Summarize.")],
                      notify=Notify())
    store.save_automation(auto)
    return auto


def test_list_empty():
    result = runner.invoke(app, ["automations", "list"])
    assert result.exit_code == 0
    assert "No automations" in result.output


def test_list_and_show():
    _saved()
    result = runner.invoke(app, ["automations", "list"])
    assert result.exit_code == 0 and "daily" in result.output
    result = runner.invoke(app, ["automations", "show", "daily"])
    assert result.exit_code == 0 and "08:00" in result.output


def test_enable_disable_remove():
    _saved()
    assert runner.invoke(app, ["automations", "disable", "daily"]).exit_code == 0
    assert store.load_automation("daily").enabled is False
    assert runner.invoke(app, ["automations", "enable", "daily"]).exit_code == 0
    assert store.load_automation("daily").enabled is True
    assert runner.invoke(app, ["automations", "remove", "daily"]).exit_code == 0
    assert store.list_automations() == []


def test_show_missing_errors():
    result = runner.invoke(app, ["automations", "show", "nope"])
    assert result.exit_code == 1
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_cli_automations.py -v`
Expected: FAIL (no `automations` command).

- [ ] **Step 3: Implement**

`src/lydia/cli/automate_flow.py`:

```python
"""Shared plain-English automation creation flow, used by `lydia automate`
and the /automate slash command in chat."""

from __future__ import annotations

import typer

from lydia.automations import store
from lydia.automations.model import describe
from lydia.automations.parser import AutomationParseError, parse_automation
from lydia.cli import ui
from lydia.config.settings import LydiaConfig
from lydia.llm.protocol import ModelClient


def create_from_english(text: str, client: ModelClient, model: str,
                        config: LydiaConfig) -> bool:
    try:
        auto = parse_automation(text, client, model, config)
    except AutomationParseError as exc:
        ui.print_error(str(exc))
        return False
    ui.console.print(f"\nHere's what I understood:\n  {describe(auto)}\n")
    exists = store.recipe_path(auto.name).exists()
    prompt = "Overwrite this existing automation?" if exists else "Save this automation?"
    if not typer.confirm(prompt):
        ui.print_info("Discarded.")
        return False
    store.save_automation(auto)
    from lydia.cli import scheduler
    hint = ("" if scheduler.automations_enabled()
            else " Heartbeat is off — run `lydia automations schedule enable` so it actually fires.")
    ui.print_info(f"Saved '{auto.name}'.{hint}")
    return True
```

In `src/lydia/cli/main.py`, add sub-apps (next to the existing `add_typer` block):

```python
automations_app = typer.Typer(help="Create and manage plain-English automations.")
app.add_typer(automations_app, name="automations")
automations_schedule_app = typer.Typer(help="Manage the automations heartbeat (macOS launchd).")
automations_app.add_typer(automations_schedule_app, name="schedule")
```

Commands (place after the briefing commands; `_client_and_model` mirrors the connect/resolve dance `briefing_run` and `ask` already do, so factor it exactly like this):

```python
def _client_and_model(config: LydiaConfig):
    """Connect, or exit(1) with a printed error. Caller must close the client."""
    from lydia.cli.chat import resolve_model

    client = build_client(config)
    if not client.is_alive():
        ui.print_error(f"Cannot reach {config.server_url or config.ollama_host}.")
        raise typer.Exit(1)
    try:
        model = resolve_model(client, config)
    except OllamaError as exc:
        ui.print_error(str(exc))
        raise typer.Exit(1)
    return client, model


@app.command()
def automate(request: str = typer.Argument(..., help="What to automate, in plain English")) -> None:
    """Create an automation from a plain-English description."""
    from lydia.cli.automate_flow import create_from_english

    config = load_config()
    client, model = _client_and_model(config)
    with client:
        ok = create_from_english(request, client, model, config)
    raise typer.Exit(0 if ok else 1)


@automations_app.command("list")
def automations_list() -> None:
    from lydia.automations import store
    autos = store.list_automations()
    if not autos:
        ui.print_info("No automations yet. Create one with: lydia automate \"...\"")
        return
    from lydia.automations.model import describe
    state = store.load_state()
    for auto in autos:
        flag = "" if auto.enabled else " [disabled]"
        last = state.get(auto.name, {}).get("last_run", "never")
        ui.console.print(f"{describe(auto)}{flag}  [dim]last run: {last}[/dim]")


@automations_app.command("show")
def automations_show(name: str) -> None:
    import json as _json
    from lydia.automations import store
    from lydia.automations.model import AutomationError, describe
    try:
        auto = store.load_automation(name)
    except AutomationError as exc:
        ui.print_error(str(exc))
        raise typer.Exit(1)
    ui.console.print(describe(auto))
    ui.console.print(_json.dumps(auto.to_dict(), indent=2))


@automations_app.command("run")
def automations_run(name: str) -> None:
    """Execute one automation immediately (ignores its trigger) — for testing."""
    from datetime import datetime
    from lydia.automations import runner as auto_runner, store
    from lydia.automations.model import AutomationError
    try:
        auto = store.load_automation(name)
    except AutomationError as exc:
        ui.print_error(str(exc))
        raise typer.Exit(1)
    config = load_config()
    client, model = _client_and_model(config)
    with client:
        state = store.load_state()
        sections = None
        if auto.trigger.type == "event":
            items = auto_runner.poll_new_items(auto.trigger, config)
            sections = [("current items", "\n".join(t for _i, t in items))]
        record = auto_runner.run_one(auto, config, client, model,
                                     datetime.now(), state, extra_sections=sections)
        store.save_state(state)
        store.append_run(record)
    ui.console.print(record["result_snippet"] or "(no output)")
    ui.print_info(f"ok={record['ok']} notified={record['notified']}")


@automations_app.command("enable")
def automations_enable(name: str) -> None:
    _set_enabled(name, True)


@automations_app.command("disable")
def automations_disable(name: str) -> None:
    _set_enabled(name, False)


def _set_enabled(name: str, value: bool) -> None:
    from lydia.automations import store
    from lydia.automations.model import AutomationError
    try:
        auto = store.load_automation(name)
    except AutomationError as exc:
        ui.print_error(str(exc))
        raise typer.Exit(1)
    auto.enabled = value
    store.save_automation(auto)
    ui.print_info(f"'{name}' {'enabled' if value else 'disabled'}.")


@automations_app.command("remove")
def automations_remove(name: str) -> None:
    from lydia.automations import store
    if store.delete_automation(name):
        ui.print_info(f"Removed '{name}'.")
    else:
        ui.print_error(f"No automation named '{name}'.")
        raise typer.Exit(1)


@automations_app.command("tick")
def automations_tick() -> None:
    """One heartbeat pass — normally invoked by launchd, not by hand."""
    from lydia.automations import runner as auto_runner
    config = load_config()
    client, model = _client_and_model(config)
    with client:
        results = auto_runner.tick(config, client, model)
    for record in results:
        status = "ok" if record["ok"] else f"FAILED: {record['error']}"
        ui.console.print(f"{record['name']}: {status}")
    if not results:
        ui.print_info("Nothing due.")


@automations_schedule_app.command("enable")
def automations_schedule_enable(
    interval: int = typer.Option(300, "--interval", help="Seconds between ticks (60-3600)"),
) -> None:
    from lydia.cli import scheduler
    try:
        path = scheduler.enable_automations(interval_seconds=interval)
    except scheduler.ScheduleError as exc:
        ui.print_error(str(exc))
        raise typer.Exit(1)
    ui.print_info(f"Heartbeat enabled every {interval}s ({path}).")


@automations_schedule_app.command("disable")
def automations_schedule_disable() -> None:
    from lydia.cli import scheduler
    scheduler.disable_automations()
    ui.print_info("Heartbeat disabled.")
```

For `auth login ntfy`, in `auth_login` (main.py:377), add an `ntfy` branch alongside gmail/outlook/canvas (match the existing branch style):

```python
if provider == "ntfy":
    import secrets as pysecrets
    from lydia.config import secrets as lydia_secrets

    topic = f"lydia-{pysecrets.token_hex(6)}"
    lydia_secrets.set_secret(lydia_secrets.NTFY_TOPIC, topic)
    ui.print_info(
        f"Your private ntfy topic: {topic}\n"
        "1. Install the ntfy app (App Store / Play Store)\n"
        f"2. Subscribe to the topic '{topic}'\n"
        "3. Test it: lydia auth status ntfy — or just wait for an automation to fire.\n"
        "Treat the topic name like a password — anyone who knows it can read your alerts."
    )
    return
```

Also extend `auth_status` (show whether `NTFY_TOPIC` is set, printing the topic so Levi can re-subscribe a new phone) and `auth_logout` (`delete_secret(NTFY_TOPIC)`), and update the two commands' help strings that enumerate providers ("gmail | outlook | canvas" → "gmail | outlook | canvas | ntfy").

In `cli/chat.py`, inside `_handle_slash`, add before the final else:

```python
elif command == "/automate":
    if not arg:
        ui.print_error("Usage: /automate <what to automate, in plain English>")
    else:
        from lydia.cli.automate_flow import create_from_english
        create_from_english(arg, session.client, session.model, session.config)
```

(match the exact local variable names used by the neighboring branches; the argument variable may be named differently, so read the function first) and add a row to the help table at the top of the file: `| /automate <text> | Create an automation in plain English |`.

- [ ] **Step 4: Run to verify**

Run: `.venv/bin/pytest tests/test_cli_automations.py -v` then `.venv/bin/pytest`
Expected: all PASS, full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/lydia/cli/ tests/test_cli_automations.py
git commit -m "Wire automations into the CLI and chat REPL"
```

---

### Task 9: Docs + full verification

**Files:**
- Modify: `README.md` (features section: automations + ntfy; new commands)
- Modify: `ROADMAP.md` (move automations into Done with a dated entry; add the summer-brain note: `server_url` unset → local Mac Ollama; verify tool-calling empirically per CLAUDE.md before trusting a newly pulled Mac model; prevent Mac sleep for timely ticks, since launchd catch-up still runs missed schedules on wake)
- Modify: `CLAUDE.md` (one paragraph: the `automations/` package layer may import agent/connectors/llm/config, never cli; store's single `AUTOMATIONS_DIR` patch point for tests)

- [ ] **Step 1: Write the docs** (prose, no code; describe the feature the way README currently describes briefings, including the `lydia automate` example from the spec)
- [ ] **Step 2: Full suite**

Run: `.venv/bin/pytest` and `cd server && ../.venv/bin/pytest`
Expected: all PASS (270 + new CLI-package tests; 14 server tests untouched).

- [ ] **Step 3: Commit**

```bash
git add README.md ROADMAP.md CLAUDE.md
git commit -m "Document the automations engine"
```

---

## Manual end-to-end verification (Levi's machine, live Ollama, after all tasks)

Not automatable in CI; do these in order and report results honestly:

1. `lydia auth login ntfy` → subscribe on the phone → verify a test push arrives (e.g. in chat: "send a test notification to my phone", which exercises the `notify` tool).
2. `lydia automate "every morning at 8, check my email and canvas and send me a briefing"` → confirm the echo reads correctly → save.
3. `lydia automations run morning-briefing` → real push arrives with a real briefing.
4. `lydia automations schedule enable` → `launchctl list | grep lydia` shows the job → watch `~/.lydia/automations/tick.log` for one real tick ("Nothing due." is a pass).
5. Local-model check: with `server_url` unset, confirm the configured Mac model passes the CLAUDE.md tool-calling curl check.
