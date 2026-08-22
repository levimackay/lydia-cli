# Chapter 4: What Lydia Is Actually Allowed to Do

The previous chapter covered the agent loop, the request → tool call →
observe → respond cycle that lets the AI model do more than just talk. This
chapter is about the other half of that picture: the actual list of things
the model is allowed to ask for, and every safety mechanism standing between
"the model asked for this" and "this actually happened to a file on your
computer."

This is the biggest chapter in this document set, for a simple reason: the
single file at the center of it, `src/lydia/agent/tools.py`, is the largest
file in the entire project at 860 lines. That size isn't accidental
complexity. It's because this file is where every tool the model can call
gets defined, described to the model, and given a safety rating, all in one
place, so that "what can the AI do, and how careful is Lydia being about it"
has one file where the whole answer lives.

## Two layers: the doing, and the deciding

Lydia splits every action (reading a file, writing a file, running a git
command, running a shell command) into two separate layers that don't know
about each other:

**`src/lydia/tools/`** contains what this project's own internal
documentation calls "pure functions." A pure function here means: it takes
a project folder and some plain arguments, does the actual work (reads the
file, writes the file, runs `git commit`), and either returns a plain
result or raises an error. Nothing in this folder knows that an AI model
exists. Nothing in it knows what a yes/no confirmation prompt looks like.
Nothing in it knows whether the request came from a careful plan or a wild
guess. It just does the filesystem/git/shell thing, honestly, and reports
what happened.

**`src/lydia/agent/tools.py`** is the layer above that. For every function
in `tools/`, it builds a `ToolSpec`, a wrapper that adds three things the
raw function doesn't have:

1. A **JSON schema**, which is a structured, machine-readable description
   of what inputs this tool needs (a file path, a search pattern, a commit
   message, and so on) that gets sent to the AI model so it knows exactly
   how to ask for this tool correctly.
2. A **risk tier** (explained in the next section), meaning how careful
   Lydia should be before actually running this.
3. A **handler**, the actual glue code that takes the model's request,
   maybe shows a confirmation prompt, calls the real `tools/` function, and
   turns the result into something the model can read.

### Why bother separating these two things?

Because it means the actual file-writing, file-deleting, and git-committing
logic (the code that could genuinely damage a real project if it had a bug)
can be tested completely on its own, with zero involvement from any
AI model, any confirmation dialog, or any part of the user interface. A
test can call `filesystem.apply_write` directly, hand it a fake temporary
folder, and check that it behaved correctly, without needing Ollama running
or a fake AI model pretending to ask for things. This is stated directly in
the project's own engineering notes: `tools/` "must stay UI- and
agent-agnostic... It knows nothing about confirmation prompts, risk levels,
or the LLM," while `agent/tools.py` "is where UI-independent policy lives."

It also means the confirmation prompt itself, the actual yes/no dialog the
user sees on screen, isn't hardwired into any of this. It's handed in from
outside as a small piece of code (a "callback") called `ToolContext.confirm`.
`agent/tools.py` doesn't need to know it's talking to a terminal window with
colored text (that's `cli/ui.py`'s job); it just calls `ctx.confirm(...)`
and gets back `True` or `False`.

## The three risk tiers

Every tool Lydia offers the model is labeled with exactly one of three risk
levels, and that label decides how much friction stands between the model
asking and the action actually happening.

**`safe`** runs immediately, with no confirmation prompt at all. This
tier is for actions that can't destroy anything: reading a file, listing a
folder's contents, searching code, checking git status, checking the
weather, checking email. Even though some of these touch outside services
(email, weather), none of them changes anything the user would need to undo,
so Lydia doesn't bother the user about them.

**`confirm`**: before this runs, Lydia shows the user exactly what is
about to happen (usually as a diff, or a plain description) and asks a
yes/no question. Nothing happens until the user says yes. This tier covers
writing a file, editing a file, deleting a file, committing to git, and
pushing to git. These are the actions that change something real, so the
default posture is: show the human first, always.

**`command`** is its own tier because it covers one very specific,
very broad tool: `run_command`, which lets the model run an arbitrary
terminal command (a "shell command" is literally any command you could type
into a terminal yourself, like `npm install` or `pytest` or, in theory,
something far more destructive). Because a single shell command could be
almost anything, this tier doesn't just ask "should I confirm this or not."
It first runs the exact text of the command through a separate danger
classifier (covered below) before deciding what to do with it.

There's also a fourth idea layered on top of the risk tiers, called the
session **mode**, which decides how strictly a tier's rule actually gets
enforced this session:

- **`ask` mode** (today's default) confirms every single `confirm`- and
  `command`-tier action, no exceptions.
- **`auto` mode** skips the confirmation prompt for non-dangerous actions,
  which is useful for `lydia ask "..." --yes`, a non-interactive way of
  running Lydia where no human is sitting there to click yes. But, and this
  matters, even in `auto` mode, anything flagged as genuinely dangerous
  (deleting a file, pushing to git, or a shell command the classifier flags
  as dangerous) still stops and asks. There's a dedicated flag on the
  confirmation request itself, `danger=True`, that `auto` mode always
  respects no matter what.
- **`plan` mode** is the most restrictive: it doesn't just add friction, it
  removes the mutating tools (`write_file`, `edit_file`,
  `multi_edit_file`, `delete_file`, `run_command`, `git_add`, `git_commit`,
  `git_push`) from the list of tools the model is even told about, so the
  model literally cannot ask for them. It's a research-only mode. Notice
  that `git_add` is included here even though its risk tier is `safe` (it
  doesn't need a confirmation prompt). Because it still changes something
  (git's staging area), plan mode strips it out anyway. Risk tier answers
  "does this need a human's yes," while plan mode answers a different
  question, "should this even be offered right now."

## Path safety: keeping the model inside the sandbox

Every single filesystem tool (reading, writing, listing, searching,
deleting, everything) takes a path as text from the model. That path is
just a string the AI model typed, the same way it might type anything else
in a reply. The model is a helpful assistant, not a trusted, error-proof
system: it could get confused, hallucinate a wrong path, or (in the worst
case) be tricked by something in a file it read into asking for a path it
shouldn't.

A dangerous version of this would be a path like `../../../../etc/passwd`
or an absolute path pointing somewhere completely outside the project
you're working on. That's the classic way software accidentally lets
someone "escape" the folder they're supposed to be confined to and touch
files elsewhere on your computer.

Lydia closes this off with one small, shared function:
`tools/paths.py::resolve_within`. Every filesystem tool, without exception,
runs the model's requested path through this function before touching disk.
It combines the requested path with the project's root folder, fully
resolves it (following out any `..` pieces, symlinks, etc. down to the real
final location), and then checks: is this final location still inside the
project root? If yes, it's allowed. If no, meaning the resolved path lands
anywhere outside the project folder, it's refused outright, before any
read, write, or delete is attempted.

This is a deliberate, repo-wide rule, not a suggestion: the project's own
internal engineering notes say plainly, "don't bypass this by calling
`Path` directly on user/model-supplied paths inside a new tool." Every
filesystem tool funnels through this one checkpoint. It's the single
biggest reason Lydia can safely let an AI model roam around and read/write
files on your real computer at all: the model's freedom is bounded to one
folder, with zero exceptions, checked in code rather than left as a
guideline.

## The backup and undo system

Every time Lydia is about to overwrite or delete a file, it keeps a copy of
what was there before, automatically, with no extra step required, before
the change happens.

Here's exactly where those copies live: `.lydia/backups/{timestamp}/{path}`,
where `{path}` mirrors the file's own location inside your project. So if
Lydia is about to overwrite `src/utils.py`, the old version gets saved to
something like
`.lydia/backups/20260822-143012-000001/src/utils.py` before the new content
is written.

This exact layout, mirroring the file's relative path underneath a
timestamped folder, fixed a real bug from an earlier version of Lydia.
Originally, backups were just named `{timestamp}-{filename}`, with no
folder information at all. That meant if a project had two files with the
same name in different folders, say `src/utils.py` and `tests/utils.py`,
their backups would collide: one would silently overwrite the other's
backup file, and an "undo" could quietly restore the wrong file's content.
Nesting the backup under the file's full relative path fixes this
completely, because two files can share a filename but never share a full
path within the same project.

Backups are treated as "best-effort": if something goes wrong while trying
to write the backup itself (a permissions problem, a full disk), Lydia
doesn't let that block the actual edit the user already approved. It just
quietly gives up on saving that particular backup rather than freezing the
whole operation.

To actually use these backups to undo something, there are two commands:

- **`lydia restore list`** shows every backup that currently exists,
  newest first, numbered, with the file's path and the exact timestamp it
  was backed up.
- **`lydia restore apply <n>`** takes one of those numbers, shows you a
  diff of what restoring it would change, asks for a final yes/no
  confirmation, and then writes that old content back to the file's
  original location.

In other words: nothing the AI model does to a file is ever truly a
one-way door. Because every write and delete is confirmed before it
happens *and* backed up as it happens, a mistake, whether the model's or a
misjudged approval, can always be walked back afterward.

## The dangerous-command classifier

`run_command` is the tool that lets the AI model run an actual shell
command, the same kind of thing you'd type into a terminal yourself. This
is by far the most open-ended tool Lydia has, because a shell command can,
in principle, do almost anything a computer is capable of doing. So before
Lydia decides how to handle a command, it runs the exact text of that
command through `tools/terminal.py::classify_command`, which returns either
`"safe"` or `"dangerous"`.

The classifier looks for specific, well-known patterns of commands that
tend to cause irreversible damage, things like:

- Deleting files recursively and forcibly (`rm -rf` and every variation of
  writing those two flags)
- Wiping or reformatting a drive (`mkfs`, or `dd` writing directly to a
  disk device)
- Forcing git to throw away history or overwrite a shared branch
  (`git push --force`, `git reset --hard`, `git clean -f`)
- Running something with elevated system permissions (`sudo`), or
  recursively changing file ownership/permissions in a sweeping way
  (`chmod -R 777`, `chown -R`)
- Downloading a script from the internet and piping it straight into a
  shell to execute blindly (`curl ... | sh`)
- Shutting down or rebooting the computer
- A classic "fork bomb" (a command engineered to spawn processes
  endlessly until the computer grinds to a halt)
- Publishing a package to a public registry (`npm publish`), or dropping a
  database table

Anything matching one of these patterns is flagged `dangerous`. That flag
changes what happens next, in a way that stays consistent even across
session modes: a dangerous command always requires a human's explicit
yes/no, no matter what mode Lydia is running in, including `auto` mode,
which otherwise skips confirmation for everything else. The reasoning,
stated directly in the code's own comments, is simple: there's no human
present to catch a real mistake if `--yes`/auto mode silently approved
something destructive, so the safe default is to still ask, every time, for
anything in this category. `auto` mode is meant to remove friction from
routine, low-stakes actions, not to remove the last line of defense against
something unrecoverable.

## Reading files without blowing the model's context

An AI model can only "see" a limited amount of text at once. This project
calls that limit the model's context window, and it's covered in more
depth in chapter 5. `read_file` runs into a very concrete version of that
limit: what happens when the model asks to read a genuinely large source
file?

The naive approach would be to just cut the file off at some fixed number
of characters and tack on a note like "...[6000 more characters]." Lydia
used to do exactly that, and it turned out to be a real, observed bug: a
normal-sized real file in this very project (`cli/main.py`, 875 lines and
about 35 kilobytes) got silently cut off around line 150, with the model
given no way to know how to see the rest, or even that asking for "the
rest" was possible.

The fix, `_truncate_read_file`, does two smarter things. First, it always
cuts at a clean line boundary, never truncating in the middle of a line of
code, which would produce garbled, half-a-line text the model would have to
guess about. Second, and more importantly, it tells the model exactly what
to do next: the truncated output ends with a message naming the exact last
line number it was allowed to see, and instructs the model to call
`read_file` again with that number plus one as the new `start_line` to keep
reading from where it left off. Better still, it can use `search_code` or
`search_semantic` first to jump straight to the relevant section instead of
reading a huge file start to finish. `read_file` itself already supported
reading a specific `start_line`/`end_line` range; the bug was never that
this feature didn't exist, it was that nothing ever told the model the
feature existed once it hit the wall.

## Searching the codebase: the two tools, briefly

Two different tools let the model find things across a whole project
instead of reading every file one by one:

- **`search_code`** does a literal, plain substring search. It looks for
  an exact piece of text across the project's files and returns every
  matching line, labeled with its file and line number. This is the tool
  to reach for when you already know the exact word or phrase you're
  looking for.
- **`search_semantic`** searches by *meaning* rather than exact wording,
  so it can find "where is the login logic" even if the code never uses
  the word "login." It depends on a separate indexing system built ahead
  of time (`lydia index`) that turns the project's code into searchable
  numeric representations. That whole system, how the index gets built,
  what an "embedding" is, and how the search actually works, is its own
  full topic, covered in Chapter 6.

## The git tools: confirm-first, same as file writes

Lydia gives the model five git actions, and they follow the exact same
"show, then ask" philosophy as file writes:

- **`git_status`** shows which files are changed, staged, or untracked.
  Read-only, so it's `safe` tier: no confirmation needed.
- **`git_diff`** shows the actual line-by-line changes, either for
  everything unstaged or (if asked) just what's already staged, optionally
  narrowed to one file. Also `safe`, since it's just looking, not changing
  anything.
- **`git_add`** stages one or more files so they're ready to be
  committed. This is `safe` tier (no confirmation prompt) even though it
  does change something (git's internal staging area), because staging by
  itself doesn't alter any file's content and is trivially reversible.
  It's still treated as a "mutating" action for the purposes of plan mode,
  though, and plan mode strips it out along with the rest.
- **`git_commit`** permanently records the currently staged changes as a
  new commit in the project's history, using a message the model supplies.
  This is `confirm` tier: before it runs, Lydia shows the user the proposed
  commit message and asks for a yes/no, exactly like it would show a diff
  before a file write. The underlying `tools/git.py::commit` function also
  refuses outright if nothing has actually been staged. The model has to
  have called `git_add` first.
- **`git_push`** uploads locally committed changes to a remote location
  (like GitHub), something other people, or your other machines, could
  see or be affected by. This is `confirm` tier and additionally marked
  `danger=True`, which means that, same as file deletes and dangerous
  shell commands, it always asks for a human's yes, even in `auto` mode.

In every case, the underlying `tools/git.py` functions shell out to the
real `git` program on your computer using an explicit list of arguments
(never a raw text command string), which is itself a small safety detail:
it means a commit message or file path containing unusual characters can
never accidentally be interpreted as an extra git command.

## Tying this back to the big picture

Chapter 1 (`00-start-here.md`) described the agent loop's step 6 in one
sentence: "if the model wants to change something... Lydia stops and shows
you exactly what it wants to do, in a yes/no prompt, before doing it."
This chapter is the full, concrete version of that one sentence: the exact
list of things the model is allowed to ask for, the three-tier system that
decides how much friction stands in front of each one, the one function
that keeps every path request boxed inside your project folder, the
automatic backup system that makes every change reversible, and the
pattern-matching safety net that keeps even `--yes`/auto mode from silently
running something catastrophic. Everything the model can actually *do* to
your real files, your real git history, and your real terminal, as
opposed to everything it can merely *say*, passes through the machinery
described in this chapter first.
