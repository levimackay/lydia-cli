# Chapter 5: Talking to AI Models

Every previous chapter danced around one question without answering it:
when Lydia's agent loop decides it needs the model to think, how does the
"asking" actually happen? What goes out over the wire, what comes back,
and how does Lydia manage to support three completely different ways of
reaching an AI model (a local Ollama daemon, a remote Lydia server, and
Google's hosted Gemini) without the rest of the program having to care
which one is in use?

That's the `llm/` folder. This chapter walks through it file by file:
`protocol.py`, `client.py`, `remote_client.py`, `gemini_client.py`,
`factory.py`, `models.py`, and `types.py`.

## The big idea: one shared shape, three different insides

Before touching any code, it helps to borrow an analogy from something
totally ordinary: a power outlet.

A wall outlet in your house has a fixed shape: two or three flat prongs
in a fixed arrangement. It doesn't know or care what you plug into it. A
lamp, a phone charger, a vacuum cleaner, and a television all have wildly
different insides, with different purposes, different circuitry, and
different amounts of power they draw, but they all end in a plug built to
that same fixed shape. Because of that, your wall doesn't need a different
kind of outlet for every appliance you might ever own. It needs one
outlet shape, and appliance manufacturers agree to build to it.

That's exactly the trick Lydia's `llm/` folder plays, and it is the
single most important idea in this whole part of the program.

Lydia's agent loop (the request → tool → observe → respond cycle from
Chapter 3) needs to send messages to "the model" and get a reply back.
But "the model" could mean three very different things: a copy of Qwen
running on your own computer through Ollama, the same setup running on a
different, more powerful computer that you reach over the network, or
Google's Gemini running on Google's servers on the other side of the
internet. Each of those requires genuinely different code to actually
reach: different addresses, different security handling, different
data formats on the wire.

If the agent loop had to know about all three of those differences
directly, every place in the program that talks to a model would need a
tangle of "if it's Ollama, do this; if it's remote, do that; if it's
Gemini, do this instead" logic, repeated everywhere a message gets sent.
That would be fragile and it would make adding a fourth option (the
project's own roadmap mentions OpenAI or Anthropic someday) painful
everywhere at once.

Instead, `llm/protocol.py` defines one fixed shape, the "outlet," called
`ModelClient`. In plain English, it's a checklist of things any AI-model
connector must be able to do:

- **`is_alive()`** checks whether the model source can currently be
  reached at all.
- **`list_models()`** lists which models are available to use.
- **`has_model(name)`** checks whether one specific model is available.
- **`embed(model, inputs)`** turns text into the numeric "meaning
  fingerprints" used by Lydia's codebase search feature (covered in
  Chapter 6).
- **`chat_stream(...)`** sends a conversation (plus the tools the model
  is allowed to use) and gets the reply back as a stream of pieces.
- **`close()`** shuts the connection down cleanly when done.

Three real, separate pieces of code in `llm/` (`OllamaClient`,
`RemoteClient`, and `GeminiClient`) each build to that exact checklist.
None of them inherit from a shared parent class or announce "I am a
ModelClient" anywhere in their own code. In Python, this particular style
of interface is called a **Protocol**: instead of formally declaring "I
am one of these," a piece of code simply happens to have every method on
the checklist, with matching inputs and outputs, and that's enough. It
counts as fitting the shape, the same way any plug with the right prong
arrangement counts as fitting the outlet, no certificate required. This
is the concept programmers mean by an **interface**: an agreed-upon shape
that different pieces of code can satisfy independently.

Because of this, every other part of Lydia that needs to talk to a model
(the agent loop, the tool-execution code, the codebase-search indexer
and retriever) is written to expect "something that fits the
`ModelClient` shape," never "specifically `OllamaClient`." Those parts of
the program are handed a client object once, at startup, and from then on
they call `is_alive()`, `chat_stream()`, and so on without ever asking
which of the three concrete kinds it actually is. The agent loop that
handles a tool call from a locally-run Qwen model runs through the exact
same code path as one talking to Gemini on Google's servers. The only
place in the entire codebase that has to know all three options exist,
the only place that plugs the actual appliance into the actual outlet,
is one function, `build_client()`, covered near the end of this chapter.

## Talking to Ollama: what actually gets sent and received

`OllamaClient`, in `llm/client.py`, is the original and default
connector, the one used whenever nothing else is configured. It's worth
walking through exactly what happens on a single chat turn, because the
other two clients are variations on the same pattern.

Ollama runs its own miniature web server on your machine at
`http://localhost:11434` (introduced in Chapter 1). `OllamaClient` talks
to it using ordinary HTTP requests, the same underlying technology a web
browser uses to load a page, through a Python library called `httpx`.
Three addresses (called **endpoints**) matter:

- `GET /api/version` is a health check, used by `is_alive()`.
- `GET /api/tags` is the list of models you've already downloaded.
- `POST /api/chat` is the actual conversation endpoint.

When Lydia wants a reply from the model, it builds a **request**: one
JSON object containing which model to use, the entire conversation so
far as a list of messages (system instructions, what you typed, what the
model said before, any results from tools it already used), the schemas
describing every tool the model is allowed to call this turn, and a few
settings like how "creative" the model should be (`temperature`) and how
much conversation it's allowed to see at once (`num_ctx`, covered
below). Building this JSON object is the job of a small standalone
function, `build_chat_payload()`, which lives outside the `OllamaClient`
class itself. More on why in the wire-format section further down.

The reply doesn't come back as one single block of text. It streams in,
which Chapter 0's glossary introduced as sending a response piece by piece
instead of all at once. Specifically, Ollama sends it as **NDJSON**
("newline-delimited JSON"): the server writes one complete, self-contained
JSON object, then a newline character, then the next complete JSON
object, then a newline, and so on, for as long as the model keeps
generating. Lydia reads the response one line at a time as it arrives (`for
line in response.iter_lines()`) and hands each line to a function called
`parse_chat_line()`, which turns that one line of JSON into a small
Python object called a `ChatChunk` (defined in `llm/types.py`), a
container holding just that one piece: some text, maybe a "the model is
done" flag, maybe token-count statistics. The very last chunk in the
stream is marked `done=True` and carries stats about the whole turn
(how many tokens were generated, how long it took).

This is the mechanical reason AI chat tools appear to "type" their
answer instead of showing it all at once instantly: they genuinely are
receiving and displaying the answer piece by piece, in the order the
model is generating it, because that's the literal shape of the data
coming across the network connection.

## The real gotchas: things that went wrong before they were understood

Ollama's behavior is not perfectly predictable or obvious from the
outside, and several real problems had to be discovered by testing, not
guessed at from documentation. Each one is a good story on its own.

**"Thinking" replies hide in a separate field.** Some models, and Qwen3.5
is the one Lydia is tested against, are built to reason through a problem
in writing before committing to a final answer, similar to a person
thinking out loud before speaking. Ollama gives this reasoning its own
field in the response, called `thinking`, completely separate from the
`content` field that holds the actual reply. The first time this was
encountered, it looked like a bug: the model would appear to sit there
producing nothing for a long stretch, because the code was only watching
`content`, and `content` genuinely was empty. All the real activity was
arriving in `thinking`, unwatched. The fix was to read and handle both
fields (`parse_chat_line()` pulls out `thinking` alongside `content`, and
the terminal display shows it dimmed, as a collapsible preview, so you
can see the model is actively working rather than staring at a blank
screen).

**Tool requests arrive whole, not gradually.** Everything else in a
streamed reply trickles in: a few words of `content` at a time, a
sentence of `thinking` at a time. Tool calls don't. When a model decides
to use a tool, the entire request, which tool and with which arguments,
shows up complete, in one single chunk, even though the request as a
whole is still technically a "streaming" response. There's no
partially-formed tool call to display gradually; either a chunk carries a
complete `tool_calls` list or it doesn't carry one at all. Lydia's
parsing code reflects this directly: `parse_chat_line()` reads the whole
`tool_calls` list out of whichever single line contains it, with no
accumulation logic needed for that part.

**Ollama forgets the model to save memory, and reloading it is slow.**
By default, if you don't send Ollama any request for five minutes, it
unloads the current model from memory entirely, freeing up that RAM (or
graphics-card memory) for other things. That's a sensible default for a
program meant to sit quietly on your machine. But it means the very next
message you send after a pause has to wait for Ollama to reload the
whole model back into memory first, a delay of several seconds,
noticeable as an unexplained stall right at the start of a new
conversation. Lydia works around this by sending a `keep_alive` setting
on every single chat request (defaulting to `30m`, thirty minutes),
which is Ollama's own way of saying "please don't unload this model for
a while, I'll probably be back." It costs nothing when you are actively
chatting, and it quietly prevents that reload delay from happening in
the middle of normal use.

**Some models lie about supporting tools.** Ollama has a real, structured
way for a model to request a tool call: that `message.tool_calls` field
covered above. In theory, any model whose "chat template" (a kind of
built-in formatting rulebook Ollama uses per model) declares tool support
should use that field correctly. In practice, it was discovered that at
least one real, commonly-used model, `qwen2.5-coder`, declares that it
supports tools, but then, when it actually wants to use one, writes the
request out as plain text inside the ordinary `content` field instead of
using the structured field at all. Lydia's code only ever looks at the
structured `tool_calls` field for tool requests, so with a model like
this, the agent loop simply never sees any tool call happen. Not an
error, not a warning, just silence. From Lydia's point of view, the model
looks like it's ignoring every tool it was offered.

This is a nasty kind of bug precisely because it's silent and looks like
a completely different problem (a "dumb" model that won't use tools),
when the actual cause is a mismatch in how the model chooses to format
its own output. The only reliable fix was to stop trusting a model's
claimed capabilities and instead test it directly: send it a deliberately
simple request offering one trivial tool, and check with your own eyes
whether the reply's `tool_calls` field is actually populated, or whether
the tool request leaked into `content` as plain text instead. `llm/models.py`
keeps a running list of models confirmed broken this way
(`KNOWN_NON_TOOL_CALLING`, currently including `qwen2.5-coder`,
`deepseek-coder`, and `phi3.5`) so Lydia's automatic model-picking logic
knows to skip them rather than silently handing you a model that will
never use a single tool. This is also exactly why any *new* model has to
be manually verified this same way before it's trusted as a default: a
model's own advertised capabilities aren't proof of anything, only an
actual test is.

**The context window can quietly overflow, losing data with no error.**
Every AI model has a hard limit on how much text (instructions, prior
conversation, tool results, everything combined) it can consider at
once when generating a reply. This limit is called the **context
window**, and Lydia controls its size with the `num_ctx` setting sent on
every request. Think of it as the model's short-term memory capacity: it
can only "see" that many tokens' worth of the conversation at a time (a
**token** is roughly a word or word-fragment, the unit AI models
actually count in). Lydia's own system prompt plus its roughly 25 tool
descriptions already cost around 3,500 tokens before a single word of
actual conversation is added, so this limit fills up faster than it
might sound like it should.

The dangerous part is what Ollama does when the limit is reached: it
doesn't refuse the request, and it doesn't raise any error. It silently
drops the oldest messages from the conversation to make room, and
generates a reply as if nothing happened. From the outside, this looks
exactly like the model "forgetting" something you told it earlier, or
acting as though it never read a file it actually did read a few turns
ago. From the model's perspective at that point, it genuinely
never saw that information; it had already been quietly discarded before
this request was even sent. This is a particularly nasty category of bug
because there's no crash, no warning, and no log entry pointing at the
cause. The only symptom is the model behaving as if part of the
conversation simply never existed. Diagnosing it required deliberately
measuring real token counts against a real repository (using the actual
`prompt_eval_count` statistic Ollama reports back on `done` chunks)
rather than guessing, and the fix was raising `num_ctx` from its original
8192 up to 16384, big enough to comfortably fit a multi-file
conversation without the severe slowdown an even bigger window caused on
ordinary laptop hardware.

## RemoteClient: the same idea, reached over a network instead of your own machine

`RemoteClient`, in `llm/remote_client.py`, exists for the setup described
in Chapter 0: Ollama running on a second, more powerful computer (a
gaming PC with a real graphics card, say), with your laptop's `lydia`
talking to it instead of running the model itself. The file it's used
with is Lydia's own optional server program, `lydia-server`, covered in
full in Chapter 7.

The key thing to understand here is how little actually changes. Rather
than reimplementing the whole conversation, `RemoteClient` deliberately
reuses `build_chat_payload()` and `parse_chat_line()`, the exact same
functions `OllamaClient` uses to build the request and read the reply,
because the Lydia server's own endpoints are thin pass-throughs onto its
own Ollama daemon: the server receives essentially the same JSON shape,
forwards it to its own local Ollama, and hands the same NDJSON stream
right back. Reusing the same functions instead of writing new ones means
Lydia only had to get the wire format right once, and both `OllamaClient`
and `RemoteClient` are guaranteed to stay in agreement about it. If one
changed and the other didn't, the two would quietly drift out of sync.

Three real differences separate it from talking to Ollama directly:

- **An address to reach, instead of `localhost`.** Rather than the
  fixed, always-local `http://localhost:11434`, `RemoteClient` is given
  whatever address the Lydia server is actually running at, commonly
  reached over a private network tool called Tailscale rather than the
  open internet, so it's private without needing a whole VPN setup.
- **A security token.** Because this request is now leaving your own
  machine, `RemoteClient` attaches a bearer token (a secret string proving
  you're allowed to use this particular server) to every request, in an
  `Authorization` header. Ollama itself has no such concept, since anyone
  who can reach `localhost:11434` on your own machine is, by definition,
  already you.
- **An HTTPS-encrypted hop instead of talking to something already
  running on your own computer.** Traffic to `localhost` never leaves
  your machine at all, so there's nothing to intercept. Traffic to a
  remote server travels over an actual network, so it's carried over
  HTTPS (the padlock-icon, encrypted version of the same HTTP protocol)
  so nobody else on that network can read your conversation or file
  contents as they pass by.

Everything else (building the request, reading the streamed reply chunk
by chunk, handling a "thinking" model's separate field, handling a tool
call arriving whole) is identical, because it's the identical wire
format traveling to a different address with a different lock on the
door.

## GeminiClient: the first option that isn't Ollama at all

`GeminiClient`, in `llm/gemini_client.py`, is different in kind from the
other two. `OllamaClient` and `RemoteClient` both ultimately reach an
actual Ollama daemon somewhere: one running on your own machine, one
running on someone else's. `GeminiClient` reaches Google's own hosted
Gemini models directly, running on Google's servers, with no Ollama
anywhere in the picture.

To use it, you need what's called an **API key**. In plain terms, that's
a personal password that proves to Google's servers that a specific
account (yours) is allowed to use this particular paid service, and that
usage should be billed to that account. Every single message you send
through Gemini this way costs a small amount of real money, charged by
Google directly to whoever owns that key, unlike Ollama, where the
model runs on hardware you already own and there's no per-message bill
at all.

Because of that cost, and because Lydia's entire stated purpose is "works
with just Ollama running locally, no API keys, no accounts, no cost" (as
Chapter 0 puts it), Gemini is never chosen automatically. It only
activates if you explicitly opt in by setting `config.provider` to
`"gemini"` and supplying your own key, which is stored securely in your
computer's own keychain rather than sitting in a plain settings file,
the same treatment given to other sensitive credentials like email
logins (covered in Chapter 8).

The mechanics inside `GeminiClient` are a good illustration of what the
shared `ModelClient` shape is actually buying Lydia: underneath, Gemini's
API looks nothing like Ollama's. It uses different names for
conversation roles (`model` instead of `assistant`, `function` instead of
`tool`), a different shape for describing available tools
(`functionDeclarations` grouped together, rather than Ollama's flatter
list), and a different streaming format called Server-Sent Events,
commonly abbreviated **SSE**, where each streamed piece arrives prefixed
with the literal text `data:` rather than as a bare NDJSON line.
`GeminiClient` contains dedicated functions (`_to_gemini_contents`,
`_to_gemini_tools`, `_parse_candidate`) whose entire job is translating
between Lydia's own internal message shape and Gemini's very different
one, in both directions. None of that translation complexity leaks out
anywhere else in the program, precisely because it's all hidden behind
the same `chat_stream()` method every other client also exposes. The
agent loop calling `chat_stream()` on a `GeminiClient` has no idea any of
this translation is happening. It just gets `ChatChunk` objects back,
same as always.

One more honest detail worth knowing: this translation work wasn't
written from assumptions about how Gemini's API "should" work based on
reading documentation alone. It was checked against Google's real,
live API first, the same hands-on-verification discipline used for
discovering Ollama's own gotchas above. That's how it was confirmed, for
instance, that Gemini's SSE stream sends one complete JSON object per
`data:` line rather than fragments that need to be pieced together, and
that JSON Schema type names written in lowercase (the normal way) work
correctly without needing to be adjusted for Gemini's format.

Also worth noting: `chat_stream()`'s method signature includes
Ollama-specific settings (`num_ctx`, `think`, `keep_alive`) because
that's the one fixed shape every `ModelClient` must match. `GeminiClient`
accepts all three, and simply ignores them, since none of those concepts
map onto a hosted API the way they do onto a local Ollama daemon
managing its own memory. That's a normal, intentional consequence of a
shared interface: a shape wide enough to cover every real client will
sometimes hand one particular client a setting it has no use for, and the
right response is just to accept and ignore it quietly, not to complain.

## build_client: the one place that knows all three options exist

`llm/factory.py` contains a single function, `build_client()`, and it is
deliberately the only place in the entire program allowed to know that
three kinds of `ModelClient` exist at all. Everything about how Lydia
decides which one to actually construct lives here, and nowhere else:

1. If your settings say `provider = "gemini"`, build a `GeminiClient`
   (and fail with a clear error immediately if no Gemini key has been
   configured, rather than silently falling back to something else).
2. Otherwise, if a remote server address (`server_url`) is set in your
   settings, build a `RemoteClient` pointed at it.
3. Otherwise, in the default "just works" case with no configuration at
   all, build an `OllamaClient` pointed at your own machine's Ollama.

This function is called exactly once, near the start of a Lydia session,
and the single object it returns, whichever concrete class it happened
to build, gets handed down into the agent loop, the tool-execution
code, and the codebase-search system. From that point on, none of that
downstream code ever asks "which kind of client is this?" It only ever
calls the methods every `ModelClient` is guaranteed to have. This is what
the project's own internal notes mean when they call `factory.py` "the
one seam." It's the single, narrow point where a future fourth option
(the project's roadmap already mentions OpenAI or Anthropic as
candidates) would need exactly one new `if` branch here, plus one new
class satisfying the `ModelClient` checklist, and nothing else anywhere
in the rest of the program would need to change at all.

## How this connects back to the big picture

Chapter 0 described the agent loop as a cycle: your request goes to the
model, the model asks to use a tool, Lydia carries that out and reports
back, and the cycle repeats until there's a final answer. This chapter
has been about exactly one link in that cycle, the moment a message
actually leaves Lydia's own process and a reply comes back, and about
why that link had to be built the way it was.

The `ModelClient` interface is what lets Lydia keep its founding promise
(work entirely for free, on your own machine, with no account and no
company in the loop) while still leaving room for real exceptions to that
rule when you specifically choose them: borrowing a more powerful
computer over your own network, or paying Google directly for Gemini
access. Neither exception required touching the agent loop, the tool
system, or the codebase-search system at all, because none of those
parts of Lydia were ever written to know Ollama existed in the first
place. They were written to know about the shape of the outlet, not the
appliance plugged into it.
