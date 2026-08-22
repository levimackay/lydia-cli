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
| `LYDIA_SERVER_TOKEN` | none | A single bearer token, for a one-person setup — seeded into the token store on every startup |
| `LYDIA_SERVER_TOKENS` | none | `token1:alice,token2:bob` for more than one user, also seeded on every startup — both this and `LYDIA_SERVER_TOKEN` can be set at once |
| `LYDIA_SERVER_TOKENS_DB` | `~/.lydia/server/tokens.sqlite3` | Where the token store lives. Tokens are hashed (SHA-256) before being written — this file never contains a raw token. |
| `LYDIA_SERVER_SSL_KEYFILE` / `LYDIA_SERVER_SSL_CERTFILE` | none | TLS key/cert pair. See "HTTPS" below. |

### Managing tokens without an env var or a restart

`LYDIA_SERVER_TOKEN`/`LYDIA_SERVER_TOKENS` are a bootstrap mechanism, not
the only way in — the store behind them is a real SQLite database
(`database/tokens.py::TokenStore`), so tokens can be added, revoked, and
given an expiry at runtime, with the server already running:

```bash
lydia-server-token add alice                    # prints a token once — copy it now
lydia-server-token add bob --expires-in 86400    # expires in 24h
lydia-server-token revoke-user alice             # revoke everything alice has — the practical path,
                                                  # since the raw token above was never seen again after add
echo "$TOKEN" | lydia-server-token revoke        # revoke one specific token, if you have it
lydia-server-token list                          # user, status, source, created/expiry — never the raw token
```

`revoke` deliberately does not take the token as a command-line argument
— process arguments are visible to other local users for as long as the
process runs (`ps`, `/proc/<pid>/cmdline`), which is exactly the kind of
exposure a bearer token shouldn't have. It reads from stdin if piped, or
prompts without echoing if you run it interactively.

These operate on whatever `LYDIA_SERVER_TOKENS_DB` points at, same as the
running server — run it on the same machine (or point `LYDIA_SERVER_TOKENS_DB`
at the same file) to manage a remote server's tokens. A token seeded from
`LYDIA_SERVER_TOKEN`/`LYDIA_SERVER_TOKENS` keeps working even after you
remove it from the env var and restart — that env var is only ever a
bootstrap, never a revocation switch — the server logs a warning when
that happens so it's not silent; use `revoke-user` if you actually meant
to remove access.

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
├── cli.py                   lydia-server-token entry point
├── api/v1.py                the routes
├── auth/bearer.py           bearer-token dependency
├── config/settings.py       env-var-sourced settings, seeds the token store
├── database/tokens.py        SQLite-backed TokenStore (add/get/revoke/expiry)
├── services/ollama_provider.py   builds + pools the ModelClient this server proxies to
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
pytest   # 61 tests — chat/embed/models routes run against a fake ModelClient
         # double, token storage against a real tmp_path SQLite file; no
         # real Ollama needed either way
```
