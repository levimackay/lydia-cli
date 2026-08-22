# The CLI and the REPL: What Happens When You Type `lydia`

This document covers everything that happens *before* the AI actually
starts thinking. It's the layer the previous document called `src/lydia/`'s
front door: the code that reads what you typed at the terminal, decides
which of Lydia's many capabilities you're asking for, and draws everything
you see on screen: the purple gradient logo, the colored panels, the
typing-effect as an answer streams in, the yes/no prompt before anything
risky happens.

None of the files covered here decide *what Lydia should do* in response to
a question. That thinking happens one layer deeper, in `agent/`, which is
document 3. Everything in this document is about **input and output**: what
you can type, and how what comes back gets put on your screen.

Four files make up this layer, all inside `src/lydia/cli/`:

- **`main.py`** is the biggest file, defining every command `lydia` accepts.
- **`chat.py`** is the interactive chat loop you get by typing `lydia` alone.
- **`ui.py`** handles every bit of terminal drawing: colors, panels, the
  streaming markdown effect, the confirmation dialog, the logo.
- **`__init__.py`** is empty. Its only job is to mark the `cli` folder as a
  Python package (see the glossary in document 1) so the other files inside
  it can be imported as `lydia.cli.main`, `lydia.cli.chat`, and so on. There
  is nothing to explain inside it because there is nothing in it.

## Typer: turning Python functions into terminal commands

`main.py` is built on a library called **Typer**. Its whole job is to take
ordinary Python functions and turn each one into something you can trigger
by typing a command at the terminal, complete with argument parsing, `--help`
text, and error messages, without Lydia's own code having to hand-write any
of that plumbing.

Concretely: `main.py` defines a function called `ask`, decorated with
`@app.command()`. That one decorator is what makes `lydia ask "fix the
bug"` a real, working terminal command. Typer reads the function's
parameters (`question`, `model`, `yes`) and automatically turns them into
the arguments and `--flags` you can pass. If you asked `main.py` to expose
30 different capabilities as 30 different Python functions, Typer is what
turns all 30 into `lydia <something>` commands with consistent, professional
`--help` output, for free.

Some commands are further organized into **sub-apps**, meaning a `Typer`
object nested inside another one, which is how `lydia config show` and
`lydia config set` end up sharing the `config` prefix, or `lydia restore
list` and `lydia restore apply` share `restore`. Each group (`config_app`,
`memory_app`, `restore_app`, `auth_app`, `briefing_app`, `automations_app`,
`listen_app`) is its own small `Typer` instance, attached to the main `app`
under a name.

### The commands you'll actually use day to day

**`lydia` (no command at all)** is the default. If you just run
`lydia` with nothing after it, Typer's `default()` function fires, notices
you didn't ask for a specific subcommand, and launches the interactive chat
(covered in the next section). This is the mode described throughout
document 1, the one that feels like Claude Code or Codex.

**`lydia ask "question"`** is a one-shot question. Instead of opening the
full back-and-forth chat, Lydia connects to the model, asks it exactly one
thing, prints the answer, and exits immediately. This is meant for scripts
and quick lookups ("what does this error mean," piped into another tool)
where you don't want an open conversation. By default `ask` does *not* give
the model any tools (no reading files, no running commands). It's a plain
question-and-answer. Add `--yes` (or `-y`) and it switches on the full tool
loop (reading/writing files, running commands, git) but with every
confirmation prompt auto-approved rather than asked interactively, which is
meant for scripts or CI pipelines where nobody is sitting there to type
"y." Even
in that mode, anything flagged as genuinely dangerous still gets refused
rather than silently allowed (more on that flag in the confirm-dialog
section below).

**`lydia analyze [path]`** scans a project folder (defaults to the
current one) and prints a quick summary table: what kind of project it
looks like, how many files, how many lines of code, which languages, which
files look like the important "manifest" files (like `package.json` or
`pyproject.toml`), and which source files are the largest. This is a fast
sanity check ("what am I even looking at") without invoking the AI model
at all. It's pure scanning of files on disk (`context/scanner.py`, covered
in document 6).

**`lydia models`** lists every AI model currently installed in Ollama,
with a human-readable size (like "4.7 GB"). Useful for checking what you
have before telling Lydia to use a specific one.

**`lydia init`** sets up a project to work with Lydia by creating a
`.lydia/` folder inside it, with a starter config file and a `.gitignore`
so Lydia's own housekeeping files (backups, conversation history, the
search index) never get accidentally committed to your project's git
history. It also tries to guess a sensible "verify command." For example,
if it sees a `pyproject.toml`, it suggests `pytest -q` as the command Lydia
should run to check its own work after making a change.

**`lydia index [path]`** builds (or refreshes) the semantic search index
used by the AI's `search_semantic` tool, so it can find relevant code by
meaning rather than exact text match. This one requires Ollama specifically
(not a remote server or Gemini) because of how the search index is built;
document 6 explains why in full.

### Configuration and memory commands

**`lydia config show`** prints every setting Lydia is currently using,
merged from the global config file (`~/.lydia/config.json`, applying to
every project) and the current project's config file if one exists, with
the project's values winning where they overlap. Sensitive values like API
keys are never printed in full, only "set" or "not set."

**`lydia config set KEY VALUE`** changes one setting, either globally or,
with `--project`, just for the current project. Some keys (API keys) are
treated specially and refuse `--project` outright, because a project config
file is meant to be safe to commit to git, and a secret sitting in a
committed file would leak to anyone with access to the repository. A
handful of the most sensitive keys (like a Gemini API key) don't go into
that plain JSON file at all. They're routed to your operating system's own
secure credential storage (the "keychain"), the same treatment given to
email/calendar login tokens. Document 8 covers this in depth.

**`lydia memory list` / `memory add <fact>` / `memory forget <n>`**: Lydia
can remember plain-English facts about a project across separate sessions
("this project uses tabs, not spaces," "always run tests before
committing"). These three commands view, add, and remove those remembered
facts from outside a chat session. The exact same actions are also
available as `/memory`, `/remember`, and `/forget` typed *during* a chat
session (see the slash-commands section below). They're two entry points
to the same stored list of facts, one for the terminal, one for mid-conversation.

### File-safety commands

**`lydia restore list`**: every time the AI writes to or deletes a file,
Lydia automatically saves a backup first. This command lists those backups,
newest first, numbered.

**`lydia restore apply N`** restores a file back to one of those backed-up
versions, by number. Before doing it, Lydia shows you a colored diff (a
side-by-side view of exactly what lines would change) and asks you to
confirm, using the same yes/no pattern described below for the AI's own
actions.

### `--version` and `--verbose`

These aren't subcommands but flags on the base `lydia` command itself.
`lydia --version` (or `-V`) prints the installed version number and exits
immediately, without starting a chat. `lydia --verbose` (or `-v`) turns on
much more detailed internal logging, useful for diagnosing a problem rather
than for normal use.

### Commands covered in later chapters

`main.py` also defines a large family of commands for Lydia's
personal-assistant side, which is a separate topic from the coding agent
covered by this document set. They're listed here just so you recognize
them when you see them, with a pointer to where each is actually explained:

- **`lydia auth login/status/logout <provider>`** connects Lydia to
  Gmail, Outlook, Canvas, or a phone-notification service (ntfy). See
  document 10.
- **`lydia briefing run/show`** and **`lydia briefing schedule
  enable/disable`** generate and schedule a daily personal summary. See
  document 9.
- **`lydia automate "<plain English>"`** plus the whole `lydia automations
  ...` family (`list`, `show`, `run`, `enable`, `disable`, `remove`,
  `tick`, `schedule enable/disable`) handle recurring tasks described in
  plain English. See document 9.
- **`lydia listen`** and **`lydia listen enable/disable/status`** run the
  always-on, "Hey Jarvis"-style voice assistant. See document 10.

## The REPL: what happens when you just type `lydia`

**REPL** stands for **Read-Eval-Print-Loop**. It's an old term from
programming, and it means exactly what it sounds like: a program that
*reads* something you typed, *evaluates* it (does something with it),
*prints* a result, and then loops back around and does it all again, over
and over, forever, until you deliberately quit. A REPL is the opposite of a
program that runs once and exits. It's the shape of every chat interface
you've ever used, including Claude Code, Codex, and Lydia's own `lydia`
command with no arguments.

`chat.py`'s `run_chat()` function is that loop. Before the loop even
starts, it does some setup: it connects to Ollama (or a remote Lydia
server, covered in document 5) and checks it's actually reachable, printing
a clear error and exiting if not; it picks which AI model to use
(`resolve_model()`, using whatever you've configured if it's actually
installed, otherwise auto-picking a sensible one, and warning you if the
chosen model is known
not to support "tool calling," meaning the AI wouldn't be able to read
files or run commands at all even if you asked it to); it scans the current
project if you're inside one; and it prints the startup banner (see below).
Then the loop itself begins, and it is genuinely simple at its core: show a
prompt, wait for you to type a line and press Enter, decide whether that
line was a slash command or an ordinary question, handle it, and go back to
showing the prompt. It keeps doing that until you type `/exit` or press
Ctrl-D (the traditional terminal shortcut for "end of input").

### prompt_toolkit: what makes typing at Lydia feel like typing at a real shell

The actual line of text you type doesn't come from Python's plainest,
built-in way of asking for input. It comes from a library called
**prompt_toolkit**, which is what gives Lydia's input line several
conveniences you'd otherwise only get from a proper command-line shell:

- **Command history.** Every line you type is saved to a file
  (`~/.lydia/prompt_history`), and pressing the up arrow cycles backward
  through what you've typed before, both in this session and in every
  previous one, exactly like pressing up arrow in a terminal to reuse a
  command.
  This is prompt_toolkit's `FileHistory` feature; Lydia just points it at a
  file inside its own settings folder.
- **A colored, mode-aware prompt.** The text you see before your cursor,
  `Lydia (ask) >`, for example, isn't a fixed string; it's rebuilt fresh
  every time you're asked to type something, and its color changes
  depending on which of Lydia's three **modes** you're currently in: cyan
  for `plan`, magenta for `ask`, green for `auto`. Modes control how much
  Lydia is allowed to do without stopping to ask you first; the underlying
  permission logic for that lives in `agent/`, covered in the next
  document. What matters here is just that the prompt's color is a live,
  always-visible reminder of which mode you're in.
- **A dedicated key binding for switching modes fast.** Pressing
  Shift-Tab, at any time while you're typing, instantly cycles you through
  plan → ask → auto → plan again, without needing to type anything or
  press Enter. This is registered through prompt_toolkit's `KeyBindings`
  system, which lets Lydia hook arbitrary key presses to arbitrary
  behavior. You'll also see the same effect from typing `/mode auto` (or
  `plan`/`ask`) directly, which is the same underlying action triggered a
  different way.

### Slash commands

Any line you type that starts with `/` isn't sent to the AI model at all.
It's intercepted by `_handle_slash()` and treated as an instruction to
Lydia's own interface, not a question. This is the same convention you may
recognize from Discord, Slack, or Claude Code itself: a leading slash means
"this is a command for the tool, not a message for the other side of the
conversation." The commands available are:

- **`/help`** prints a table of every slash command and what it does.
- **`/mode [plan|ask|auto]`** shows the current mode, or switches to a
  new one, the same effect as Shift-Tab described above.
- **`/model <name>`** switches which installed model this session uses,
  checking first that it's actually installed.
- **`/models`** lists every model installed in Ollama, marking which one
  is currently active with an arrow.
- **`/new`** clears the conversation so far and starts fresh, as if
  you'd just launched `lydia` again, without actually restarting the
  program.
- **`/remember <fact>`** saves a plain-English fact about this project
  that will still be there the next time you run `lydia` here. This is the
  same underlying action as `lydia memory add` from the terminal command
  list above.
- **`/memory`** lists everything currently remembered about this
  project.
- **`/forget <n>`** removes one remembered fact by its number, as shown
  by `/memory`.
- **`/automate <plain English>`** creates a recurring automation without
  leaving the chat (document 9 covers what an automation actually is).
- **`/exit`** (also `/quit` or `/q`, and Ctrl-D) ends the session.

Anything typed that starts with `/` but doesn't match one of these prints
an "unknown command" error rather than silently being treated as a real
question. Lydia never accidentally sends a mistyped slash command to the
AI model as if it were a genuine question.

Any line that *doesn't* start with `/` is treated as an ordinary message
and handed to `ChatSession.send()`, which is the method that actually kicks
off the request → tool → response cycle described in document 1, the part
covered in full in document 3.

## Rich: why the terminal looks like an application instead of plain grey text

Everything printed to your screen comes from a library called **Rich**:
the colored dot before an info message, the red "error:" prefix, the
tables produced by `lydia models` and `lydia analyze`, the boxed panel
around the startup banner, the diff coloring when reviewing a file
restore. Without it, a terminal program in Python can really only print
plain, uncolored monospace text one line at a time, the same way a very
old computer would have. Rich adds: color and bold/dim/italic styling
(triggered by writing tags like `[bold red]...[/bold red]` inside a string,
which Rich then turns into the actual terminal color codes), boxed panels,
proper tables with aligned columns, syntax-highlighted code and diffs, and
a "spinner" (`console.status("Indexing...")`, used while `lydia index`
runs) to show that something slow is happening in the background rather
than leaving you staring at a frozen-looking terminal.

### How streaming markdown actually works

This is the mechanic behind the "typing" effect you see when Lydia answers
you. The model's reply visibly grows on screen in real time rather than
appearing all at once after a long pause.

Recall from document 1's glossary that **streaming** means a response
arrives piece by piece rather than all at once. When Lydia sends your
question to the AI model, it doesn't get the whole answer back as one
block of text. It gets a continuous trickle of small chunks, each one
containing the next few words. `ui.py`'s `stream_response()` (and its
close relative `stream_agent_response()`, used when tools are involved)
does something simple but effective with that trickle:

1. It keeps a running list of every piece of text received so far.
2. Each time a new piece arrives, it joins the whole list back into one
   string and re-renders *that entire string* as Markdown (the lightweight
   text-formatting style used in the answer, where `**bold**`, bullet
   lists, and code blocks get turned into actual bold text, actual
   bullets, and so on) using Rich's `Markdown` class.
3. That re-render happens inside a Rich `Live` display, which is Rich's
   feature for redrawing a specific region of the terminal in place,
   erasing what was there and drawing the new, slightly-longer version,
   rather than printing a new line every single time. It's capped at 12
   redraws per second, which is fast enough to look smooth without wasting
   effort re-drawing dozens of times a second for changes too small to see.

So the "typing" effect you're watching isn't the model typing character by
character. It's Lydia re-rendering the *entire answer so far*, from
scratch, roughly twelve times a second, each time with a little more text
in it, and it happens to render fast enough that it looks like watching
text stream in live.

There's one more wrinkle, and it's directly tied to a detail from this
project's own engineering notes: some models (like the qwen3.5 family
Lydia is built around) are "thinking" models, meaning they stream their own
step-by-step reasoning as a *separate* stream of text before writing the
actual answer. If Lydia only displayed the final answer text, the screen
would appear to sit frozen and empty for several seconds while the model
reasoned in the background. Instead, `_thinking_preview()` shows the last
few lines of that reasoning, dimmed and prefixed with "thinking…", so you
can see the model is actively working. The moment real answer content
starts arriving, that preview disappears and the actual Markdown-rendered
answer takes over the same screen space.

## The confirmation dialog: how Lydia stops and asks before doing anything risky

Document 1 described the safety promise at the heart of the whole project:
if the AI model wants to change anything (write a file, delete a file,
run a terminal command, make a git commit), Lydia is supposed to stop and
show you exactly what it wants to do, in a yes/no prompt, before it
actually happens. `ui.py`'s `confirm()` function is the literal
implementation of that promise, at the display level. (What decides
*whether* a given action needs confirming in the first place, the "risk
tier" of a tool, is policy that lives one layer deeper, in `agent/`, and
is covered in documents 3 and 4. This document only covers how the
resulting yes/no prompt is drawn and answered.)

When something needs approval, `confirm()` receives a small package of
information describing the request: a short title, a longer "detail"
description of exactly what would happen, and a flag for whether it's been
marked as genuinely dangerous. It prints that as a boxed panel: a plain
accent-purple border normally, or a red border specifically when the
dangerous flag is set, so a risky action visually stands out from a routine
one before you've even read the text. If the detail text looks like a diff
(a file-edit preview showing removed and added lines), it's rendered with
the same colored diff syntax highlighting a developer would see in any
proper code tool, rather than as a wall of plain text. Then it asks
"Proceed?" using Rich's `Confirm.ask`, which is what actually waits for you
to type y or n (or just press Enter to accept the shown default) and
returns `True` or `False` back to the code that asked.

Two details worth knowing:
- If you cancel out of that prompt (Ctrl-C or Ctrl-D), it's treated as a
  "no," never as an error that crashes the program.
- The `auto_confirm()` function is a second, non-interactive version of the
  same idea, used only by `lydia ask --yes`, the scripting-friendly mode
  described earlier. With nobody present to type y/n, it approves
  everything *except* whatever's flagged dangerous, and flagged-dangerous
  actions default to declined rather than silently allowed. This
  deliberately mirrors the same "always stop for dangerous things" rule the
  interactive dialog enforces. It just can't literally ask a human, so it
  fails on the safe side instead.

## The gradient banner: a small, deliberate polish detail

The first thing you see when you launch `lydia` is a large block-letter
"LYDIA CLI" wordmark, colored in a smooth gradient sliding from blue
through violet to pink, flanked by a small diamond-shaped icon on either
side, inside a bordered panel showing which model and project are active.

The big block lettering itself comes from a library called **pyfiglet**,
which turns plain text into "ASCII art": large letters built out of
repeated block characters, arranged into a specific decorative font (Lydia
uses one called `ansi_shadow`). That part alone would already look like a
normal, single-colored banner. The gradient effect is Lydia's own code on
top of that: `_gradient_color()` picks three reference colors (a light
blue, a violet, and a pink), and for every single character across the
width of the logo, it works out how far along that character is
(0% at the far left edge, 100% at the far right) and mathematically blends
between the reference colors by that percentage, coloring each character
individually. The result reads as one smooth color sweep across the whole
word, rather than a single flat color or an abrupt jump between colors.
There's also a fallback: if your terminal window is too narrow to fit the
full art, it quietly falls back to plain bold text instead of drawing a
banner that would wrap or get cut off awkwardly.

None of this changes what Lydia *does*. It exists for the same reason
professional command-line tools bother with a clean startup screen at
all: this is explicitly a portfolio project, built in part to demonstrate
the same level of craft as the commercial tools it's modeled on
(Claude Code, Codex), and a first impression that looks considered rather
than default-grey-and-plain is part of that demonstration, even though it
has zero effect on whether the agent loop underneath it works correctly.

## How this fits into the bigger picture

Document 1 introduced the idea that Lydia is really an **agent loop**:
your question goes to the AI model, the model can ask to use tools, Lydia
carries those tool requests out and reports back what happened, and the
cycle repeats until the model gives you a final answer, stopping to ask
your permission first whenever it wants to change something. Everything in
*this* document has been about the parts of that story you can actually
see and touch: the `lydia` command and its subcommands (`main.py`), the
loop that keeps asking you for the next line of input and knows the
difference between a slash command and a real question (`chat.py`), and
every visual element along the way: the colored streaming answer, the
yes/no confirmation panel, the startup banner (`ui.py`).

What none of this document explains is *how the model decides what to
do*: how a plain-English question gets turned into "I should read this file,"
what the model is actually told about the project before it answers, or
how Lydia keeps track of an ongoing conversation and remembered facts
across many turns. That decision-making machinery, the actual "brain"
behind the request → tool → observe → respond cycle, lives in the
`agent/` package, and it's the subject of the next document,
`03-agent-loop-and-tools.md`.
