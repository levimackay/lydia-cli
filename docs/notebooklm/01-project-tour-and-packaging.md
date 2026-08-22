# Chapter 1: The Project Tour, and How Lydia Becomes Something You Can Install

This chapter answers two questions. First: if you opened the `lydia`
folder on disk and looked around, what's actually in there, and what is
each part for? Second: how does a folder full of `.py` files on Levi's
laptop turn into something a stranger can install with one command,
typed into a terminal on a computer that has never seen this code
before?

## Two packages, one repo

The first thing to understand about Lydia's layout is that it is not
one program. It is two separate, installable pieces of software that
happen to live in the same folder on disk (the same **repo**, short for
repository).

To understand why they're separate, it helps to understand what a
**package** actually is, beyond the one-line definition in the glossary.
When you write Python code, you don't have to cram everything into a
single giant file. You organize related pieces into modules (individual
`.py` files) and group related modules into a folder, which is a
package. What
makes a folder a *package*, in the sense that matters here, is that it
comes with instructions for how to install it: what it needs to run,
what command it should create on your computer once installed, and what
its official name is. Those installation instructions live in a file
called `pyproject.toml`, which this chapter covers in detail below.

Lydia has two such folders, each with its own `pyproject.toml`:

- **`src/lydia`** is the CLI package. This is Lydia itself: the thing you
  type `lydia` into your terminal to run. Everyone who wants to use
  Lydia needs this one.
- **`server/lydia_server`** is an optional second package, `lydia-server`.
  As `00-start-here.md` explained, this is the piece you'd run on a
  second, more powerful computer if you wanted your laptop's `lydia`
  command to send its AI thinking to that stronger machine instead of
  running the model locally.

Why split them into two packages instead of just... one folder with
everything in it? Because most people who install Lydia will never touch
`server/` at all. They run Ollama and `lydia` on the same one computer,
which is the whole point of the "just works locally, no setup" design.
Packaging the server separately means someone installing the CLI never
downloads code, or drags in dependencies, for a feature they're not
using. It also means the two pieces can be versioned and released on
their own separate schedules later, since they're really solving two
different problems (being an agent, versus being a network relay for
one).

The `server/` package does depend on the `lydia` package, though, not
as "one program calling into another over a network," but as what's
called **a library**: `server/`'s own code directly reuses pieces of
`src/lydia`'s code (specifically, the part that knows how to talk to
Ollama) by importing it, the same way your own code imports Python's
built-in tools. That's why `CONTRIBUTING.md`'s dev setup installs both
packages into the same environment. `server/` literally cannot run
without `lydia` already being installed alongside it, while `lydia` runs
perfectly fine on its own with no `server/` present at all. This
one-way dependency (server needs lydia; lydia never needs server) is
also why `CLAUDE.md`'s architecture notes describe `server/` as
something that "depends on lydia as a library" and stress that it "never
touches tools/, agent/, or cli/". The server borrows exactly one thing
from the CLI package (its Ollama-talking code) and nothing else.

## A guided tour of `src/lydia`

Opening `src/lydia`, you'll find it split into ten subfolders, each one
a focused piece of the whole agent. Here's what each one is, in one
sentence, with a pointer to the later chapter that goes deep on it:

- **`cli/`** is everything about the actual typing-and-reading experience:
  the commands you can run (`lydia ask`, `lydia config`, and so on), the
  interactive chat window itself, and the colored/formatted text you see
  on screen. Covered in depth in `02-cli-and-repl.md`.
- **`agent/`** is the "brain" logic: the instructions given to the AI
  model describing what it is and what it's allowed to do, the list of
  available tools, and the loop that repeatedly asks the model what it
  wants to do next until it's done. Covered in `03-agent-loop-and-tools.md`.
- **`tools/`** holds the actual actions the agent can take: reading a file,
  writing a file, running a terminal command, making a git commit. These
  are written to know nothing about confirmation dialogs or the AI model
  itself. They just do the action when called. Covered in
  `04-agent-tools-filesystem-git-terminal.md`.
- **`llm/`** is the code that actually sends requests to Ollama (or a
  remote Lydia server, or Google's Gemini) and makes sense of what comes
  back. Covered in `05-talking-to-ai-models.md`.
- **`context/`** is the code that scans a code project and builds a
  searchable index of it, so the agent can find relevant code without
  reading every single file every time you ask it something. Covered in
  `06-search-the-codebase.md`.
- **`database/`** is the SQLite storage layer that the semantic search
  index in `context/` is actually saved into on disk. Also touched on in
  `06-search-the-codebase.md`.
- **`connectors/`** holds the personal-assistant integrations: Gmail,
  Outlook, Canvas, stock prices, AI news. Covered in
  `10-connectors-and-voice.md`.
- **`automations/`** is the code behind "do this thing on a schedule,"
  like a daily briefing. Covered in `09-automations-and-scheduling.md`.
- **`voice/`** is the always-listening voice assistant: detecting a wake
  word, converting speech to text, and speaking a reply back. Covered in
  `10-connectors-and-voice.md`.
- **`config/`** is where settings (like which AI model to use) and
  secrets (like API keys) get stored and read back safely. Covered in
  `08-config-and-secrets.md`.

## A guided tour of `server/`

The `server/` folder is its own small project, with its own
`pyproject.toml` and its own `lydia_server` package inside it (note the
underscore; more on that naming pattern below). Its subfolders are:

- **`api/`** holds the actual network endpoints the server responds to,
  the addresses a `lydia` client sends requests to.
- **`auth/`** checks that whoever is sending a request to the server
  is actually allowed to (this is what the `LYDIA_SERVER_TOKEN` mentioned
  in the README and `CLAUDE.md` is for).
- **`config/`** holds the server's own settings, kept separate from the
  CLI's settings since it's a different program.
- **`database/`** covers the server's own storage needs.
- **`models/`** defines the shapes of the data the server sends and
  receives, written down formally so requests and responses are always
  structured the same predictable way.
- **`services/`** is the server's actual logic for handling a request,
  sitting between `api/` (what comes in over the network) and the parts
  of `lydia` it reuses as a library (which actually talk to Ollama).

All of `server/` is covered in full in `07-server-and-remote-control.md`.

## What `pyproject.toml` actually is

Every package that can be installed with Python's tooling needs a file
named exactly `pyproject.toml` sitting at its root. `lydia`'s is at
the very top of the repo, and `lydia_server`'s is inside `server/`.
`.toml` is just a text file format (like JSON, mentioned in the
glossary) designed to be easy for both humans and programs to read; the
name stands for "Tom's Obvious, Minimal Language," after the person who
designed it, but nobody needs to remember that. Think of `pyproject.toml`
as the package's ID card and instruction manual in one file: its name,
its version number, who wrote it, what other software it needs to run,
what command it should create on your computer, and how to build it into
an installable file.

Look at what `src/lydia`'s `pyproject.toml` actually lists as
`dependencies`, the software Lydia absolutely cannot run without:
`typer` (for building the command-line commands), `rich` (for the
colored, formatted terminal output), `prompt-toolkit` (for the
interactive chat input line), `httpx` (for making network requests to
Ollama), `pyfiglet` (the big ASCII-art "LYDIA CLI" banner you see when
it starts), `numpy` (numeric operations, used by the semantic search
math), and `keyring` (for storing secrets in your operating system's
built-in password manager rather than a plain text file). That's the
whole list, and it's deliberately short.

Underneath that list sits a comment block explaining exactly why it was
kept short, and it's worth repeating in plain language because it's the
core packaging decision of the whole project: the personal-assistant
features (Gmail, Outlook, stock prices) and the voice mode pull in some
genuinely large pieces of software behind the scenes, things like
Google's API client library and speech-processing libraries, that
together add up to several hundred extra megabytes. Someone installing
Lydia for the first time, just to try the coding agent, shouldn't be
forced to download all of that for features they may never use. So
those heavier dependencies were pulled out into what's called **optional
extras**, defined in a separate section of the same file,
`[project.optional-dependencies]`:

- **`assistant`** covers Gmail, Outlook, stock prices, and news. Pulls in
  `yfinance`, `feedparser`, Google's API client, and Microsoft's `msal`
  login library.
- **`voice`** is the always-listening wake-word mode. Pulls in
  `sounddevice` (microphone access), `openwakeword` (wake-word
  detection), and `faster-whisper` (speech-to-text).
- **`all`** is simply both `assistant` and `voice` together, as a
  shortcut.
- **`dev`** is everything in `all`, plus `pytest` and `pytest-cov`, the
  tools used to run and measure Lydia's own test suite. This is what
  someone contributing code to Lydia would install, not someone just
  using it.

The comment also explains *how* this split is made to actually work
without breaking anything: every place in the code that needs one of
these optional libraries only imports it right when that specific
feature is used (this is called being "function-scoped," as opposed to
loading everything the moment the program starts). That means someone
who installed just the bare minimum can still use the coding agent
completely normally, with nothing crashing on startup, and only trips over
a missing library if they specifically try to use, say, the Gmail
feature without having installed the `assistant` extra. When that
happens, a helper called `require_extra` (in
`src/lydia/cli/optional_deps.py`) catches the error and turns it into a
plain-English message naming exactly which extra to install, instead of
a confusing technical crash.

`server/`'s `pyproject.toml` follows the same shape but is much shorter:
its only real dependencies are `fastapi` (the web framework it's built
on), `uvicorn` (the program that actually runs a FastAPI server), and
`httpx` (again, for making its own requests onward to Ollama). Its
comment records the same fact mentioned earlier: it also requires the
`lydia` package to be installed in the same environment, since it
imports code from it directly.

## What "PyPI" is, and what `pip install` actually does

**PyPI** (the Python Package Index, usually pronounced "pie-P-I") is a
free, public website (and behind it, a big storage warehouse) where
anyone can upload a Python package for the whole world to download.
It's the same idea as an app store, except for Python software instead
of phone apps, and instead of a company curating what's on it, anyone
can publish anything to it under a name nobody else is already using.
`pip` is the program, included with Python itself, that knows how to
talk to PyPI: `pip install lydia-cli` means "go to PyPI, find the
package published under the name `lydia-cli`, download it, and set it
up on this computer."

Concretely, step by step, `pip install lydia-cli` does this:

1. It contacts PyPI over the internet and asks for the package named
   `lydia-cli`.
2. PyPI sends back a file (a **wheel**, a pre-built, ready-to-use
   package format, or occasionally a **sdist**, a "source distribution"
   that gets built locally) containing all of `src/lydia`'s code.
3. `pip` reads that package's own `pyproject.toml`-derived metadata to
   see what dependencies it declared (`typer`, `rich`, `httpx`, and the
   rest of the base list above), then downloads and installs each of
   those too, if they aren't already present.
4. `pip` looks at the `[project.scripts]` section (explained next) and
   creates an actual `lydia` command on your computer, so that typing
   `lydia` in any terminal window afterward runs this program.

The README also recommends `pipx` over plain `pip` for installing
Lydia. The difference: `pip install` normally puts a package's files
somewhere shared with all your other Python projects, which can cause
version conflicts between unrelated projects over time. `pipx` instead
gives each command-line tool its own private, isolated space
automatically, which is exactly what you want for a standalone program
like `lydia` that you just want to run from anywhere, not depend on
from other Python code.

## Why the PyPI name and the command you type are different

Here's a detail that trips people up the first time they see it: you run
`pip install lydia-cli`, but afterward you type `lydia`, not `lydia-cli`,
to actually use it. These are two different names doing two different
jobs, and `pyproject.toml` is where both are set:

```
[project]
name = "lydia-cli"
...
[project.scripts]
lydia = "lydia.cli.main:main"
```

`name = "lydia-cli"` is the package's identity *on PyPI*, the name
under which it's listed and searched for on the website, and what you
type after `pip install`. `[project.scripts]` is a separate instruction
that says "when this package is installed, create a command called
`lydia` on the user's computer, and when they run it, call the `main`
function that lives in `lydia.cli.main`." Nothing requires these two
names to match, and here they don't, for a simple, undramatic reason
recorded in `ROADMAP.md`: when Levi went to publish Lydia, the plain
name `lydia` was already taken on PyPI by an unrelated project. Rather
than rename the whole codebase and the command everyone would actually
type, only the PyPI listing name changed, to `lydia-cli`, matching the
GitHub repository's own name, while the command you type every day,
and the `import lydia` used throughout the code internally, stayed
untouched. This is also exactly why `lydia-server`'s `pyproject.toml`
declares two separate commands under `[project.scripts]`:
`lydia-server` (the server itself) and `lydia-server-token` (a small
helper for managing access tokens), both created from the one
`lydia-server` PyPI package.

## How a release actually gets published

Getting a new version of Lydia onto PyPI, so that everyone who later
runs `pip install --upgrade lydia-cli` receives it, is not a manual
"upload a file to a website" step. It's automated through a **GitHub
Actions workflow**, which is a script that GitHub itself runs on its own
servers, triggered by something happening in the repository, defined in
`.github/workflows/publish.yml`.

That workflow is set to trigger specifically when a **GitHub Release**
is published. A GitHub Release is a formal, named snapshot of the
project at a specific point in its history, created by picking a
**tag** (a permanent label attached to one exact commit, like `v0.2.0`,
marking "this is the code as it existed when we called it version
0.2.0") and then explicitly clicking "Publish release" on GitHub's
website. `docs/PUBLISHING.md` spells out the full sequence for cutting
one: bump the version number inside `pyproject.toml`, commit that
change, create the tag and push it, then create and publish the Release
from that tag on GitHub. The workflow is deliberately wired to only
that one specific action, not to every single push of code, and not
even to creating a tag by itself, so that publishing something to PyPI,
where the world can immediately download it, is always a distinct,
deliberate, reviewable button-press, never an accidental side effect of
ordinary day-to-day commits.

Once triggered, the workflow does three things on GitHub's own
temporary computer: it installs Python and a tool called `build`, runs
`python -m build` to produce the wheel and sdist files described
earlier, and then hands those files to a tool called
`pypa/gh-action-pypi-publish`, which uploads them to PyPI.

The way it's allowed to upload to PyPI at all is worth explaining
because it's a genuinely good security decision, not just plumbing. The
old-fashioned way to let an automated script publish to PyPI is to
create a secret password (an API token) for the PyPI account, and paste
it into GitHub as a stored secret the workflow can use. The problem with
that approach is that the secret sits there indefinitely, and if it
ever leaks, whether through a misconfigured log, a compromised
dependency, or anything else, an attacker has permanent, standing
access to publish
whatever they want under that project's name. Lydia's workflow instead
uses PyPI's **Trusted Publisher** mechanism, built on a standard called
**OIDC** (OpenID Connect). In plain terms: instead of GitHub handing
PyPI a long-lived password, GitHub issues a short-lived, single-use
proof of identity each time the workflow actually runs, essentially
saying "I am definitely the `publish.yml` workflow, running inside the
`levimackay/lydia-cli` repository, right now." PyPI, having been
told in advance (during one-time setup on its own website) to trust
exactly that specific workflow from that specific repository, exchanges
that proof for a temporary permission to upload, valid only for this
one run. There is no password sitting in GitHub for anyone to steal,
because there never was one to begin with. `docs/PUBLISHING.md` records
the exact one-time configuration this required on PyPI's side (the
project name, repository name, workflow filename, and a matching
`pypi` "environment" set up on the GitHub side, which is what the
workflow's `environment: pypi` line and its `id-token: write`
permission line are pointing at).

One more thing worth knowing before ever cutting a release: PyPI
versions are permanent once uploaded. A version number, once published,
can never be reused or replaced. Even deleting it from PyPI doesn't
free the number back up. If a release ships with a mistake in it, the
fix is always a new, higher version number, never a re-upload of the
old one. That's also why `docs/PUBLISHING.md` recommends a manual sanity
check before ever triggering a release: actually building the wheel
locally, installing it into a fresh, empty environment, and running
`lydia --version` and `lydia --help` against it. That check matters
because the automated tests, described in a later chapter, never do it
(they only ever install the code in a live-editing mode, not as an
actually built package), so a packaging mistake like a missing file
could slip through everything else and still end up permanently live
on PyPI.

Currently, only `lydia-cli` is wired up to this automated publishing.
`server/`'s `lydia-server` package is not yet published to PyPI at
all. `docs/PUBLISHING.md` notes it's a smaller-audience, self-hosted piece
of software, and if it's ever worth publishing, it would get its own
separate PyPI listing and its own separate trusted-publisher setup,
since the CLI and the server are expected to be released on their own
independent schedules rather than always moving in lockstep.

## `CHANGELOG.md` and `CONTRIBUTING.md`

Two more files at the root of the repo round out the picture of "what
does someone outside this project's own head need to find here."

`CHANGELOG.md` is meant to be a running, dated list of what changed in
the project over time, the kind of file a user or contributor checks to
see "what's new since I last looked." As of this writing it's very
sparse (just one dated entry, "General upkeep"), which is worth naming
plainly rather than glossing over: it hasn't been kept rigorously up to
date entry-by-entry the way `ROADMAP.md` has. `ROADMAP.md` is where the
real, detailed, dated history of what was built and why actually lives
in this project.

`CONTRIBUTING.md` is aimed squarely at someone who wants to submit a
code change to the project, an outside contributor, which `CONTRIBUTING.md`
itself notes has already happened a few times (a Linux scheduler fix, a
config bug fix, a CI fix). It lays out the two-package project layout
in brief, points to `CLAUDE.md` for the deeper architectural reasoning,
gives the exact commands to set up a development environment (installing
both packages in "editable" mode, meaning changes to the code take
effect immediately without reinstalling), explains how to run the test
suite, and sets expectations for what a good pull request looks like:
focused on one change, tested, with a commit message explaining *why*
not just *what*. It also explicitly says, twice over in slightly
different ways across the project's own files, that AI-attribution
trailers (like `Co-Authored-By: Claude`) should never be added to
commits here, by convention. That preference shows up again, phrased
even more strongly, in `CLAUDE.md` itself.

## Back to the big picture

`00-start-here.md` described Lydia as two halves, the always-needed CLI
and the optional server, packaged and distributed completely
separately, and this chapter is the "how" behind that one sentence.
Everything here has been about the *shell* around Lydia: how its code is
organized into folders so a person (or a later chapter of this
documentation) can find any given piece of functionality, and how that
code turns into an actual `lydia` command sitting on someone's computer,
installed for free, with no account and no API key, exactly matching
the core design decision from Chapter 0: "works with just Ollama
running locally." None of this chapter touched what happens *inside*
that command once it's installed and you actually run it and start
talking to it. That begins in `02-cli-and-repl.md`.
