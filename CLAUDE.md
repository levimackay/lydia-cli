# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Lydia is a local AI coding agent CLI — a personal, API-key-free alternative to
Claude Code / Cursor, built on top of a local [Ollama](https://ollama.com)
daemon, with an optional FastAPI server (`server/`) so Ollama can run on a
more powerful remote machine instead. It is a portfolio project for Levi
(CS student), so code quality, tests, and a clean architecture matter more
than shipping speed.

This is a **two-package monorepo**: `src/lydia` (the CLI, always needed)
and `server/lydia_server` (optional, a separate installable package that
depends on `lydia` as a library). Both are typically installed into one
shared venv for local dev.

## Commands

```bash
# Install both packages (editable) into one shared venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pip install -e "server/[dev]"

# Run the CLI package's test suite (381 tests)
.venv/bin/pytest
.venv/bin/pytest tests/test_agent_loop.py                                   # one file
.venv/bin/pytest tests/test_agent_loop.py::test_tool_call_then_final_answer # one test

# Run the server package's test suite (61 tests) — has its own pyproject.toml,
# so run it from server/, not the repo root
cd server && ../.venv/bin/pytest

# Run the CLI itself (after install -e, `lydia` is also on PATH via a symlink
# into /opt/homebrew/bin)
lydia                      # interactive chat REPL in the current project
lydia ask "question"       # one-shot, no tools, good for smoke-testing the LLM layer
lydia analyze              # project scanner output
lydia config show

# Run the server locally (needs a LYDIA_SERVER_TOKEN or it refuses to start)
LYDIA_SERVER_TOKEN=dev-token .venv/bin/lydia-server
```

There is no separate lint/format command configured yet.

### Testing against the real Ollama daemon

Unit tests never touch the network (`httpx.MockTransport` for the LLM client,
tmp_path repos for git/filesystem tools) — `pytest` should never require
Ollama to be running. To manually exercise the real thing:

```bash
ollama list                 # confirm a model is pulled; qwen3.5 variants are what's tested locally
lydia config set think off  # qwen3 is a thinking model; off = much faster manual testing
```

When testing the agent loop end-to-end (tool calls + confirmation prompts),
piping input via `printf ... | lydia` is unreliable — Rich's `Confirm.ask`
and `prompt_toolkit` fight over a non-tty stdin and the confirm dialog will
spuriously EOFError (it fails *safe*, i.e. auto-declines, so this looks like
a bug but isn't one). If you need to script an end-to-end test of a
confirmation flow, drive it through a real pty (see git history around the
Milestone 3 commit for a working example using Python's `pty` module), not a
plain pipe. `lydia ask "..." --yes` sidesteps this entirely for scripted
end-to-end checks since it never needs a y/n prompt in the first place.

To manually verify the client/server split end-to-end (not just
`server/tests/`'s fake-provider unit tests): run a real server locally
against the real local Ollama, point a `lydia` project config at it, and
confirm both that it works *and* that the server's own log only shows
`/v1/chat`/`/v1/models` traffic — never any file access — which is the
actual proof that tool execution stayed client-side:

```bash
LYDIA_SERVER_TOKEN=dev-token .venv/bin/lydia-server &
lydia config set server_url http://127.0.0.1:8000 --project
lydia config set api_key dev-token --project
lydia ask "read some_file.py and summarize it" --yes
```

## Architecture

Layering, outer to inner — each layer only depends on the ones below it:

```
cli/      Typer commands + Rich rendering + prompt_toolkit REPL   (depends on: agent, llm, config, context, automations, voice)
agent/    orchestration: system prompt, tool registry, the loop   (depends on: llm, tools, config)
voice/    speech input/output: mic capture, Whisper STT, macOS `say` TTS, wake-word detection (depends on: agent, llm, config)
tools/    pure functions that touch the filesystem/shell/git      (depends on: nothing else in lydia)
llm/      ModelClient protocol + OllamaClient + RemoteClient       (depends on: nothing else in lydia)
context/  project scanner + semantic index (chunk/embed/search)   (depends on: llm (embeddings), database)
automations/ scheduled recipes: model execution, storage, heartbeat (depends on: agent, connectors, llm, config)
database/ SQLite storage for the semantic index                  (depends on: nothing else in lydia)
config/   layered JSON settings                                   (depends on: nothing else in lydia)

server/   (separate package, lydia_server/) — FastAPI inference proxy.
          Depends on lydia as a library (reuses OllamaClient directly as
          its provider). Never touches tools/, agent/, or cli/ — tool
          execution always stays client-side. See server/README.md.
```

`llm/` is two concrete clients behind one structural interface
(`llm/protocol.py::ModelClient`): `OllamaClient` talks to a local Ollama
daemon directly, `RemoteClient` talks to a `server/` instance over HTTPS.
Everything above `llm/` — `agent/loop.py`, `agent/tools.py`,
`context/indexer.py`/`retriever.py` — type-hints against `ModelClient`,
never a concrete class, and is handed whichever one `llm/factory.py::build_client`
constructs based on `config.server_url`. This is *the* seam that makes
local-only and client/server usage the same codepath everywhere except one
factory function.

`automations/` stores JSON recipes (model-parsed from plain English), runs the
model in a stripped-down mode to execute them on a schedule, and persists
runtime state. It imports `agent/`, `connectors/`, `llm/`, and `config/`, but
never `cli/` — this keeps it usable from the server (future) and keeps CLI
concerns separate from automation concerns. `store.py` holds the single
`AUTOMATIONS_DIR` constant (`~/.lydia/automations/`), which tests patch for
hermetic storage without filesystem side effects.

`voice/` is the always-listening voice assistant — `run_loop` orchestrates the
lifecycle (wake detection, transcription, model inference, speech synthesis) and
is invoked from `cli/main.py::listen_run`. It uses `faster_whisper` for
speech-to-text and `piper` for synthesis, both running locally. Tests never
open the microphone or load models (test audio frames and mocked model clients
instead); the CLI tests verify wiring only. The launchd plist generation for
always-on listening lives in `cli/scheduler.py`, mirroring the automations and
briefing heartbeat patterns.

`tools/` must stay UI- and agent-agnostic: every function takes a project
root and plain arguments and returns plain data or raises `ToolError`. It
knows nothing about confirmation prompts, risk levels, or the LLM.

`agent/tools.py` is where UI-independent policy lives: it wraps each
`tools/*` function in a `ToolSpec` with a JSON schema (sent to the model)
and a risk tier (`safe` / `confirm` / `command`). Confirmation itself is a
callback (`ToolContext.confirm`) injected from outside — `agent/` never
imports `rich` or `cli`. The Rich-based confirm dialog lives in
`cli/ui.py::confirm` and is wired in by `cli/chat.py`.

`agent/loop.py::run_agent_turn` is the plan → call tool → observe → respond
loop. It's intentionally free of any I/O side effects it doesn't own: it
takes a `stream_fn` callable to render (or silently drain) the model's
streaming output, and `on_tool_call` / `on_tool_result` hooks for the
console. This is what makes it testable with a fake client
(`tests/test_agent_loop.py::FakeClient`) instead of hitting Ollama.

### The Ollama integration gotchas

- Ollama's *thinking* models (qwen3.5, deepseek-r1, ...) stream reasoning in
  a separate `message.thinking` field, not `message.content`. If you only
  read `content` the reply looks empty until the model finishes "thinking".
  Both fields are parsed in `llm/client.py::parse_chat_line` and rendered
  in `cli/ui.py` (dimmed, collapsing preview).
- Tool calls arrive as one complete (non-streamed) `message.tool_calls` list
  in a single chunk, even when `stream: true` — they are never token-by-token
  streamed. See `llm/types.py::ToolCall` and the parsing in
  `llm/client.py::parse_chat_line`.
- Ollama unloads a model from memory 5 minutes after its last request by
  default, and reloading costs several seconds — noticeable as a stall on
  the first message of a new session. `config.keep_alive` (default `30m`)
  is passed on every `chat_stream` call specifically to avoid this; don't
  drop it when adding a new call site.
- Not every model that looks like it supports tool calling actually wires
  it into Ollama's structured `message.tool_calls` field — some (e.g.
  `qwen2.5-coder:7b`) write the call as plain JSON text inside
  `message.content` instead, which `run_agent_turn` never parses, so the
  model silently never uses any tool. Before recommending a model as a
  default, verify empirically with a trivial curl call, not by assumption:
  ```
  curl -s http://localhost:11434/api/chat -d '{"model":"MODEL","stream":false,
  "messages":[{"role":"user","content":"weather in Paris? use the tool"}],
  "tools":[{"type":"function","function":{"name":"get_weather","description":"x",
  "parameters":{"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}}}]}'
  ```
  and check the response has a `tool_calls` field on `message`, not JSON text in `content`.

### The client/server wire format

`llm/client.py` has three module-level functions that exist specifically so
the wire format is defined exactly once, used by both `OllamaClient` (talks
to Ollama) and `RemoteClient` (talks to `server/`, which itself talks to
Ollama and passes the shape through): `build_chat_payload` (request body),
`parse_chat_line` (client-side: NDJSON line → `ChatChunk`), and
`serialize_chat_chunk` (server-side: `ChatChunk` → NDJSON line, the exact
inverse — `tests/test_client.py::test_serialize_chat_chunk_round_trips_through_parse_chat_line`
pins this). If you change one, check whether the other needs to change too.

`server/lydia_server/api/v1.py::get_provider` deliberately does *not* use a
FastAPI yield-dependency for the provider, even though that's the more
idiomatic pattern for setup/teardown. For the streaming `/v1/chat` route, a
yield-dependency's teardown runs as soon as the endpoint function returns
the `StreamingResponse` object — which is *before* the body has actually
streamed — so it would close the provider's connection mid-stream. The
provider is closed explicitly inside the generator's `finally` instead.

### Path safety

Every tool that touches the filesystem resolves paths through
`tools/paths.py::resolve_within`, which refuses anything that escapes the
project root (`..`, absolute paths elsewhere). Don't bypass this by calling
`Path` directly on user/model-supplied paths inside a new tool.

### Config layering

`config/settings.py::load_config` merges `~/.lydia/config.json` (global) then
`<project>/.lydia/config.json` (project, found by walking up for a `.lydia/`
or `.git/` directory) — project wins. Unknown keys are ignored with a
warning rather than erroring, so old config files don't break on upgrade.

## Current state and what's next

See `README.md` for the user-facing feature list and `ROADMAP.md` for the
detailed next-steps plan. Short version: Milestones 1 (CLI + streaming
chat), 2 (semantic retrieval), 3 (agent loop + tool calling + git), 6
(persistent project memory), the client/server split (`server/`, remote
inference over Tailscale) with connection pooling and real SQLite-backed
multi-user token storage on top of it, are all done. What's left is
mostly deferred server work that the current design doesn't block but
doesn't need yet (non-Ollama providers, a task queue, AMD GPU
verification on the actual target hardware) plus M7 (plugins, no design
started). Check `ROADMAP.md` before picking up new work — it has
file-level pointers and the reasoning behind past ordering decisions
(e.g. why M3 shipped before M2).

## Standing preferences for this repo

- **Never add a `Co-Authored-By: Claude` (or any Claude/Anthropic)
  attribution trailer to commit messages here.** The user wants to be the
  sole contributor shown on GitHub — this was explicitly requested and
  enforced once already by rewriting pushed history to strip it.
