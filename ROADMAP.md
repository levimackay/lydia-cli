# Roadmap

Status snapshot and a concrete plan for what's next. Written so either Levi
or a future Claude Code session can pick up any item without re-deriving
context — each one names the files to touch and what "done" looks like.

## Done

- **M1 — Core CLI.** Typer commands (`lydia`, `ask`, `analyze`, `models`,
  `init`, `config show/set`), a `prompt_toolkit` REPL with history and slash
  commands, Rich streaming Markdown rendering, layered JSON config, model
  auto-selection preferring installed coder models, thinking-model support,
  a gradient ASCII banner.
- **M3 — Agent loop.** Native Ollama tool calling; tools for
  read/list/search/write/delete file, `run_command` with a dangerous-command
  classifier and a permission-mode policy, and git status/diff/add/commit/
  push. File writes/deletes/commits/pushes always show a diff or message and
  require y/n approval; writes/deletes keep a timestamped backup. All
  filesystem tools refuse to touch paths outside the project root.
- **M6 — Persistent project memory.** `agent/facts.py` stores a curated,
  capped list of facts at `.lydia/memory.json` (separate from the raw
  session transcript in `agent/memory.py`, which is a log, not something fed
  back into future conversations). Facts are folded into the system prompt
  via `agent/prompts.py::build_system_prompt`. Three ways to add one: the
  model calling the `remember` tool mid-conversation, `/remember <fact>` /
  `/memory` / `/forget <n>` slash commands in chat, or `lydia memory
  add/list/forget` outside of chat. Verified end-to-end: a fact added in one
  process is present in a fresh process's system prompt with no extra steps.
- **CI.** `.github/workflows/test.yml` runs the full suite on Python
  3.11-3.13 for every push/PR. Verified against a clean clone with no
  pre-existing git identity — the git-tool tests set repo-local identity
  themselves, so no CI-side git config is needed.
- **M2 — Retrieval for large repos.** `context/indexer.py` chunks source
  files into language-agnostic ~60-line windows (snapped to the nearest
  blank line within a short lookahead, so boundaries usually land between
  functions) and embeds each one via Ollama (`nomic-embed-text`, 768-dim).
  `database/sqlite.py` stores chunks + embeddings as float32 blobs in
  `.lydia/index.sqlite3`. Re-indexing is incremental — a file is only
  re-embedded if its content hash changed since the last index. New safe
  tool `search_semantic` in `agent/tools.py`, offered alongside literal
  `search_code`; it reports "not indexed yet" cleanly if `lydia index`
  hasn't been run. Verified end-to-end: indexed a real project, confirmed
  incremental re-runs skip unchanged files and pick up changed/deleted
  ones, and confirmed the real agent loop (not just the retriever in
  isolation) chooses `search_semantic` correctly and gives the right
  answer against a live Ollama daemon, 3/3 runs.
- **Undo command.** `lydia restore list` / `lydia restore apply <n>`.
  Fixed a real bug along the way — backups were previously named
  `{stamp}-{filename}` with no directory info, so two files with the same
  name in different directories (e.g. `src/utils.py` and `tests/utils.py`)
  would silently collide. Backups now live at
  `.lydia/backups/{stamp}/{original/relative/path}`, mirroring the
  project tree, so restoring is unambiguous.
- **`--yes` / non-interactive mode.** `lydia ask "..." --yes` gives `ask`
  full tool access via `ui.auto_confirm`, which approves everything except
  tools/commands flagged dangerous (no human present to approve real
  danger, so it fails safe rather than approving blindly). Plain `lydia
  ask` without `--yes` is unchanged — still tool-free chat.
- **Automations engine (M5, 2026-07-17).** Plain-English task scheduling: `lydia
  automate "every morning at 8, check my email and canvas"` parses a natural-
  language description into a JSON recipe with triggers (time-of-day or events
  like new email), steps the model runs, and notification styles (`always`,
  `if_important`, `never`). Recipes live at `.lydia/automations/{name}.json`.
  A launchd heartbeat (`lydia automations schedule enable`) runs every 5 minutes
  (configurable) to check if any automations are due; missed ticks on sleep are
  caught up on wake. Notifications go to macOS via `ntfy` (requires one-time
  auth setup) or a webhook. The model runs in a stripped-down mode — deterministic,
  fast, only the tasks you defined. **Local model setup note:** if `server_url`
  is unset (local Mac Ollama), verify tool-calling support empirically per
  `CLAUDE.md` before trusting a newly pulled model; not every model that looks
  like it supports tools actually wires them into Ollama's structured field.
  **Mac sleep note:** Prevent Mac sleep during scheduled runs to avoid heartbeat
  delays; a periodic wake signal (e.g. `pmset`) or caffeinate wrapper can ensure
  timely ticks, but launchd's catch-up on wake still runs missed automations
  faithfully.
- **More CLI-level tests.** `tests/test_cli_commands.py` covers `analyze`,
  `init`, `config show/set`, `restore list/apply`, and `--version` via
  `CliRunner`. `ask`/`models`/the chat REPL are deliberately not covered
  this way since they need a live Ollama daemon — see "Testing against the
  real Ollama daemon" in `CLAUDE.md` for how those get verified instead.
- **Cross-platform audit.** Checked (not run): grepped the source for
  hardcoded macOS paths, unix-only path joins, and `os.name`/`sys.platform`
  branches — none found beyond `cli/scheduler.py`; everything else routes
  through `pathlib`. The one real, unavoidable limitation:
  `tools/terminal.py::run_command` uses `subprocess.run(..., shell=True)`,
  which invokes `cmd.exe` on Windows, not bash — so unix-style commands a
  model generates (`ls`, `grep`, `rm -rf`) won't translate as-is. This has
  never actually been run on Windows or Linux; "checked via static
  analysis" is not the same claim as "tested," and the distinction matters
  if you're about to rely on it.
- **Cross-platform scheduled briefings.** `cli/scheduler.py` was macOS-only
  (`launchd`) and crashed with a raw `FileNotFoundError` on any other OS
  (reported in #1). It now has a Linux backend too (`systemd --user`
  service + timer, enabled via `systemctl --user`), selected automatically
  by `platform.system()`; Windows still raises a clear `ScheduleError`
  instead of a traceback, since neither backend applies there.
  `--notify`'s desktop notification is still macOS-only (`osascript`) —
  a `notify-send` equivalent for Linux is a natural follow-up.
- **Client/server split.** New `server/` package (FastAPI) so Ollama can
  run on a separate, more powerful machine (e.g. a gaming PC with a real
  GPU) while `lydia` keeps running from a laptop with no change in feel.
  Resolved design fork: tool execution (file edits, git, shell) stays
  **client-side** always — the server is purely an inference proxy
  (`/v1/health`, `/v1/models`, `/v1/chat`, `/v1/embed`), never touches a
  filesystem. This means no WebSockets are needed (confirmation prompts
  never have to interrupt the server mid-stream) and a chat turn keeps the
  same shape Ollama's own `/api/chat` already has.
  - `llm/protocol.py::ModelClient` — the structural interface both
    `OllamaClient` (local) and `RemoteClient` (server/lydia_server, over
    HTTPS + bearer auth) satisfy; everything downstream (`agent/loop.py`,
    `agent/tools.py`, `context/indexer.py`/`retriever.py`) type-hints
    against this, not a concrete class.
  - `llm/factory.py::build_client(config)` picks which one to construct
    based on whether `config.server_url` is set — local-only usage is
    completely unaffected (zero config changes needed).
  - `llm/client.py` gained three module-level helpers so the wire format
    exists in exactly one place: `build_chat_payload`, `parse_chat_line`
    (client-side parsing), `serialize_chat_chunk` (server-side, the
    inverse) — `server/lydia_server/api/v1.py` reuses `OllamaClient`
    directly as its provider rather than reimplementing Ollama-calling
    logic.
  - Auth: bearer token, `{token: user_id}` mapping sourced from env vars
    (`LYDIA_SERVER_TOKEN` / `LYDIA_SERVER_TOKENS`) — swappable for a real
    multi-user store later without changing the auth dependency's
    interface. HTTPS via `tailscale cert` (see `server/README.md`) rather
    than a self-signed cert, since there's no public domain to get a
    normal one for a Tailscale-only host.
  - Verified end-to-end against the real Ollama daemon: started the real
    server locally, pointed a real `lydia` session at it, ran a full
    chat + tool-call turn (`read_file`) through the whole stack, confirmed
    via server logs that only `/v1/chat` traffic occurred — no file access
    — proving tool execution genuinely stayed client-side. Also confirmed
    local-only mode (`server_url` unset) is completely unaffected.
  - 154 tests total (140 in the CLI package, 14 in `server/`, run
    separately since they're two installable packages) — server tests run
    against a fake `ModelClient` double, no real Ollama needed.
  - Full design reasoning, API shapes, and the folder structure live in
    `server/README.md` and the plan this was built from.
- **Connection pooling for the server's Ollama provider (2026-08-22).**
  `api/v1.py::get_provider` used to construct a fresh `OllamaClient` — a
  fresh `httpx.Client`, a fresh TCP connection — per request, then close
  it in a `finally` block once that request finished. Now
  `services/ollama_provider.py::get_shared_provider` returns one
  process-wide `OllamaClient` per `ollama_host`, and routes no longer
  close what they're handed; the pooled client is closed exactly once, in
  `main.py`'s new `lifespan` context manager, on actual server shutdown.
  `get_provider` (the FastAPI dependency) stays a plain function, not a
  yield-dependency, for the same reason it always was — see the
  docstring. Verified against a real Ollama daemon: started the real
  server, made two consecutive `/v1/models` requests (both succeeded,
  proving the shared client survives being reused, not just usable once)
  and a real streaming `/v1/chat` completion, then a clean shutdown. 8 new
  tests: `test_ollama_provider.py` (same host → same instance, different
  host → different instance, `close_shared_providers` actually closes the
  underlying connection and clears the cache so the next call builds
  fresh) and `test_main.py` (the app's `lifespan` actually calls
  `close_shared_providers` on shutdown, not just in theory) — plus the
  existing `/v1/models`, `/v1/embed`, `/v1/chat` tests in `test_v1.py`
  updated from asserting the old close-after-every-request behavior to
  asserting the new share-across-requests one.
- **Real multi-user token storage (2026-08-22).** `config/settings.py`'s
  `{token: user_id}` dict, built once from env vars at startup, is now a
  SQLite-backed `TokenStore` (`database/tokens.py`) — tokens can be
  added, expired, and revoked while the server keeps running, with no
  restart. `LYDIA_SERVER_TOKEN`/`LYDIA_SERVER_TOKENS` are still the
  bootstrap mechanism (re-seeded into the store on every startup, so a
  fresh single-user setup needs zero extra steps), stored at
  `~/.lydia/server/tokens.sqlite3` by default (override with
  `LYDIA_SERVER_TOKENS_DB`). Tokens are hashed (SHA-256) before being
  written to disk — the env-var approach this replaces never touched
  disk at all, so this is what keeps the new file from being a plaintext
  credential dump. `auth/bearer.py`'s `settings.tokens.get(token)` call
  site and `main.py`'s `if not settings.tokens:` startup guard both
  needed zero changes — `TokenStore` implements `.get()` and `__len__()`
  to match, exactly the seam the original design left for this.
  New `lydia-server-token add/revoke/list` CLI (`cli.py`) for managing
  tokens without touching SQLite directly; `add` prints the raw token
  exactly once (only its hash is ever stored, so it can't be shown
  again). Verified against a real running server, not just tests: added
  a token via the CLI while the server was already running and used it
  immediately with no restart, then revoked a different token that was
  actively working and confirmed the very next request with it got a
  401 — proving both directions (grant and revoke) take effect live. 25
  new tests: `test_token_store.py` (add/get/expire/revoke/re-add/persist-
  across-reopen, plus a direct check that raw tokens never land in the
  database), `test_settings.py` (env-var seeding, and specifically that
  a runtime-added token survives a simulated restart with no env var
  naming it), `test_cli.py` (all three subcommands, including that
  revoked/listed output never contains a raw token).
- **Voice mode (2026-07-18).** Always-listening voice assistant — say "Hey Jarvis"
  to trigger the model, ask a question, and hear a spoken reply. `lydia listen`
  runs the loop in the foreground; `lydia listen enable/disable/status` manage
  a launchd background agent. Uses `faster_whisper` for speech-to-text (locally,
  ~150MB model, auto-downloaded), and `piper` for synthesis. Wake word and voices
  are configurable. Logs to `~/.lydia/listen.log` when running in the background.
  **Stretch goals:** support custom wake models (current default is Whisper's
  built-in voice-activity detection), `piper` voice selection UI, follow-up
  windows (stay listening after a reply without re-saying the wake word).

**Model gotcha found while shipping M2:** not every model that emits
reasonable-looking tool-call JSON actually wires it into Ollama's
structured `tool_calls` field — `qwen2.5-coder:7b` writes the call as
plain text in `message.content` instead, which `run_agent_turn` never
parses, so it silently never uses *any* tool. Confirmed via a direct
`/api/chat` call with a trivial tool before trusting it as a default.
Verify tool-calling support empirically (a simple curl test, not vibes)
before recommending a new default model — see `CLAUDE.md` for the check.

M3 was done before M2 on purpose: it was the part that turns Lydia into an
*agent* rather than a chatbot, and every repo tested against so far fits
comfortably in a model's context window, so retrieval wasn't yet the
bottleneck as of M3. M2 removes that ceiling for larger repos.

## Next up

### M7 — Plugins (stretch)

Lowest priority; only worth doing once the server is proven out in daily
use. Original ideas from project scoping: VS Code extension, browser
automation, web search, doc lookup, CI/CD integration. No design work has
started — if you pick this up, start by defining what a "plugin" actually
extends (a new tool? a new slash command? both?) before writing code.

### Deferred server work

Not started, not blocked by the current design — see `server/README.md`
and `ROADMAP.md`'s history for the client/server split entry above:

- **Non-Ollama providers** (OpenAI, Anthropic, Gemini) — opt-in, bring
  your own key, never the default (that would compromise the whole "no
  API keys required" premise for anyone not opting in). Each is just a
  new class satisfying `lydia.llm.protocol.ModelClient`; `services/ollama_provider.py`
  is the only file that currently decides which one gets constructed.
- **Task queue / background jobs, project indexing service, vector DB
  beyond the current SQLite approach, web dashboard.** All from the
  original project scoping; none designed yet.
- **AMD GPU acceleration is unverified** on the actual target hardware
  (RX 9060 XT) — Ollama's AMD support runs through ROCm, better on Linux
  than Windows. `ollama ps` should show GPU usage during a request; if it
  silently falls back to CPU, the server won't actually be faster than
  local inference on a decent laptop.

## Smaller polish items (no milestone, pick up anytime)

- **Packaging.** `pyproject.toml` is set up for `pip install -e .`; hasn't
  been published anywhere (PyPI, or even a simple `brew tap`) so the README
  install instructions still say "clone this repo." Same applies to
  `server/pyproject.toml`.
