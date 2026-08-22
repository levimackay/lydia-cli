"""Server-side configuration, sourced entirely from environment variables.

No config file — this runs as a long-lived service, and env vars are the
standard way to configure that (systemd/Task Scheduler unit, docker, or
just a launch script) without a secrets-bearing file to accidentally
commit.

Token storage is a SQLite-backed `TokenStore` (see `database/tokens.py`),
not a plain dict — real multi-user accounts, with expiry and revocation,
that survive without a restart. `LYDIA_SERVER_TOKEN`/`LYDIA_SERVER_TOKENS`
below are still the bootstrap mechanism (a fresh single-user setup still
needs zero steps beyond setting one env var, same as before this
changed); every token they name gets (re-)seeded into the store on every
startup. To add or revoke a token at runtime without an env var or a
restart, use the `lydia-server-token` CLI (`cli.py`), which reads and
writes the same database file. Nothing in `auth/bearer.py` or the API
routes changed for this — they only ever call `settings.tokens.get(...)`,
which `TokenStore` also implements.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from lydia_server.database.tokens import TokenStore

DEFAULT_TOKENS_DB_PATH = Path.home() / ".lydia" / "server" / "tokens.sqlite3"


def _load_tokens() -> TokenStore:
    """Opens (creating if needed) the token store at LYDIA_SERVER_TOKENS_DB
    (default: ~/.lydia/server/tokens.sqlite3), then seeds it from env vars:

    `LYDIA_SERVER_TOKEN=<token>` for a quick single-user setup, or
    `LYDIA_SERVER_TOKENS="token1:alice,token2:bob"` for more than one.
    Both may be set at once. Tokens added at runtime via `lydia-server-token`
    and never named in an env var are left alone — this only ever adds/
    refreshes the env-var-named ones, never removes anything.
    """
    db_path = Path(os.environ.get("LYDIA_SERVER_TOKENS_DB", str(DEFAULT_TOKENS_DB_PATH)))
    store = TokenStore(db_path)

    single = os.environ.get("LYDIA_SERVER_TOKEN")
    if single:
        store.add(single, "default")
    multi = os.environ.get("LYDIA_SERVER_TOKENS", "")
    for pair in filter(None, (p.strip() for p in multi.split(","))):
        token, _, user_id = pair.partition(":")
        if token:
            store.add(token, user_id or token)

    return store


@dataclass
class ServerSettings:
    # Bind address: default localhost-only. Set to a Tailscale interface
    # IP explicitly to accept connections over the tailnet — never "0.0.0.0"
    # (that would also listen on the raw LAN / any public interface).
    host: str = field(default_factory=lambda: os.environ.get("LYDIA_SERVER_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.environ.get("LYDIA_SERVER_PORT", "8000")))
    ollama_host: str = field(
        default_factory=lambda: os.environ.get("LYDIA_SERVER_OLLAMA_HOST", "http://localhost:11434")
    )
    # A real ServerSettings holds a TokenStore; tests often pass a plain
    # dict directly instead (a lighter-weight fake — `.get()`/`.__len__()`
    # both mean the same thing on either), so this stays typed as the
    # narrower shape both satisfy rather than TokenStore specifically.
    tokens: TokenStore | dict[str, str] = field(default_factory=_load_tokens)
    # Optional TLS — point these at a `tailscale cert`-issued key/cert pair
    # to serve HTTPS directly; unset runs plain HTTP (fine for local dev,
    # or if TLS is terminated by something in front of this process).
    ssl_keyfile: str | None = field(default_factory=lambda: os.environ.get("LYDIA_SERVER_SSL_KEYFILE"))
    ssl_certfile: str | None = field(default_factory=lambda: os.environ.get("LYDIA_SERVER_SSL_CERTFILE"))


@lru_cache
def get_settings() -> ServerSettings:
    return ServerSettings()
