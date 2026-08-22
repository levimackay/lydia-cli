## What this does and why

<!-- One or two sentences. If this fixes an open issue, write "Fixes #123". -->

## How it was tested

<!--
- `pytest` output (which package(s) — CLI, server, or both)
- If you verified against a real Ollama daemon, which model and what you ran
-->

## Checklist

- [ ] `pytest` passes locally (`.venv/bin/pytest`, and `cd server && ../.venv/bin/pytest` if you touched `server/`)
- [ ] Added/updated a test for any behavior change
- [ ] No `Co-Authored-By: Claude` or similar AI attribution trailer in the commit(s)
- [ ] Checked [`ROADMAP.md`](../ROADMAP.md) / [`CLAUDE.md`](../CLAUDE.md) if this touches an area they document
