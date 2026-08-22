# Chapter 3: The Agent Loop and Lydia's Memory

`00-start-here.md` described the agent loop conceptually as a cycle:
request → tool → observe → respond. This chapter opens the hood on that
cycle. Almost everything that makes Lydia an "agent" instead of a plain
chatbot lives in four small files inside `src/lydia/agent/`:

- `prompts.py` assembles the invisible instructions sent to the AI
  model before your conversation even starts.
- `loop.py` runs the actual plan → call-tool → observe → respond cycle,
  one user message at a time.
- `memory.py` writes a plain, permanent log of every message in a
  session, purely for your own record-keeping.
- `facts.py` keeps a short, curated, capped list of things worth
  remembering forever, which gets fed into every future system prompt.

Two of these (`memory.py` and `facts.py`) are easy to confuse because
both are called "memory" in casual conversation, but they solve
completely different problems. A large chunk of this chapter is devoted
to pulling them apart, because understanding that split is the key to
understanding how Lydia can "remember" things across sessions without
every conversation growing forever.

## Part 1. The system prompt: Lydia's invisible instruction sheet

### What a system prompt actually is

Every time you send Lydia a message, the model doesn't just see your
message. Behind the scenes, Lydia prepends a large block of text,
written in plain English rather than code, that tells the model who it's
supposed to be, how it's supposed to behave, and exactly what tools it's
allowed to use. This block is called the **system prompt**. It's sent
as a separate message with the special role `"system"`, ahead of the
actual conversation, on *every single turn*. The model has no persistent
memory of its own between requests, so Lydia has to remind it who it is
every time.

Think of it like a job description and a company handbook handed to a
new employee before their first phone call with a customer: the customer
(you) never sees that handbook, but it shapes every word the employee
says.

`agent/prompts.py` is the file that writes this handbook. The core of it
is a large multi-paragraph string called `SYSTEM_PROMPT`, and a function
called `build_system_prompt` that takes that base text and layers
additional, situation-specific information on top of it before every
turn.

### What's written into the base prompt

The hardcoded `SYSTEM_PROMPT` string tells the model, in order:

- **Who it is:** "a senior software engineer working in the user's
  terminal."
- **How to talk:** be concise, answer first, use Markdown, use fenced
  code blocks, ask one clarifying question when something is ambiguous,
  and, importantly, never invent files, APIs, or command output it
  hasn't actually seen. That last rule exists because a language model
  will otherwise happily guess at plausible-sounding file contents
  rather than admit it doesn't know, which is exactly the kind of
  mistake an agent with real tools can't afford to make.
- **What tools exist and how to choose between them:** read/search the
  project, edit/write/delete files, run shell commands, and drive git.
  The prompt gives concrete guidance on *which* tool to reach for in
  which situation. For example, use `find_files` when you know a
  filename pattern but not its location, `search_code` when you know the
  exact text to search for, and `search_semantic` (a "what does roughly
  this idea" search, covered in a later chapter) only when told it's
  already indexed. It also tells the model to prefer the smallest
  possible change, to prefer `edit_file` (a targeted snippet replacement)
  over `write_file` (a full-file rewrite) unless the whole file is being
  replaced, and to bundle several changes to one file into a single
  `multi_edit_file` call rather than several separate edits, so the
  user reviews one diff and gives one approval instead of being
  interrupted repeatedly.
- **The approval rule:** file writes, edits, deletes, commits, and pushes
  ask the user to approve first, unless the session is in "auto" mode,
  in which case only genuinely destructive actions still ask. The model
  is told this confirmation is handled for it automatically, so it
  should just call the tool and read the result rather than trying to
  ask the user itself.
- **What to do if a tool call fails or is declined:** don't silently
  retry, explain what happened and ask how to proceed.
- **The `remember` tool:** described here so the model knows it exists
  and when to use it: for durable facts about the project (tech stack,
  conventions, decisions), not one-off task details. This tool is the
  first of the three ways facts get saved, covered in Part 4 below.
- **The `update_todos` checklist tool:** for any task with roughly three
  or more distinct steps, the model is told to publish a visible
  checklist up front and update it as it completes each step, so a long
  task doesn't look stalled with no visible progress.

There's a second, entirely separate prompt in the same file,
`BRIEFING_SYSTEM_PROMPT`, used only for the daily personal-briefing
feature (covered in a later chapter). It tells the model to compose a
checklist from data it's handed directly, and explicitly forbids it from
pretending it can call a tool, because in that mode it can't.

### `build_system_prompt`: assembling the final version per turn

The hardcoded text above is only the starting point. `build_system_prompt`
is the function that's actually called before every message, and it
glues four more pieces onto that base text, each one optional:

1. **A live snapshot of the current project**, if one was provided (a
   `ProjectSummary`, built by the project scanner covered in the next
   chapter): the project's root folder, what kind of project it looks
   like, how many files and lines of code it has, which programming
   languages appear and in what proportion, and a short list of key
   files (things like `package.json` or `pyproject.toml`). This gives
   the model useful context about the codebase without it having to go
   read a dozen files just to get its bearings.

2. **Remembered facts**, if any exist. This is the bridge between the
   long-term facts store (`facts.py`, Part 4 below) and the model's
   actual awareness of them: each fact's text gets turned into one bullet
   point under the heading "Remembered facts about this project (from
   earlier sessions)" and appended to the prompt. This is the entire
   mechanism by which something "remembered" in one session becomes
   known in a completely different, later session. There's no separate
   channel for it. The facts are just plain text folded into the same
   instruction block the model reads before answering anything.

3. **A verify-command reminder**, if the project has one configured (e.g.
   "run the test suite") and the session isn't in read-only plan mode:
   after making code changes, the model is told to actually run that
   command and check the result before declaring the work done, rather
   than assuming its edit was correct.

4. **A plan-mode addendum**, if the session is currently in "plan mode."
   In this mode the model temporarily doesn't have the tools that change
   anything (`write_file`, `edit_file`, `delete_file`, `run_command`,
   the git-writing tools), only read-only ones. The addendum explains
   this restriction to the model directly and tells it to research
   thoroughly, then present a plan in its written answer instead of
   acting, and ask the user to switch modes before anything actually
   changes.

The result of all this is one long string, rebuilt fresh before each
message is sent (not just when something changes), so if you switch
modes or a fact gets added mid-session, the very next message already
reflects it.

## Part 2. `run_agent_turn`: the loop itself, step by step

`agent/loop.py` is, by the project's own account, the single most
important file in the whole codebase. It's the actual decision loop.
The function `run_agent_turn` runs one complete "turn": everything that
happens from the moment you hit enter on a message to the moment Lydia
gives you its final answer, including any number of tool calls in
between.

Here's the story of what happens, told the way it actually unfolds in
the code:

1. **The system prompt and the running conversation are combined** into
   one request. The system prompt (built as above) is placed first,
   then every prior message in the conversation so far (your earlier
   questions, the model's earlier answers, and the results of any
   earlier tool calls) all get sent to the model together. The model
   has no memory of its own; the *entire* context it can see is
   whatever's in this one request.

2. **The request is sent to the model as a stream**, along with the list
   of every tool it's allowed to use this turn (converted into the JSON
   schema format language models expect, a precise description of each
   tool's name, purpose, and what arguments it takes).

3. **The code checks: did the model ask to use a tool, or did it just
   answer?** The model's streamed response can come back one of two
   ways: plain text (a direct answer), or a request to call one or more
   tools (e.g. "call `read_file` with path `main.py`"). This is the
   fork in the road that the whole loop is built around.

   - **If there were no tool calls,** that response *is* the final
     answer. It gets added to the conversation history and returned
     immediately, and the turn is over.

   - **If the model did ask for one or more tools,** the loop does not
     stop. For each requested tool call, it looks up the matching tool
     by name, actually executes it (reads the real file, runs the real
     shell command, whatever it is), and appends the tool's result back
     into the conversation as a new message with the special role
     `"tool"`. Then it loops back around to step 1 and sends the model
     another request, this time including everything that just
     happened, so the model can see what the tool returned and decide
     what to do next: ask for another tool, or finally answer.

4. **This repeats, up to 25 times per turn** (`MAX_TOOL_ITERATIONS`).
   That cap exists as a safety net against a model that gets stuck in a
   loop calling tools without ever converging on an answer. If it hits
   the cap, Lydia gives up gracefully with a message telling the user it
   stopped and suggesting the request be broken into smaller steps,
   rather than spinning forever.

The result returned to the caller is always the same shape: the final
text answer, plus a dictionary of generation statistics (things like
token counts) from the last model response.

### Where a bad tool call goes: `execute_tool`

Actually running a tool is handled by a small helper, `execute_tool`.
It's worth calling out because of what it deliberately does *not* do:
swallow errors silently. If the model asks for a tool name that doesn't
exist, it returns a clear "unknown tool" result rather than crashing. If
the tool raises a known, expected error (a bad path, a file operation
that failed for a sensible reason), that error's message is captured and
handed back to the model as the tool's result, not shown to the user as
a crash but fed to the model as information, exactly the same way a
successful result would be. And if something genuinely unexpected goes
wrong, that too gets turned into a text result describing the error
rather than letting the whole request blow up. This matters because it
means the model actually gets to see when it did something wrong and
try a different approach on the next iteration, instead of the whole
conversation just dying.

### `stream_fn`, `on_tool_call`, and `on_tool_result`: separating the brain from the screen

Notice that nowhere in the description above did anything get "printed
to the screen." That's deliberate, and it's one of the most important
design decisions in this file. `run_agent_turn` never draws anything
itself. It takes three optional callback functions as arguments and
calls *them* at the right moments, leaving all the actual screen-drawing
to whoever passed those functions in:

- **`stream_fn`** is handed the raw stream of response pieces coming
  back from the model and is responsible for turning it into a finished
  result (the full text plus any tool calls). The default version
  (`default_stream_fn`) just silently collects everything with no
  visual output at all, which is useful for non-interactive callers.
  The real interactive chat program (covered in the next chapter's sibling
  document on the CLI) passes in a different version that also prints
  each chunk of text to the terminal as it arrives, which is what
  creates the "typing" effect described as *streaming* in the glossary.

- **`on_tool_call`** is called the instant the model asks for a tool,
  before the tool actually runs. This is the hook that lets the
  interactive program show something like "reading main.py..." on
  screen.

- **`on_tool_result`** is called right after a tool finishes, with both
  the original call and its result. This is the hook that lets the
  interactive program show the outcome, like a colored diff of a file
  edit or a green checkmark.

All three are optional and default to doing nothing. The point of
routing everything through them, instead of just calling `print()`
directly inside the loop, is to keep **decision logic** (what tool to
call, when to stop, how to handle an error) completely separate from
**screen-drawing logic** (how something looks in the terminal). The loop
itself has no idea whether it's being driven by a real terminal, a
different kind of program entirely, or nothing at all.

## Part 3. Why this design makes the loop testable without Ollama

Because `run_agent_turn` only depends on a `ModelClient`, a defined,
abstract shape of "something that can be asked to chat and streams back
a response," rather than a hardcoded connection to the real Ollama
program, it's possible to hand it a **fake client** during testing
instead of the real thing.

A fake (also called a "test double" or "stub") is a small stand-in
object built specifically for testing, that pretends to be the real
thing well enough to fool the code being tested, but is entirely
scripted. Instead of actually asking an AI model anything, a fake client
used in Lydia's test suite is just told in advance, in code, "when
you're asked to chat, respond with exactly this canned answer." And
sometimes that canned answer is itself "ask to call this specific tool
with these specific arguments." The test can then verify that the loop
handled that scripted situation correctly: did it call the right tool,
did it feed the result back in, did it stop at the right point.

This matters for a few concrete reasons:

- **Speed.** A real request to Ollama takes real time, often seconds,
  especially for a "thinking" model. A fake client returns its canned
  answer instantly, so hundreds of tests can run in well under a second
  total.
- **No dependency on Ollama running at all.** The project's own test
  suite (over 400 tests) is required to run cleanly with no network
  access and no Ollama daemon installed, including in the automated CI
  system that checks every code change. If the agent loop's tests
  required a real, running AI model, the whole test suite would be
  fragile and slow, and it would fail on any machine (including the
  automated CI runner) that doesn't have a model downloaded.
- **Exact, repeatable scenarios.** A real AI model's answers vary from
  run to run. A fake client always gives back exactly the same
  response, which is what makes it possible to test something precise
  like "does the loop stop after exactly 25 tool-call iterations."
  That's not a scenario you could reliably force out of a real model on
  demand.

This is the same reason `stream_fn`, `on_tool_call`, and `on_tool_result`
being injected callbacks matters for testing, not just for separating
concerns cleanly: a test can pass in its own tiny recording functions
for those hooks too, and afterward check exactly what was recorded,
with no real terminal, no real user, and no real screen involved at any
point.

## Part 4. Two different kinds of "memory," and why both exist

This is the part of the agent that's easiest to get backwards, so it's
worth being explicit: **Lydia has two completely separate systems that
both get casually called "memory," and they solve different problems on
purpose.**

### `agent/memory.py`: the session transcript (short-term, not fed back)

Every message sent in a chat session, yours and the model's alike, gets
appended, one line at a time, to a file on disk: one file per session,
named with a timestamp, stored as `.jsonl` (each line is one message,
written as JSON, hence "JSON Lines"). Writing one line at a time,
immediately, rather than building up the whole file in memory and
writing it once at the end, means the log is crash-safe: if Lydia (or
your computer) crashes mid-session, everything said up to that point is
already safely on disk.

This is purely a historical record. Think of it as a saved chat
transcript, the kind you might export from a messaging app. It exists so
a past conversation can be found and re-read later (`list_sessions` and
`load_session` are the functions that do that lookup), but critically:
**nothing in this file is automatically fed back into a future
conversation.** Starting a new session does not make the model aware of
what was said in a previous one. It's short-term, in the sense that it
only matters for reviewing the past, not for shaping the model's future
behavior.

### `agent/facts.py`: the curated, permanent memory (long-term, always fed back)

`facts.py` is the opposite in almost every respect. Instead of a
complete, ever-growing transcript, it holds a short list of individual,
standalone sentences, or "facts," like "this project uses PostgreSQL" or
"run tests with `just test`." Each fact is just a piece of text plus the
timestamp it was created.

Two design choices make this genuinely different from the transcript,
both deliberate:

- **It's capped.** A constant, `MAX_FACTS`, currently set to 100, caps
  how many facts can exist at once. Adding a 101st fact silently drops
  the oldest one to make room. This exists specifically so this list
  can never grow without bound, because, as covered in Part 1, *every
  single fact in this file gets pasted into the system prompt on every
  single future turn.* An ever-growing, unbounded list here would mean
  every future conversation gets slower and more expensive (in terms of
  how much of the model's limited context window it burns through) the
  longer a project is used. The transcript in `memory.py` has no such
  cap, precisely because it's never re-sent to the model. It just sits
  on disk for a human to read later if they want to.

- **It's curated, not automatic.** Nothing gets added to this list just
  by being said in conversation. A fact only lands here through one of
  three deliberate actions (covered next), never as a side effect of
  ordinary chatting. This is what keeps the list small and genuinely
  useful instead of becoming a noisy dump of everything that was ever
  typed.

The plain way to describe the difference out loud: `memory.py` answers
"what did we just say," and it's a complete but disposable record, kept
for your own reference. `facts.py` answers "what should Lydia always
know about this project," and it's a short, hand-picked list,
deliberately small enough to hand to the model every single time so it
never has to be told the same important thing twice.

### The three ways a fact actually gets added

All three of these end up calling the same underlying function,
`facts.remember`, and all three write to the exact same file:
**`.lydia/memory.json`**, inside the current project's folder. It's a
plain JSON file, human-readable, that you could open and read yourself
if you wanted to see exactly what Lydia has been told to remember.

1. **The model calls the `remember` tool mid-conversation.** This is the
   tool described back in Part 1. The model itself decides, based on
   something you said, that it's worth saving permanently, and calls the
   tool the same way it would call `read_file` or `run_command`. This
   tool is marked "safe" (no approval prompt needed) since it only
   writes a short note to a small memory file, not a project file.
   Immediately after a turn finishes, the running chat session reloads
   the facts from disk and rebuilds the system prompt with them, so if
   the model remembers something on message 3, it's visible to it
   starting on message 4, in the very same session, without needing a
   restart.

2. **You type `/remember <fact>` directly**, as a slash command inside
   the interactive chat (there's also `/memory` to list everything
   currently remembered, and `/forget <n>` to remove one by its listed
   number). This is the manual override for when you want to tell Lydia
   something to remember without waiting for it to decide to use its
   tool.

3. **You run `lydia memory add "<fact>"` from an ordinary terminal
   command**, completely outside of any chat session (with matching
   `lydia memory list` and `lydia memory forget <n>` commands). This is
   the same mechanism available even when you're not actively chatting
   with Lydia at all.

Whichever of the three you use, the effect is identical and permanent:
the fact is appended to `.lydia/memory.json`, and from that point on,
every future session in that project, today, tomorrow, or next month,
will have that fact folded into its system prompt automatically,
courtesy of `build_system_prompt` in Part 1. That's the entire mechanism
behind Lydia appearing to "remember" something about a project across
completely separate runs of the program: there's no ongoing connection
or database server involved, just a small JSON file being read back in
every time.

## How this connects back to the big picture

`00-start-here.md` described the agent loop in the abstract, as the
"request → tool → observe → respond" cycle that's the core mechanism
behind every AI coding assistant, including the commercial ones Lydia is
modeled after. This chapter has shown exactly how that abstract cycle is
implemented as real, runnable code: `build_system_prompt` writes the
instructions the model receives before anything else, `run_agent_turn`
is the loop that actually walks through call-tool-and-observe as many
times as needed before handing back a final answer, and the split
between `memory.py` and `facts.py` is what lets Lydia distinguish
between "keep a record of this conversation" and "actually remember
this permanently, everywhere, from now on."

One thing this chapter deliberately did not cover in depth: what the
actual tools are: what `read_file`, `search_code`, `write_file`,
`run_command`, and the rest actually do, and the safety rules (like the
approval prompts mentioned in Part 1) that govern the riskier ones. That
full inventory, tool by tool, is the entire subject of the next chapter,
`04-agent-tools-filesystem-git-terminal.md`.
