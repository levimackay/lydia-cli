"""Storage for credentials that must not live in plaintext JSON.

Everything in config/settings.py (`LydiaConfig`) is deliberately plain JSON
so it's easy to inspect and edit by hand — that's fine for things like
`api_key` (a bearer token for a Lydia Server the user runs themselves) but
not for OAuth refresh tokens or personal-account API keys (Gmail, Outlook,
Canvas). Those go through the OS keychain instead via the `keyring` package.

Namespaced under the single service name "lydia" so all of Lydia's secrets
show up together in Keychain Access, keyed by the constants below.
"""

from __future__ import annotations

import keyring
from keyring.errors import KeyringError

_SERVICE = "lydia"

GMAIL_REFRESH_TOKEN = "gmail_refresh_token"
OUTLOOK_TOKEN_CACHE = "outlook_token_cache"
# Not secret by themselves, but co-located here so everything the Outlook
# login flow needs to silently refresh a token later lives in one place.
OUTLOOK_CLIENT_ID = "outlook_client_id"
OUTLOOK_AUTHORITY = "outlook_authority"
CANVAS_TOKEN = "canvas_token"
NTFY_TOPIC = "ntfy_topic"
GEMINI_API_KEY = "gemini_api_key"


class SecretsError(Exception):
    """The OS keychain backend is unavailable or the operation failed."""


def get_secret(key: str) -> str | None:
    try:
        return keyring.get_password(_SERVICE, key)
    except KeyringError as exc:
        raise SecretsError(f"Could not read '{key}' from the system keychain: {exc}") from exc


def set_secret(key: str, value: str) -> None:
    try:
        keyring.set_password(_SERVICE, key, value)
    except KeyringError as exc:
        raise SecretsError(f"Could not save '{key}' to the system keychain: {exc}") from exc


def delete_secret(key: str) -> None:
    try:
        keyring.delete_password(_SERVICE, key)
    except keyring.errors.PasswordDeleteError:
        pass  # already absent — logout should be idempotent
    except KeyringError as exc:
        raise SecretsError(f"Could not remove '{key}' from the system keychain: {exc}") from exc
