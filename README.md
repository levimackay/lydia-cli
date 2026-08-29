# Lydia

[![tests](https://github.com/levimackay/lydia-cli/actions/workflows/test.yml/badge.svg)](https://github.com/levimackay/lydia-cli/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/lydia-cli.svg)](https://pypi.org/project/lydia-cli/)

*Named after my wife, Lydia, who always felt like she couldn't help with
my coding projects or homework, so I built something that could.*

**A local AI coding agent for your terminal. No API keys, no subscriptions,
no cloud.** Lydia reads your code, edits files, runs commands, drives git,
and checks its own work by running your tests, all through a local
[Ollama](https://ollama.com) model on your own machine. It's a personal
alternative to Claude Code, Cursor's agent, or Copilot Workspace, for
anyone who wants that workflow without paying for API usage or sending
code to a third party.

```
   ██     ██╗  ██╗   ██╗██████╗ ██╗ █████╗      ██████╗██╗     ██╗     ██
  ████    ██║  ╚██╗ ██╔╝██╔══██╗██║██╔══██╗    ██╔════╝██║     ██║    ████
 ██████   ██║   ╚████╔╝ ██║  ██║██║███████║    ██║     ██║     ██║   ██████
 ██████   ██║    ╚██╔╝  ██║  ██║██║██╔══██║    ██║     ██║     ██║   ██████
  ████    ███████╗██║   ██████╔╝██║██║  ██║    ╚██████╗███████╗██║    ████
   ██     ╚══════╝╚═╝   ╚═════╝ ╚═╝╚═╝  ╚═╝     ╚═════╝╚══════╝╚═╝     ██

╭─────────────────╮
│ model  qwen3.5  │
│ project  Python │
╰─────────────────╯
Type your request, or /help for commands. Ctrl-D to exit.

Lydia (auto) > add input validation to the login handler and run the tests
```

The tradeoff is real: local models on consumer hardware are smaller and
slower than a frontier hosted model, so Lydia won't be as capable. It's
built for personal projects, learning, and "good enough and free" beating
"best available and metered", and as a backup for when a hosted tool is
down or you've hit its limit, for the routine stuff (why won't this
compile, organize this folder) rather than deep multi-file work.

## Features

- **A real coding agent.** Reads files, searches your codebase (literal or
  semantic), edits (`edit_file`/`multi_edit_file`, one diff per approval,
  never a full-file rewrite for a small change), writes new files, runs
  shell commands, and drives git, all via Ollama's native tool calling.
- **Closes the loop.** Set `verify_command` (e.g. `pytest -q`) and Lydia
  runs it after changes and fixes failures before calling the task done.
- **Nothing touches disk or git without your okay** (outside `auto` mode).
  Every write/delete shows a diff first and keeps a backup; every
  destructive shell pattern (`rm -rf`, `git push --force`, `sudo`, ...) is
  *always* confirmed, in every mode. See [Safety model](#agent-tools-and-the-safety-model).
  - `plan` / `ask` (default) / `auto`: three permission modes, one Shift-Tab away.
- **Project-aware from the first message.** A repo scan feeds the
  language/type into the system prompt automatically.
- **Also works as a personal assistant**, additively: Gmail/Outlook,
  Canvas, stocks, AI news, a daily briefing, even a voice mode. See
  [Beyond coding](#beyond-coding).
- **Per-project and global config**, so a smaller/faster model on one repo
  doesn't have to be your default everywhere.

## Requirements

- Python 3.11+
- [Ollama](https://ollama.com), running locally, with a tool-calling model pulled:
  ```bash
  ollama pull qwen3.5
  ```

## Install

```bash
pipx install lydia-cli
# or: pip install lydia-cli
```

[`pipx`](https://pipx.pypa.io) is the better choice for a standalone CLI:
isolated environment, no venv/symlink management. The base install is
deliberately lean; the personal-assistant connectors and voice mode pull
in much heavier dependencies and are opt-in extras:

```bash
pipx install "lydia-cli[assistant]"        # + Gmail/Outlook/stocks/news
pipx install "lydia-cli[voice]"            # + always-listening voice mode
```

Using a feature without its extra installed gives a clear error naming
which one to add, not a crash. Building from source or contributing? See
[`CONTRIBUTING.md`](CONTRIBUTING.md) for the dev setup.

## Usage

| Command | What it does |
|---|---|
| `lydia` | Interactive agent chat in the current project |
| `lydia ask "why is this failing?"` | One-shot question, no tools |
| `lydia ask "..." --yes` | Same, with full tool access, for scripts/CI |
| `lydia analyze` | Project summary |
| `lydia index` | Build the semantic search index |
| `lydia init` | Create `.lydia/` project config |
| `lydia config show` / `set <key> <value>` | View/change configuration |
| `lydia memory list` / `add <fact>` | Facts Lydia remembers about this project |
| `lydia restore list` / `apply <n>` | Restore a file from `.lydia/backups/` |

Inside chat: `/help`, `/mode`, `/model <name>`, `/new`, `/remember <fact>`, `/exit`.

```
Lydia (auto) > fix the bug where login accepts an empty password

› read_file(path='src/auth/login.py')
Found it. login() never checks password is non-empty before hashing.

› edit_file(path='src/auth/login.py', ...)
› run_command(command='pytest -q')
  ran `pytest -q` (exit 0)

Added the check and ran the test suite to confirm. All passing.
```

In `ask` mode (the default), edits show a diff and ask first instead of
just applying.

## Configuration

Layered JSON: `~/.lydia/config.json` (global) then `<project>/.lydia/config.json`
(per-repo, made by `lydia init`). Project wins.

| Key | Default | Meaning |
|---|---|---|
| `model` | auto | Auto-picks the best installed coder model if unset |
| `mode` | `ask` | `ask` / `auto` / `plan`, see [Safety model](#agent-tools-and-the-safety-model) |
| `verify_command` | not set | e.g. `pytest -q`, run and self-corrected on after every change |
| `num_ctx` | `16384` | Context window passed to Ollama; see [Performance](#performance-and-model-choice) if you change tool count/system prompt |
| `provider` | `ollama` | `ollama` or `gemini` (opt-in, bring your own key); see [Beyond your own machine](#beyond-your-own-machine) |
| `server_url` | not set | Talk to a remote Lydia Server instead of local Ollama |

Everything else (`temperature`, `keep_alive`, `think`, `api_key`,
`canvas_base_url`, briefing schedule) is in `config/settings.py`'s field
comments. `lydia config show` prints the full effective set.

## Performance and model choice

If a model is slow or downloads forever, the fix usually isn't waiting it
out. It's picking a model your hardware actually fits.
[**llmfit**](https://github.com/AlexsJones/llmfit) ranks every model by
fit/speed/quality for your specific machine before you spend time pulling
one that was never going to run well: `llmfit fit`.

Beyond model size: use a coding-specific model if your hardware allows it
(`qwen3.5-coder`/`deepseek-coder`, auto-preferred if installed); `think:
off` skips visible reasoning tokens for speed; `keep_alive` (default
`30m`) avoids a multi-second reload every message. None of this closes
the gap with a hosted model. It narrows it as much as "runs entirely on
your machine" allows.

## Beyond your own machine

Two opt-in options if one machine + local Ollama isn't enough. Neither
is required for anything above.

**A second, more powerful machine** (a gaming PC with a real GPU) can run
inference while `lydia` keeps running normally on a laptop. Tool
execution always stays on the laptop, only inference goes over the
network:
```bash
# on the server machine: git clone, pip install -e . -e server/, then
LYDIA_SERVER_TOKEN=<token> lydia-server
# on the client: lydia config set server_url https://<host>:<port>
```
Full setup, token management, API design in [`server/README.md`](server/README.md).

**Or bring your own Gemini key** and skip local inference entirely:
```bash
lydia config set provider gemini
lydia config set gemini_api_key   # prompts, hidden, never a CLI argument
```
Key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey),
stored in your OS keychain, never plain JSON. Chat/tools/embeddings all
work; semantic search (`lydia index`) still needs `provider=ollama`. The
index format is tied to whichever model embedded it, and switching
providers on an existing one risks comparing incompatible vectors, so
both refuse outright rather than doing that silently.

## Agent tools and the safety model

| Tool | Risk | Behavior |
|---|---|---|
| `read_file`, `search_code`/`search_semantic`, `list_dir`, `find_files`, `git_status`/`diff` | safe | Runs immediately |
| `write_file`, `edit_file`, `multi_edit_file`, `delete_file` | confirm | Diff + y/n, backed up to `.lydia/backups/` |
| `git_commit`, `git_push` | confirm | Shows message/target, y/n |
| `run_command` | policy | Follows session mode; a destructive pattern always asks regardless of mode |
| `remember` | safe | Saves a fact to `.lydia/memory.json` |

`mode` (`ask` default / `auto` skips confirmation for routine actions,
still asks for anything dangerous / `plan` offers no mutating tool at
all) governs every confirm/policy row above, and is always visible in the
prompt. Every path is resolved relative to the project root and refused
if it tries to escape it.

## Beyond coding

**Personal assistant.** Gmail, Outlook, Canvas, stocks, and AI news,
additively, through the same session: `lydia auth login gmail` (or
`outlook`/`canvas`), then `lydia briefing run` or `briefing schedule
enable --time 08:00` for a daily one. Fetches every source
deterministically first, then only uses the model to synthesize, so it
can't skip a source and improvise instead.

**Automations.** Plain-English scheduled tasks: `lydia automate "every
morning at 8, check my email and canvas"`. Runs in a stripped-down,
deterministic mode on a 5-minute heartbeat (`lydia automations schedule
enable`), catching up after sleep.

**Voice mode.** Say a wake word, ask a question, hear a reply, fully
local (openWakeWord + Whisper + macOS `say`). `lydia listen` (foreground)
or `lydia listen enable` (background, survives logout). Can check
email/Canvas/calendar/weather/stocks/news and open apps, but never edits
files or runs shell commands.

Setup walks you through what each needs interactively; `lydia config show`
lists every tunable (voice wake word/model/voice, weather location, and
so on) with its current value. Scheduling works on macOS and Linux, not
Windows yet; scheduled desktop notifications are macOS-only for now.

## Architecture

```
lydia-cli/
├── src/lydia/
│   ├── cli/         Typer commands, the chat REPL, Rich rendering
│   ├── agent/       system prompt, tool registry, the plan→call→observe→respond loop
│   ├── tools/       pure functions: filesystem, terminal, git; no UI/agent knowledge
│   ├── connectors/  Gmail/Outlook/Canvas/stocks/AI news, same purity contract as tools/
│   ├── llm/         ModelClient protocol + OllamaClient/RemoteClient/GeminiClient
│   ├── context/     repository scanner + semantic search index
│   ├── automations/ automation recipes: parser, store, launchd-driven runner
│   ├── voice/       always-listening assistant: wake word, STT, TTS, audio I/O
│   └── config/      layered JSON settings + OS-keychain-backed secrets
└── server/         optional: FastAPI inference proxy for remote/GPU Ollama
```

Everything above `llm/` type-hints against `ModelClient`, never a
concrete client. `llm/factory.py::build_client` is the one place that
picks Ollama/Remote/Gemini based on config. See [`CLAUDE.md`](CLAUDE.md)
for the full layering rules and integration gotchas.

## Development, Roadmap, Contributing

```bash
pytest              # CLI suite, 413 tests, no Ollama required
cd server && pytest # server suite, 61 tests
```

All hermetic: mocked HTTP transports, throwaway `tmp_path` repos, no
live daemon needed. [`CONTRIBUTING.md`](CONTRIBUTING.md) has the full dev
setup and PR checklist; [`ROADMAP.md`](ROADMAP.md) has what's done, what's
next, and the reasoning behind past calls, so check it before starting
something new. Bug reports and PRs welcome; a few have already landed
from outside contributors.

## License

MIT. See [`LICENSE`](LICENSE).

**Last updated:** 2026-08-29 11:47 PDT

