# Chapter 8: Config and Secrets: How Lydia Remembers Your Settings and Protects Your Passwords

This chapter covers two small but important folders: `src/lydia/config/`,
which stores ordinary settings like "which AI model to use," and the one
file inside it, `secrets.py`, that decides which settings are too
sensitive to store the normal way. It also covers a small helper file,
`cli/optional_deps.py`, that keeps a missing piece of software from
turning into a confusing crash.

## What "config" means here

"Config" is short for configuration, the settings that control *how*
Lydia behaves, as opposed to the code that defines *what* Lydia is
capable of doing at all. Things like: which AI model to talk to, whether
it should "think out loud" before answering, how cautious it should be
about running commands without asking first, and whether it should talk
to a remote Lydia Server instead of the Ollama on this machine.

These settings live in a JSON file, not hardcoded inside the program's
code, for a simple practical reason: a JSON file can be opened in any
text editor and changed in seconds, with no programming knowledge and no
need to reinstall or rebuild anything. If "which model to use" were
buried inside a `.py` file, changing it would mean editing source code.
Because it's just a JSON file sitting on disk, Lydia can read it fresh
every time it starts, and a person (or the `lydia config set` command)
can change it just by writing a new value into that file.

The list of every setting Lydia understands, along with its default
value, lives in one place: the `LydiaConfig` structure at the top of
`src/lydia/config/settings.py`. Each one has a short comment explaining
what it does and why its default is what it is. For example, `think`
defaults to `"auto"` (let the model decide whether to reason out loud),
and `mode` defaults to `"ask"` (confirm every action that changes
something, the safe default described in Chapter 3).

## Two layers: global settings and project settings

Lydia doesn't read from just one config file. It reads from two and
combines them. This is called **layering**.

1. **Global config**, `~/.lydia/config.json`. The `~` means "your home
   folder." This file applies everywhere, no matter which project
   folder you're currently working in. Think of it as your personal
   defaults for Lydia.
2. **Project config**, `<project>/.lydia/config.json`, a file that
   lives inside one specific project folder. This file only applies
   when you're working inside that particular project.

When Lydia starts, it reads the global file first, then reads the
project file (if one exists) and lets any setting in the project file
overwrite the matching setting from the global file. If a setting
appears in the project file, the project file wins; if it doesn't
appear there, the global value (or the built-in default, if neither
file mentions it) is used instead.

Why would you want two layers instead of just one? A concrete example
straight from Lydia's own design: suppose you normally want Lydia to use
the local Ollama running on your own machine. That's your global
default, and it applies to every project by default. But one particular
project is heavy enough that you'd rather point it at a more powerful
remote machine (the Lydia Server setup described in Chapter 7). Instead
of switching your global setting back and forth every time you enter or
leave that project, you set `server_url` and `api_key` just once, inside
that one project's `.lydia/config.json`. Every other project keeps using
your local Ollama untouched, and that one project quietly uses the
remote server, because its project-level file overrides the global
default only for itself.

### How Lydia finds "which project am I in"

To find the right project config file, Lydia needs to know which
project folder you're currently inside. It does this by **walking up**
the folder structure: starting at your current folder, it checks
whether that folder contains a `.lydia/` folder or a `.git/` folder (the
marker that a folder is a git repository, explained in Chapter 00's
glossary). If neither is there, it checks the parent folder, then that
folder's parent, and so on, moving up one level at a time until it
either finds one of those markers or runs out of folders to check (it
reaches the very top of the filesystem with nothing found).

This is exactly the same trick many other developer tools use to figure
out "which project is the user currently working inside of," even when
they've navigated a few folders deep inside it. You don't have to be
sitting in the exact root folder of a project for it to be found; being
anywhere inside it (or one of its subfolders) is enough, because the
walk keeps going upward until it hits the marker.

## What happens when an old setting no longer exists

Lydia gets updated over time, and settings occasionally get renamed or
removed entirely. If your `config.json` file still has a leftover
setting from an older version of Lydia that the current version doesn't
recognize anymore, Lydia doesn't crash. It notices the setting isn't one
it knows about, prints a warning saying so, and simply ignores that one
entry. Everything else in the file still loads and works normally.

This matters because the alternative would be much worse: if an unknown
setting caused the whole program to refuse to start, upgrading Lydia
could suddenly make it completely unusable for anyone who had an old
setting sitting untouched in their config file from months ago, for a
reason that has nothing to do with anything they're currently doing.
Ignoring-with-a-warning means an upgrade never breaks the whole program
over one stale leftover value.

## The OS keychain: a locked vault for real secrets

Some information Lydia needs isn't a preference like "which model to
use." It's an actual credential: a password-equivalent that proves who
you are to another company's service. Examples that come up in later
chapters: a paid Gemini API key (Google's AI service, an opt-in
alternative to Ollama), and login tokens for Gmail, Outlook, and Canvas
(the personal-assistant connectors covered in Chapter 10).

For these, Lydia does not use the plain `config.json` file. Instead it
stores them in the **OS keychain**, a secure, encrypted storage vault
that's built into your operating system itself (on a Mac, this is
literally the same "Keychain Access" app that stores the passwords your
web browser remembers for you). The keychain only unlocks for whichever
user is currently logged into the computer; it's designed specifically
so that ordinary files and ordinary programs can't just read what's
inside it.

The reason this distinction matters: `config.json` is plain text. Anyone
who can open that file (by looking at it directly, by a program reading
it, by a backup or a sync tool copying it somewhere) can read every
value inside it instantly, no unlocking required. That's fine for a
setting like "which model to use," where there's nothing to protect. It
is not fine for a real credential, where anyone who reads it could then
use it to send email as you, log into your Canvas account, or run up
charges on a billed API key in your name. The keychain exists precisely
to keep that second category of information out of reach of anything
that isn't specifically allowed to unlock it.

Lydia's code for this lives in `src/lydia/config/secrets.py`, and it
uses a small third-party library called `keyring` to talk to whichever
keychain the operating system provides. Every secret Lydia stores is
filed under one shared label, `"lydia"`, so if you ever open Keychain
Access yourself, all of Lydia's stored secrets show up grouped together
under that one name, rather than scattered under a dozen different
entries.

## Why the split, in Lydia's own reasoning

The docstring at the top of `secrets.py` states the actual design
principle plainly, and it's worth restating in plain terms, because it's
the whole reason two different storage systems exist side by side
instead of just always using the more secure one everywhere.

The key distinction is **who issued the credential, and who's on the
hook if it leaks**:

- Something like `server_url` and its matching `api_key` (from the
  Lydia Server setup in Chapter 7) is something *you* made up when you
  set up your own remote server. It's not billed by anyone, it doesn't
  represent a login to some other company's account, and if it leaked,
  the worst case is someone could talk to a server you control on your
  own network. That's low enough stakes, and useful enough to be able
  to glance at or edit by hand, that it's fine sitting in plain
  `config.json`.
- Something like the Gemini API key, or a Gmail/Outlook login token, is
  fundamentally different: it was issued to you *by a third party*, a
  company that will bill you for usage under that key, or that will let
  whoever holds the token act as you inside your real email or school
  account. That's a different trust category entirely: a leak here
  isn't hypothetical inconvenience, it's someone else spending your
  money or reading your real mail. Those go in the keychain, never in
  plain JSON.

Put another way: Lydia keeps the line drawn not by "is this a password
in general" but by whether the thing is self-issued and low-stakes
(plain config) or third-party and high-stakes (keychain).

## Friendly errors for optional features: `optional_deps.py`

Not every feature Lydia can do is installed by default. Gmail/Outlook
login, stock/news lookups, and voice mode all depend on extra software
packages that are large (collectively several hundred megabytes) and
that most people trying out the core coding-agent feature will never
touch. So Lydia's installer splits them out into optional groups, called
**extras**. Running `pip install lydia-cli` gets you the base program, while
`pip install "lydia-cli[assistant]"` also pulls in everything Gmail/
Outlook/Canvas/stocks/news need, and `[voice]` pulls in everything voice
mode needs.

The problem this creates: if someone runs a feature that needs one of
those extras, but they only installed the base package, the missing
piece of software would normally cause Python to throw a
**ModuleNotFoundError** and print a **traceback**, the raw, technical
error dump a program spits out when something goes wrong internally. A
traceback is meant for a programmer to read; it's a wall of file paths
and code line numbers that means nothing to someone without a
programming background, and seeing one makes a program feel broken even
when the actual fix is simple.

`src/lydia/cli/optional_deps.py` exists specifically to prevent that.
Its one function, `require_extra`, wraps the exact spot in the code
where an optional feature (like Gmail login) tries to use its optional
software. If that software genuinely isn't installed, `require_extra`
catches that specific situation before it turns into a traceback, and
instead prints a short, clear, human-readable message explaining what
happened and exactly what to type to fix it. For example: "Gmail login
needs the 'assistant' extra, which isn't installed... Install it with:
pip install \"lydia-cli[assistant]\"". It deliberately only catches this
one specific kind of error (a missing module), not every possible
error. A real bug happening *inside* an already-installed feature still
shows its real error normally, rather than being wrongly blamed on a
missing install.

### A real bug this design caught: vanishing square brackets

While building this exact feature, a genuine bug turned up, and it's
worth explaining because it's a good concrete example of how something
that looks perfectly correct can quietly fail. The friendly install
message needs to contain the text `[assistant]`, in square brackets. The
tool Lydia uses to print colored, styled text to the terminal is called
Rich, and Rich has its own convention: square brackets in a printed
string are treated as **markup**, an instruction telling Rich to
change color or style for the text that follows, similar to how a word
processor might use hidden formatting codes. When Rich encounters a
`[tag]` it doesn't recognize as a real styling instruction, it doesn't
print it literally. It silently strips it out and prints nothing in
its place.

That meant the first version of this error message, printed through
Rich, would have silently swallowed the `[assistant]` part (the exact
piece of text someone would need to actually copy and run to fix their
problem), and nobody would have any obvious sign that anything was
missing. The message would just look slightly odd, with a gap where
`[assistant]` should have been.

The fix was to explicitly mark the brackets as "these are literal
characters, not a styling instruction," using a function Rich itself
provides for exactly this (`rich.markup.escape`), before handing the
text to the printer. And because this kind of bug is the sort that can
silently reappear if someone edits the message later without realizing
the trap, it was locked in with an automated test (part of the test
suite described in Chapter 11) that checks the *actual rendered output*
contains the real, literal brackets, not just that the code "didn't
crash." If the escaping is ever accidentally removed in the future,
that test fails immediately instead of the bug quietly coming back.

## How this fits into the bigger picture

Chapter 00 explained that Lydia's core promise is working with just
Ollama running locally, with no API keys, no accounts and no cost, and
everything else opt-in on top of that baseline. This chapter is where
that promise is actually enforced in the code: ordinary behavior lives
in a plain, editable JSON file anyone can inspect by hand, opt-in
paid or third-party features (Gemini, Gmail, Outlook, Canvas) keep their
real credentials locked away in the operating system's own secure vault
rather than sitting in that same plain file, and any optional feature
that isn't installed fails with a clear, human instruction instead of
an unreadable technical crash. None of this changes what Lydia can do.
It's the part that decides where each piece of information is allowed
to live, and makes sure a missing optional install or a leftover old
setting never turns into a program that won't start at all.
