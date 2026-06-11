"""httpx-backed upstream client for the GHC model API.

Implements the ``send`` contract the Forwarder uses. Two modes:

* ``send`` — buffered: awaits the full response (used for non-stream requests
  and for the rebind-retry decision, since we must inspect status/body).
* ``stream`` — passes the upstream SSE body through chunk-by-chunk while a
  UsageAccumulator scrapes token counts for the usage event.

Auth: the account's ``gho_`` token is sent directly as the bearer (verified
against Copilot CLI 1.0.61).
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import httpx

from ghcproxy.common.config import UpstreamConfig
from ghcproxy.credential.client import build_upstream_headers
from ghcproxy.proxy.forwarder import UpstreamResult

# Request headers from the client we must NOT forward upstream.
_STRIP = {"host", "authorization", "content-length", "connection",
          "accept-encoding", "x-initiator", "copilot-integration-id"}


class HttpxUpstream:
    def __init__(self, cfg: UpstreamConfig, client: httpx.AsyncClient) -> None:
        self._cfg = cfg
        self._client = client

    def _headers(self, account, client_headers: dict, anthropic: bool) -> dict:
        headers = build_upstream_headers(self._cfg, account.oauth_token, anthropic=anthropic)
        # carry through a few innocuous client headers (e.g. accept) but never auth
        for k, v in (client_headers or {}).items():
            if k.lower() not in _STRIP and k.lower() not in {h.lower() for h in headers}:
                headers[k] = v
        return headers

    def _url(self, account, path: str) -> str:
        base = (account.api_base or self._cfg.default_api_base).rstrip("/")
        return f"{base}{path}"

    async def send(self, *, account, path, method, headers, body, anthropic) -> UpstreamResult:
        resp = await self._client.request(
            method, self._url(account, path),
            headers=self._headers(account, headers, anthropic),
            content=body or None,
            timeout=self._cfg_timeout(),
        )
        return UpstreamResult(status=resp.status_code, headers=dict(resp.headers),
                              body=resp.content)

    @asynccontextmanager
    async def stream(self, *, account, path, method, headers, body, anthropic):
        req = self._client.build_request(
            method, self._url(account, path),
            headers=self._headers(account, headers, anthropic),
            content=body or None, timeout=self._cfg_timeout())
        resp = await self._client.send(req, stream=True)
        try:
            yield resp
        finally:
            await resp.aclose()

    def _cfg_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(600.0, connect=15.0)
