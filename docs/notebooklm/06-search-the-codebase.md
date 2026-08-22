# Chapter 6: How Lydia Searches Its Own Codebase

## The problem: too much code, not enough room

Every AI model has a **context window**, the maximum amount of text it can
"see" at once in a single conversation, measured in tokens (roughly,
chunks of a word). Chapter 5 covers this in more depth, but the short
version that matters here: Lydia's default context window is 16,384
tokens, and the system prompt plus the roughly 25 tool descriptions Lydia
hands the model already eat up about 3,500 tokens before a single
question is even asked.

That's fine for a small project. But a real codebase, even a
medium-sized one, can easily contain hundreds of thousands of words
across dozens or hundreds of files. There is simply no way to stuff an
entire project into one conversation and ask the model to "read all of
this and then answer." It wouldn't fit, and even if a model had an
enormous context window, reading everything on every single question
would be slow and wasteful.

So Lydia needs a different strategy: instead of showing the model
everything, find just the handful of pieces of code that are actually
relevant to the question being asked, and show it only those. That's
what this whole chapter is about: a feature the project's own internal
planning documents label "Milestone 2: Retrieval for large repos."

This is the same basic idea search engines use. Google doesn't read the
entire internet out loud to you, it finds the few most relevant pages.
The difference here is that what's being searched is your own project's
source code, and it all runs locally, no internet involved.

## Step one: cutting files into "chunks"

Before any searching can happen, Lydia has to break every source file in
the project into smaller pieces, called **chunks**. This happens in
`context/indexer.py`. A chunk is a small window of a file, roughly 60
lines, controlled by a constant named `MAX_CHUNK_LINES`. It's small
enough that a model can meaningfully digest it, but big enough to usually
contain a complete, coherent unit of code rather than a single stray
line.

Here's the detail worth understanding well, because it's a genuinely
clever piece of the design: a plain 60-line cut would frequently slice
straight through the middle of a function or a block of logic, which
would make that chunk confusing or useless on its own. Lydia avoids
this without needing to actually understand any particular programming
language's grammar. Instead, once it reaches the 60-line mark, it peeks
ahead a further 10 lines (`LOOKAHEAD_LINES`) looking for the next
**blank line**, and if it finds one, it moves the cut point there
instead. Blank lines in real code overwhelmingly show up between
functions, between blocks, between logical sections, not in the
middle of one. So "snap to the next nearby blank line" is a cheap,
language-agnostic trick that gets you most of the benefit of a real code
parser (which would have to be built separately for every programming
language Lydia might encounter) for almost none of the cost. Consecutive
chunks also overlap by a few lines (`OVERLAP_LINES`, 3 lines) so that
something sitting right at a boundary doesn't get orphaned out of both
chunks.

Only files with a recognized programming-language extension get chunked
at all (the same list of extensions from `context/scanner.py`, covered
later in this chapter), directories like `.git`, `node_modules`, and
`__pycache__` are skipped entirely, and any single file bigger than
300,000 bytes is skipped as a safety valve against accidentally trying
to index some enormous generated file.

## Step two: turning each chunk into numbers ("embeddings")

Having small chunks of code text solves the size problem, but it doesn't
yet solve *finding the right ones*. If Lydia just searched for chunks
containing the exact words you typed, that would be plain literal text
search: useful, but limited, because it only finds a match if you
happen to use the same words the code does. If you ask "where do we
check if a user is allowed to do something" but the code actually says
`has_permission` and `authorize`, a literal word search would find
nothing, even though that's exactly the code you meant.

What Lydia actually uses is called **semantic search**: search based on
*meaning*, not exact words. The way this works under the hood is worth
explaining carefully, because the underlying idea (an "embedding") shows
up constantly in modern AI systems.

An **embedding** is a way of converting a piece of text into a list of
numbers (called a **vector**) in such a way that pieces of text with
similar *meaning* end up with similar numbers, even when they don't
share any of the same words. A rough real-world analogy: imagine you
assigned every song ever made a set of coordinates, like a spot on a
map, based on their mood and style rather than their genre label. Songs
that "feel" similar would land near each other on the map, even if one
is jazz and the other is folk, and completely different songs (a lullaby
and a war anthem) would land far apart, even if they happen to share a
word in their titles. An embedding does something similar to text: two
completely differently-worded descriptions of "checking whether a user
is logged in" would land at very similar coordinates, while two chunks
of code doing unrelated things would land far apart, no matter how many
literal words they happen to share.

Lydia doesn't invent this technique or compute these numbers itself.
That would require building and training its own specialized AI model,
which is a huge undertaking on its own. Instead, it asks Ollama to do
the work, using a small model built specifically for this one job:
`nomic-embed-text`. Every chunk of code text gets sent to that model,
and it hands back a list of exactly 768 numbers representing that
chunk's "coordinates" in meaning-space. (Chunks are sent in batches of
32 at a time, set by `EMBED_BATCH_SIZE`, purely as a practical
efficiency detail, not because it changes the result.)

## Step three: storing and searching the numbers

All of those chunks, along with their 768-number embeddings, get saved
into a SQLite database file (recall from Chapter 0's glossary: SQLite is
a database that lives entirely in one file on disk). This file lives at
`.lydia/index.sqlite3` inside your project folder, and the code that
manages it lives in `database/sqlite.py`. Each row in the database's
`chunks` table stores: which file the chunk came from, its starting and
ending line numbers, the chunk's actual text, a fingerprint of the file
it came from (explained next), and the embedding itself. The embedding
is stored as a compact binary blob of raw numbers rather than as
human-readable text. That's about ten times smaller, and it avoids
having to re-parse text back into numbers every time it's read, which
matters since this file can end up holding thousands of chunks for a
large project.

When you actually ask Lydia a question and it needs to search the code,
here's what happens, handled by `context/retriever.py`:

1. Your question itself gets turned into an embedding, the exact same
   way each code chunk was: sent to `nomic-embed-text`, and it comes
   back as another list of 768 numbers.
2. Lydia then compares your question's numbers against every stored
   chunk's numbers, using a standard mathematical way of measuring how
   "close" two lists of numbers are (called cosine similarity; think
   of it as measuring the angle between two directions on that
   meaning-map from the analogy above, where a smaller angle means more
   similar meaning).
3. The chunks are sorted by how close they are to your question, and
   only the top handful (8, by default) are handed back as the search
   result, each one tagged with which file it came from and which
   lines it covers, so the model can go read more of that file directly
   if it needs to.

This is the whole trick: instead of showing the model every file, Lydia
shows it only the eight small pieces of code that are mathematically
closest in *meaning* to what you asked. That's usually a dramatic
reduction in size, and usually the actually-relevant pieces.

## Step four: not re-doing all that work every time

Computing embeddings for every chunk of every file is not instant. It's
real work being handed off to Ollama, chunk by chunk. Re-running that
from scratch every single time you index a large project would be slow
and wasteful, especially since, realistically, only a few files change
between one indexing run and the next.

So Lydia's indexer is **incremental**: it keeps track of a short
"fingerprint" of each file's contents, called a content hash, and skips
re-processing any file whose fingerprint hasn't changed. Concretely,
every time a file is indexed, Lydia runs its entire text through a
hashing function (SHA-256) that produces a short, fixed-length string
that's effectively unique to that exact file content. Change even one
character in the file, and the hash comes out completely different.
That hash gets stored alongside the file's chunks in the database.

The next time you run the indexer, for each file it finds on disk, it
computes that file's current hash and checks it against the hash already
stored in the database for that path. If they match, the file hasn't
changed since last time, so Lydia skips it entirely: no re-chunking, no
re-embedding, nothing. Only files that are brand new, or whose hash has
actually changed, get chunked and re-embedded. And on top of that, if a
file existed in the index before but no longer exists on disk (it was
deleted or renamed), Lydia notices it's now "stale" and removes its
chunks from the database too, so search results never point you at code
that isn't there anymore.

This whole process (walk the project, hash each file, skip unchanged
ones, chunk and embed the rest, clean up deleted ones) is what runs
when you type `lydia index`, and it's what the `build_index` function in
`context/indexer.py` does end to end.

## Two different tools: search_semantic vs. search_code

Lydia's agent (covered in Chapters 3 and 4) actually has two separate
code-search tools available to it, and it's worth being clear about the
difference:

- **`search_code`** is plain literal text search. It looks for an
  exact word or phrase appearing in the project's files, the same idea
  as pressing Ctrl+F in a text editor, just applied across every file at
  once. It needs no index and no embeddings; it works immediately.
- **`search_semantic`** is the meaning-based search described in this
  chapter. It can find relevant code even when you don't know, or don't
  use, the exact words the code itself uses, but it depends entirely on
  the SQLite index built by `lydia index` existing already. If you ask a
  question before ever running `lydia index`, the tool doesn't crash or
  silently return nothing useful. It checks `is_indexed()` first
  (which just checks whether the database file exists and actually has
  chunks in it) and, if there's no index yet, cleanly reports back "not
  indexed yet" so the model (and you) know exactly what happened and
  what to do about it.

The model generally has both tools available and picks whichever seems
more suited to the question. An exact function or variable name is
often faster to find with literal search, while a vaguer, conceptual
question ("where do we handle login") is exactly what semantic search
was built for.

## Why switching AI providers doesn't just work here

Chapter 5 explains that Lydia can talk to more than one kind of AI
system: Ollama by default, but also Google's Gemini as an opt-in
alternative. That flexibility runs into a real limitation here, and
it's worth understanding why, because it's a good example of a system
deliberately refusing to do something rather than doing it wrong.

The 768 numbers Ollama's `nomic-embed-text` model produces for a piece
of text are meaningful only relative to *that specific model*. A
different embedding model, say one belonging to Gemini, would encode
meaning using a completely different scheme: a different quantity of
numbers, on a different scale, shaped by a completely different
training process. Two embeddings from two different models are not
directly comparable, even if by coincidence they happened to produce
the same count of numbers. Going back to the earlier map analogy: it's
like comparing a coordinate from a map of the United States to a
coordinate from a map of Japan. Both are pairs of numbers, but treating
them as points on the same map would put you somewhere meaningless.

This matters because Lydia's current design hardcodes which embedding
model built any given index. It doesn't keep a record, per index, of
which model or provider actually produced its numbers. That means if you
built a search index while using Ollama, then switched your configured
provider over to Gemini, and then tried to run a semantic search, Lydia
would have no built-in way to detect that the question's new
Gemini-shaped embedding doesn't belong on the same map as the old
Ollama-shaped chunk embeddings already sitting in the database. Comparing
them anyway wouldn't produce an error. It would produce a confidently
wrong answer, silently, which is far worse than an obvious failure.

Rather than ship that risk, Lydia's code takes the blunt but honest
option: both places semantic search could run simply refuse outright
whenever the configured provider isn't Ollama, with a clear message
explaining why, instead of attempting a comparison that can't be
trusted. Literal `search_code` is completely unaffected by any of this,
since it never involves embeddings at all. Only `search_semantic` and
the `lydia index` command carry the guard. Properly supporting multiple
providers here (tracking which model and dimensionality built each
index, and re-embedding automatically when the provider changes) is
listed as real, deliberately unfinished future work, not something that
was overlooked.

## A lighter-weight cousin: context/scanner.py and `lydia analyze`

Separate from all of the chunking-and-embedding machinery above, Lydia
has a second, much simpler way of getting an overview of a project:
`context/scanner.py`, which powers the `lydia analyze` command.

This scanner does not involve Ollama, embeddings, or the database at
all. It's a plain, fast walk through the project's folders and files,
with no AI involved. What it produces is a summary: how many files the
project has, how many total lines of code, a breakdown of which
programming languages are used and in what proportion (Python, Markdown,
JSON, and so on, using the same extension-to-language mapping that
`indexer.py` also relies on to decide which files are even source code
worth chunking), which "manifest" files are present (things like
`pyproject.toml` or `package.json`, which reveal what kind of project
this is, whether a Python project, a Node.js project, and so on), and a
list of the largest individual source files by line count.

Think of the difference this way: `context/scanner.py` and `lydia
analyze` answer "what kind of project is this, broadly, and how big is
it," which is a quick, cheap overview a person might want before diving
in. `context/indexer.py`, `context/retriever.py`, and `search_semantic`
answer a completely different question, "which specific few pieces of
this project's code are relevant to this particular question," and
that one genuinely does require the AI-powered embedding machinery
described throughout the rest of this chapter.

## Tying this back to the big picture

Chapter 0 introduced the agent loop: your question goes to the model,
the model asks to use a tool, Lydia's code actually performs that
action and hands the result back, and the cycle repeats until the model
has enough information to answer. Everything in this chapter is what
makes one particular tool call, `search_semantic`, actually useful on
a real, large project, rather than useless or overwhelming.

Without this system, an agent working on a big codebase would either
have to guess which files might be relevant (and often guess wrong), or
try to stuff far more text into the model's limited context window than
could ever fit. By pre-chunking the project, converting each chunk's
*meaning* into comparable numbers with Ollama's own embedding model, and
storing all of it in a local SQLite database that only re-processes what
actually changed, Lydia gives the model a fast, cheap way to pull out
just the handful of code snippets that actually matter to the question
being asked. It's the same underlying idea (find the relevant few pieces
of a much bigger whole) that makes commercial tools like Claude Code and
Codex able to work across large real-world projects too.
