# Lydia Automations: Design

Date: 2026-07-17
Status: approved (design), pending implementation plan

## Why

Levi wants Lydia to grow from an on-demand CLI agent into a Jarvis-style
assistant that acts without being asked: scheduled check-ins, phone alerts,
and n8n-style multi-step workflows, all with zero recurring cost, running on
his own hardware. n8n Cloud pricing (and the hassle of self-hosting a
separate product) is the thing being replaced: Lydia already owns the
connectors, the agent loop, and the scheduling seed (the briefing system),
so the automation engine belongs inside her.

Hard constraint for the next ~6 weeks: the gaming-PC GPU server will be
off (Levi is away for the summer). Everything here must run fully on the
MacBook Air with local Ollama. This requires **no code changes**: when
`server_url` is unset, `llm/factory.py::build_client` already builds a
local `OllamaClient`. The PC becomes a drop-in upgrade again in the fall.

Out of scope for this build (each needs its own spec later):

- **Unattended coding tasks** (agent editing files with nobody watching,
  which needs a dedicated safety design).
- **Voice** (wake word / STT / TTS).
- **PC-side deployment changes**: nothing server-side changes at all.

## What exists today (foundation, verified in code)

- `cli/scheduler.py`: launchd plist management, but hardcoded to one job
  (`com.lydia.briefing` → `lydia briefing run --notify`, one daily time).
- `cli/briefing.py` holds the execution pattern to generalize: gather
  connector output deterministically (no model tool-choice, so no
  hallucinated sources), one model turn to synthesize, `osascript`
  notification, state at `~/.lydia/briefing.json`.
- `agent/tools.py::build_registry`: connectors already exposed as agent
  tools (`check_email`, `check_canvas`, `check_stocks`, `check_news`).
- Non-interactive agent runs already work: `ToolContext` +
  `ui.auto_confirm` (auto-declines anything flagged dangerous) +
  `run_agent_turn` with the silent `default_stream_fn`.
- Config layering in `config/settings.py` (add a dataclass field → it's
  loadable/settable everywhere); secrets in the OS keychain via
  `config/secrets.py`.
- No dedupe, no per-job state, no phone push, single-job scheduler. Those
  are the gaps this design fills.

## Design

### 1. Recipe model & storage

An automation is one JSON file at `~/.lydia/automations/<slug>.json`:

```json
{
  "name": "morning-briefing",
  "description": "every morning at 8, check my email and canvas and send me a briefing",
  "enabled": true,
  "trigger": {"type": "schedule", "time": "08:00"},
  "steps": [
    {"kind": "connector", "tool": "check_email", "args": {"account": "personal"}},
    {"kind": "connector", "tool": "check_canvas", "args": {}},
    {"kind": "model", "instructions": "Summarize into a short morning briefing with a checklist."}
  ],
  "notify": {"channel": "ntfy", "when": "always"}
}
```

Trigger types:

- `schedule`: daily at `HH:MM`.
- `interval`: every N minutes (`{"type": "interval", "minutes": 30}`).
- `event`: poll a connector every tick; fire only when there are **new**
  items (per the dedupe state, §5) that match an English `condition`
  (e.g. `{"type": "event", "source": "email", "account": "school",
  "condition": "from my professor or mentions a deadline"}`). The
  condition is evaluated by the model against only the new items.

Step kinds:

- `connector`: a direct call to an existing safe tool handler from
  `build_registry` (the briefing pattern: deterministic, model never
  invents data). Only tools with risk `"safe"` are allowed here.
- `model`: one chat turn given the accumulated step outputs plus
  `instructions`. Its output becomes the automation's result text.

`notify.channel` is `"ntfy"`, `"mac"` (osascript notification), or
`"none"`. `notify.when` is `"always"` or `"if_important"`. The latter
lets the model step end its output with a literal `NOTHING_TO_REPORT`
marker to suppress the push (deterministic string check, not a second
model call). Validation rejects `"if_important"` on a recipe with no
`model` step, since nothing could emit the marker.

### 2. Creation UX (plain English in)

- `lydia automate "<english>"` and `/automate <english>` in chat.
- The model parses the sentence into the recipe schema (single chat turn,
  JSON output validated against the schema; on invalid JSON, one retry
  with the validation error shown to the model, then fail with a clear
  message).
- Lydia prints a human-readable echo ("Every day at 08:00: check email
  (personal) + Canvas → summarize → push to phone") and saves only on
  y/n confirmation. The original sentence is kept in `description`.
- Management commands: `lydia automations list | show <name> |
  run <name> | enable <name> | disable <name> | remove <name>`.
  `run` executes immediately (same code path as the tick) for testing.

### 3. Execution: the heartbeat tick

- One new launchd job, label `com.lydia.automations`, using
  `StartInterval` (default 300 s) → runs `lydia automations tick`.
  Managed by `lydia automations schedule enable|disable`, generalizing
  `cli/scheduler.py` (parameterize label / program arguments / interval
  vs. calendar; `com.lydia.briefing` keeps working unchanged).
- The tick: load recipes + state → for each enabled automation decide
  due-ness →
  - `schedule`: due when now ≥ time and it hasn't run yet today
    (catch-up: a Mac asleep at 08:00 still gets the briefing at the
    first awake tick).
  - `interval`: due when now − last_run ≥ interval.
  - `event`: always poll; fire only on new matching items.
- Execution runs steps in order, briefing-style, then notifies.
- Every run appends a record (start, duration, ok/error, result snippet)
  to `~/.lydia/automations/runs.json`, capped (keep last ~200 entries).
- Errors: an automation that throws is recorded and pushes one
  "automation <name> failed" notice, rate-limited to at most one
  failure push per automation per 6 hours so a broken job can't spam.
- Concurrency guard: a lockfile (`~/.lydia/automations/tick.lock`) makes
  overlapping ticks exit early rather than double-run.

### 4. Phone push via ntfy

- New `connectors/ntfy.py`, following the connector contract (pure
  function, `ConnectorError`, injectable transport):
  `send_push(topic, title, message, priority="default")` POSTs to
  `https://ntfy.sh/<topic>`.
- The topic is generated randomly on first setup (`lydia auth login
  ntfy` prints a QR-able topic name and subscription instructions) and
  stored in the keychain via `config/secrets.py` (key `NTFY_TOPIC`).
  Anyone who knows the topic can read the messages, so it is a secret.
- New agent tool `notify` (risk `"safe"`) in `build_registry`, wrapping
  the connector, so interactive chat and `--yes` runs can push to the
  phone too, not just automations.
- The existing `osascript` Mac notification stays as a secondary local
  channel (used when `notify.channel` is `"mac"`).

### 5. State & dedupe

- `~/.lydia/automations/state.json` (following the `agent/facts.py`
  persisted-JSON pattern): per-automation `last_run` timestamp, and
  per-event-trigger `seen_ids` (capped list, newest kept).
- Connector change in `email_gmail.py` / `email_outlook.py`:
  `EmailSummary` gains a stable `id` field (Gmail already fetches
  `ref["id"]` and discards it; Outlook messages carry `id` in the Graph
  response). Canvas `Assignment` gains `id` the same way. Purely
  additive; existing formatting functions unchanged.

### 6. Summer-brain setup (docs only, no code)

- README/ROADMAP note: unset `server_url` (or never set it) → local
  Ollama. Pull a small model on the Mac and **verify tool-calling
  empirically** using the existing check documented in `CLAUDE.md`
  (the qwen2.5-coder:7b gotcha) before trusting it.
- To keep ticks running with the lid closed, the Mac must be prevented
  from sleeping while on power (System Settings, or `caffeinate`);
  otherwise launchd catch-up behavior (§3) still runs missed schedules
  on wake, degraded but not broken.

## Module layout

New package `src/lydia/automations/`:

- `model.py`: recipe dataclasses + JSON (de)serialization + validation.
- `store.py`: load/save/list recipes and `state.json`, lockfile.
- `parser.py`: English → recipe via one model turn + schema validation.
- `runner.py`: due-ness logic, step execution, notify, run log.
- CLI wiring in `cli/main.py` (`automate`, `automations` sub-app);
  scheduler generalization in `cli/scheduler.py`; ntfy in
  `connectors/ntfy.py`; `notify` tool + `EmailSummary.id`/`Assignment.id`
  in existing files.

Each unit is independently testable with the codebase's existing
dependency-injection style (injectable clock, transport, model client).

## Testing

- Unit tests per module with fakes (fake clock for due-ness, fake
  transport for ntfy, fake `ModelClient` for parser/runner), same style
  as the existing 154 tests; no live Ollama needed in CI.
- One documented manual end-to-end check (per CLAUDE.md convention):
  create an automation in plain English against live local Ollama, run
  `lydia automations run <name>`, receive a real ntfy push on the phone,
  and observe one real scheduled firing via the tick.

## Success criteria

- "Every morning at 8, check my email and Canvas and send me a briefing"
  typed once → a push notification arrives on Levi's phone every morning
  with no further interaction, entirely on the MacBook Air, at $0.
- An event automation ("tell me when my professor emails") fires once per
  new matching email, and never re-alerts on the same message.
- Deleting/disabling an automation takes effect on the next tick; no
  orphaned launchd jobs (there is only ever the one heartbeat plist).
