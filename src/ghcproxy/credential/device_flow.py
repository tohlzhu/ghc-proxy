"""GitHub OAuth Device Flow — operator login that yields a durable ``gho_`` token.

Flow (RFC 8628 as GitHub implements it):

1. ``POST /login/device/code`` with ``client_id`` + ``scope`` -> ``device_code``,
   ``user_code`` (``XXXX-XXXX``), ``verification_uri``, ``interval``.
2. A human opens ``verification_uri`` and enters ``user_code`` in a browser.
3. ``POST /login/oauth/access_token`` is polled every ``interval`` seconds;
   it returns ``error=authorization_pending`` until the human finishes, then
   ``access_token`` (the ``gho_`` token).

This module is transport-agnostic: it takes an ``http`` object exposing
``post_form(url, data, headers) -> (status, json)`` so it can be unit-tested
with a fake and run with httpx in production.
"""
from __future__ import annotations

from dataclasses import dataclass

from ghcproxy.common.config import DeviceFlowConfig


class AuthorizationPending(Exception):
    """The user has not yet completed browser authorization."""


class SlowDown(AuthorizationPending):
    """GitHub asked us to increase the poll interval."""


class DeviceFlowError(Exception):
    """Terminal device-flow failure (access_denied, expired_token, ...)."""


@dataclass
class DeviceCode:
    device_code: str
    user_code: str
    verification_uri: str
    interval: int
    expires_in: int


class DeviceFlow:
    def __init__(self, cfg: DeviceFlowConfig, http) -> None:
        self._cfg = cfg
        self._http = http

    async def request_device_code(self) -> DeviceCode:
        # Guard against an unconfigured client_id. A placeholder like
        # "Iv1.<CLIENT_ID>" makes GitHub answer POST /login/device/code with an
        # opaque 404; fail fast with an actionable message instead so the
        # operator knows to set GHCPROXY_DEVICE_FLOW__CLIENT_ID.
        client_id = self._cfg.client_id
        if not client_id or "<" in client_id or ">" in client_id:
            raise DeviceFlowError(
                f"device flow client_id is not configured ({client_id!r}); "
                "set GHCPROXY_DEVICE_FLOW__CLIENT_ID to a valid public Copilot "
                "OAuth client id")
        status, body = await self._http.post_form(
            self._cfg.device_code_url,
            {"client_id": self._cfg.client_id, "scope": self._cfg.scope},
            {"Accept": "application/json"},
        )
        if status != 200 or "device_code" not in body:
            raise DeviceFlowError(f"device code request failed: {status} {body}")
        return DeviceCode(
            device_code=body["device_code"],
            user_code=body["user_code"],
            verification_uri=body["verification_uri"],
            interval=int(body.get("interval", 5)),
            expires_in=int(body.get("expires_in", 900)),
        )

    async def poll_once(self, device_code: str) -> str:
        """One poll. Returns the ``gho_`` token, or raises.

        AuthorizationPending/SlowDown are expected and mean "keep polling".
        """
        status, body = await self._http.post_form(
            self._cfg.access_token_url,
            {
                "client_id": self._cfg.client_id,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            {"Accept": "application/json"},
        )
        if body.get("access_token"):
            return body["access_token"]
        err = body.get("error")
        if err == "authorization_pending":
            raise AuthorizationPending()
        if err == "slow_down":
            raise SlowDown()
        raise DeviceFlowError(err or f"unexpected token response: {status} {body}")
