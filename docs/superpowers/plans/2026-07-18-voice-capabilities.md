# Voice Capabilities + Speed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Lydia weather, macOS calendar, open-app/file, and find/read-file abilities (chat + voice), and make voice fast (small model, thinking off).

**Architecture:** Two new connectors (`weather.py`, the free Open-Meteo API with IP-geolocation fallback; `calendar_mac.py`, an AppleScript read of macOS Calendar), one new `open_app` tool wrapping `open`, all registered as safe-risk ToolSpecs in `agent/tools.py` following the existing `_check_*` pattern. `voice/assistant.py` widens `VOICE_TOOLS` and forces `think=False`; new config keys `weather_location` and `voice_model`.

**Design decisions (approved by Levi 2026-07-18):** all four capabilities; voice actions execute without confirmation; voice uses `qwen3.5:4b` (verified empirically: emits structured `tool_calls`).

## Global Constraints

- Unit tests NEVER hit the network (httpx.MockTransport / monkeypatched subprocess) or a live Ollama. `.venv/bin/pytest` from repo root; all 341 existing tests stay green.
- `voice/` never imports `lydia.cli`. New tools are risk `"safe"`.
- No new dependencies.
- **Never add a `Co-Authored-By: Claude` (or any Claude/Anthropic) trailer to commits.** Plain imperative subjects.
- New config keys (Task 1 adds both): `weather_location: str | None = None`, `voice_model: str | None = None`, both appended after `voice_tts_voice` in `LydiaConfig`.

---

### Task 1: Weather connector + tool + config keys

**Files:**
- Create: `src/lydia/connectors/weather.py`
- Modify: `src/lydia/config/settings.py` (two new fields after `voice_tts_voice`)
- Modify: `src/lydia/agent/tools.py` (`_check_weather` handler + ToolSpec after `notify`)
- Test: `tests/test_connectors_weather.py`, extend `tests/test_agent_tools.py`

**Interfaces:**
- Produces: `get_weather(location: str | None = None, transport=None) -> str` raising `ConnectorError`; ToolSpec `"check_weather"` (safe) with optional `location` string arg, handler falls back to `ctx.config.weather_location`.

- [ ] **Step 1: Failing tests**

```python
"""tests/test_connectors_weather.py"""
import httpx
import pytest

from lydia.connectors.base import ConnectorError
from lydia.connectors.weather import get_weather


def _transport(handlers):
    def handle(request):
        for prefix, payload in handlers.items():
            if request.url.host.startswith(prefix):
                return httpx.Response(200, json=payload)
        return httpx.Response(404)
    return httpx.MockTransport(handle)


FORECAST = {
    "current": {"temperature_2m": 87.1, "apparent_temperature": 84.0,
                "precipitation": 0.0, "weather_code": 1, "wind_speed_10m": 7.0},
    "daily": {"time": ["2026-07-18", "2026-07-19"],
              "temperature_2m_max": [95.0, 97.2], "temperature_2m_min": [61.0, 63.5],
              "precipitation_probability_max": [5, 10]},
}


def test_named_location_geocodes_then_fetches():
    transport = _transport({
        "geocoding-api": {"results": [{"latitude": 43.1, "longitude": -115.7, "name": "Mountain Home"}]},
        "api.open-meteo": FORECAST,
    })
    out = get_weather("Mountain Home", transport=transport)
    assert "Mountain Home" in out and "87" in out and "Mostly clear" in out and "95" in out


def test_no_location_uses_ip_geolocation():
    transport = _transport({
        "ip-api": {"status": "success", "lat": 43.1, "lon": -115.7, "city": "Boise"},
        "api.open-meteo": FORECAST,
    })
    out = get_weather(transport=transport)
    assert "Boise" in out


def test_unknown_location_raises():
    transport = _transport({"geocoding-api": {"results": []}})
    with pytest.raises(ConnectorError):
        get_weather("Nowhereville", transport=transport)
```

Extend `tests/test_agent_tools.py` (mirror the existing notify-tool tests' style: `ctx(tmp_path)` helper, monkeypatch the connector module function):

```python
def test_check_weather_uses_config_location(tmp_path, monkeypatch):
    from lydia.connectors import weather as weather_mod
    seen = {}
    monkeypatch.setattr(weather_mod, "get_weather",
                        lambda location=None, transport=None: seen.setdefault("loc", location) or "Sunny, 90F")
    context = ctx(tmp_path)
    context.config.weather_location = "Mountain Home"
    result = tools._check_weather({}, context)
    assert result.ok and "Sunny" in result.content
    assert seen["loc"] == "Mountain Home"
```

- [ ] **Step 2: Run to verify failure.** `.venv/bin/pytest tests/test_connectors_weather.py -q` → FAIL.

- [ ] **Step 3: Implement**

In `settings.py`, append after `voice_tts_voice`:

```python
    # e.g. "Mountain Home, Idaho". None = auto-detect from IP (works while traveling).
    weather_location: str | None = None
    # Model used for voice turns only. None = same resolution as chat. A small
    # tool-calling model (qwen3.5:4b) keeps spoken replies fast.
    voice_model: str | None = None
```

```python
"""src/lydia/connectors/weather.py — current weather + 2-day outlook via Open-Meteo.

Free, no API key. Location comes from an explicit name (geocoded), or, when
none is given, IP geolocation via ip-api.com — right wherever the laptop is.
"""

from __future__ import annotations

import httpx

from lydia.connectors.base import ConnectorError

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
IP_LOCATE_URL = "http://ip-api.com/json"

# WMO weather interpretation codes, abbreviated to what Open-Meteo emits.
_CODES = {
    0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Icy fog", 51: "Light drizzle", 53: "Drizzle",
    55: "Heavy drizzle", 61: "Light rain", 63: "Rain", 65: "Heavy rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 80: "Rain showers",
    81: "Rain showers", 82: "Violent rain showers", 95: "Thunderstorm",
    96: "Thunderstorm with hail", 99: "Thunderstorm with heavy hail",
}


def _locate(client: httpx.Client, location: str | None) -> tuple[float, float, str]:
    if location:
        resp = client.get(GEOCODE_URL, params={"name": location, "count": 1})
        resp.raise_for_status()
        results = resp.json().get("results") or []
        if not results:
            raise ConnectorError(f"Could not find a place called '{location}'.")
        hit = results[0]
        return hit["latitude"], hit["longitude"], hit.get("name", location)
    resp = client.get(IP_LOCATE_URL)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "success":
        raise ConnectorError("Could not determine your location from your IP.")
    return data["lat"], data["lon"], data.get("city", "your area")


def get_weather(location: str | None = None, transport=None) -> str:
    try:
        with httpx.Client(transport=transport, timeout=10.0) as client:
            lat, lon, name = _locate(client, location)
            resp = client.get(FORECAST_URL, params={
                "latitude": lat, "longitude": lon,
                "current": "temperature_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m",
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "timezone": "auto", "forecast_days": 2,
                "temperature_unit": "fahrenheit", "wind_speed_unit": "mph",
            })
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise ConnectorError(f"Weather lookup failed: {exc}") from exc

    cur, daily = data["current"], data["daily"]
    sky = _CODES.get(cur.get("weather_code"), "Unknown conditions")
    lines = [
        f"Weather in {name}: {sky}, {cur['temperature_2m']:.0f}F "
        f"(feels like {cur['apparent_temperature']:.0f}F), wind {cur['wind_speed_10m']:.0f} mph.",
    ]
    for i, day in enumerate(daily["time"]):
        label = "Today" if i == 0 else "Tomorrow"
        lines.append(
            f"{label}: high {daily['temperature_2m_max'][i]:.0f}F, "
            f"low {daily['temperature_2m_min'][i]:.0f}F, "
            f"{daily['precipitation_probability_max'][i]}% chance of precipitation."
        )
    return "\n".join(lines)
```

In `agent/tools.py`, the handler (lazy import, matching `_check_news`) + ToolSpec registered right after `notify`:

```python
def _check_weather(args: dict, ctx: ToolContext) -> ToolResult:
    from lydia.connectors import weather

    location = args.get("location") or ctx.config.weather_location
    try:
        return ToolResult(ok=True, content=weather.get_weather(location=location))
    except ConnectorError as exc:
        return ToolResult(ok=False, content=str(exc))
```

```python
        ToolSpec(
            "check_weather", "Current weather and 2-day forecast for a place (or the user's location).",
            {
                "type": "object",
                "properties": {"location": {"type": "string", "description": "City or place name (optional; defaults to the user's location)"}},
                "required": [],
            },
            "safe", _check_weather,
        ),
```

- [ ] **Step 4: Run to green** on the focused files, then the full suite.
- [ ] **Step 5: Commit**: `Add weather connector with IP-located Open-Meteo forecasts`

---

### Task 2: macOS Calendar connector + tool

**Files:**
- Create: `src/lydia/connectors/calendar_mac.py`
- Modify: `src/lydia/agent/tools.py` (`_check_calendar` + ToolSpec after `check_weather`)
- Test: `tests/test_connectors_calendar.py`, extend `tests/test_agent_tools.py`

**Interfaces:**
- Produces: `get_events(days: int = 2, runner=subprocess.run) -> str` raising `ConnectorError`; ToolSpec `"check_calendar"` (safe) with optional integer `days` (1–14, default 2).

- [ ] **Step 1: Failing tests**

```python
"""tests/test_connectors_calendar.py"""
import subprocess

import pytest

from lydia.connectors.base import ConnectorError
from lydia.connectors.calendar_mac import get_events

RAW = "CS 452 Lecture|Monday, July 20, 2026 at 10:00:00 AM|Boise State\nDentist|Tuesday, July 21, 2026 at 2:30:00 PM|\n"


def _runner(stdout, returncode=0, stderr=""):
    def run(cmd, **kwargs):
        assert cmd[0] == "osascript"
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)
    return run


def test_formats_events():
    out = get_events(days=3, runner=_runner(RAW))
    assert "CS 452 Lecture" in out and "Dentist" in out and "Boise State" in out


def test_no_events_message():
    out = get_events(runner=_runner(""))
    assert "No events" in out


def test_osascript_failure_raises():
    with pytest.raises(ConnectorError, match="Calendar"):
        get_events(runner=_runner("", returncode=1, stderr="Not authorized"))


def test_days_out_of_range_clamped():
    seen = {}
    def run(cmd, **kwargs):
        seen["script"] = cmd[-1]
        return subprocess.CompletedProcess(cmd, 0, "", "")
    get_events(days=99, runner=run)
    assert "14 * days" in seen["script"]
```

`tests/test_agent_tools.py` addition (same monkeypatch style as weather):

```python
def test_check_calendar_tool(tmp_path, monkeypatch):
    from lydia.connectors import calendar_mac
    monkeypatch.setattr(calendar_mac, "get_events", lambda days=2, runner=None: f"{days} days: Dentist Tuesday")
    result = tools._check_calendar({"days": 5}, ctx(tmp_path))
    assert result.ok and "Dentist" in result.content and "5 days" in result.content
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement**

```python
"""src/lydia/connectors/calendar_mac.py — read upcoming events from macOS Calendar.

AppleScript via osascript: ships with macOS, no dependency, read-only. macOS
shows a one-time automation permission prompt for Calendar on first use. The
`whose` query is not fast, but a personal calendar over a few days is fine.
"""

from __future__ import annotations

import subprocess

from lydia.connectors.base import ConnectorError

_SCRIPT = """
set d1 to current date
set d2 to d1 + ({days} * days)
set out to ""
tell application "Calendar"
    repeat with c in calendars
        repeat with e in (every event of c whose start date is greater than or equal to d1 and start date is less than or equal to d2)
            set out to out & (summary of e) & "|" & ((start date of e) as string) & "|" & (location of e) & linefeed
        end repeat
    end repeat
end tell
return out
"""


def get_events(days: int = 2, runner=subprocess.run) -> str:
    days = max(1, min(int(days), 14))
    script = _SCRIPT.format(days=days)
    result = runner(["osascript", "-e", script], capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise ConnectorError(
            "Calendar lookup failed: " + (result.stderr or "osascript error").strip()
            + " (if this mentions authorization, grant Calendar access in "
            "System Settings > Privacy & Security > Automation)"
        )
    lines = []
    for raw in result.stdout.splitlines():
        parts = raw.split("|")
        if len(parts) < 2 or not parts[0].strip():
            continue
        summary, start = parts[0].strip(), parts[1].strip()
        where = parts[2].strip() if len(parts) > 2 and parts[2].strip() not in ("", "missing value") else ""
        lines.append(f"- {summary} — {start}" + (f" ({where})" if where else ""))
    if not lines:
        return f"No events in the next {days} day(s)."
    return f"Events in the next {days} day(s):\n" + "\n".join(lines)
```

`agent/tools.py`:

```python
def _check_calendar(args: dict, ctx: ToolContext) -> ToolResult:
    from lydia.connectors import calendar_mac

    try:
        return ToolResult(ok=True, content=calendar_mac.get_events(days=args.get("days", 2)))
    except ConnectorError as exc:
        return ToolResult(ok=False, content=str(exc))
```

```python
        ToolSpec(
            "check_calendar", "Upcoming events from the user's macOS Calendar.",
            {
                "type": "object",
                "properties": {"days": {"type": "integer", "description": "How many days ahead to look (1-14, default 2)"}},
                "required": [],
            },
            "safe", _check_calendar,
        ),
```

Note: `runner(...)` in `get_events` passes `timeout=30`, and the fake runners in tests accept `**kwargs`, so this is test-compatible.

- [ ] **Step 4: Run to green**, full suite.
- [ ] **Step 5: Commit**: `Add macOS Calendar connector`

---

### Task 3: open_app tool + voice wiring (tools, model, thinking off)

**Files:**
- Modify: `src/lydia/agent/tools.py` (`_open_item` + ToolSpec `"open_app"` after `check_calendar`)
- Modify: `src/lydia/voice/assistant.py` (VOICE_TOOLS, VOICE_SYSTEM_PROMPT, think=False)
- Modify: `src/lydia/cli/main.py` (`listen_run`: use `config.voice_model` when set)
- Test: extend `tests/test_agent_tools.py`, `tests/test_voice_assistant.py`

**Interfaces:**
- Produces: ToolSpec `"open_app"` (safe): args `target` (required string: app name or file/folder path). Voice loop consumes `config.voice_model`.

- [ ] **Step 1: Failing tests**

`tests/test_agent_tools.py`:

```python
def test_open_app_launches_named_app(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(tools.subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    result = tools._open_item({"target": "Spotify"}, ctx(tmp_path))
    assert result.ok and calls == [["open", "-a", "Spotify"]]


def test_open_app_opens_existing_path(tmp_path, monkeypatch):
    doc = tmp_path / "resume.pdf"
    doc.write_text("x")
    calls = []
    monkeypatch.setattr(tools.subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    result = tools._open_item({"target": str(doc)}, ctx(tmp_path))
    assert result.ok and calls == [["open", str(doc)]]


def test_open_app_missing_path_fails(tmp_path):
    result = tools._open_item({"target": str(tmp_path / "nope.txt")}, ctx(tmp_path))
    assert not result.ok


def test_open_app_failure_reports(tmp_path, monkeypatch):
    monkeypatch.setattr(tools.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "Unable to find application"))
    result = tools._open_item({"target": "NotARealApp"}, ctx(tmp_path))
    assert not result.ok and "NotARealApp" in result.content
```

(`tools.py` already imports `subprocess` at module level; verify that, and add the import if it doesn't.)

`tests/test_voice_assistant.py`:

```python
def test_voice_registry_includes_new_tools():
    names = {spec.name for spec in assistant.voice_registry()}
    for expected in ("check_weather", "check_calendar", "open_app", "find_files", "read_file"):
        assert expected in names
    assert "write_file" not in names and "run_command" not in names


def test_voice_turn_disables_thinking(no_real_push, monkeypatch):
    seen = {}
    real = assistant.run_agent_turn
    def spy(**kwargs):
        seen.update(kwargs)
        return real(**kwargs)
    monkeypatch.setattr(assistant, "run_agent_turn", spy)
    spoken, chimes = [], []
    _run(FakeClient(["ok"]), "hello", spoken, chimes)
    assert seen["think"] is False
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement**

`agent/tools.py`:

```python
def _open_item(args: dict, ctx: ToolContext) -> ToolResult:
    """Open a macOS app by name, or a file/folder by path, via `open`."""
    target = str(args.get("target", "")).strip()
    if not target:
        return ToolResult(ok=False, content="Nothing to open: 'target' is required.")
    from pathlib import Path as _Path

    looks_like_path = "/" in target or target.startswith("~")
    if looks_like_path:
        path = _Path(target).expanduser()
        if not path.exists():
            return ToolResult(ok=False, content=f"No such file or folder: {target}")
        cmd = ["open", str(path)]
    else:
        cmd = ["open", "-a", target]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return ToolResult(ok=False, content=f"Could not open {target}: {(result.stderr or '').strip()}")
    return ToolResult(ok=True, content=f"Opened {target}.")
```

```python
        ToolSpec(
            "open_app", "Open a macOS application by name, or a file/folder by path.",
            {
                "type": "object",
                "properties": {"target": {"type": "string", "description": "App name (e.g. 'Spotify') or a file/folder path"}},
                "required": ["target"],
            },
            "safe", _open_item,
        ),
```

`voice/assistant.py`:

```python
VOICE_TOOLS = {
    "check_email", "check_canvas", "check_stocks", "check_news", "notify",
    "check_weather", "check_calendar", "open_app", "find_files", "read_file",
}
```

Update the system prompt and force thinking off in the `run_agent_turn` call (`think=False` instead of `think=config.think_flag`):

```python
VOICE_SYSTEM_PROMPT = (
    "You are Lydia, a spoken voice assistant. The user talked to you out loud "
    "and your reply will be read aloud by text-to-speech. Answer in one to "
    "three short sentences of plain conversational prose — no markdown, no "
    "lists, no code, no emoji. You have tools for live data (email, Canvas, "
    "calendar, weather, stocks, news), for finding and reading the user's "
    "files, and for opening apps or files — use them whenever the request "
    "needs them, without asking permission. Otherwise just answer."
)
```

In `cli/main.py`, `listen_run` replaces `model = resolve_model(client, config)` with:

```python
        model = config.voice_model or resolve_model(client, config)
```

- [ ] **Step 4: Run to green**, full suite.
- [ ] **Step 5: Commit**: `Add weather, calendar, and open-app abilities to voice`

---

### Task 4 (controller): machine setup, docs, verification

- [ ] `lydia config set voice_model qwen3.5:4b` (4b verified for tool calls); `ollama list` confirms it's pulled.
- [ ] README voice section: mention new abilities + one-time Calendar automation permission; CLAUDE.md: note `voice_model`/`weather_location` keys. Commit `Document expanded voice abilities`.
- [ ] Live checks: `lydia ask "what's the weather right now" --yes` (real Open-Meteo); `lydia ask "what's on my calendar this week" --yes` (triggers the macOS Calendar permission prompt); `lydia listen` + "Hey Lydia, open Spotify".
