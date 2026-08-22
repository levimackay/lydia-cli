"""Friendly errors for features gated behind an optional pip extra.

pyproject.toml splits heavy, rarely-needed dependencies (the Gmail/
Outlook/stocks/news connectors need google-api-python-client/msal/
yfinance/feedparser; voice mode needs sounddevice/openwakeword/
faster-whisper — collectively several hundred MB) out of the base
install into `[assistant]` and `[voice]` extras. Every import of one of
those packages is already function-scoped (never at module load time),
so a base `pip install lydia-cli` runs everything else fine — only the
specific gated feature fails, and it should fail with "install this
extra" rather than a raw ModuleNotFoundError.
"""

from __future__ import annotations

from contextlib import contextmanager

import typer
from rich.markup import escape

from lydia.cli import ui


@contextmanager
def require_extra(extra: str, feature: str):
    """Wrap a lazy import + use of an optional-extra feature.

        with require_extra("assistant", "Gmail login"):
            from lydia.connectors.auth import gmail_oauth
            gmail_oauth.login()

    Catches ModuleNotFoundError specifically (not ImportError broadly —
    a real ImportError inside the feature's own code, e.g. a version
    mismatch, should surface as-is rather than being misreported as
    "extra not installed")."""
    try:
        yield
    except ModuleNotFoundError as exc:
        # ui.print_error -> Rich console.print interprets [...] as markup
        # by default, which would silently eat the "[assistant]" in the
        # install command below (Rich treats an unrecognized tag as
        # markup to strip, not literal text) — escape it explicitly.
        install_hint = escape(f'pip install "lydia-cli[{extra}]"')
        ui.print_error(
            f"{feature} needs the '{extra}' extra, which isn't installed "
            f"(missing: {exc.name}).\nInstall it with: {install_hint}"
        )
        raise typer.Exit(1) from exc
