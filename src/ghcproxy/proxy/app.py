"""FastAPI proxy application — front-end ingress for Claude Code / Codex / OpenClaw.

Routes:
* ``GET  /healthz``                      — liveness/readiness
* ``GET  /metrics``                      — Prometheus
* ``GET  /v1/models``                    — proxied model list
* ``POST /v1/chat/completions``          — OpenAI dialect (GPT + Claude)
* ``POST /v1/responses``                 — OpenAI Responses (Codex)
* ``POST /v1/messages``                  — Anthropic dialect (Claude Code)
* ``/admin/*``                           — operator API (see admin.py)

Per request: authenticate the proxy key -> resolve user -> 1:1 bind to an
account -> forward upstream with the account's gho_ token -> stream/return the
response -> emit prompt + usage events to Kafka. Login-expiry triggers an
automatic re-route (handled in Forwarder for the buffered path).
"""
from __future__ import annotations

import asyncio
import time

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from ghcproxy.common.keys import hash_api_key
from ghcproxy.credential.client import is_login_expired
from ghcproxy.observability import metrics
from ghcproxy.proxy.auth import InvalidAuth, extract_proxy_key
from ghcproxy.proxy.usage import UsageAccumulator, usage_from_json
from ghcproxy.router.binding import NoAccountAvailable

# (path, is_anthropic) for the buffered/stream forward routes
_OPENAI_PATHS = {"/v1/chat/completions": "/chat/completions",
                 "/v1/responses": "/responses"}


def create_app(ctx) -> FastAPI:
    """``ctx`` is an AppContext holding repo, cache, forwarder, upstream, sink, cfg."""
    app = FastAPI(title="GHC Proxy", version="0.1.0")

    # ---- infra endpoints ------------------------------------------------
    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics_endpoint():
        await _refresh_account_gauges()
        return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    # ---- auth helper ----------------------------------------------------
    async def authenticate(request: Request) -> str:
        key = extract_proxy_key(dict(request.headers))
        sha = hash_api_key(key)
        sha_hex = sha.hex()
        user_id = await ctx.cache.get_user_for_key(sha_hex)
        if user_id is None:
            user_id = await ctx.repo.user_for_key_hash(sha)
            if user_id is None:
                raise InvalidAuth("unknown proxy key")
            await ctx.cache.set_user_for_key(sha_hex, user_id)
        return user_id

    # ---- model list (buffered) -----------------------------------------
    @app.get("/v1/models")
    async def models(request: Request):
        return await _buffered(request, path="/models", method="GET", anthropic=False)

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        return await _maybe_stream(request, "/chat/completions", anthropic=False)

    @app.post("/v1/responses")
    async def responses(request: Request):
        return await _maybe_stream(request, "/responses", anthropic=False)

    @app.post("/v1/messages")
    async def messages(request: Request):
        return await _maybe_stream(request, "/v1/messages", anthropic=True)

    # ---- core handlers --------------------------------------------------
    async def _authn_or_error(request: Request):
        try:
            return await authenticate(request), None
        except InvalidAuth as e:
            return None, JSONResponse({"error": {"message": str(e), "type": "auth"}},
                                      status_code=401)

    async def _buffered(request: Request, *, path: str, method: str, anthropic: bool):
        user_id, err = await _authn_or_error(request)
        if err:
            return err
        body = await request.body()
        protocol = "anthropic" if anthropic else "openai"
        started = time.perf_counter()
        try:
            result = await ctx.forwarder.handle(
                user_id, path=path, method=method,
                headers=dict(request.headers), body=body, anthropic=anthropic)
        except NoAccountAvailable:
            metrics.NO_ACCOUNT.inc()
            return JSONResponse(
                {"error": {"message": "no backend account available", "type": "capacity"}},
                status_code=503, headers={"Retry-After": "30"})
        metrics.UPSTREAM_LATENCY.labels(protocol).observe(time.perf_counter() - started)
        metrics.REQUESTS.labels(protocol, metrics.status_class(result.status)).inc()
        if result.rebound:
            metrics.REBINDS.inc()
        await _emit_usage(user_id, result.account_id,
                          usage_from_json(_safe_json(result.body)), body, result.body)
        return Response(content=result.body, status_code=result.status,
                        media_type=result.headers.get("content-type", "application/json"))

    async def _maybe_stream(request: Request, upstream_path: str, *, anthropic: bool):
        user_id, err = await _authn_or_error(request)
        if err:
            return err
        body = await request.body()
        if _wants_stream(body):
            return await _stream(user_id, request, upstream_path, body, anthropic)
        return await _buffered(request, path=upstream_path, method="POST", anthropic=anthropic)

    async def _stream(user_id, request, upstream_path, body, anthropic):
        protocol = "anthropic" if anthropic else "openai"
        # Resolve the account BEFORE constructing the StreamingResponse: the
        # generator body runs lazily, so an exception raised inside it would
        # escape the response and surface as a 500. Bind here, return 503 on
        # capacity exhaustion.
        try:
            binding = await ctx.binding.get_or_bind(user_id)
        except NoAccountAvailable:
            metrics.NO_ACCOUNT.inc()
            return JSONResponse(
                {"error": {"message": "no backend account available", "type": "capacity"}},
                status_code=503, headers={"Retry-After": "30"})
        account = await ctx.repo.get_account(binding.account_id)
        cm, resp = await _open_stream(account, request, upstream_path, body, anthropic)
        account_id = binding.account_id
        rebound = False

        auth_error_body = await _read_auth_error_body(resp, cm)
        if auth_error_body is not None:
            if is_login_expired(resp.status_code, auth_error_body):
                try:
                    new_binding = await ctx.binding.rebind_away_from(binding.account_id, user_id)
                except NoAccountAvailable:
                    metrics.NO_ACCOUNT.inc()
                    return JSONResponse(
                        {"error": {"message": "no backend account available", "type": "capacity"}},
                        status_code=503, headers={"Retry-After": "30"})
                account = await ctx.repo.get_account(new_binding.account_id)
                cm, resp = await _open_stream(account, request, upstream_path, body, anthropic)
                account_id = new_binding.account_id
                rebound = True
                retry_auth_error_body = await _read_auth_error_body(resp, cm)
                if retry_auth_error_body is not None:
                    if is_login_expired(resp.status_code, retry_auth_error_body):
                        await ctx.repo.quarantine_account(account_id, "retry: login expired")
                        await ctx.repo.release_binding(user_id)
                    metrics.REQUESTS.labels(protocol, metrics.status_class(resp.status_code)).inc()
                    metrics.REBINDS.inc()
                    return Response(
                        content=retry_auth_error_body, status_code=resp.status_code,
                        media_type=resp.headers.get("content-type", "application/json"))
            else:
                metrics.REQUESTS.labels(protocol, metrics.status_class(resp.status_code)).inc()
                return Response(content=auth_error_body, status_code=resp.status_code,
                                media_type=resp.headers.get("content-type", "application/json"))

        metrics.REQUESTS.labels(protocol, metrics.status_class(resp.status_code)).inc()
        if rebound:
            metrics.REBINDS.inc()
        media_type = resp.headers.get("content-type", "text/event-stream")

        async def gen():
            acc = UsageAccumulator()
            try:
                async for chunk in resp.aiter_bytes():
                    for line in chunk.split(b"\n"):
                        acc.observe_sse(line)
                    yield chunk
                await _emit_usage(user_id, account_id, acc.result(), body)
            finally:
                await cm.__aexit__(None, None, None)

        return StreamingResponse(gen(), media_type=media_type,
                                 status_code=resp.status_code)

    async def _open_stream(account, request, upstream_path, body, anthropic):
        cm = ctx.upstream.stream(
            account=account, path=upstream_path, method="POST",
            headers=dict(request.headers), body=body, anthropic=anthropic)
        resp = await cm.__aenter__()
        return cm, resp

    async def _read_auth_error_body(resp, cm) -> bytes | None:
        if resp.status_code not in (401, 403):
            return None
        body = await resp.aread()
        await cm.__aexit__(None, None, None)
        return body

    async def _emit_usage(user_id, account_id, usage, request_body: bytes,
                          response_body: bytes | None = None):
        # Observability must never break or stall the response: bound the whole
        # block by a short timeout and swallow any error (e.g. a wedged broker).
        async def _do():
            await ctx.sink.usage({
                "user_id": user_id, "account_id": account_id, "model": usage.model,
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "ts": time.time()})
            prompt_event = {
                "user_id": user_id, "account_id": account_id, "model": usage.model,
                "request_bytes": len(request_body), "request": _json_or_text(request_body),
                "ts": time.time()}
            if response_body is not None:
                prompt_event["response_bytes"] = len(response_body)
                prompt_event["response"] = _json_or_text(response_body)
            await ctx.sink.prompt(prompt_event)
            if usage.model:
                await ctx.repo.bump_usage(user_id, account_id, usage.model,
                                          usage.prompt_tokens, usage.completion_tokens)
        try:
            await asyncio.wait_for(_do(), timeout=5.0)
        except Exception:
            pass

    async def _refresh_account_gauges():
        if not hasattr(ctx.repo, "list_accounts"):
            return
        try:
            accounts = await ctx.repo.list_accounts()
        except Exception:
            return
        metrics.QUARANTINED_ACCOUNTS.set(
            sum(1 for a in accounts if a.get("status") == "quarantined"))
        metrics.IDLE_ACCOUNTS.set(
            sum(1 for a in accounts if a.get("status") == "idle"))

    # mount admin
    from ghcproxy.admin.api import build_admin_router
    app.include_router(build_admin_router(ctx), prefix="/admin")

    app.state.ctx = ctx
    return app


def _safe_json(body: bytes) -> dict:
    import json
    try:
        obj = json.loads(body)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _json_or_text(body: bytes):
    import json
    try:
        return json.loads(body)
    except Exception:
        return body.decode("utf-8", "replace")


def _wants_stream(body: bytes) -> bool:
    import json
    try:
        return bool(json.loads(body).get("stream"))
    except Exception:
        return False
