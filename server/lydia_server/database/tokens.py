"""SQLite-backed bearer token storage.

Replaces the env-var-only `{token: user_id}` dict `config/settings.py`
used to build directly. That dict was fine for one person but couldn't
add, expire, or revoke a token without editing an env var and restarting
the process — this can, from any process that opens the same database
file, while the server keeps running.

Tokens are stored as SHA-256 hashes, never in plaintext. The env-var
approach this replaces never touched disk at all, so hashing here is what
keeps this file from becoming a plaintext credential dump the moment it's
persisted somewhere.
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path


def _hash_token(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


@dataclass
class TokenInfo:
    user_id: str
    created_at: float
    expires_at: float | None
    revoked: bool
    source: str


class TokenStore:
    """Dict-like lookup (`.get(token) -> user_id | None`) so
    `auth/bearer.py`'s `settings.tokens.get(token)` call site never had to
    change to use this instead of a plain dict. `__len__` is also
    implemented so `main.py`'s `if not settings.tokens:` startup check
    (originally written for a dict) keeps working unmodified too.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(db_path.parent, 0o700)

        # sqlite3.connect() creates the file (and any -journal/-wal/-shm
        # side files it needs during writes) using the process umask,
        # which on most systems means world/group-readable (0o644) —
        # wrong for a file whose whole purpose is holding credential
        # material, even hashed. A temporary restrictive umask covers
        # anything SQLite creates during __init__; the explicit chmod
        # below additionally tightens a pre-existing file that was
        # created before this fix (umask alone wouldn't touch it).
        previous_umask = os.umask(0o077)
        try:
            db_path.touch(exist_ok=True)
            os.chmod(db_path, 0o600)
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tokens (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL,
                    revoked_at REAL,
                    source TEXT NOT NULL DEFAULT 'cli'
                )
                """
            )
            # `source` didn't exist before this fix — ALTER TABLE ... ADD
            # COLUMN on a table that already has it would error, so only
            # run it against a database created by the earlier version.
            existing_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(tokens)")}
            if "source" not in existing_columns:
                self._conn.execute("ALTER TABLE tokens ADD COLUMN source TEXT NOT NULL DEFAULT 'cli'")
            self._conn.commit()
        finally:
            os.umask(previous_umask)

    def add(
        self,
        token: str,
        user_id: str,
        expires_in_seconds: float | None = None,
        source: str = "cli",
    ) -> None:
        """Insert or replace a token. expires_in_seconds=None means it never expires.
        Re-adding an existing token clears any prior revocation — matches
        _load_tokens() re-seeding the same env-var token on every startup.

        `source` distinguishes env-var-seeded tokens ('env') from ones
        added via the CLI ('cli', the default) — see
        stale_env_sourced_users() for why this matters."""
        now = time.time()
        expires_at = now + expires_in_seconds if expires_in_seconds is not None else None
        self._conn.execute(
            "INSERT OR REPLACE INTO tokens (token_hash, user_id, created_at, expires_at, revoked_at, source) "
            "VALUES (?, ?, ?, ?, NULL, ?)",
            (_hash_token(token), user_id, now, expires_at, source),
        )
        self._conn.commit()

    def stale_env_sourced_users(self, current_env_tokens: set[str]) -> list[str]:
        """Which active, env-sourced tokens are no longer named in the
        current process's env vars.

        Before this store existed, `_load_tokens()` rebuilt a plain dict
        from env vars on every startup — nothing else survived, so
        removing a token from LYDIA_SERVER_TOKEN(S) and restarting *was*
        how you revoked it. Now that tokens persist in this database,
        that's no longer true: an env-sourced token silently keeps working
        after being dropped from the env var, unless something says so.
        Called from _load_tokens() right after seeding, with the set of
        raw tokens named in *this* run's env vars, so it can warn instead
        of silently doing nothing."""
        current_hashes = {_hash_token(t) for t in current_env_tokens}
        rows = self._conn.execute(
            "SELECT user_id, token_hash FROM tokens WHERE source = 'env' AND revoked_at IS NULL"
        ).fetchall()
        return [user_id for user_id, token_hash in rows if token_hash not in current_hashes]

    def get(self, token: str) -> str | None:
        """Returns the user_id for a valid (not revoked, not expired) token, or None."""
        row = self._conn.execute(
            "SELECT user_id, expires_at, revoked_at FROM tokens WHERE token_hash = ?",
            (_hash_token(token),),
        ).fetchone()
        if row is None:
            return None
        user_id, expires_at, revoked_at = row
        if revoked_at is not None:
            return None
        if expires_at is not None and expires_at < time.time():
            return None
        return user_id

    def revoke(self, token: str) -> bool:
        """Marks a token revoked (soft delete — keeps an audit trail of when
        it was issued and revoked). Returns whether an active token matched."""
        cursor = self._conn.execute(
            "UPDATE tokens SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
            (time.time(), _hash_token(token)),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def revoke_user(self, user_id: str) -> int:
        """Revokes every active token for a user_id. This is the practical
        admin operation — whoever runs `add` never sees the raw token
        again after it's printed once, so an admin revoking someone
        else's access almost never has the raw token to pass to revoke()
        and needs this instead. Returns how many tokens were revoked."""
        cursor = self._conn.execute(
            "UPDATE tokens SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
            (time.time(), user_id),
        )
        self._conn.commit()
        return cursor.rowcount

    def list_tokens(self) -> list[TokenInfo]:
        """For inspection/CLI listing — never returns the raw token or its hash."""
        rows = self._conn.execute(
            "SELECT user_id, created_at, expires_at, revoked_at, source FROM tokens ORDER BY created_at"
        ).fetchall()
        return [
            TokenInfo(user_id=r[0], created_at=r[1], expires_at=r[2], revoked=r[3] is not None, source=r[4])
            for r in rows
        ]

    def __len__(self) -> int:
        (count,) = self._conn.execute("SELECT COUNT(*) FROM tokens").fetchone()
        return count

    def close(self) -> None:
        self._conn.close()
