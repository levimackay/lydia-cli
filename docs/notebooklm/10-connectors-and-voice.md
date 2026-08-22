# Connectors and Voice: Lydia's Personal-Assistant Extras

The last document covered automations, Lydia doing things on a schedule
without being asked. This document covers the two feature groups that make
those automations (and everyday chatting) actually useful for personal
tasks rather than just coding: **connectors**, which let Lydia fetch real
information from the outside world (your email, your school's assignment
list, the weather, stock prices, news headlines, a push notification to
your phone), and **voice mode**, which lets you talk to Lydia out loud
instead of typing.

These live in two separate folders, `src/lydia/connectors/` and
`src/lydia/voice/`, and they don't depend on each other. Voice mode is a way of
*talking to* Lydia; connectors are things Lydia can *look up* once it's
listening, whether that's by voice or by typed text. They're grouped into
one chapter because both are "extra" features layered on top of the core
coding agent, both are entirely opt-in, and both were built the same way:
small, swappable pieces wired together, easy to test without needing the
real internet or real hardware.

## What a "connector" actually is

A **connector** is a small module whose entire job is talking to one
specific outside service and turning whatever that service hands back into
a plain, simple shape (a short list of Python objects with a few obvious
fields, like `sender`, `subject`, `snippet` for an email) that the rest of
Lydia can use without needing to know anything about that service's
particular API.

Think of it like a translator. Gmail has its own particular way of
describing an email (nested JSON with fields like `payload.headers`, label
IDs like `"UNREAD"`, and so on). Microsoft Outlook describes an email
completely differently. If every part of Lydia that wanted to show you an
email had to understand both of those formats, plus Canvas's format for
assignments, and Open-Meteo's format for weather, and Yahoo Finance's
format for stock prices, the code would be a tangled mess, and adding a
new service would mean touching code all over the program.

Instead, each connector is a wall between "the messy, service-specific
details of talking to Gmail/Outlook/Canvas/etc." and "the rest of Lydia,"
and that wall only lets simple, uniform data through. Every connector in
`src/lydia/connectors/` follows the same shape:

- A function that fetches the data (e.g. `get_recent_emails`,
  `get_upcoming_assignments`, `get_weather`).
- A small data class describing one item of that data in plain fields
  (`EmailSummary`, `Assignment`, `IndexSnapshot`, `NewsItem`).
- A `format_*` function that turns a list of those into a short block of
  plain text. This is what actually gets shown to the AI model or to you,
  since the model reads plain text, not Python objects.
- If something goes wrong (no internet, a bad token, the service is down),
  the connector raises one shared error type, `ConnectorError`, defined in
  `connectors/base.py`. Whatever called the connector catches that and
  shows a plain-English message, instead of the program crashing with a
  confusing technical error.

This "pure function" design (no user-interface code, no talking to the AI
model, no popup asking "are you sure?") is deliberate and matches the same
rule Lydia's coding tools follow (covered in the tools chapter): a
connector takes plain inputs and returns plain data or raises an error, and
nothing more. That's also what makes each one easy to test: a test can hand
a connector fake, canned data instead of a real network connection and
check that it processes that data correctly, without needing a working
internet connection, a real Gmail account, or a real Canvas login every
time the test suite runs.

## OAuth, in plain terms: how "logging into Gmail through Lydia" works

Gmail and Outlook are the two connectors that need to know *who you are*
before they can read your inbox. The others (weather, stocks, news) are
public data anyone can request, and Canvas and ntfy use a different, simpler
kind of secret (explained further down). For Gmail and Outlook, Lydia uses
something called **OAuth**, and it's worth understanding exactly what it
does and doesn't do, because it's the safest of the available options.

The wrong way to do this, and a way plenty of sketchy apps still do it,
would be for Lydia to just ask you to type your real Gmail password into a
prompt, and then Lydia stores that password and uses it to log in as you
whenever it wants. That's dangerous for a few reasons: your actual password
is now sitting on disk somewhere, readable by that program forever; if that
program (or the file it's stored in) is ever compromised, whoever gets it
can do *anything* your account can do, forever, until you change your
password everywhere; and you have no way to see or limit what it's allowed
to do versus what it's not.

OAuth avoids all of that. Here's what actually happens when you run
`lydia auth login gmail` or `lydia auth login outlook`:

1. Lydia opens a login page that belongs to Google (or Microsoft), not a
   page Lydia built. It's the real login page you already trust, on
   Google's or Microsoft's own website.
2. You type your real password there, directly into Google's/Microsoft's
   page. Lydia never sees it, never touches it, never stores it.
3. Google/Microsoft asks you to confirm something specific and limited:
   "Lydia is asking for permission to *read your email*. Allow?", not
   "full access to your account," just the one narrow thing it asked for.
4. If you approve, Google/Microsoft hands Lydia back a **token**, a long,
   random string that works kind of like a hotel key card instead of your
   house key. It only opens the one door it was issued for (in Gmail's
   case, read-only inbox access; see `SCOPES` in
   `connectors/email_gmail.py`, which is literally the single string
   `"https://www.googleapis.com/auth/gmail.readonly"`), it can be
   cancelled by the hotel (Google/Microsoft) at any time without you
   needing to change your master key (your password), and it doesn't
   reveal your password even if someone steals the card.
5. Lydia stores that token, not your password, in your operating system's
   secure keychain (covered in the config/secrets chapter) and uses it on
   your behalf from then on.

Because the token is scoped that narrowly, even if it were somehow stolen,
whoever had it could only read your recent emails, not send email as you,
not access your files, not reset your password, not touch anything else in
your account. And because it's a revocable grant rather than your actual
password, you can go into your Google Account settings or Microsoft
account's "Apps and services" page at any time and revoke Lydia's access
instantly, without changing your password or affecting any other app.

The two connectors implement two slightly different flavors of this same
idea, because Google and Microsoft each have their own version of the
handshake:

- **Gmail** (`connectors/auth/gmail_oauth.py`) uses what's called the
  "installed-app" flow: `login()` pops open a browser tab pointed at
  Google's consent page, and once you approve, a tiny local web server
  that Lydia starts for a moment catches Google's reply and grabs the
  token. Before any of this works the first time, you (Levi) have to do a
  short one-time setup in Google Cloud Console to create Lydia's own
  "OAuth client" identity (spelled out step by step in that file's own
  comments). This is standard for any app that wants to use Google
  sign-in, not something specific to Lydia being homemade.
- **Outlook** (`connectors/auth/outlook_oauth.py`) uses Microsoft's
  "device code" flow instead: instead of a browser popup, Lydia prints a
  short code and a URL, you visit that URL on any device and type the
  code in, and Microsoft's page handles the rest. This flow exists
  specifically for programs (like a plain terminal CLI) that don't have an
  easy way to catch a browser redirect themselves. It also needs a
  one-time setup step in Microsoft's Azure portal, documented in the same
  file.

Both `login()` functions store what they get back using `config/secrets.py`
(the keychain-backed secret storage explained in the config chapter), and
both have a matching `logout()` that deletes it, instantly revoking
Lydia's access from Lydia's side, on top of you always being able to revoke
it from Google's/Microsoft's side directly.

## Walking through each connector

Every connector below fetches one specific kind of real-world information
and turns it into that same plain-list-of-simple-objects shape described
earlier.

**Gmail** (`connectors/email_gmail.py`) and **Outlook**
(`connectors/email_outlook.py`) each fetch your most recent inbox messages
(sender, subject line, a short preview snippet, and whether it's unread)
using the OAuth token described above. Gmail talks to Google's own Gmail
API library; Outlook talks to Microsoft Graph (Microsoft's general-purpose
API for Microsoft 365 data) using plain web requests. Both are read-only:
neither one can send, delete, or modify anything in your inbox, only look
at it.

**Canvas** (`connectors/canvas.py`) reads your upcoming assignments across
all your active classes from Canvas, the learning-management system many
schools (including BYU-Idaho) use. Instead of OAuth, Canvas uses a simpler
kind of secret: a **personal access token**, which is a long random string
you generate yourself from inside Canvas's own settings page and paste into
Lydia once. It works like the OAuth tokens above (revocable, doesn't expose
your real password) but skips the whole login-page handshake since Canvas
generates it for you directly rather than through a third-party sign-in
flow.

**calendar_mac** (`connectors/calendar_mac.py`) is different from the rest:
it doesn't talk to the internet at all. It reads events straight out of the
macOS Calendar app already installed on your Mac, using Apple's own
`osascript` scripting tool to ask the built-in Calendar database for
upcoming events. The first time it runs, macOS itself (not Lydia) will pop
up a one-time system permission prompt asking whether to let it read your
calendar. That's Apple's own privacy protection, working exactly the same
way it would for any other app on your Mac. If your real calendar actually
lives in Google or Outlook rather than Apple's own calendar, the connector
tells you to add that account under macOS's Internet Accounts settings so
the Mac's own Calendar app syncs it in. Lydia then just reads whatever the
Mac already has.

**weather** (`connectors/weather.py`) fetches current conditions and a
two-day forecast from Open-Meteo, a free weather service that needs no
account or API key at all. If you don't name a location, it figures out
roughly where your computer is from your internet connection's public IP
address (this is a coarse, city-level guess, a common and privacy-light
way for a program to guess "roughly where is this device," nothing more
precise than that), using a service reached over a secure HTTPS connection
specifically so a weather report can't be tampered with by anyone snooping
on the network before it reaches the AI model's prompt.

**stocks** (`connectors/stocks.py`) fetches a general snapshot of three
well-known U.S. stock market indices (the S&P 500, the Nasdaq, and the Dow
Jones), showing the current price and how much it's moved since the
previous close. It's a market overview, not a personal portfolio tracker;
it doesn't know about any stocks you personally own. It uses `yfinance`, an
unofficial library that reads public Yahoo Finance data, again with no
account or key needed.

**news** (`connectors/news.py`) pulls recent AI-related headlines from a
short, fixed list of RSS feeds (a standard format many news and blog sites
publish automatically, meant to be read by software rather than a browser):
currently TechCrunch's AI tag, AI News, and MIT Technology Review's AI
section. Notably, this connector deliberately does *not* try to summarize
the articles itself. It just hands back the raw headlines and links, and
leaves the actual summarizing to the AI model, since that keeps the
connector itself simple, predictable, and easy to test.

**ntfy** (`connectors/ntfy.py`) is the one connector that sends information
*out* instead of fetching it in. It pushes a notification to your phone
via ntfy.sh, a free push-notification service. You install the ntfy app on
your phone and subscribe it to one secret "topic" name (effectively a
password: anyone who knows the topic name could send notifications to it,
which is why it's generated randomly and stored in your keychain rather
than being something guessable like your name). Lydia can then post a
title and message to that topic from your Mac, and it shows up as a push
notification on your phone almost immediately, which is useful for things
like "an automation finished" or a scheduled briefing being ready.

## Why these are opt-in extras

As covered in the packaging chapter, Lydia's base install deliberately
includes only what the core coding agent needs. The email, Canvas, and
market-data connectors depend on a handful of extra libraries (Google's API
client, Microsoft's MSAL library, `yfinance`, `feedparser`) that most people
trying Lydia out as a coding assistant have no use for and shouldn't be
forced to download. They're grouped behind the `[assistant]` install extra
in `pyproject.toml`, and nothing in the base program breaks or even notices
if they're missing. A connector's own import of its library only happens
the moment that specific feature is actually used, so the rest of Lydia
works fine either way.

## Voice mode: talking to Lydia out loud

Voice mode is a separate feature from connectors, though it often ends up
using them. Asking Lydia out loud "what's on my calendar today" runs
straight into the calendar connector once the words have been turned into
text. Voice mode's own job is just the talking-and-listening part: it lives
in `src/lydia/voice/`, and like the connectors, it needs its own optional
install extra (`[voice]`, adding `sounddevice` for microphone access,
`openwakeword` for wake-word detection, and `faster-whisper` for
speech-to-text) since most people don't want a program listening to their
microphone by default.

The whole pipeline, start to finish, is four stages that hand off to each
other one at a time:

**1. Wake word.** Before anything else happens, a small, lightweight,
always-on listener is running continuously, and it does exactly one job:
recognize a single specific trigger phrase (by default, "Hey Jarvis": the
name is just whatever `.onnx` model file is configured; `wake.py::WakeDetector`
also supports a custom-trained wake phrase, like a literal "Hey Lydia," if
one's ever trained). This matters for privacy: nothing you say is being
transcribed, understood, or sent anywhere while this stage is running. The
wake-word model's only capability is scoring each small chunk of audio for
"does this sound like the trigger phrase," and throwing that chunk away
immediately after. It only "wakes up" (literally starts recording what you
say next) the moment that one phrase is detected, marked in the code by
`WakeDetector.process()` returning `True` exactly once per activation (it
"re-arms" itself only after the audio quiets back down, so one wake word
doesn't fire a dozen times in a row).

**2. Speech-to-text.** Once woken, Lydia records what you say, waiting
briefly for you to actually start talking, then recording until you go
quiet again, and turns that recorded audio into plain text using a tool
called `faster_whisper` (`voice/stt.py`), running a local, lightweight
version of OpenAI's open-source Whisper speech-recognition model, entirely
on your own Mac. No audio ever leaves your machine for this step, matching
the same "everything stays local" principle behind the rest of Lydia.
Whisper has a known quirk where it sometimes "hears" words in silence or
background noise (it might transcribe pure quiet as something like "You" or
"Thank you."); the code measures Whisper's own confidence about whether
speech was really present and throws away anything below a threshold to
avoid Lydia responding to noises you never actually said.

**3. The normal agent pipeline.** Whatever text came out of step 2 is
handed to the exact same request → tool → observe → respond agent loop
described in the very first document in this set (`run_agent_turn`, the
same function the typed-chat REPL uses). Voice mode isn't a separate,
simpler version of Lydia; it's the same brain with a different front door.
It does use a shorter list of tools than the full coding agent (things like
checking email, Canvas, weather, stocks, and news, opening apps, and basic
file lookup; see `VOICE_TOOLS` in `voice/assistant.py`) and a system
prompt telling the model to answer in a few short spoken-sounding sentences
rather than the long, markdown-formatted answers it might give in text
chat, since a wall of bullet points is awkward to have read aloud.

**4. Text-to-speech.** Once the model replies, that written answer gets
read aloud back to you (`voice/tts.py`), again fully locally, using macOS's
own built-in `say` command (some of the project's own planning notes
mention a tool called `piper` as the intended text-to-speech engine; the
actual shipped code uses macOS `say` instead, which needs no extra
download at all since it's already part of every Mac). Before speaking, the
text is cleaned up, stripping markdown symbols, links, and emoji, so `say`
doesn't read things like literal asterisks or hash symbols out loud.

Every one of those four stages is written so it can be swapped out for a
fake version in tests, matching a pattern used throughout Lydia: `run_loop`
in `voice/assistant.py` takes the microphone, the wake detector, the
transcriber, and the speaking function all as arguments, rather than
creating real versions of them itself.

## Foreground vs. always-on background listening

`lydia listen`, run with no extra word after it, starts the voice loop
right there in your terminal window and keeps listening until you press
Ctrl-C to stop it, which is good for testing it out or using it for a
while on purpose.

`lydia listen enable` (and its partners `disable` and `status`) is the
always-on version: instead of running in a terminal you have to keep open,
it registers Lydia's voice loop as a background service using the same
`launchd` mechanism (macOS's built-in system for running programs
automatically) covered in the automations chapter. `enable` writes a
background-job description file and starts it, `disable` removes it, and
`status` just reports whether it's currently set up to run. Once enabled
this way, Lydia is listening for its wake word continuously in the
background, even after you log in fresh or restart your Mac, with its
activity written to a log file (`~/.lydia/listen.log`) instead of a visible
terminal window, so you can check what it heard and said without it needing
to stay in front of you.

## How voice mode gets tested without a microphone

Automated tests for voice mode never turn on a real microphone, never play
real sound, and never load a real speech-recognition or speech-synthesis
model. Doing that inside a test suite would be slow (loading a real
Whisper model takes real time), unreliable (a test that depends on an
actual sound card behaves differently on every machine and CI runner), and
would require a working microphone on whatever computer happens to be
running the tests, including automated ones with no audio hardware at all.

Instead, because every stage of the pipeline is injectable (as noted
above), the tests feed in pre-made fake audio data (plain arrays of
numbers standing in for a recorded voice) and fake stand-ins for the
wake-word detector, the transcriber, and the text-to-speech function, then
check that the pieces hand off to each other correctly: that a detected
wake word triggers a recording, that recorded audio gets sent to the
transcriber, that the transcribed text reaches the same agent loop the rest
of Lydia uses, and that whatever the model replies gets sent onward to be
spoken. It's testing the wiring and the logic, not the actual audio
hardware or the actual AI models underneath. Those are exercised only by
hand, by actually running `lydia listen` and talking to it.

## How this connects to the big picture

Everything in `00-start-here.md` is about Lydia's core identity as a free,
local, no-account-needed coding agent built on Ollama. Connectors and voice
mode are the clearest example of how Lydia grew past being *just* that: the
same agent loop, the same tool-calling design, and the same "local by
default, opt-in for anything extra" philosophy get reused to turn Lydia
into something closer to a personal assistant, not just a coding tool,
while never compromising the original design's core promises. The email
and Canvas connectors still ask permission narrowly and revocably (OAuth)
rather than demanding your real passwords; the market-data and weather
connectors need no account at all; and voice mode keeps every actual voice
recording and every spoken reply on your own machine, the same way the core
agent keeps your code on your own machine. Nothing here required loosening
any of the guarantees the rest of the project makes. It's the same
architecture, pointed at a wider set of problems.
