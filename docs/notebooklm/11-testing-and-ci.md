# 11. Testing and CI: How Lydia Checks Its Own Correctness

This is the final chapter in this set. Every earlier document described a
piece of Lydia: the CLI, the agent loop, the tools, the model clients, the
server, the config system, the automations and connectors. This one
describes something different: not a piece of the *product*, but the
machinery Lydia's author built to keep proving, automatically, that all of
those pieces still work correctly every time the code changes.

## What a test actually is, concretely

A **test** (defined briefly in `00-start-here.md`) is a small piece of code
that calls a real function from the actual program, feeds it a specific,
known input, and then checks (using a statement called an **assertion**)
that the result is exactly what it should be. If the result doesn't match,
the test fails loudly and immediately: a red message naming exactly which
check broke and what it saw instead of what it expected. Nothing gets
silently swallowed.

A concrete example, using a real safety rule from Lydia's codebase
(covered in `04-agent-tools-filesystem-git-terminal.md`): every filesystem
tool is supposed to refuse a path that tries to escape the project folder,
like `../../etc/passwd` or `../escape.py`. A test for this doesn't just
read the code and eyeball it. It actually calls the real function with
exactly that input and asserts that it raises the "path escapes the
project" error. This is `test_write_blocked_outside_root` in
`tests/test_filesystem_tools.py`:

```python
def test_write_blocked_outside_root(tmp_path: Path) -> None:
    from lydia.tools.paths import PathEscapesProjectError
    with pytest.raises(PathEscapesProjectError):
        propose_write(tmp_path, "../escape.py", "x")
```

That test runs every single time anyone changes anything in the codebase,
forever, as part of the full suite. If somebody later edits the path-safety
logic and accidentally breaks the escape check, this specific test turns
red immediately, instead of that mistake quietly shipping and only being
discovered later, maybe by something actually escaping the project folder
on a real machine.

Lydia's tests are written using **pytest**, the standard Python tool for
writing and running tests. A pytest test is just a Python function whose
name starts with `test_`, living in a file whose name starts with `test_`.
Running the `pytest` command finds every such function across the whole
project and runs them all, reporting which passed and which failed.

## Two packages, two separate test suites

Chapter 1 established that Lydia is really two separate installable
packages sharing one folder, `src/lydia` (the CLI) and `server/`
(the optional remote-inference server), and that separation carries
through to testing:

- The CLI package's tests live in `tests/` at the repo root (about 413 of
  them as of this writing) and run with a plain `pytest` from the repo
  root.
- The server package's tests live in `server/tests/` (about 61 of them)
  and have to be run from inside `server/` (`cd server && pytest`)
  because `server/` has its own `pyproject.toml` and is a genuinely
  separate installable package, not just a subfolder pytest happens to
  also look inside.

Both are typically installed into one shared development environment, but
they're tested as the two independent products they actually are.

## Rule one: unit tests never touch the network

A recurring rule stated directly in `CLAUDE.md` is that Lydia's regular
test suite should never require Ollama to actually be running, and should
never make a real network request. This matters for two practical reasons:
tests that depend on a real running AI model would be painfully slow (an
AI model can take seconds to respond; a test suite with hundreds of tests
needs to run in seconds total, not minutes), and they'd be unreliable:
whether a test passes would depend on what happens to be installed on
whatever machine runs it, including a cloud CI machine that has no Ollama
installed at all. Instead, the codebase uses three distinct techniques,
one per kind of dependency being avoided, and each shows up as a real,
readable pattern in the test files.

### Technique one: a fake stand-in for the AI model ("FakeClient")

The agent loop (`agent/loop.py`, covered in `03-agent-loop-and-tools.md`)
is the code that sends a message to the model, reads back whether it wants
to call a tool, runs that tool, and repeats. Testing this logic for real
would mean actually running a model and hoping it makes the exact tool
calls the test expects, which is slow and not reliably repeatable.

Instead, `tests/test_agent_loop.py` defines a **`FakeClient`** class: an
object that looks enough like a real model client (it has the same
`chat_stream` method the real `OllamaClient` has) but, instead of talking
to Ollama, just returns a pre-written, scripted sequence of responses that
the test author typed out by hand. Each call to `chat_stream` hands back
the next scripted response in the list:

```python
class FakeClient:
    """Returns a scripted sequence of responses, one per call to chat_stream."""
    def __init__(self, responses: list[list[ChatChunk]]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    def chat_stream(self, **kwargs):
        self.calls.append(kwargs)
        return iter(self.responses[len(self.calls) - 1])
```

This kind of stand-in object is often called a **test double** (or, more
specifically here, a "fake"). It's not the real thing, but it behaves
enough like the real thing, in exactly the ways the code under test cares
about, to make the test meaningful.

A real example: `test_tool_call_then_final_answer` scripts two responses.
First, a fake model response that says "I want to call the `read_file`
tool on `a.py`," then, a second fake response that says "The file sets x
to 1." The test then runs the real `run_agent_turn` function (the actual
production code, unmodified) against this fake client, and checks that:
the real `read_file` tool actually ran (it did, on a real temp file, see
below), the model was called exactly twice, and the conversation history
ended up recording the exact sequence `user, assistant, tool, assistant`.
This proves the loop's actual orchestration logic is correct (reading a
tool call, running it, feeding the result back, asking again) without
any dependency on what a real AI model would say, and without waiting on
one to think. It runs in a tiny fraction of a second.

Other tests in the same file use the same `FakeClient` to check what
happens when a human declines a proposed file write (`confirm=lambda req:
False`), when the model asks for a tool that doesn't exist, when a tool's
own code crashes with an unexpected exception, and when the model tries to
call tools forever without ever giving a final answer (the loop has a
hard iteration cap, `MAX_TOOL_ITERATIONS`, and there's a test proving it
actually stops there rather than looping forever).

### Technique two: a real, temporary, throwaway folder ("tmp_path")

Some of Lydia's tools genuinely need to touch a real filesystem, or run
real `git` commands. Faking those out would mean not really testing them
at all. Pytest solves this with a built-in feature called `tmp_path`: any
test function that asks for a parameter named `tmp_path` automatically
gets handed a path to a brand-new, empty, real folder on disk, created
fresh for that one test, and pytest cleans it up afterward. It's a real
folder with real files, and nothing about file reads, writes, or `git`
commands inside it is faked. It's just disposable and never touches any
of the author's actual projects.

`tests/test_filesystem_tools.py` uses this constantly. For example,
`test_apply_write_creates_file_and_backup_on_overwrite` actually writes a
real file into the temp folder, calls the real `apply_write` function on
it, and then checks, by actually reading the real file back off disk,
that its contents changed and that a real timestamped backup copy exists
in a real `.lydia/backups/` folder underneath the temp folder:

```python
def test_apply_write_creates_file_and_backup_on_overwrite(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("old\n")
    proposal = propose_write(tmp_path, "a.py", "new\n")
    apply_write(tmp_path, proposal)
    assert (tmp_path / "a.py").read_text() == "new\n"
    backups = list((tmp_path / ".lydia" / "backups").glob("*/a.py"))
    assert len(backups) == 1
```

`tests/test_git_tools.py` does the same thing one level up: its `repo`
fixture (a small setup function pytest hands to any test that asks for
it) runs `git init` inside a fresh `tmp_path`, then really runs `git
config user.email` / `git config user.name` inside that same throwaway
folder, before handing the path to the test. Everything downstream is a
real git repository being operated on by real `git` commands (`git
status`, `git add`, `git commit`), just one that lives for a few
milliseconds in a temp directory and is thrown away the moment the test
finishes, never touching a real project.

### Technique three: a fake network layer ("httpx.MockTransport")

The model-talking code in `llm/client.py` (covered in
`05-talking-to-ai-models.md`) makes real HTTP requests using a library
called `httpx`. Testing it for real would mean an actual Ollama daemon
sitting at `http://localhost:11434` and answering. Instead,
`tests/test_client.py` uses a feature `httpx` itself provides for testing:
`httpx.MockTransport`. Instead of connecting to a real network address, a
mock transport is a small handler function the test writes, which gets
handed every outgoing request and gets to decide exactly what fake
response to send back, without any of it ever leaving the process, let
alone reaching an actual server.

```python
def test_chat_stream_accumulates_chunks() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        payload = json.loads(request.content)
        assert payload["stream"] is True
        return httpx.Response(200, content=ndjson(
            {"message": {"content": "Hel"}, "done": False},
            {"message": {"content": "lo!"}, "done": False},
            {"message": {"content": ""}, "done": True, "eval_count": 5},
        ))
    chunks = list(make_client(handler).chat_stream("m", [Message("user", "hi")]))
    assert "".join(c.content for c in chunks) == "Hello!"
```

This lets the test check two things at once: that the client sends the
request the way it's supposed to (checked inside the handler itself, by
inspecting the outgoing request), and that it correctly interprets
whatever comes back (checked afterward, on the parsed result), both
without any real Ollama daemon existing anywhere.

**The round-trip test.** `CLAUDE.md` calls out one specific test as
important enough to name directly:
`test_serialize_chat_chunk_round_trips_through_parse_chat_line`. This one
tests something subtler than a single request/response. Recall from
`05-talking-to-ai-models.md` and the server chapter that the client/server
split needs the exact same "shape" of data understood on both ends: the
`lydia` CLI's `RemoteClient` reads a line of data coming from a Lydia
Server and turns it into a `ChatChunk` object (`parse_chat_line`), while
the server side takes its own `ChatChunk` object and turns it back into
that same line of data to send out (`serialize_chat_chunk`). Those two
functions have to be exact inverses of each other, or a real conversation
between a `lydia` client and a real `lydia-server` would silently corrupt
data somewhere on the wire, dropping a tool call or garbling the model's
reasoning text.

The round-trip test proves this without needing a real server or a real
network connection at all: it builds one `ChatChunk` object by hand (with
text content, "thinking" text, a tool call, and stats attached), runs it
through `serialize_chat_chunk` to turn it into the wire format, then
immediately runs that straight back through `parse_chat_line`, and asserts
the result is identical, field by field, to the original. If a future
change to one of those two functions ever stops matching the other (say,
someone renames a field on one side but not the other), this test catches
it instantly, entirely offline, long before anyone would notice a real
client and a real server silently disagreeing about data over an actual
network connection.

## CliRunner: "typing" commands at the CLI without a real terminal

`tests/test_cli_commands.py` tests the actual `lydia` command-line tool:
things like `lydia analyze`, `lydia config show`, `lydia init`, `lydia
restore list`. Normally exercising these would mean a human sitting at a
real terminal, typing a command, and reading what prints back. Instead,
Typer (the library `lydia`'s CLI is built with, mentioned in
`02-cli-and-repl.md`) ships a testing tool called `CliRunner`. A test
creates one `CliRunner` and then calls `runner.invoke(app, [...])`, handing
it the exact list of words that would otherwise be typed after `lydia` on
a real command line. This simulates a full command invocation, in
memory, with no real terminal involved, and hands back an object with the
exit code and everything that would have printed to the screen.

```python
def test_analyze_detects_python_project(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "app.py").write_text("x = 1\n")
    result = runner.invoke(app, ["analyze"])
    assert result.exit_code == 0
    assert "Python" in result.stdout
```

This is combined with `tmp_path` (a fresh temp folder that gets
`chdir`'d into for every test, via an `isolated_cwd` fixture) so each test
runs `lydia`'s actual command logic against a real, disposable little
project folder it built by hand for that one test, proving, for real,
that `lydia analyze` correctly notices a `pyproject.toml` file and reports
"Python," or that `lydia init` really creates a real `.lydia/config.json`
file with the right contents.

## What's deliberately not tested this way, and why

Not everything in Lydia goes through this automated, fake-everything
approach. `ROADMAP.md` and `CLAUDE.md` are explicit that `lydia ask`,
`lydia models`, and the live interactive chat REPL are **not** covered by
the automated test suite the way `analyze`/`config`/`init`/`restore` are.
Those get verified by hand, against a real running Ollama daemon, instead.

The reasoning is plain: some behavior can only be genuinely proven by
actually running the real model and watching what it actually does. A
concrete example straight from `CLAUDE.md`: whether a specific downloaded
model (say, `qwen2.5-coder:7b`) actually supports Ollama's structured tool
calling, or whether it just writes what *looks* like a tool call as plain
text inside its answer (which Lydia's parsing code would never recognize
as a real tool call, silently), is not something a scripted fake response
can prove one way or the other. A `FakeClient` test can only ever prove
"if the model behaves like *this*, then Lydia's code handles it
correctly." It cannot prove that a given real model actually *does*
behave that way. That has to be checked directly, for example with a real
`curl` request straight to Ollama's own `/api/chat` endpoint and reading
the real response, exactly as `CLAUDE.md`'s "Ollama integration gotchas"
section walks through. Similarly, the chat REPL's live, interactive,
streaming feel, including known quirks like `Confirm.ask` fighting with
`prompt_toolkit` over a non-terminal input, is the kind of thing that has
to be watched happening in a real terminal to be believed, not
reconstructed from a scripted fake.

So the rule in practice is: anything about *Lydia's own logic* (parsing a
response shape correctly, refusing an unsafe path, orchestrating a tool
call loop correctly, printing the right thing for a given input) gets an
automated test with a fake standing in for the network/model/filesystem
where appropriate. Anything about *whether a real external thing (a
specific downloaded model, a real terminal) actually behaves the way
Lydia assumes* gets verified by hand, against the real thing, because a
fake can't prove a fact about something it isn't.

## CI: what happens automatically on every push

**CI (Continuous Integration)**, defined briefly in `00-start-here.md`, is
the automated system that runs this whole test suite without anyone
having to remember to do it by hand. Lydia's CI configuration lives in
`.github/workflows/test.yml`, and it uses **GitHub Actions**, GitHub's
own built-in automation system, which runs jobs on GitHub's own cloud
computers whenever something happens to a repository hosted there (like a
push, or a pull request).

Here's exactly what that file sets up, and what happens every single time
new code is pushed to the `main` branch, or a pull request is opened
against it:

1. GitHub spins up a fresh, clean, temporary macOS computer in the
   cloud (`runs-on: macos-latest`), one that has never seen this project
   before and gets thrown away again after the job finishes. Nothing
   about the state of this machine is inherited from any previous run.
2. It checks out a copy of the exact code being pushed (`actions/checkout`).
3. It installs Python.
4. It installs both of Lydia's packages, the same way a human developer
   would (`pip install -e ".[dev]"` for the CLI package, then `pip install
   -e "server/[dev]"` for the server package), which also pulls in every
   dependency both packages need, including pytest itself.
5. It runs the CLI package's full test suite (`pytest`, from the repo
   root).
6. It runs the server package's full test suite (`cd server && pytest`).

If any single test in either suite fails, the whole run is marked failed
and shows up clearly on GitHub, tied to that exact push or pull request.
That's the entire point: a mistake gets caught automatically, on GitHub's
infrastructure, without anyone needing to have remembered to run `pytest`
themselves before pushing.

**Why four Python versions.** The `strategy: matrix` block repeats this
entire process four separate times, once each for Python 3.11, 3.12,
3.13, and 3.14, meaning eight full test runs happen per push (four Python
versions × two packages), all in parallel. This matters because Python
itself changes slightly from one release to the next (a function's exact
behavior, a warning that becomes an error, a default that flips), and
code that works perfectly on the newest version isn't guaranteed to work
on an older one still in wide use, or vice versa. Testing against all
four of the versions Lydia claims to support catches a break that only
shows up on the oldest or newest one, instead of assuming "it worked on my
machine" generalizes to everyone else's.

## The git-identity gotcha, and why CI needs no special setup for it

One specific, easy-to-miss detail is worth calling out on its own, because
it's the kind of thing that looks like it should be a real CI headache but
turns out not to be, by design. Git tools (covered in
`04-agent-tools-filesystem-git-terminal.md`) need to know *who* is making
a commit (a name and an email address) to attribute that commit to. On
a developer's own laptop, that's normally set once, globally, with `git
config --global user.name` / `user.email`, and every repo on that machine
just inherits it. But a fresh cloud CI machine, spun up new for every run
as described above, has no such global git identity configured at all,
and never will, because it's thrown away right after.

Lydia's git-tool tests don't rely on one existing. As shown earlier, the
`repo` fixture in `tests/test_git_tools.py` sets its own throwaway git
identity (`git config user.email t@example.com` and `git config
user.name Test`) *inside* the temporary test repo itself, every single
time that fixture runs, rather than assuming any identity is already
configured on whatever machine happens to be running the tests. That's
exactly the same "real-but-disposable folder" pattern described above,
just extended to cover the one extra bit of setup a real `git commit`
needs to succeed. Because of this, `ROADMAP.md`'s own "Done" list for CI
notes it was specifically verified against a clean clone with no
pre-existing git identity, proving the CI machine needs zero extra
one-time setup for this to work, ever.

## What "all green" actually proves, and what it doesn't

When the whole suite passes (every test in both packages, on all three
Python versions), that's real, meaningful evidence. It means every
behavior someone thought carefully enough about to write a test for is
still working exactly as that test describes, checked automatically,
every single time, with no chance of a human simply forgetting to run the
check by hand before pushing. That's not a small thing. It's the
difference between "I'm pretty sure this still works" and "a machine just
proved, a few minutes ago, that this specific list of behaviors still
works."

But a passing suite is not the same claim as "the whole program has no
bugs." A test suite can only ever check what someone actually thought to
write a test for. It says nothing about behavior nobody anticipated, edge
cases nobody considered, a model doing something unexpected that no fake
response ever scripted, or a real interaction (like the REPL's terminal
quirks, or a specific model's tool-calling behavior) that was deliberately
left out of the automated suite for the reasons explained above and is
only checked by hand, occasionally, rather than on every single change.
"All green" is strong, continuously-refreshed evidence for everything the
existing ~470-some tests actually cover, not a guarantee that covers
everything else.

## Back to the big picture

`00-start-here.md` frames Lydia as a real, working rebuild of the same
kind of system behind Claude Code and Codex, proof that the whole
request → tool → observe → respond agent loop, the model-talking layer,
the filesystem/git/terminal tools, and the optional client/server split
can genuinely be built end to end. This chapter is the part of that
project that makes it possible to say that with confidence rather than
hope: hundreds of small, fast, fully automated checks, each one calling
real production code with a known input and a known expected result,
running on every single push, on a fresh machine, across every supported
Python version, catching a broken path-safety rule or a mismatched wire
format the moment it's introduced, while the handful of things that
genuinely can't be faked (does this specific model actually support tool
calling, does the live chat REPL actually feel right in a real terminal)
are verified deliberately by hand against the real thing instead of
being tested in a way that would only look rigorous without actually
proving anything.

This is the last chapter in this set. Together with the ten before it, it
covers everything in Lydia's two packages: what each piece does, why it
was built the way it was, and, with this chapter, how the whole project
keeps proving to itself, automatically, that it still works.
