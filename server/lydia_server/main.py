"""The Lydia Server entry point.

    lydia-server              run with settings from environment variables

See config/settings.py for the full list of LYDIA_SERVER_* env vars.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from lydia_server import __version__
from lydia_server.api.v1 import router as v1_router
from lydia_server.config.settings import get_settings
from lydia_server.services.ollama_provider import close_shared_providers


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    # Requests reuse one pooled provider per host (services/ollama_provider.py)
    # instead of opening a fresh connection each time — this is where that
    # connection actually gets closed, once, on shutdown.
    close_shared_providers()


def create_app() -> FastAPI:
    app = FastAPI(title="Lydia Server", version=__version__, lifespan=_lifespan)
    app.include_router(v1_router)
    return app


app = create_app()


def run() -> None:
    import uvicorn

    settings = get_settings()
    if not settings.tokens:
        raise SystemExit(
            "No auth tokens configured. Set LYDIA_SERVER_TOKEN (single user) "
            "or LYDIA_SERVER_TOKENS (comma-separated token:user pairs) before starting."
        )
    uvicorn.run(
        "lydia_server.main:app",
        host=settings.host,
        port=settings.port,
        ssl_keyfile=settings.ssl_keyfile,
        ssl_certfile=settings.ssl_certfile,
    )


if __name__ == "__main__":
    run()
