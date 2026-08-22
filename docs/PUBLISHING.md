# Publishing to PyPI

`lydia-cli` isn't on PyPI yet — this is what needs to happen, once, before
`.github/workflows/publish.yml` can actually publish a release. All of
this needs a PyPI account (pypi.org) with access I don't have, so it's a
manual step for whoever owns that account.

## One-time setup: PyPI Trusted Publisher

The publish workflow uses PyPI's [Trusted Publisher](https://docs.pypi.org/trusted-publishers/)
(OIDC) mechanism instead of a stored API token — no secret to leak, rotate,
or accidentally commit. It has to be configured on PyPI's side before the
workflow can push anything:

1. Create the `lydia-cli` project on PyPI, either by:
   - Reserving the name ahead of time: [pypi.org/manage/account/publishing/](https://pypi.org/manage/account/publishing/)
     -> "Add a new pending publisher" (works even before the project exists), or
   - Publishing once manually first (`twine upload dist/*` with an API
     token) and adding the trusted publisher afterward from the project's
     own settings page.
2. Either way, when adding the trusted publisher, fill in:
   - **PyPI project name**: `lydia-cli`
   - **Owner**: `levimackay`
   - **Repository name**: `lydia-cli`
   - **Workflow name**: `publish.yml`
   - **Environment name**: `pypi`
3. In this repo's GitHub settings (Settings -> Environments), create an
   environment named `pypi` (matches `environment: pypi` in the workflow).
   Optionally add a required reviewer here for extra protection against an
   accidental publish — the workflow already only runs on a published
   GitHub Release, which is itself a deliberate action, but a required
   reviewer adds a second confirmation step if wanted.

That's it — no `PYPI_API_TOKEN` secret to set anywhere. The workflow
authenticates itself to PyPI per-run using a token PyPI issues based on
the trusted publisher relationship above.

## Cutting a release

Once the trusted publisher is configured:

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

## `lydia-server` is not published yet

Only the `lydia-cli` package (the coding agent) is wired up to publish.
`server/` (the remote-inference proxy) is a smaller-audience, self-hosted
component — if it's worth publishing separately later, it needs its own
PyPI project name (`lydia-server` is available, confirmed at the time
this was written) and its own trusted publisher entry, but a second
`publish-server.yml` workflow (or a matrix step in this one) rather than
being bolted onto this one, since the two packages version and release on
different cadences.

## Before the very first publish

Worth a final sanity pass beyond what CI already checks:

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
