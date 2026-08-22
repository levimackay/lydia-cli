# Lydia Server

A small FastAPI app that proxies chat/embedding requests to a local Ollama
daemon over HTTPS with bearer-token auth — so a `lydia` CLI client running
somewhere else (a laptop) can use this machine's Ollama (and its RAM/GPU)
instead of its own.

**What this server does NOT do**: touch your project's files, run git, or
run shell commands. Tool execution always happens on whichever machine
runs the `lydia` CLI — this server is purely an inference proxy. See the
root [`README.md`](../README.md#running-lydia-server-remote-gpu-inference)
for why, and the migration plan this was built from for the full reasoning
(`git log` around when `server/` was added, or ask a Claude Code session
pointed at this repo — `CLAUDE.md` has the summary).

## Install

Needs the `lydia` package (`../src`) in the same environment:

```bash
# from the repo root
python3 -m venv .venv
.venv/bin/pip install -e .           # the lydia CLI package
.venv/bin/pip install -e server/     # this package
```

## Run

```bash
LYDIA_SERVER_TOKEN=<a-long-random-token> .venv/bin/lydia-server
```

Refuses to start with no tokens configured (`LYDIA_SERVER_TOKEN` or
`LYDIA_SERVER_TOKENS` — see below) since an unauthenticated inference
proxy on your network isn't something to start by accident.

## Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `LYDIA_SERVER_HOST` | `127.0.0.1` | Bind address. Set to your Tailscale interface IP to accept tailnet connections. **Never set this to `0.0.0.0`** — that also listens on your raw LAN/any public interface, not just Tailscale. |
| `LYDIA_SERVER_PORT` | `8000` | Bind port |
| `LYDIA_SERVER_OLLAMA_HOST` | `http://localhost:11434` | Where this server's own Ollama is listening |
| `LYDIA_SERVER_TOKEN` | none | A single bearer token, for a one-person setup |
| `LYDIA_SERVER_TOKENS` | none | `token1:alice,token2:bob` for more than one user — both this and `LYDIA_SERVER_TOKEN` can be set at once |
| `LYDIA_SERVER_SSL_KEYFILE` / `LYDIA_SERVER_SSL_CERTFILE` | none | TLS key/cert pair. See "HTTPS" below. |

Token storage today is deliberately just a flat env-var-sourced dict
(`config/settings.py::_load_tokens`) — the one thing that would need to
grow for real multi-user accounts (a database, expiry, revocation) without
anything in `auth/bearer.py` or the API routes changing, since they only
ever call `settings.tokens.get(token)`.

## HTTPS

There's no public domain to get a normal certificate for a home server on
Tailscale. The clean answer is **`tailscale cert`** — it issues a real,
browser/client-trusted Let's Encrypt certificate for your machine's
MagicDNS name (e.g. `gaming-pc.your-tailnet.ts.net`) with no manual cert
wrangling:

```bash
tailscale cert gaming-pc.your-tailnet.ts.net
# writes gaming-pc.your-tailnet.ts.net.crt / .key in the current directory
```

Point `LYDIA_SERVER_SSL_KEYFILE` / `LYDIA_SERVER_SSL_CERTFILE` at those
files. Without them, `lydia-server` runs plain HTTP — fine for local
development, or if something in front of this process already terminates
TLS.

## API

All `/v1/*` routes except `/v1/health` require `Authorization: Bearer
<token>`. Request/response shapes deliberately mirror Ollama's own
`/api/chat` and `/api/embed` bodies (see `models/chat.py`), so the CLI's
existing Ollama-talking code (`lydia.llm.client`) is reused almost as-is
for talking to this server instead (`lydia.llm.remote_client.RemoteClient`).

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/v1/health` | no | Liveness + version |
| GET | `/v1/models` | yes | Models available on this server's Ollama |
| POST | `/v1/chat` | yes | Streaming chat completion (NDJSON) |
| POST | `/v1/embed` | yes | Batch embeddings |

## Architecture

```
lydia_server/
├── main.py                 FastAPI app factory + lydia-server entry point
├── api/v1.py                the routes
├── auth/bearer.py           bearer-token dependency
├── config/settings.py       env-var-sourced settings, token storage
├── services/ollama_provider.py   builds the ModelClient this server proxies to
└── models/chat.py           Pydantic request/response schemas
```

`services/ollama_provider.py` is intentionally thin: `lydia.llm.client.OllamaClient`
already satisfies everything a provider needs (`chat_stream`, `embed`,
`list_models`, `is_alive`) via the `ModelClient` protocol the CLI itself is
built around (`lydia.llm.protocol`). A future non-Ollama provider (OpenAI,
Anthropic, Gemini — opt-in, bring your own key, never the default) is just
another class satisfying that same protocol.

## Development

```bash
pytest   # 22 tests, all against a fake ModelClient double — no real Ollama needed
```
