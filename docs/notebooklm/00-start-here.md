# Lydia, Explained From Zero: Start Here

This is the first of a set of documents that explain everything about
Lydia: what it is, what every part of it does, and why it was built the
way it was. It assumes you have never written a line of code. Every
technical word gets explained the first time it shows up.

Lydia is not being developed further for now. This documentation exists
so the person who built it can explain, from memory, exactly what every
piece does and why it exists, without having to re-read the code itself.

## What problem is Lydia solving?

There are commercial tools like Claude Code, made by Anthropic, and
Codex, made by OpenAI, that let a person type a request in plain English
("add a login page to my app," "find the bug in this file") and have an
AI write and edit real code for them, running right there in a terminal
(a terminal is the black text-only window programmers use to type
commands directly to the computer, instead of clicking icons).

Those tools work by sending your code and your request over the internet
to a company's servers, where a large AI model reads it and sends back
an answer. That costs money per use (or a subscription), and it means
your code leaves your machine.

Lydia is a from-scratch rebuild of that same idea, but built to run
**entirely on your own computer**, for free, with no company in the
loop and no internet connection required for the core coding-agent
functionality. It talks to a program called **Ollama** (explained
below) instead of a company's servers. It will never be as good as
Claude Code or Codex, which are built by teams of professional
engineers with far more resources, and that was never the goal. The
goal was to build a working version of the same kind of system, end to
end, to prove it could be done and to learn how every piece works by
building it.

## The single most important idea: what is an "AI agent"?

A plain chatbot (like a basic version of ChatGPT) only does one thing:
you type text, it types text back. It cannot look at your files, run
commands, or change anything on your computer. It can only talk.

An **agent** is a chatbot that has been given a specific, limited set of
actions it is allowed to take, called **tools**, plus a loop that lets
it use them repeatedly before giving you a final answer. For Lydia,
those tools are things like "read a file," "write to a file," "run a
terminal command," "check what changed in this code project (git)."

So when you ask Lydia "fix the bug in `main.py`," here is what actually
happens, step by step:

1. Your question, plus a big set of instructions describing what Lydia
   is and what tools it has, gets sent to the AI model.
2. The model doesn't answer directly. Instead, it responds with a
   request: "I'd like to use the `read_file` tool on `main.py`."
3. Lydia's code actually reads that file off your disk and sends the
   file's contents back to the model as a new message.
4. The model reads that, and might ask to use another tool ("now let
   me use `search_code` to find where this function is called"), and
   the cycle repeats.
5. Eventually the model has gathered enough information and just
   replies with an answer in plain English, maybe including a proposed
   file edit.
6. If the model wants to *change* something (write a file, delete a
   file, run a command, commit to git), Lydia stops and shows you
   exactly what it wants to do, in a yes/no prompt, before doing it.
   Nothing destructive happens without you approving it first.

This request → tool → observe → respond cycle is called **the agent
loop**, and it is the core mechanism behind every AI coding assistant
that exists, including Claude Code and Codex. Lydia's version of this
loop lives in one specific file, which a later document
(`03-agent-loop-and-tools.md`) walks through function by function.

## What is Ollama, and why does Lydia depend on it?

The AI model itself, the actual "brain" that reads your question and
decides what to do, is not something Lydia builds. Building a
large language model (the technology behind ChatGPT, Claude, etc.) from
scratch requires enormous amounts of computing power and data that no
individual has access to. Instead, Lydia uses **open-weight models**:
AI models that companies like Alibaba (Qwen) or Google (Gemma) have
released publicly, for free, so anyone can download and run them on
their own hardware.

**Ollama** is a free program that makes running these models on your
own computer easy. You tell it "download this model," and afterward you
can send it questions over a local connection (`http://localhost:11434`,
a web address that only your own computer can reach, never the public
internet) and it answers using your computer's own processor or
graphics card. No data ever leaves your machine, and there's no bill.

Lydia's core design decision, stated everywhere in its own internal
documentation, is: **works with just Ollama running locally, no API
keys, no accounts, no cost.** Every other feature (see below) is opt-in
on top of that baseline.

## The two halves of the project

Lydia is really two separate, installable pieces of software living in
one folder:

- **`src/lydia/`** is the actual CLI (command-line interface, the thing
  you type `lydia` into your terminal to run) and agent. This is the
  part everyone needs. It talks to Ollama directly by default.
- **`server/`** is an optional second program, `lydia-server`. If you
  have a second, more powerful computer (Levi's case: a gaming PC with a
  good graphics card, versus his laptop), you can run Ollama on *that*
  machine and run this server on it too. Then your laptop's `lydia`
  command can be pointed at the powerful machine over the network
  instead of running the AI model on the weaker laptop hardware. Your
  files never leave your laptop, though. Only the AI's thinking process
  happens on the other machine. This is explained in full in
  `07-server-and-remote-control.md`.

These are packaged and distributed completely separately (see
`01-project-tour-and-packaging.md`), even though they live in the same
folder on disk and share some code.

## A short glossary (refer back to this any time)

- **CLI (command-line interface):** a program you interact with by
  typing text commands into a terminal, as opposed to clicking buttons
  in a window. `lydia` is a CLI.
- **Function:** a named, reusable block of code that does one specific
  job, like a mini-recipe. You "call" a function to run it, optionally
  handing it some inputs, and it can hand back a result.
- **File / module:** in Python (the programming language Lydia is
  written in), each `.py` file is called a **module**, which is a
  container holding a related group of functions.
- **Package:** a folder full of related modules, organized so they can
  be installed and imported (used) together as one unit. `src/lydia` and
  `server/lydia_server` are each a package.
- **API (Application Programming Interface):** a defined way for one
  program to ask another program to do something and get an answer
  back, usually over a network connection, using a fixed, agreed-upon
  format. When Lydia "talks to Ollama," it's making API requests to it.
- **JSON:** a simple, universal text format for structuring data as
  labeled fields, e.g. `{"name": "Lydia", "version": 1}`. Almost
  everything in this project that gets saved to disk, or sent over a
  network, is written as JSON.
- **Database:** an organized file for storing and quickly searching
  through structured data, instead of just a plain text file. Lydia
  uses **SQLite**, a database that lives entirely in one file on disk
  (no separate server program needed), for its semantic code search
  feature.
- **Git:** a version-control system, meaning a tool that tracks every change
  ever made to a folder of code over time, so changes can be reviewed,
  undone, or shared. Nearly every real software project uses it. Lydia
  has tools that let the AI agent make git commits on your behalf.
- **Repository ("repo"):** a folder of code being tracked by git.
- **Streaming:** sending a response piece by piece as it's generated,
  instead of waiting for the whole thing and sending it all at once.
  This is why AI chat tools appear to "type" their answer in real time
  rather than showing it all instantly.
- **Server / client:** a "server" is a program that sits and waits for
  requests over a network and responds to them; a "client" is a program
  that sends those requests. Ollama is a server (it waits for requests
  on your own machine); `lydia` is a client of it. `lydia-server` is
  *also* a server (Lydia's own optional relay server), and the `lydia`
  CLI can be a client of *that* too, in addition to being a client of
  Ollama directly.
- **Test / test suite:** a small program written specifically to check
  that another piece of code behaves correctly, by feeding it known
  inputs and checking the output matches what's expected. Lydia has
  over 400 of these (`11-testing-and-ci.md` covers this in depth), and
  they run automatically every time code changes, to catch mistakes
  before they ship.
- **CI (Continuous Integration):** an automated system (Lydia uses
  GitHub Actions) that runs the full test suite automatically every
  time new code is pushed, so a human doesn't have to remember to run
  the tests by hand.

## How to use the rest of these documents

Each remaining document covers one part of Lydia's folder structure and
can mostly be read on its own, though reading them roughly in order
(the numbers in the filenames) builds up context most naturally:

1. `01-project-tour-and-packaging.md`: the folder structure, and how
   the project gets turned into something people can `pip install`.
2. `02-cli-and-repl.md`: what happens when you type `lydia` and start
   chatting.
3. `03-agent-loop-and-tools.md`: the core agent loop and the "brain"
   logic that decides what to do and remembers what it's told.
4. `04-agent-tools-filesystem-git-terminal.md`: the actual actions
   the agent is allowed to take, and the safety rules around them.
5. `05-talking-to-ai-models.md`: how Lydia talks to Ollama, a remote
   Lydia server, or Google's Gemini, all through one shared design.
6. `06-search-the-codebase.md`: how Lydia finds relevant code in a
   large project without reading every file every time.
7. `07-server-and-remote-control.md`: the optional second program that
   lets a weak machine borrow a strong machine's AI power.
8. `08-config-and-secrets.md`: how settings and passwords/API keys are
   stored safely.
9. `09-automations-and-scheduling.md`: how Lydia can be told to do
   things on a recurring schedule, like a daily briefing.
10. `10-connectors-and-voice.md`: the personal-assistant extras (email,
    calendar, weather, stocks, news) and the always-listening voice mode.
11. `11-testing-and-ci.md`: how the project checks its own correctness
    automatically.

Every document ends with a short section tying its topic back to the
big picture described here.
