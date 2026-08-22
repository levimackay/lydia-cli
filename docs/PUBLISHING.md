# Publishing to PyPI

`lydia-cli` is live on PyPI: https://pypi.org/project/lydia-cli/ — `pip
install lydia-cli` / `pipx install lydia-cli` both work. This documents
how that's wired up and what cutting the next release looks like.

## One-time setup: PyPI Trusted Publisher (done)

The publish workflow uses PyPI's [Trusted Publisher](https://docs.pypi.org/trusted-publishers/)
(OIDC) mechanism instead of a stored API token — no secret to leak,
rotate, or accidentally commit. This is configured on PyPI's side
already, recorded here for reference (e.g. if it ever needs to be
recreated, or replicated for `lydia-server` — see below):

- **PyPI project name**: `lydia-cli`
- **Owner**: `levimackay`
- **Repository name**: `lydia-cli`
- **Workflow name**: `publish.yml`
- **Environment name**: `pypi`

Manageable at [pypi.org/manage/project/lydia-cli/settings/publishing/](https://pypi.org/manage/project/lydia-cli/settings/publishing/)
(once the project exists, that's where trusted publishers live — not the
account-level pending-publisher page used for the very first setup).
The matching GitHub side is a `pypi` environment under this repo's
Settings -> Environments, which `.github/workflows/publish.yml` deploys
to (`environment: pypi`) — that's what the OIDC token exchange is scoped
against.

No `PYPI_API_TOKEN` secret exists anywhere in this repo. The workflow
authenticates itself to PyPI per-run using a token PyPI issues based on
the trusted publisher relationship above.

## Cutting a release

1. Bump `version` in `pyproject.toml` (and `server/pyproject.toml` too, if
   also publishing the server package — see below).
2. Commit that, tag it (`git tag v0.2.0 && git push origin v0.2.0`), and
   create a GitHub Release from that tag (Releases -> Draft a new release
   -> pick the tag -> Publish release). Publishing the release is what
   triggers `.github/workflows/publish.yml`.
3. Watch the Actions tab — the workflow builds both the wheel and sdist
   (`python -m build`) and uploads them via
   `pypa/gh-action-pypi-publish`.
4. `pip install lydia-cli` should work within a few minutes.

**PyPI versions are immutable.** Once a version number is published it
can never be re-uploaded, even after deleting it — if a release turns
out broken, ship a new version number, don't try to fix v0.1.0 in place.

## `lydia-server` is not published yet

Only the `lydia-cli` package (the coding agent) is wired up to publish.
`server/` (the remote-inference proxy) is a smaller-audience, self-hosted
component — if it's worth publishing separately later, it needs its own
PyPI project name (`lydia-server` is available, confirmed at the time
this was written) and its own trusted publisher entry, but a second
`publish-server.yml` workflow (or a matrix step in this one) rather than
being bolted onto this one, since the two packages version and release on
different cadences.

## Before any release

Worth a final sanity pass beyond what CI already checks — this is what
verified v0.1.0 before it shipped:

- `python -m build` locally, then install the built wheel into a
  throwaway venv (`python3 -m venv /tmp/check && /tmp/check/bin/pip
  install dist/*.whl`) and run `lydia --version` / `lydia --help` — CI
  never does this (it only ever installs editable), so a build-only
  packaging mistake (missing package data, wrong entry point) wouldn't
  otherwise be caught before it's live on PyPI.
- Confirm the base install stays lean and the `[assistant]`/`[voice]`
  extras actually unlock the features they gate — see
  `src/lydia/cli/optional_deps.py` and the `pyproject.toml` comment above
  `dependencies = [...]` for why they're split out.
