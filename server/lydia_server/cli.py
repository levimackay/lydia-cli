"""lydia-server-token: add, revoke, and list bearer tokens without editing
env vars or restarting the server — see database/tokens.py for why this
exists instead of the old env-var-only setup.

    lydia-server-token add <user_id> [--expires-in SECONDS]
    lydia-server-token revoke              # reads the token from stdin/prompt, never argv
    lydia-server-token revoke-user <user_id>
    lydia-server-token list

`revoke` deliberately does not take the token as a positional argument —
process arguments are visible to other local users (`ps`, /proc, shell
history) for as long as the process runs, which is exactly the kind of
exposure a bearer token shouldn't have. It reads from stdin if piped, or
prompts without echoing if run interactively.
"""

from __future__ import annotations

import argparse
import secrets
import sys
from datetime import datetime, timezone
from getpass import getpass

from lydia_server.config.settings import get_settings


def _fmt_ts(ts: float | None) -> str:
    if ts is None:
        return "-"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _read_token_securely() -> str:
    """Piped input (`echo "$TOKEN" | lydia-server-token revoke`): read the
    first line from stdin. Interactive terminal: prompt without echoing,
    the same way `ssh-add`/`sudo` do for secrets. Either way, the token
    never appears in argv, so it never shows up in `ps`, /proc/*/cmdline,
    or shell history."""
    if sys.stdin.isatty():
        return getpass("Token to revoke: ").strip()
    return sys.stdin.readline().strip()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lydia-server-token")
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="Generate and store a new token for a user")
    add.add_argument("user_id")
    add.add_argument(
        "--expires-in", type=float, default=None, metavar="SECONDS", help="Default: never expires"
    )

    sub.add_parser(
        "revoke",
        help="Revoke one token — reads it from stdin/prompt, never as a CLI argument",
    )

    revoke_user = sub.add_parser(
        "revoke-user", help="Revoke every active token for a user (doesn't require having the raw token)"
    )
    revoke_user.add_argument("user_id")

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
        token = _read_token_securely()
        if not token:
            print("No token provided.", file=sys.stderr)
            return 1
        if store.revoke(token):
            print("Revoked.")
            return 0
        print("No matching active token found.", file=sys.stderr)
        return 1

    if args.command == "revoke-user":
        count = store.revoke_user(args.user_id)
        if count:
            print(f"Revoked {count} active token(s) for '{args.user_id}'.")
            return 0
        print(f"No active tokens found for '{args.user_id}'.", file=sys.stderr)
        return 1

    if args.command == "list":
        infos = store.list_tokens()
        if not infos:
            print("No tokens stored.")
            return 0
        for info in infos:
            status = "revoked" if info.revoked else "active"
            print(
                f"{info.user_id:<20} {status:<8} {info.source:<4} created {_fmt_ts(info.created_at)}"
                f"  expires {_fmt_ts(info.expires_at)}"
            )
        return 0

    return 1  # argparse's `required=True` makes this unreachable; kept for completeness.


if __name__ == "__main__":
    raise SystemExit(main())
