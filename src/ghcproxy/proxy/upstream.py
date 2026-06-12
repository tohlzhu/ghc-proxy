"""httpx-backed upstream client for the GHC model API.

Implements the ``send`` contract the Forwarder uses. Two modes:

* ``send`` — buffered: awaits the full response (used for non-stream requests
  and for the rebind-retry decision, since we must inspect status/body).
* ``stream`` — passes the upstream SSE body through chunk-by-chunk while a
  UsageAccumulator scrapes token counts for the usage event.

Auth: the bearer is resolved per-request via the token service — a short-lived
Copilot token B for editor/``ghu_`` accounts (exchanged at
``copilot_internal/v2/token``), or the durable token directly for CLI/``gho_``
accounts (whose exchange 404s). On a 401/403 the cached bearer is invalidated so
the rebind/retry re-resolves instead of reusing a dead token.

Bearer-resolution failures are mapped to synthetic responses so the rest of the
stack (Forwarder / app streaming path) handles them with its existing logic:
  * ``CopilotAuthExpired`` (dead durable login) -> synthetic **401**: triggers
    quarantine + rebind-retry.
  * ``CopilotTokenUnavailable`` (transient 5xx/429/network) -> synthetic **503**:
    passed through, NOT quarantined.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import httpx

from ghcproxy.common.config import UpstreamConfig
from ghcproxy.credential.client import build_upstream_headers
from ghcproxy.credential.headers import derive_initiator, has_vision_content
from ghcproxy.credential.token_service import (
    CopilotAuthExpired,
    CopilotTokenUnavailable,
)
from ghcproxy.proxy.forwarder import UpstreamResult

# Request headers from the client we must NOT forward upstream.
_STRIP = {"host", "authorization", "content-length", "connection",
          "accept-encoding", "x-initiator", "copilot-integration-id"}

# Synthetic upstream responses for bearer-resolution failures.
_AUTH_EXPIRED = (401, b'{"error":{"message":"login expired","type":"auth"}}')
_TRANSIENT = (503, b'{"error":{"message":"token temporarily unavailable","type":"upstream"}}')


class _BearerError(Exception):
    """Internal: carries the synthetic (status, body) to return to the caller."""

    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body


class HttpxUpstream:
    def __init__(self, cfg: UpstreamConfig, client: httpx.AsyncClient,
                 token_service=None) -> None:
        self._cfg = cfg
        self._client = client
        self._tokens = token_service

    async def _headers(self, account, client_headers: dict, anthropic: bool,
                       body: bytes) -> dict:
        bearer = await self._bearer(account)
        headers = build_upstream_headers(
            self._cfg, bearer, anthropic=anthropic,
            initiator=derive_initiator(body or b""),
            vision=has_vision_content(body or b""))
        # carry through a few innocuous client headers (e.g. accept) but never auth
        for k, v in (client_headers or {}).items():
            if k.lower() not in _STRIP and k.lower() not in {h.lower() for h in headers}:
                headers[k] = v
        return headers

    async def _bearer(self, account) -> str:
        if self._tokens is None:
            # No token service wired (e.g. legacy/unit context): use the durable
            # token directly. Valid for CLI/``gho_`` accounts.
            return account.oauth_token
        try:
            return await self._tokens.bearer_for(account)
        except CopilotAuthExpired as exc:
            raise _BearerError(*_AUTH_EXPIRED) from exc
        except CopilotTokenUnavailable as exc:
            raise _BearerError(*_TRANSIENT) from exc

    def _invalidate(self, account) -> None:
        if self._tokens is not None:
            self._tokens.invalidate(account.id)

    def _url(self, account, path: str) -> str:
        base = (account.api_base or self._cfg.default_api_base).rstrip("/")
        return f"{base}{path}"

    async def send(self, *, account, path, method, headers, body, anthropic) -> UpstreamResult:
        try:
            out_headers = await self._headers(account, headers, anthropic, body)
        except _BearerError as err:
            return UpstreamResult(status=err.status,
                                  headers={"content-type": "application/json"},
                                  body=err.body)
        resp = await self._client.request(
            method, self._url(account, path),
            headers=out_headers, content=body or None,
            timeout=self._cfg_timeout(),
        )
        if resp.status_code in (401, 403):
            self._invalidate(account)
        return UpstreamResult(status=resp.status_code, headers=dict(resp.headers),
                              body=resp.content)

    @asynccontextmanager
    async def stream(self, *, account, path, method, headers, body, anthropic):
        try:
            out_headers = await self._headers(account, headers, anthropic, body)
        except _BearerError as err:
            yield _SyntheticStreamResp(err.status, err.body)
            return
        req = self._client.build_request(
            method, self._url(account, path),
            headers=out_headers, content=body or None, timeout=self._cfg_timeout())
        resp = await self._client.send(req, stream=True)
        if resp.status_code in (401, 403):
            self._invalidate(account)
        try:
            yield resp
        finally:
            await resp.aclose()

    def _cfg_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(600.0, connect=15.0)


class _SyntheticStreamResp:
    """Minimal response object matching the streaming path's expectations.

    Lets a bearer-resolution failure flow through the app's streaming handler
    (which reads ``status_code``, ``headers``, and ``aread`` on 4xx) exactly as a
    real upstream error would — so 401 rebinds and 503 passes through.
    """

    def __init__(self, status_code: int, body: bytes) -> None:
        self.status_code = status_code
        self.headers = {"content-type": "application/json"}
        self._body = body

    async def aiter_bytes(self):
        yield self._body

    async def aread(self) -> bytes:
        return self._body

    async def aclose(self) -> None:
        pass
