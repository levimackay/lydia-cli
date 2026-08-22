"""lydia-server-token: add, revoke, and list bearer tokens without editing
env vars or restarting the server — see database/tokens.py for why this
exists instead of the old env-var-only setup.

    lydia-server-token add <user_id> [--expires-in SECONDS]
    lydia-server-token revoke <token>
    lydia-server-token list
"""

from __future__ import annotations

import argparse
import secrets
import sys
from datetime import datetime, timezone

from lydia_server.config.settings import get_settings


def _fmt_ts(ts: float | None) -> str:
    if ts is None:
        return "-"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lydia-server-token")
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="Generate and store a new token for a user")
    add.add_argument("user_id")
    add.add_argument(
        "--expires-in", type=float, default=None, metavar="SECONDS", help="Default: never expires"
    )

    revoke = sub.add_parser("revoke", help="Revoke a token")
    revoke.add_argument("token")

    sub.add_parser("list", help="List all tokens (never prints raw tokens — those are hashed at rest)")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    store = get_settings().tokens

    if args.command == "add":
        token = secrets.token_urlsafe(32)
        store.add(token, args.user_id, expires_in_seconds=args.expires_in)
        print(f"Token for '{args.user_id}':\n{token}\n")
        print("This is shown once — copy it now. Only its hash is stored, so it can't be recovered later.")
        return 0

    if args.command == "revoke":
        if store.revoke(args.token):
            print("Revoked.")
            return 0
        print("No matching active token found.", file=sys.stderr)
        return 1

    if args.command == "list":
        infos = store.list_tokens()
        if not infos:
            print("No tokens stored.")
            return 0
        for info in infos:
            status = "revoked" if info.revoked else "active"
            print(
                f"{info.user_id:<20} {status:<8} created {_fmt_ts(info.created_at)}"
                f"  expires {_fmt_ts(info.expires_at)}"
            )
        return 0

    return 1  # argparse's `required=True` makes this unreachable; kept for completeness.


if __name__ == "__main__":
    raise SystemExit(main())
