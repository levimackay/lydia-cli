# 9. Automations and Scheduling

The rest of these documents cover Lydia while you're sitting in front of
it, typing. This document covers the opposite case: Lydia doing something
useful while you are *not* there, with no terminal open, maybe the laptop
closed, and nobody watching to answer a yes/no prompt.

That's what the **automations** feature is for. You describe a recurring
task in plain English once, Lydia turns it into a strict, structured
instruction set, and a background mechanism built into your operating
system wakes Lydia up on its own to carry it out: every morning at a set
time, every few minutes, or whenever something specific happens (a new
email arriving, say).

## The core idea, with a concrete example

Say you type this into a terminal:

```
lydia automate "every morning at 8, check my email and canvas"
```

That sentence is loose. A human reading it fills in a lot of unstated
detail ("email" probably means your personal Gmail, "canvas" means the
Canvas school platform, "check" probably means "tell me what's new"). A
computer program can't run on something that loose. So the very first
thing that happens is Lydia hands your sentence to the AI model, along
with a very precise system prompt (found in
`src/lydia/automations/parser.py`) that tells the model: don't reply with
conversation, reply with **only one JSON object**, in exactly this shape,
with these exact field names. JSON, as covered in the first document, is
the labeled-fields text format almost everything in Lydia gets saved as.

The model reads your sentence and outputs something like:

```
name: "morning-briefing"
trigger: run daily at 08:00
steps: check email (personal account), check canvas, then have the model
       summarize both into a short briefing
notify: push a phone notification, always
```

Lydia calls this structured result a **recipe**. Before trusting it,
Lydia runs a validation pass (`automations/model.py::validate`) that
checks every field against strict rules: is the time actually a real
24-hour clock time, is the notification channel one of the three allowed
values, does every step use an allowed tool, and so on. Small local AI
models occasionally produce JSON with a typo or a missing field, so if
validation fails, Lydia doesn't give up immediately. It sends the errors
back to the model and asks for exactly one corrected attempt
(`parse_automation` in `parser.py`). If that second attempt also fails,
Lydia tells you plainly that it couldn't understand the request rather
than saving something broken.

Once a recipe passes validation, Lydia shows you a one-line, human-
readable summary of what it understood (built by `describe()` in
`model.py`) and asks you to confirm before saving, so you get one last
chance to catch a misunderstanding before it becomes a standing,
unattended task. Confirmed recipes are written as one JSON file per
automation, at `~/.lydia/automations/<name>.json` (the `~` means your
home folder; this is a *global*, not per-project, location, because a
morning email check isn't tied to any one coding project). This is
handled by `automations/store.py`, which centralizes every file path
under one constant, `AUTOMATIONS_DIR`, so the whole storage layer can be
pointed somewhere else during testing without touching real files on
disk.

## What a "trigger" is

Every recipe has exactly one **trigger**, the rule for *when* it runs.
Lydia supports three kinds (defined in `automations/model.py::Trigger`):

- **A daily schedule.** "Run at 08:00." A fixed clock time, checked once
  a day. Internally, Lydia doesn't literally fire the automation the
  instant the clock hits 8:00. It fires the next time it happens to
  check *after* 8:00, and then remembers it already ran today so it
  doesn't fire again until tomorrow. (More on why in the heartbeat
  section below.)
- **An interval.** "Run every 30 minutes," for example, or anything else
  repeating, with a minimum of 5 minutes so an automation can't be
  configured to hammer your email or the model constantly.
- **An event.** Rather than a fixed time, this fires whenever something
  new shows up that matches a description you gave in English, like "when
  my professor emails me." The event's *source* is either
  "email" (personal Gmail or school Outlook) or "canvas," and the
  *condition* ("from my professor") is not hard-coded logic. It's kept
  as plain English and handed to the AI model at run time to judge, every
  time new items show up. This is the one place in the whole automations
  system where the model still has a live decision to make after
  creation, rather than following a fixed script.

## What "steps" and "notification style" mean

Once a trigger fires, Lydia works through the recipe's **steps**, in
order. There are two kinds of step (`automations/model.py::Step`):

- A **connector step** calls one specific, pre-built, read-only action:
  `check_email`, `check_canvas`, `check_stocks`, or `check_news`. These
  are the same underlying "connector" functions covered in
  `10-connectors-and-voice.md`; an automation is only ever allowed to use
  these four. It is deliberately *not* allowed to use any tool that edits
  a file, runs a terminal command, or touches git. That's enforced by an
  explicit allow-list (`ALLOWED_STEP_TOOLS` in `model.py`), and it's a
  safety decision, not an oversight: nobody is sitting at the keyboard to
  approve something risky at 8am, so automations are restricted to
  actions that can only look things up, never change or damage anything.
- A **model step** hands everything gathered so far by earlier steps to
  the AI model with English instructions, for example "summarize the email and
  Canvas data into a short morning briefing with a checklist." This is
  where the actual "thinking" part of an automation happens.

After the steps run, Lydia decides whether, and how, to tell you what
happened. That's the **notify** setting, which has two parts:

- **Channel** is where the message goes: `ntfy` (a push notification sent
  to your phone; this needs a one-time setup, `lydia auth login ntfy`),
  `mac` (a native macOS desktop notification, via the same `osascript`
  mechanism the daily briefing uses), or `none` (nothing is shown to you
  at all, a fully silent, background-only action).
- **When** is either `always` (tell you every single time it runs, even if
  there's nothing interesting to say) or `if_important` (only tell you if
  the outcome actually seems worth interrupting you for).

The `if_important` behavior is worth understanding because of how cheaply
it's implemented: Lydia doesn't make a second call to the model to ask
"was that important?" Instead, the model step's own system prompt is
extended with one extra instruction: if there's nothing worth telling
the user, end your reply with the exact word `NOTHING_TO_REPORT`
(`automations/runner.py::IF_IMPORTANT_SUFFIX`). Afterward, Lydia just
checks, in plain code, whether the reply ends with that exact word, a
simple text match, not another round of AI reasoning. Because only a
model step can produce that marker, a recipe that asks for
`if_important` notifications is required (by the same validation pass
mentioned earlier) to include at least one model step; a recipe with only
connector steps has no way to judge importance at all.

Choosing between these matters in practice: `always` suits something like
a daily briefing, where hearing nothing would itself feel like a bug;
`if_important` suits noisy sources like email, where you want to be
interrupted only when it actually matters; `none` suits something Lydia
should just quietly do for its own sake, like refreshing data it will use
later, with no notification loop at all.

## The heartbeat: how "every morning at 8" actually happens

Saving a recipe to a JSON file doesn't make anything run. A saved file
just sits there. Something external has to actually notice, at roughly
the right moment, that it's time to check whether any automation is due,
and Lydia isn't running continuously in the background waiting for that
moment (there's no permanent Lydia process idling on your machine).

Both macOS and Linux already ship with a built-in program whose entire
job is exactly this: "wake something up at a set time, or repeatedly at a
set interval, even if nothing else is currently running." On macOS it's
called **launchd**; on Linux, the equivalent is **systemd**, used here in
its `--user` mode (meaning it manages background tasks for your personal
login, not the whole system, so it doesn't need administrator/root
access). Lydia doesn't reinvent this. It registers itself with whichever
one matches your operating system and lets it do the waking up.

Running `lydia automations schedule enable` writes a small configuration
file describing this repeating job and hands it to `launchd` (via a
command called `launchctl load`) so it takes effect immediately and
survives reboots. On macOS, that configuration file (a "plist," short for
property list, Apple's native settings-file format) lives at
`~/Library/LaunchAgents/com.lydia.automations.plist` and tells launchd:
every 300 seconds (5 minutes, configurable with `--interval`), run
`lydia automations tick`. This "tick" is a single, short-lived command:
it wakes up, checks every saved automation to see if any of them are due
right now, runs the due ones, and then exits. There is no long-running
Lydia process sitting in memory between ticks. `automate_flow.py`, the
code behind the `lydia automate` command, even reminds you if you save an
automation while this heartbeat isn't running yet, since a saved-but-
unscheduled automation would otherwise just silently never fire.

Separately, `cli/scheduler.py` also handles scheduling for the related
daily-briefing feature (`lydia briefing schedule enable`; see the next
section), and *that* code path was rewritten to properly support Linux
by writing a matching pair of systemd unit files (a `.service` describing
what to run, and a `.timer` describing when) into
`~/.config/systemd/user/`, enabled with `systemctl --user enable --now`.
Worth knowing precisely, since it affects what actually happens if you
ever run this on Linux: the automations heartbeat itself
(`enable_automations`/`disable_automations` in `scheduler.py`, behind
`lydia automations schedule enable`) still only writes a launchd plist
and calls `launchctl` unconditionally. It does not yet route through the
same macOS-vs-Linux switch the briefing scheduler uses. In other words,
the cross-platform fix described in the project's roadmap landed for the
daily briefing's scheduling, but not yet for the automations tick
heartbeat specifically. On an actual Mac (Levi's case) this distinction
is invisible; it only matters if this is ever run on Linux.

## Why automations run the model in a "stripped-down mode"

When you're chatting with Lydia interactively (`02-cli-and-repl.md`,
`03-agent-loop-and-tools.md`), the model can propose using dozens of
different tools, including ones that change your files or your project's
git history, and every risky action pauses for you to type y/n before it
happens. That whole design depends on a live human being present to
answer.

Automations run with nobody watching, so the design is deliberately much
more constrained. Looking at `automations/runner.py::execute`, a model
step in an automation isn't given the full tool-calling agent loop at
all. It's just one plain, single-turn chat request: here is the data
already gathered by the connector steps before it, here are your
instructions, write the reply. There's no back-and-forth, no tool
requests to interpret, nothing to approve. The confirmation callback
automations pass into the tool system is even hard-coded to always
decline (`_ctx()` in `runner.py` sets `confirm=lambda _r: False`), but
because only the four safe, read-only connector tools are ever reachable
in an automation to begin with, that decline path is never actually
triggered in practice; it exists purely as a second layer of protection,
not as something that's expected to normally fire.

The result is an automation that runs faster and more predictably than
an interactive chat turn and, just as importantly, can never
improvise its way into doing something destructive while no one is
around to stop it. It sticks strictly to the steps it was told to run,
nothing more.

## Missed ticks on sleep are caught up on wake

A laptop spends a lot of its life asleep: lid closed, not actually
running any programs, including the 5-minute heartbeat. If your Mac is
asleep at 8:00am, there is no tick happening at 8:00am to notice that the
morning briefing is due. A naive scheduler could simply lose that run
forever, and you'd never get told your automation silently stopped
working.

launchd specifically avoids this. Unlike the older Unix tool `cron`
(which macOS no longer uses for this exact reason), launchd remembers
that a scheduled job was missed while the machine was asleep, and runs it
as soon as the machine wakes back up, rather than waiting for the next
regularly scheduled time or skipping it outright. Combined with how
`is_due()` in `automations/runner.py` checks a schedule trigger ("is the
current time past the target time, and did today's run not already
happen?"), this means: the Mac wakes up at 9:15, launchd immediately
fires a catch-up tick, `is_due()` sees it's past 8:00 and no run has
happened yet today, and the 8am briefing you missed runs late instead of
not at all. It's a small detail, but it's the difference between an
automation you can actually trust to have run, versus one you have to
remember to double-check.

## Cross-platform support: from a crash to a clear message

Early on, `cli/scheduler.py` only knew how to talk to launchd, and it
assumed, without checking, that it was always running on a Mac. If
someone tried to use scheduling on any other operating system, the code
would try to run a Mac-only command that simply doesn't exist there, and
the failure that came back was a raw, low-level `FileNotFoundError`, the
kind of message meant for a programmer debugging the code, not a person
just trying to use the feature. It gives no hint about what actually went
wrong or what to do about it.

That's now fixed with an explicit platform check (`_backend()` in
`scheduler.py`), which picks `launchd` on macOS, `systemd` on Linux (as
long as `systemctl` is actually available), and otherwise raises Lydia's
own error type, `ScheduleError`, with a message written in plain English,
something like "scheduled briefings aren't supported on this platform
yet." The underlying capability is the same either way: on Windows, or a
Linux machine without systemd, there genuinely is no equivalent Lydia can
hook into yet, so the feature still doesn't work there. What changed is
*how that failure is communicated*. A raw crash tells you the program is
broken; a clear, purpose-written error message tells you the program
understood exactly what you asked for and is telling you, correctly,
that it can't do it on this machine yet. The first makes you doubt the
whole tool; the second is a program behaving exactly as designed even in
the one case it can't fully support.

## A related feature: the scheduled briefing (`cli/briefing.py`)

Automations are the general-purpose version of "do this on a schedule."
`cli/briefing.py` is a single, specific, built-in use of that same
underlying idea: a daily personal summary pulling from every connected
source at once (Canvas, personal Gmail, school Outlook, the stock
market, and AI news), synthesized by the model into one short checklist,
optionally with a desktop notification when it's ready.

One deliberate design choice stands out in this file's own comments:
rather than letting the model decide for itself which sources to check
(the way a normal chat turn would), `briefing.py::_gather_sources` calls
every single source directly, every time, and only afterward hands the
model the combined results with instructions to summarize *exactly*
that and nothing more. This was a direct response to something observed
during testing: given the freedom to choose, the model would sometimes
skip fetching a source altogether and instead just invent a plausible-
looking result in its place (it once fabricated an Outlook email that
never existed, when Outlook wasn't even connected). Pre-fetching every
source and handing it over as fixed, already-real data removes that
failure mode entirely. The model's only job left is to summarize real
data it's already holding, never to guess at data it doesn't have.

## How this connects to the big picture

The opening document in this set describes Lydia's core mechanism as a
loop: your question goes to the model, the model asks to use a tool,
Lydia carries that action out and reports back, and this repeats until
the model gives a final answer, always with you there to approve
anything risky. Automations are what happens when you take that same
underlying machinery (a model, a fixed set of safe tools, structured
JSON as the shared language everything is described in) and strip out
the one thing that depended on you being present: the live conversation
and the approval prompts. What's left is a smaller, faster, strictly
bounded version of the same idea, handed off to your operating system's
own built-in scheduler so it can keep running the parts of Lydia that
don't need a human in the room, on their own, on time, even while you're
asleep.
