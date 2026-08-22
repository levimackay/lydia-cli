# Contributing to Lydia

Thanks for wanting to work on this. A few contributors have already sent
real fixes (a Linux scheduler backend, a config-coercion bug, a CI fix) —
this doc exists to make the next one easier.

## Before you start

Check [`ROADMAP.md`](ROADMAP.md) first. It tracks what's done, what's
explicitly next, and what's deferred on purpose — including the reasoning
behind past ordering decisions, so you don't have to re-derive context or
duplicate work someone already scoped out. If you're fixing a bug instead
of adding something from the roadmap, you don't need to check it, just open
an issue or a PR.

For anything nontrivial, especially a new feature or a design change,
open an issue first. It's a much smaller ask than a full PR and saves both
of us the awkwardness of a large PR going in a direction that doesn't fit.

## Project layout

This is a two-package monorepo: `src/lydia` (the CLI, always needed) and
`server/lydia_server` (optional — a FastAPI inference proxy so Ollama can
run on a separate machine). The full architecture — layering, the
`ModelClient` seam that makes local-only and client/server usage the same
codepath, tool safety policy, config layering — is documented in
[`CLAUDE.md`](CLAUDE.md). Read the relevant section there before touching
`agent/`, `tools/`, `llm/`, or `server/` — it explains *why* the code is
shaped the way it is, not just what's in each file.

## Setup

```bash
git clone https://github.com/levimackay/lydia-cli.git
cd lydia-cli
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pip install -e "server/[dev]"   # only if you're touching server/
```

You don't need Ollama running to develop or run the test suite — the unit
tests mock the LLM client entirely. You do need it if you want to actually
run `lydia` and try your change:

```bash
ollama pull qwen3.5:8b   # or whatever coder model you have room for
lydia config set think off   # optional, much faster manual testing
```

## Running the tests

```bash
.venv/bin/pytest                 # CLI package — 376 tests
cd server && ../.venv/bin/pytest # server package — 14 tests, own pyproject.toml
```

Run a single file or test while iterating:

```bash
.venv/bin/pytest tests/test_agent_loop.py
.venv/bin/pytest tests/test_agent_loop.py::test_tool_call_then_final_answer
```

Unit tests never touch the network or a real filesystem outside `tmp_path`
— if you're adding a tool or an LLM call site, follow the existing pattern
(`httpx.MockTransport` for the client, a fake `ModelClient` for the agent
loop) rather than requiring a live Ollama daemon for `pytest` to pass. CI
runs this same suite on macOS across Python 3.11–3.13 for every push and
PR.

If your change needs verifying against a *real* Ollama daemon (a new tool,
a change to the agent loop, anything in the client/server wire format),
`CLAUDE.md`'s "Testing against the real Ollama daemon" section has the
exact commands and a couple of real gotchas (piped stdin breaks the
confirmation prompt; not every model that looks like it supports tool
calling actually does) — read it before assuming something's broken.

There's no lint/format command configured yet. Match the style of the
surrounding code — this project doesn't have a style guide beyond that.

## Making a PR

- Keep it focused. A PR that fixes one bug or adds one thing is much
  easier to review than one that does three loosely related things.
- If you added or changed behavior, add or update a test for it. A fix
  with no test regressing is much harder to trust won't regress silently.
- Write a commit message that says why, not just what — `git log` in this
  repo already does this; match it rather than a one-line "fix bug."
- Don't add an AI attribution trailer (`Co-Authored-By: Claude` or
  similar) to commits, even if you used an AI tool to help write the
  change — this project keeps that off its commit history by convention.
- Make sure `pytest` (both packages, if you touched `server/`) passes
  locally before opening the PR — CI will catch it either way, but it's a
  faster loop for you to know first.

## Reporting a bug

Open an issue with what you ran, what you expected, and what actually
happened. If it's platform-specific (this project's had real Linux/macOS
differences before — see the scheduler backend split), say which OS and
Python version. If it's related to a specific Ollama model's behavior,
name the model — tool-calling support varies enough between models that
it matters (see the gotcha in `CLAUDE.md`).

## Anything else

If something in this file or `CLAUDE.md` is wrong, out of date, or missing
something you needed and had to figure out yourself, that's worth a PR
too — docs drift is a real bug, not a lesser one.
