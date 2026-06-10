"""Process entrypoint. ROLE=proxy (default) or ROLE=refresher.

Same image, two workloads:
* proxy     — stateless FastAPI ingress (HPA-scaled).
* refresher — single-writer-per-account liveness worker.
"""
from __future__ import annotations

import asyncio
import logging
import os

import uvicorn

from ghcproxy.common.config import load_settings
from ghcproxy.context import AppContext

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")


def _settings():
    return load_settings(os.environ.get("GHCPROXY_CONFIG"))


async def _run_refresher() -> None:
    from ghcproxy.credential.refresher import Refresher

    ctx = AppContext(_settings())
    await ctx.start()
    # Ensure the schema exists before the first tick. CREATE TABLE IF NOT
    # EXISTS is idempotent, so it's safe even when the proxy also inits it —
    # this removes the proxy/refresher startup race.
    try:
        await ctx.init_schema()
    except Exception:
        logging.getLogger("ghcproxy.refresher").warning(
            "schema init skipped/failed; will rely on proxy", exc_info=True)
    refresher = Refresher(ctx)
    try:
        await refresher.run()
    finally:
        await ctx.stop()


def _run_proxy() -> None:
    from contextlib import asynccontextmanager

    from ghcproxy.proxy.app import create_app

    settings = _settings()
    ctx = AppContext(settings)
    ctx.admin_token = os.environ.get("GHCPROXY_ADMIN_TOKEN")

    # build app with a lifespan that starts/stops the context
    import ghcproxy.proxy.app as appmod

    @asynccontextmanager
    async def lifespan(app):
        await ctx.start()
        if os.environ.get("GHCPROXY_INIT_SCHEMA") == "1":
            await ctx.init_schema()
        yield
        await ctx.stop()

    # FastAPI created with our context; attach lifespan
    app = create_app(ctx)
    app.router.lifespan_context = lifespan
    uvicorn.run(app, host=settings.server.host, port=settings.server.port,
                log_level=os.environ.get("LOG_LEVEL", "info").lower())


def main() -> None:
    role = os.environ.get("ROLE", "proxy")
    if role == "refresher":
        asyncio.run(_run_refresher())
    else:
        _run_proxy()


if __name__ == "__main__":
    main()
