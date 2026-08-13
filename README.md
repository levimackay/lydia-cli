# Lydia

[![tests](https://github.com/levimackay/lydia-cli/actions/workflows/test.yml/badge.svg)](https://github.com/levimackay/lydia-cli/actions/workflows/test.yml)

**A local AI coding agent for your terminal — no API keys, no subscriptions,
no cloud.** Lydia reads your code, answers questions about it, edits files,
runs commands, drives git, and checks its own work by running your tests,
all through a local [Ollama](https://ollama.com) model running on your own
machine. It can also work like a personal assistant — checking email,
Canvas assignments, the stock market, and AI news on a schedule.

It's a personal alternative to tools like Claude Code, Cursor's agent, or
GitHub Copilot Workspace, built for anyone who wants that workflow without
paying for API usage or sending code to a third party.

```
   ██     ██╗  ██╗   ██╗██████╗ ██╗ █████╗
  ████    ██║  ╚██╗ ██╔╝██╔══██╗██║██╔══██╗
 ██████   ██║   ╚████╔╝ ██║  ██║██║███████║
 ██████   ██║    ╚██╔╝  ██║  ██║██║██╔══██║
  ████    ███████╗██║   ██████╔╝██║██║  ██║
   ██     ╚══════╝╚═╝   ╚═════╝ ╚═╝╚═╝  ╚═╝

╭─────────────────╮
│ model  qwen3.5  │
│ project  Python │
╰─────────────────╯
Type your request, or /help for commands. Ctrl-D to exit.

Lydia (auto) > add input validation to the login handler and run the tests
```

## Why

Claude Code and similar tools are genuinely useful, but they require an API
key and send your code to a hosted model. Lydia is the same *workflow* —
an agent that reads your project, proposes changes, and asks before doing
anything risky — running entirely against models you've already pulled with
Ollama. Nothing leaves your machine. Nothing costs anything per token.

The tradeoff is real: local models on consumer hardware are smaller and
slower than frontier hosted models, so Lydia won't be as capable. It's built
for personal projects, learning, and situations where "good enough and free"
beats "best available and metered."

## Features

- **Interactive chat** with full streaming output, Markdown rendering, and
  live-updating "thinking" previews for reasoning models like Qwen3.
- **A real coding agent, not just chat.** Lydia reads files, searches your
  codebase by content (`search_code`) or by filename pattern (`find_files`),
  makes targeted edits (`edit_file`, or `multi_edit_file` for several
  changes to one file in a single diff), writes new files, runs shell
  commands, and drives git — by calling tools the model itself decides to
  use, via Ollama's native function-calling support.
- **Closes the loop on its own work.** Set a `verify_command` (e.g.
  `pytest -q`) and Lydia runs it after making changes, reading the output
  and fixing failures before calling the task done — instead of just hoping
  the edit was right.
- **A visible checklist for bigger jobs.** For multi-step tasks, Lydia
  tracks progress with a live-updating todo list, especially useful in auto
  mode where there's no confirmation prompt at every step to anchor you.
- **Three session modes** — `plan` (research only, no tool can touch
  anything), `ask` (confirm every change, the default), `auto` (skip
  confirmation for routine edits, still confirm anything destructive) —
  switchable with `/mode` or a Shift-Tab press, always visible in the prompt.
- **Nothing happens to your files or repo without a diff and a yes/no
  prompt** (outside auto mode). Writes and deletes always show what's about
  to change and keep a timestamped backup; commits and pushes always show
  the message/target first. Anything matching a destructive shell pattern
  (`rm -rf`, `git push --force`, `sudo`, piping a remote script into a
  shell, ...) is *always* confirmed, in every mode.
- **Also works as a personal assistant**, additively — check Gmail/Outlook
  email, Canvas assignments, the stock market, and AI news, composed into a
  daily briefing that can run on a schedule with a macOS notification. See
  [Personal assistant](#personal-assistant).
- **Project-aware from the first message.** A repository scanner detects
  the language mix, project type, and key manifest files, and feeds that
  into the system prompt automatically.
- **Per-project and global configuration**, so you can pin a smaller/faster
  model for one repo and a larger one for another.
- **Path-sandboxed by construction.** Every filesystem tool resolves paths
  relative to the project root and refuses anything that tries to escape it.

## Requirements

- Python 3.11+
- [Ollama](https://ollama.com), running locally, with at least one model
  pulled — a model with tool-calling support is needed for the agent
  features (Qwen3.5, Qwen2.5, Llama 3.1+ all work):

  ```bash
  ollama pull qwen3.5
  ```

## Install

```bash
git clone https://github.com/levimackay/lydia-cli.git && cd lydia-cli
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
ln -s "$PWD/.venv/bin/lydia" /opt/homebrew/bin/lydia   # or anywhere on your PATH
```

## Usage

| Command | What it does |
|---|---|
| `lydia` | Interactive agent chat in the current project |
| `lydia ask "why is this failing?"` | One-shot question, no tools, good for scripts |
| `lydia ask "..." --yes` | Same, but with full tool access — auto-approves everything except dangerous commands, for scripts/CI with no one to answer a y/n prompt |
| `lydia analyze` | Project summary: languages, size, key files |
| `lydia models` | List installed Ollama models |
| `lydia index` | Build/refresh the semantic search index (for `search_semantic`) |
| `lydia restore list` / `apply <n>` | List/restore file backups from `.lydia/backups/` |
| `lydia init` | Create `.lydia/` project config |
| `lydia config show` | Show effective (merged) configuration |
| `lydia config set model qwen3.5:9b` | Set a config value (`--project` for per-repo) |
| `lydia memory list` / `add <fact>` / `forget <n>` | View/manage facts Lydia remembers about this project |
| `lydia auth login gmail/outlook/canvas` | Connect a personal-assistant data source |
| `lydia auth status` / `logout <provider>` | Check or disconnect a connected source |
| `lydia briefing run` / `show` | Generate/print the daily personal briefing |
| `lydia briefing schedule enable --time 08:00` / `disable` | Turn the daily scheduled briefing on/off |
| `lydia automate "..."` | Create an automation from a plain-English description |
| `lydia automations list` / `show <name>` | List all automations or show one in detail |
| `lydia automations run <name>` | Execute one automation immediately (ignores its trigger) |
| `lydia automations enable/disable <name>` | Enable or disable an automation |
| `lydia automations remove <name>` | Delete an automation recipe |
| `lydia automations schedule enable` / `disable` | Turn the automations heartbeat (launchd) on/off |

Inside chat: `/help`, `/mode [plan\|ask\|auto]` (or Shift-Tab), `/model <name>`,
`/models`, `/new` (fresh conversation), `/remember <fact>`, `/memory`,
`/forget <n>`, `/exit`.

### A typical session

With `verify_command` set to `pytest -q` (see [Configuration](#configuration)):

```
Lydia (auto) > fix the bug where login accepts an empty password

› read_file(path='src/auth/login.py')
  read src/auth/login.py
› search_code(pattern='def login')
  searched for 'def login'
Found it — login() never checks that password is non-empty before
comparing the hash. Here's the fix:

› edit_file(path='src/auth/login.py', old_string='...', new_string='...')
  Updated src/auth/login.py
› run_command(command='pytest -q')
  ran `pytest -q` (exit 0)

I added a check that rejects an empty password before it ever reaches the
hash comparison, and ran the test suite to confirm — all passing.
```

In `ask` mode (the default), `edit_file` shows a diff and asks first:

```
╭──────────────── Edit src/auth/login.py ───────────────╮
│ --- a/src/auth/login.py                                │
│ +++ b/src/auth/login.py                                │
│ @@ -12,6 +12,8 @@                                      │
│  def login(username, password):                        │
│ +    if not password:                                   │
│ +        raise ValueError("password required")          │
│      user = find_user(username)                        │
╰──────────────────────────────────────────────────────────╯
Proceed? [y/n] (y): y
```

## Configuration

Layered JSON config — project overrides global:

- `~/.lydia/config.json` — global defaults
- `<project>/.lydia/config.json` — per-repository (created by `lydia init`)

| Key | Default | Meaning |
|---|---|---|
| `model` | auto | Ollama model name; auto-picks the best installed coder model if unset |
| `temperature` | `0.7` | Sampling temperature |
| `num_ctx` | `8192` | Context window size passed to Ollama |
| `ollama_host` | `http://localhost:11434` | Where the Ollama daemon is listening |
| `think` | `auto` | `auto`/`on`/`off` — reasoning for thinking models (Qwen3, DeepSeek-R1); `off` is much faster |
| `mode` | `ask` | `ask`/`auto`/`plan` — the session's permission mode; see [Agent tools and the safety model](#agent-tools-and-the-safety-model) |
| `verify_command` | not set | Shell command Lydia runs after code changes and self-corrects on failure, e.g. `pytest -q`; see [Auto-verify](#auto-verify) |
| `keep_alive` | `30m` | How long Ollama keeps the model loaded after a request; avoids a multi-second reload on your next message. Ollama duration string, or `-1` to never unload |
| `server_url` | not set | If set, talk to a remote Lydia Server instead of a local Ollama daemon — see [Running Lydia Server](#running-lydia-server-remote-gpu-inference) |
| `api_key` | not set | Bearer token for `server_url` |
| `canvas_base_url` | not set | Your school's Canvas URL, e.g. `https://school.instructure.com`; see [Personal assistant](#personal-assistant) |
| `briefing_schedule_enabled` / `briefing_schedule_time` | `false` / `08:00` | Managed by `lydia briefing schedule enable/disable`, not usually set directly |

## Performance and model choice

Local models are the whole point of Lydia, but they're not free-riding on a
frontier hosted model's scale — a few things make a real difference on
consumer hardware:

- **Use a coding-specific model, not a generic chat model**, if your
  hardware allows it. `qwen2.5-coder` / `qwen3.5-coder` / `deepseek-coder`
  are trained specifically on code and noticeably outperform a generic
  model of the same size on programming tasks. `llm/models.py` already
  prefers these automatically if you have one installed — you just need to
  `ollama pull` one.
- **Match model size to your RAM.** As a rough guide on Apple Silicon: 16GB
  comfortably handles up to ~7-9B models; going bigger risks swapping, which
  is far slower than a smaller model outright. `qwen2.5-coder:7b` is a
  solid default on a 16GB machine.
- **`think: off`** if you're on a reasoning model (Qwen3, DeepSeek-R1) and
  want speed over the model showing its work — reasoning tokens can easily
  add 10-30s to a reply before the actual answer starts.
- **`keep_alive`** (above) avoids paying Ollama's model-load cost
  (multi-second) on every single message in a session.

None of this closes the gap with a large hosted model — it narrows it as
much as the "runs entirely on your machine" constraint allows.

## Running Lydia Server (remote/GPU inference)

If you have a second machine with more RAM or a real GPU — a gaming PC,
say — you can run inference there instead and keep using `lydia` normally
from a laptop. **Tool execution (file edits, git, shell commands) always
stays on whichever machine runs the CLI** — only chat/tool-call inference
and embeddings go over the network. This means `lydia "fix this bug"`
works exactly the same, from any directory, whether it's talking to a
local Ollama or a remote one.

```bash
# On the server machine (needs Ollama already running):
pip install -e . -e server/            # from the repo root, one shared venv
LYDIA_SERVER_TOKEN=<a-long-random-token> lydia-server

# On the client machine:
lydia config set server_url https://<server-host>:<port>
lydia config set api_key <the-same-token>
lydia                                   # works exactly as before
```

Full server configuration (env vars), API design, and the reasoning behind
the client/server split live in [`server/README.md`](server/README.md).

## Agent tools and the safety model

Inside chat, Lydia can call tools against your project. Every tool is
classified into a risk tier that decides whether it needs your approval:

| Tool | Risk | Behavior |
|---|---|---|
| `read_file`, `list_dir`, `search_code`, `find_files` | safe | Runs immediately |
| `git_status`, `git_diff`, `git_add` | safe | Runs immediately |
| `write_file`, `edit_file`, `multi_edit_file`, `delete_file` | confirm | Shows a diff, asks y/n, keeps a backup in `.lydia/backups/` |
| `git_commit`, `git_push` | confirm | Shows the message/target, asks y/n |
| `run_command` | policy | Safe-looking commands follow the session mode below; anything matching a destructive pattern always asks, regardless of mode |
| `remember` | safe | Saves a fact to `.lydia/memory.json` so it's known in future sessions |
| `update_todos` | safe | Renders a live checklist for multi-step work; ephemeral, not persisted |
| `search_semantic` | safe | Meaning-based search over an embedding index (`lydia index` first); falls back to literal `search_code` if not indexed |
| `check_email`, `check_canvas`, `check_stocks`, `check_news` | safe | Personal-assistant sources — see [Personal assistant](#personal-assistant) |

`edit_file` replaces one exact snippet of text within an existing file (like
Claude Code's own edit tool) — the model doesn't have to reproduce the whole
file to make a small change. `multi_edit_file` does the same for several
distinct changes to one file in a single call, applied in order, with one
diff and one approval instead of several. `write_file` is for new files or a
genuine full-file rewrite. `find_files` matches file names/paths (e.g.
`*.py`) rather than contents — use `search_code` for that.

### Auto-verify

Set `verify_command` (e.g. `pytest -q`, `npm test`, `cargo test`) and Lydia
is told to run it via `run_command` after making code changes, read the
result, and fix any failures before calling the task done — instead of just
assuming an edit was correct. `lydia init` scans the project's manifest
files and suggests one automatically if it recognizes exactly one project
type; otherwise it's up to you:

```bash
lydia config set verify_command "pytest -q" --project
```

### Session modes

The current mode governs every confirm/command-tier tool above, and is
always visible in the prompt (`Lydia (ask) > `):

| Mode | Behavior |
|---|---|
| `ask` (default) | Every confirm/command-tier action asks first. |
| `auto` | Routine actions (edits, commits, safe shell commands) run without asking; anything flagged dangerous (deletes, `git push`, a destructive shell command) still asks. |
| `plan` | Research only — `write_file`/`edit_file`/`multi_edit_file`/`delete_file`/`run_command`/`git_add`/`git_commit`/`git_push` aren't even offered to the model (`update_todos` still is, for tracking the plan's own steps). Ask Lydia to plan something, review what it proposes, then switch modes to let it actually make the changes. |

Switch modes with `/mode plan`/`/mode auto`/`/mode ask` (or just `/mode` to
see the current one), or press **Shift-Tab** to cycle through them without
typing. `lydia config set mode auto` changes the default for future sessions.

All file paths are resolved relative to the project root and refused if they
try to escape it (`..`, absolute paths outside the project) — a confused or
adversarial model can't touch files elsewhere on your machine.

## Personal assistant

Additive to the coding agent, not a replacement for it — the same `lydia`
session can do both. Four read-only sources, all running through the same
local Ollama model as everything else:

| Source | Setup |
|---|---|
| Gmail | `lydia auth login gmail` (one-time Google OAuth; needs your own Google Cloud OAuth client — see the command's output for the exact steps) |
| Outlook / Microsoft 365 | `lydia auth login outlook --client-id <id>` (one-time device-code sign-in; needs your own Azure app registration) |
| Canvas (school LMS) | `lydia auth login canvas` (base URL + a personal access token from your Canvas settings) |
| Stock market (general indices) | No setup — via `yfinance` |
| AI news | No setup — via a curated RSS list |

```bash
lydia briefing run              # generate today's briefing now
lydia briefing show             # print the last one
lydia briefing schedule enable --time 08:00   # run automatically every day
lydia briefing schedule disable
```

`briefing run` fetches every connected source deterministically first, then
uses the model only to synthesize a prioritized checklist from that real
data — it never decides on its own whether to check a source, so it can't
skip one and improvise plausible-looking content instead. The full checklist
is always saved for `lydia briefing show`.

`briefing schedule enable` works on macOS (via `launchd`) and Linux (via a
`systemd --user` service + timer); it isn't supported on Windows yet. On a
scheduled run, `--notify` (on by default when scheduled) fires a short
desktop notification on macOS via `osascript` — Linux scheduling works, but
the notification itself is macOS-only for now.

## Automations

Create scheduled tasks in plain English and let the model run them on a timer.
Automations are recipes stored as JSON under `.lydia/automations/`, each with a
trigger (time-of-day or an event like new email), a step the model takes (like
"check my email and Canvas and send me a briefing"), and a notification style
(`always`, `if_important`, or `never`).

```bash
lydia automate "every morning at 8, check my email and canvas and send me a briefing"
# Lydia echoes the automation it parsed, you confirm, it saves the recipe.

lydia automations run morning-briefing       # execute immediately (testing)
lydia automations schedule enable            # turn on the launchd heartbeat
lydia automations list                       # see all recipes and their last run time
```

The model runs in a stripped-down mode for automations — only the tasks
you've defined, no interactive chat — so executions are deterministic and
(mostly) fast. Notifications go to macOS via `ntfy` (requires `lydia auth login ntfy`
first), or can be sent to a webhook endpoint if configured. A heartbeat process
runs every 5 minutes (configurable) to check if any automations are due; it
catches up on wake from sleep, so you won't miss a scheduled run.

### Limitations

The `if_important` notification filter uses model judgment to decide whether to
alert you — it reads untrusted content (email bodies, assignment descriptions)
and makes a filtering decision. A carefully crafted email *could* in principle
convince the model to suppress or mislabel an alert. Critical alerts should
use `when: always` instead; for everything else, `if_important` is a useful
convenience filter that doesn't block important information.

## Voice mode

An always-listening voice assistant — say the wake word, ask a question, and
hear a spoken reply. Fully local: openWakeWord for the wake word, Whisper
(`faster-whisper`) for transcription, macOS `say` for the voice.

By voice, Lydia can check email, Canvas, your macOS Calendar, weather (free
Open-Meteo, auto-located), stocks, and news; find and read files; open apps
and files ("open Spotify"); and send phone notifications. Voice can never
edit files or run shell commands.

```bash
lydia listen                 # start listening in the foreground (Ctrl-C stops)
lydia listen enable          # start at login (launchd) and keep running in the background
lydia listen disable         # stop the background listener
lydia listen status          # check if background listening is enabled
```

**Setup:** The first time you run `lydia listen`, macOS will prompt for
microphone permission (grant it), and the Whisper model (~150MB) will download
automatically. The first calendar question triggers a one-time Calendar
automation permission prompt too. Config keys (`lydia config set ...`):
`voice_wake_word` — an openWakeWord model name (default `hey_jarvis`) or a
path to a custom-trained `.onnx` model; `voice_model` — a small tool-calling
model for fast spoken replies (e.g. `qwen3.5:4b`); `voice_tts_voice` — a
macOS voice name from `say -v '?'`; `weather_location` — fixed place name,
or leave unset to auto-detect from IP.

`lydia listen enable` runs the assistant as a launchd agent, so it survives
logout/login and crash restarts. Logs go to `~/.lydia/listen.log`. Note that
always-listening has a non-trivial battery cost on laptops; disable it when you
don't need it with `lydia listen disable`.

## Memory

Lydia keeps two different kinds of history, deliberately separate:

- **Session transcripts** (`.lydia/history/*.jsonl`) — a full append-only log
  of every conversation, one file per session. Useful for debugging, not fed
  back into future conversations, and git-ignored.
- **Remembered facts** (`.lydia/memory.json`) — a short, curated list of
  things worth persisting across sessions (tech stack, conventions,
  decisions), added either by you (`/remember <fact>` in chat, or `lydia
  memory add <fact>`) or by the model itself via the `remember` tool when
  you tell it something worth keeping. These are folded into the system
  prompt on every session, so Lydia actually remembers them next time you
  open the project — and unlike history, this file is meant to be committed.

## Architecture

```
lydia-cli/
├── src/lydia/
│   ├── cli/         Typer commands, the chat REPL, Rich rendering
│   ├── agent/       system prompt, tool registry, the plan→call→observe→respond loop
│   ├── tools/       pure functions: filesystem, terminal, git — no UI/agent knowledge
│   ├── connectors/  Gmail/Outlook/Canvas/stocks/AI news — same purity contract as tools/
│   ├── llm/         ModelClient protocol + two implementations: OllamaClient
│   │                 (local daemon) and RemoteClient (a Lydia Server)
│   ├── context/     repository scanner + semantic search index
│   ├── database/    SQLite storage for the semantic index
│   ├── automations/ automation recipes: parser, store, launchd-driven runner
│   ├── voice/       always-listening assistant: wake word, STT, TTS, audio I/O
│   └── config/      layered JSON settings + OS-keychain-backed secrets
│
└── server/         optional: FastAPI inference proxy for a remote/GPU
                    Ollama — see "Running Lydia Server" above. Tool
                    execution never happens here; only inference does.
```

`agent/`, `tools/`, `context/` etc. type-hint against `llm.protocol.ModelClient`,
not a concrete client class — this is what lets `lydia` talk to either a
local Ollama daemon or a remote Lydia Server with zero code changes
anywhere except `llm/factory.py::build_client`, which picks based on
whether `server_url` is configured.

See `CLAUDE.md` for the layering rules and the non-obvious integration
details (how thinking-model output and tool calls are actually shaped in
Ollama's streaming API, why `tools/` never imports `agent/` or `cli/`, etc.)
— written for an AI coding assistant picking this project back up, but
useful for a human too.

## Development

```bash
.venv/bin/pytest                                    # CLI suite (367 tests, no Ollama required)
.venv/bin/pytest tests/test_agent_loop.py            # one file
.venv/bin/pytest tests/test_agent_loop.py::test_tool_call_then_final_answer  # one test

cd server && ../.venv/bin/pytest                    # server suite (14 tests, no Ollama required)
```

All tests are hermetic — the LLM client is tested against
`httpx.MockTransport`, git/filesystem tools run against a real throwaway
repo in `tmp_path`, the server is tested against a fake `ModelClient`
double. None of them require a running Ollama daemon.

## Roadmap

Milestones 1 (core CLI), 2 (semantic retrieval), 3 (agent loop, tool
calling, git workflows), 6 (persistent project memory), the client/server
split (`server/`, remote inference over Tailscale), the personal-assistant
layer (Gmail/Outlook/Canvas/stocks/AI news, scheduled briefings), session
modes (plan/ask/auto), and Claude-Code-parity editing (`edit_file`,
`multi_edit_file`, auto-verify, `update_todos`, `find_files`) are all done.
See [`ROADMAP.md`](ROADMAP.md) for what's left — packaging, and the M7
plugins stretch goal, plus deferred server work (real multi-user token
storage, a task queue, non-Ollama providers) that the current design
doesn't block but doesn't need yet either.

## License

MIT — see [`LICENSE`](LICENSE).

_Last updated: July 22, 2026_

_Last reviewed: 2026-07-20 19:33 MDT_

---
**Last updated:** 2026-08-13 14:59 MDT

---

Maintained by [Levi Mackay](https://github.com/levimackay)

