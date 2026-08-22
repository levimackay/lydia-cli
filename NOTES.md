# Internal notes

Working notes for picking this project back up, distinct from `README.md`
(user-facing) and `ROADMAP.md` (milestone history/plan). This file is for
day-to-day observations, gotchas, and loose ends that don't belong in either
of those.

## State as of 2026-07-20

- 367 tests in the CLI package (`src/lydia`), 14 in `server/`, all passing
  locally. Confirmed by running `pytest` in both locations, not just reading
  the README's claim.
- Local dev venv (`.venv/`) is on **Python 3.14.6**, one minor ahead of the
  CI matrix in `.github/workflows/test.yml` (3.11-3.13). Tests pass locally
  on 3.14, but nothing in CI actually exercises 3.14. If a 3.14-only
  incompatibility creeps in (deprecations, stdlib changes) it won't be
  caught until it ships. Worth either adding 3.14 to the matrix or
  deliberately deciding not to and noting why.
- `CLAUDE.md`'s "Commands" section still says the CLI suite is "270 tests",
  which is stale; the actual count is 367 (verified by running `pytest`).
  Didn't fix it since CLAUDE.md maintenance is explicitly a separate concern
  from README/NOTES work, but it's a quick one-line fix whenever someone's
  next in there.
- Both `CLAUDE.md` (architecture section, under `voice/`) and `ROADMAP.md`
  (Voice mode entry) say voice synthesis uses **`piper`**. It doesn't:
  `src/lydia/voice/tts.py` shells out to macOS's built-in `say` command
  (confirmed by reading the file; there's no `piper` import or subprocess
  call anywhere in `voice/`). `README.md` already has this right ("macOS
  `say` for the voice"); it's just `CLAUDE.md` and `ROADMAP.md` that are
  wrong, presumably left over from an earlier plan that changed during
  implementation. Same scope note as above: didn't touch CLAUDE.md; flagging
  ROADMAP.md too since it's not README/NOTES either, but worth a follow-up
  edit given how confidently it's stated in both places.
- `lydia automations remove <name>` (`cli/main.py::automations_remove`)
  existed in code but was missing from the README's command table, so it was
  added in this pass. `lydia automations tick` was correctly left out; its own
  docstring says it's "normally invoked by launchd, not by hand," so it's
  effectively internal plumbing, not a documented user command.
- No `.lydia/config.json` or `.lydia/memory.json` exist at the repo root.
  This repo hasn't been used against itself in a way that persisted config
  or remembered facts (only old session transcripts under
  `.lydia/history/`, which are gitignored). Nothing broken, just worth
  knowing before assuming `lydia memory list` will show anything here.

## Architecture reminders (verified against code, not just docs)

- The `llm.protocol.ModelClient` seam is real and consistently honored:
  `agent/loop.py`, `agent/tools.py`, and `context/indexer.py`/`retriever.py`
  all import the protocol type, never `OllamaClient`/`RemoteClient`
  directly. `llm/factory.py::build_client` is genuinely the only place that
  branches on `config.server_url`.
- `agent/tools.py` has 25 tool functions (`_read_file` through `_open_item`)
  registered via `build_registry()` / gated by `filter_for_mode()`. Every
  tool the README documents in the "Agent tools and the safety model" table
  is present in code; nothing extra, nothing missing.
- Config defaults live in `config/settings.py` as a dataclass; confirmed
  every key the README's Configuration table lists (`model`, `temperature`,
  `num_ctx`, `ollama_host`, `think`, `mode`, `verify_command`, `keep_alive`,
  `server_url`, `api_key`, `canvas_base_url`, `briefing_schedule_*`) matches
  exactly, including defaults.

## Ideas / next steps worth considering

- Since CI doesn't cover 3.14 but local dev already lives there, either bump
  the matrix or pin local dev to 3.13 to match what's actually tested.
  Right now there's a silent gap between "works on my machine" and "works
  in CI."
- The `piper` vs `say` inconsistency above is a good candidate for a
  five-minute cleanup pass across `CLAUDE.md` + `ROADMAP.md` next time
  either file is opened for something else: cheap to fix, currently
  actively misleading about a real implementation detail (`say` is
  macOS-only and blocking-per-call; `piper` would have been cross-platform
  and a background process, so this isn't a cosmetic difference).
- `ROADMAP.md`'s "Smaller polish items" already tracks packaging
  (PyPI/brew) as unstarted. Confirmed still true: `pyproject.toml` has no
  publish workflow and the README's install instructions are still
  clone-and-editable-install. No new information here, just confirming the
  roadmap's own status claim is accurate.
