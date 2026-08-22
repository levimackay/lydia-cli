# Chapter 7: The Server and Remote Control

This chapter covers `server/lydia_server`, the optional second program
introduced in the start-here document: `lydia-server`. Everything in this
chapter is about letting one computer borrow another computer's AI
horsepower. Nothing here touches your files, ever. That last point is
the whole design, and it comes up again and again below.

## The concrete problem this solves

Say Levi has two computers: a laptop he carries around, and a gaming PC
at home with a much better graphics card. Ollama, the program that
actually runs the AI model, works fine on both, but a big model runs
much faster on the gaming PC's GPU than on the laptop's weaker hardware.
Every reply from the laptop, run locally, is noticeably slower.

`lydia-server` is the fix. You install and run it on the gaming PC,
right alongside Ollama. Then, instead of pointing the laptop's `lydia`
command at its own local Ollama, you point it at the gaming PC over the
network. When you ask the laptop's `lydia` a question, here's what
actually happens:

1. The laptop's `lydia` sends your question (and the conversation so
   far) over the network to `lydia-server`, running on the gaming PC.
2. `lydia-server` forwards that request to the gaming PC's own local
   Ollama, which does the actual "thinking" using the fast GPU.
3. The answer streams back over the network to the laptop's `lydia`,
   which shows it to you exactly as if it had been produced locally.

Crucially, everything else (reading your files, editing them, running
git commands, running terminal commands) still happens entirely on the
laptop, because that's where your actual project lives. The gaming PC
never sees your files. It only ever sees the text of the conversation:
your questions, the model's replies, and (when the agent decides to use
a tool) the tool's name and the result that gets fed back into the
conversation as text. It never receives a file, never writes one, never
runs a shell command, never touches git. `server/README.md`, the
server's own documentation, states this outright: "What this server
does NOT do: touch your project's files, run git, or run shell
commands." The routes file itself (`api/v1.py`) says the same thing in
its very first comment: "No route here ever touches a filesystem, git,
or a shell," because "this server is purely an inference proxy."

### Why the design was drawn this way on purpose

This wasn't an accident or an oversight. It's a deliberate line drawn
early in the project, described in the project's own internal notes as
a "resolved design fork." Two things fall out of keeping tool execution
on the laptop's side only:

- **You never have to trust a second machine with your files.** The
  gaming PC could be compromised, could have other software on it you
  don't fully trust, could be shared with someone else. None of that
  matters, because it is architecturally incapable of reading, writing,
  or deleting anything in your project. The only thing crossing the
  network is conversation text.
- **No approval prompts have to cross the network mid-conversation.**
  Recall from earlier chapters that before Lydia does anything
  destructive (writing a file, running a command, making a git commit),
  it stops and asks you to approve it first. If tool execution happened
  on the *server's* side, that yes/no prompt would somehow have to be
  relayed back across the network to you, and the answer relayed back
  again, in the middle of an active response. That's a much harder
  problem to build correctly (it would need something like a
  constantly-open two-way connection, called a WebSocket, instead of
  the simpler one-way streaming this server uses). By keeping tool
  execution client-side, the approval prompt just happens locally on
  your laptop, instantly, the same way it always does, and the server
  never even knows a tool was about to run.

## What FastAPI is

`lydia-server` is built using **FastAPI**, a popular, free Python tool
for building exactly this kind of program: something that sits and
waits for requests over a network and sends back responses. This is the
same basic idea as any website's server, where a web browser sends a
request for a page and a server sends back the page's contents, except
that instead of serving web pages, `lydia-server` serves AI answers. You
define a set of "routes" (specific web addresses it knows how to respond
to, like `/v1/chat`), and FastAPI handles all the plumbing: listening on
a network port, matching incoming requests to the right route, converting
the request data into a usable form, and sending the response back in
the right format.

`lydia_server/main.py` is where the FastAPI application itself gets
built. It creates the app, gives it a title and version, and attaches
the full set of routes to it. It also defines what's called a
**lifespan**: code that runs once when the server starts up and once
when it's shutting down, wrapped around the entire time the server is
running. Lydia's lifespan doesn't do anything at startup, but at
shutdown it makes sure the connection to Ollama gets closed cleanly
(more on why that matters in the connection-pooling section below).

## The routes, one by one

All of the server's actual behavior lives in `api/v1.py`. There are
four routes, all grouped under a shared `/v1/` prefix:

**`/v1/health`** is a simple "are you alive?" check. It takes no
authentication and returns basically nothing but "ok" and a version
number. This exists so something monitoring the server (or a person
troubleshooting a connection problem) can quickly confirm the server
process is up and responding, without needing a valid login token just
to ask that question.

**`/v1/models`** returns the list of AI models currently available on
this server's Ollama (name, size, and when it was last modified). This
is how the laptop's `lydia` can show you which models it's allowed to
pick from when it's talking to a remote server, the same way it would
list locally installed models when running against your own machine.

**`/v1/chat`** is the actual conversation route, and the one that does
the real work. You send it the message history, which model to use, and
some options (temperature, context size, whether the model should show
its reasoning, what tools are available). It streams the model's answer
back piece by piece as it's generated, the same streaming behavior
described in earlier chapters, rather than making you wait for the
whole answer to finish before you see anything.

**`/v1/embed`** takes a batch of text strings and returns their
**embeddings**: lists of numbers that represent each piece of text's
meaning, used for the semantic code search feature covered in chapter
6. This lets the "borrow a stronger machine" idea extend to indexing a
codebase too, not just chatting, since turning code into embeddings is
also AI work that benefits from a good GPU.

Every route except `/v1/health` requires proof that you're allowed to
use this server. That's the bearer token system, covered next.

## Bearer tokens: proving you're allowed in

A **bearer token** is a secret string of random characters that you
include with every request you send to the server, as proof that you're
allowed to use it. The name comes from the idea that whoever "bears"
(holds) the token is trusted. The server doesn't verify your identity
some other way, it just checks: does this request include a token that
matches one I recognize? If yes, it's allowed through; if no, it's
rejected.

A good analogy is a hotel keycard. The front desk doesn't re-check your
ID every time you want into your room. They gave you a keycard once,
and from then on, having a working keycard *is* the proof. Anyone who
has the card can get in, which is exactly why it matters that the card
doesn't get left lying around or handed to strangers, and why a hotel
can deactivate a specific card without changing every lock in the
building.

`auth/bearer.py` is the small piece of code that checks this on every
protected request. It looks for a token in the request, and if one is
missing or doesn't match anything the server knows about, it immediately
rejects the request with an "unauthorized" error, before any AI work
happens at all.

The server also refuses to even start if no tokens are configured at
all. `main.py`'s `run()` function checks this before doing anything
else and exits immediately with an error message if the token list is
empty. This is deliberate, not an oversight: an AI inference server
sitting on your network with zero authentication is the kind of thing
that should never happen by accident. Forcing you to explicitly set up
at least one token before the server will even boot makes "oops, I left
it wide open" much harder to do by mistake.

## The TokenStore: how tokens are actually managed

### The old way: an environment variable

In the server's first version, tokens lived entirely in an
**environment variable**, a piece of configuration set when you launch
the program (for example, `LYDIA_SERVER_TOKEN=some-secret-value`). Every
time the server started, it read that variable and built its list of
valid tokens from scratch. This worked, but it had a real limitation:
there was no way to add a new person's access, or take away an
existing person's access, without stopping the server, changing the
environment variable, and starting it again. For a server meant to keep
running continuously, serving requests from a laptop that might
connect at any hour, that's a real inconvenience.

### The new way: a small database file

Tokens now live in a **SQLite database**, the same kind of single-file
database used elsewhere in Lydia for the semantic search index (see
chapter 6). It lives by default at `~/.lydia/server/tokens.sqlite3`.
Because it's a real file on disk that the running server keeps open,
tokens can be added or removed while the server keeps running, with no
restart required. A separate small command-line program,
`lydia-server-token`, is used to manage it:

- **`add <user>`** creates a brand-new token for a given person (or
  "user id") and prints it to the screen exactly once. This is the only
  moment the raw token is ever visible, so copy it down right then,
  because it can never be displayed again afterward. A token can
  optionally be given an expiration time, after which it stops working
  automatically.
- **`revoke`** turns off one specific token, given the token itself.
- **`revoke-user <user>`** turns off *every* token belonging to a given
  person at once. This is the one an administrator actually reaches for
  in practice. Since a raw token is only ever shown once, at creation
  time, the person managing access almost never has the actual token
  value to hand to `revoke` later. Revoking by user id sidesteps that
  entirely.
- **`list`** shows every token that's ever been created: who it
  belongs to, whether it's active or revoked, where it came from, and
  its creation/expiration dates, but never the raw token value itself.

The old environment-variable approach (`LYDIA_SERVER_TOKEN` /
`LYDIA_SERVER_TOKENS`) didn't go away. It's still there as a quick way
to bootstrap a single-person setup with zero extra steps. Every token
named in those environment variables gets automatically re-added to the
database every time the server starts. But it's no longer the *only*
way in: the database is the real, persistent source of truth, and it
can be managed independently of whatever the environment variable
currently says.

### Why raw tokens are never actually stored

The database never stores a token's actual text. Instead, it stores a
**hash** of it, the result of running the token through a one-way
scrambling function (specifically, one called SHA-256) that turns any
input into a fixed-length string of gibberish. The important property
of a hash is that it only works in one direction: you can easily check
whether a given token matches a stored hash (by hashing the token again
and comparing), but there is no way to reverse a hash back into the
original token that produced it.

The analogy used in the server's own code comments is a fingerprint:
storing a fingerprint of a key instead of the key itself. A fingerprint
is enough to confirm "yes, this is the same key I saw before," but no
one can look at a fingerprint and manufacture a working copy of the
key from it. Even if someone stole the entire token database file, all
they would have is a list of scrambled fingerprints. They couldn't
extract anyone's actual working token from it and use it to log in.

## The real security review, and the three things it caught

This part of the project has a genuinely interesting story behind it,
worth understanding in its own right. After building the new
database-backed token system, it was put through an automated security
review, a second pass specifically looking for security problems,
separate from and in addition to "does the feature work as intended."
That review found three real issues, and all three were fixed the same
day. This is a good, concrete example of why a dedicated security
review matters even for code that already works correctly: none of
these three problems would show up in ordinary testing, because none of
them are about whether the feature *functions*. They're all about what
happens if someone with bad intentions is paying attention.

**Issue one: the raw token was visible to other people on the same
computer.** The original design of the `revoke` command took the token
you wanted to revoke as a plain typed-in argument, like
`lydia-server-token revoke abc123secret`. The problem: on most
computers, any program is allowed to ask the operating system "what
commands are currently running, and with what arguments?" (tools like
`ps` do exactly this). For as long as that revoke command was running,
its full text, including the secret token, would be visible to
literally anything else on the machine that looked. That's a real
exposure for something whose entire purpose is being a secret. The fix
was to stop passing the token as an argument at all. Now `revoke` reads
the token either from piped input (if you're feeding it in through a
script) or from an interactive, un-echoed prompt, the same style used
by tools like `ssh-add` or `sudo`, where the characters you type don't
even appear on screen. Either way, the token never appears anywhere the
operating system logs or exposes to other programs.

**Issue two: the database file itself was readable by other accounts on
the same machine.** When the token database file was first created, it
inherited the operating system's default file permissions, which
commonly allow anyone else with an account on that same computer to
read it, even though only the person running the server should be able
to. This mattered even with the hashing described above, because a
readable file is still a readable file: no reason to leave it more
exposed than necessary. The fix locks the file (and the folder it lives
in) down immediately upon creation so that only the owning user account
can read or write it at all. Nobody else on the machine can even open
it.

**Issue three: removing a token from the environment variable silently
stopped working.** This one is subtler, and it's a great example of how
a genuine improvement can accidentally break something that used to
work by coincidence. Under the *old* design, environment-variable-only
tokens, removing a token and restarting the server actually did revoke
it, but only because the entire token list got rebuilt from scratch
on every startup, so anything not currently named in the environment
variable simply ceased to exist. That wasn't a deliberate "revocation"
feature; it was a side effect of how the old system happened to work.
When the persistent database was introduced, that side effect quietly
disappeared: a token removed from the environment variable now just...
keeps working, forever, because it's still sitting in the database and
nothing tells it to stop. Someone who assumed "I removed it from the
config and restarted, so it's revoked," the way it used to behave,
would be wrong, silently, with no error or warning to tell them
otherwise. The fix doesn't auto-revoke it (removing an environment
variable for some unrelated reason shouldn't be able to accidentally
cut off someone's access without anyone deciding that on purpose).
Instead, the server now tracks where each token came from, whether it
was seeded from an environment variable or added by hand through the
CLI, and on every startup it checks: is there an active,
environment-sourced token that is no longer named in the current
environment variables? If so, it prints a clear warning telling you
exactly that, and telling you to run `revoke-user` if you actually meant
to remove that access. The mismatch becomes visible instead of silent.

Taken together, these three fixes are a clean illustration of a general
truth about writing secure software: "it works" and "it's secure" are
two different questions, and answering the first one doesn't answer the
second. A feature can pass every functional test (add a token, use it,
revoke it, confirm it stops working) and still leak a secret to other
users on the machine, still leave a credential file world-readable, or
still quietly break a guarantee an older version used to provide by
accident. That's specifically what a dedicated security-focused review
pass is for: not "does this do what it's supposed to," but "what could
go wrong here that a normal test would never think to check."

## Connection pooling: reusing one connection instead of opening a new one each time

Every time `lydia-server` needs to talk to Ollama, it does so over a
network connection (even though Ollama is running on the very same
machine, it's still reached the same way any network service is
reached, through its own local address). The question is whether to open
a brand-new connection every single time a request comes in, or to open
one connection and keep reusing it.

Think of it like phone calls. Opening a new connection for every single
request is like hanging up and completely re-dialing the number from
scratch for every sentence you want to say: dialing, waiting for it to
ring, waiting for the other side to pick up, exchanging pleasantries,
*then* finally saying the one thing you called about, and hanging up
again immediately. It works, but all that setup and teardown is wasted
effort if you're going to call the same number again thirty seconds
later. Keeping one call open and just continuing to talk on it, instead
of hanging up and redialing every time, is faster because you skip all
that repeated setup.

That's exactly the change made to `services/ollama_provider.py`. The
original version built a brand-new connection object every time a
request came in, then closed it once that request was done. The current
version, `get_shared_provider`, keeps one connection object alive for
the whole life of the server process and just hands the same one to
every request that needs it. `main.py`'s lifespan is where that shared
connection actually gets closed, but only once, when the server itself
is truly shutting down, not after each individual request. Closing it
after every request would defeat the entire point of sharing it in the
first place, since you'd be back to hanging up and redialing every time.

## Why `/v1/chat` couldn't use the normal cleanup pattern

FastAPI has a common, tidy pattern for "set something up, hand it to the
route, then automatically clean it up afterward" called a
**yield-dependency**. It would have been the more standard way to hand
each route its shared Ollama connection. But it doesn't work correctly
here, and the reason is specific to streaming.

Recall that `/v1/chat` doesn't send its whole answer at once. It
streams it back gradually, piece by piece, as the model generates it.
FastAPI's yield-dependency cleanup runs as soon as the route's function
*returns*, which for a streaming response happens almost immediately:
the function returns a "streaming response" object right away, and the
actual sending of content happens afterward, gradually, outside that
function call. If the shared connection's cleanup had been tied to that
yield-dependency pattern, it would have run the moment the function
returned the streaming response object, which is *before* the
streaming has actually finished sending anything at all. The connection
would get torn down while it was still supposed to be actively feeding
the model's answer back to the client, breaking the response mid-flight
essentially every time.

The fix in `api/v1.py` is that `get_provider`, the function that hands
out the shared connection, is written as a plain function, not a
yield-dependency, specifically so nothing tries to tear it down right
after the route function returns. The connection's real lifetime is
tied only to the server process itself: it gets built once, reused
across every request of every kind (chat, models, embed) for as long as
the server runs, and only closed for real in the lifespan's shutdown
code, once, when the whole process is actually ending, never in the
middle of a still-streaming response.

## How this fits into the bigger picture

Going back to the very first idea in this document set: Lydia's whole
purpose is running an AI coding agent entirely on your own hardware,
with no company, no API key, and no bill. `lydia-server` doesn't change
that promise. It just lets "your own hardware" mean *whichever* machine
you own has the best GPU, instead of forcing you to use whatever machine
happens to be running the `lydia` command at the moment. The laptop
stays the one true owner of your project and everything the agent loop
(chapter 3) and its tools (chapter 4) do to it; the gaming PC is never
anything more than a fast, replaceable calculator for the "what should I
say or do next" part of the process. Everything covered in this
chapter (the bearer tokens, the hashed database, the file permission
lockdown, the un-echoed prompts, the pooled connection) exists purely
in service of making that borrowed-horsepower relationship trustworthy
and fast, without ever asking you to hand a second machine the keys to
your actual files.
