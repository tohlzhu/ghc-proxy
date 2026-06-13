"""Operator/admin API.

Endpoints for managing the account pool and issuing front-end keys. In
production this router must sit behind operator authentication (e.g. mTLS,
SSO, or an admin token) — wired via the ``admin_token`` config and the
``require_admin`` dependency below. Kept minimal and explicit.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ghcproxy.common.keys import generate_api_key, hash_api_key
from ghcproxy.credential.device_flow import (
    AuthorizationPending,
    DeviceFlowError,
    SlowDown,
)

# Account statuses an operator may set directly from the console. Lifecycle
# states (logging_in/bound) are owned by the system, not the operator.
_OPERATOR_ACCOUNT_STATUSES = {"idle", "disabled", "quarantined"}
_USER_STATUSES = {"active", "disabled"}
_DEFAULT_WINDOW_DAYS = 30


class ImportAccountReq(BaseModel):
    login: str
    oauth_token: str            # gho_ token (operator supplies)
    plan: str = "enterprise"
    api_base: str = "https://api.enterprise.githubcopilot.com"


class CreateUserReq(BaseModel):
    external_id: str
    display_name: str | None = None


class CreateKeyReq(BaseModel):
    name: str | None = None
    scopes: list[str] | None = None
    rate_limit: int | None = None


class UserStatusReq(BaseModel):
    status: str


class AccountStatusReq(BaseModel):
    status: str


class StartLoginReq(BaseModel):
    login: str


def _parse_window(date_from: str | None, date_to: str | None) -> tuple[dt.date, dt.date]:
    """Resolve the (from, to) date window, defaulting to the last 30 days."""
    today = dt.datetime.now(dt.timezone.utc).date()
    try:
        to = dt.date.fromisoformat(date_to) if date_to else today
        frm = (dt.date.fromisoformat(date_from) if date_from
               else to - dt.timedelta(days=_DEFAULT_WINDOW_DAYS - 1))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid date") from exc
    return frm, to


def build_admin_router(ctx) -> APIRouter:
    router = APIRouter()

    def require_admin(x_admin_token: str | None = Header(default=None)):
        expected = getattr(ctx, "admin_token", None)
        # Deny by default: if no admin token is configured the admin API is
        # closed, not open. An operator must explicitly set GHCPROXY_ADMIN_TOKEN
        # (and present it) to use these endpoints.
        if not expected or x_admin_token != expected:
            raise HTTPException(status_code=403, detail="admin token required")

    @router.get("/accounts")
    async def list_accounts(x_admin_token: str | None = Header(default=None)):
        require_admin(x_admin_token)
        return await ctx.repo.list_accounts()

    @router.post("/accounts")
    async def import_account(req: ImportAccountReq,
                             x_admin_token: str | None = Header(default=None)):
        require_admin(x_admin_token)
        account_id = await ctx.repo.add_account(
            req.login, req.oauth_token, plan=req.plan, api_base=req.api_base,
            status="idle")
        return {"id": account_id, "status": "idle"}

    @router.post("/users")
    async def create_user(req: CreateUserReq,
                          x_admin_token: str | None = Header(default=None)):
        require_admin(x_admin_token)
        user_id = await ctx.repo.create_user(req.external_id, req.display_name)
        key = generate_api_key()
        key_id = await ctx.repo.add_api_key(user_id, hash_api_key(key), name="default")
        # plaintext key returned exactly once
        return {"user_id": user_id, "api_key_id": key_id, "api_key": key}

    # ---- usage analytics (read-only over usage_rollup) ------------------
    @router.get("/usage/timeseries")
    async def usage_timeseries(
            x_admin_token: str | None = Header(default=None),
            date_from: str | None = Query(default=None, alias="from"),
            date_to: str | None = Query(default=None, alias="to")):
        require_admin(x_admin_token)
        frm, to = _parse_window(date_from, date_to)
        return await ctx.repo.usage_timeseries(frm, to)

    @router.get("/usage/by-user")
    async def usage_by_user(
            x_admin_token: str | None = Header(default=None),
            date_from: str | None = Query(default=None, alias="from"),
            date_to: str | None = Query(default=None, alias="to")):
        require_admin(x_admin_token)
        frm, to = _parse_window(date_from, date_to)
        return await ctx.repo.usage_by_user(frm, to)

    @router.get("/usage/by-account")
    async def usage_by_account(
            x_admin_token: str | None = Header(default=None),
            date_from: str | None = Query(default=None, alias="from"),
            date_to: str | None = Query(default=None, alias="to")):
        require_admin(x_admin_token)
        frm, to = _parse_window(date_from, date_to)
        return await ctx.repo.usage_by_account(frm, to)

    @router.get("/usage/by-model")
    async def usage_by_model(
            x_admin_token: str | None = Header(default=None),
            date_from: str | None = Query(default=None, alias="from"),
            date_to: str | None = Query(default=None, alias="to")):
        require_admin(x_admin_token)
        frm, to = _parse_window(date_from, date_to)
        return await ctx.repo.usage_by_model(frm, to)

    # ---- users & keys ---------------------------------------------------
    @router.get("/users")
    async def list_users(x_admin_token: str | None = Header(default=None)):
        require_admin(x_admin_token)
        # repo returns key METADATA only (no plaintext, no hash)
        return await ctx.repo.list_users_with_keys()

    @router.patch("/users/{user_id}")
    async def set_user_status(user_id: str, req: UserStatusReq,
                              x_admin_token: str | None = Header(default=None)):
        require_admin(x_admin_token)
        if req.status not in _USER_STATUSES:
            raise HTTPException(status_code=400, detail="invalid user status")
        ok = await ctx.repo.set_user_status(user_id, req.status)
        if not ok:
            raise HTTPException(status_code=404, detail="user not found")
        return {"user_id": user_id, "status": req.status}

    @router.post("/users/{user_id}/keys")
    async def issue_key(user_id: str, req: CreateKeyReq,
                        x_admin_token: str | None = Header(default=None)):
        require_admin(x_admin_token)
        key = generate_api_key()
        key_id = await ctx.repo.add_api_key(
            user_id, hash_api_key(key), name=req.name,
            scopes=req.scopes, rate_limit=req.rate_limit)
        # plaintext key returned exactly once
        return {"user_id": user_id, "api_key_id": key_id, "name": req.name,
                "api_key": key}

    @router.post("/keys/{key_id}/rotate")
    async def rotate_key(key_id: str,
                         x_admin_token: str | None = Header(default=None)):
        require_admin(x_admin_token)
        meta = await ctx.repo.get_api_key_meta(key_id)
        if meta is None:
            raise HTTPException(status_code=404, detail="key not found")
        await ctx.repo.revoke_api_key(key_id)
        key = generate_api_key()
        new_id = await ctx.repo.add_api_key(
            meta["user_id"], hash_api_key(key), name=meta["name"],
            scopes=meta["scopes"], rate_limit=meta["rate_limit"])
        # plaintext key returned exactly once
        return {"user_id": meta["user_id"], "api_key_id": new_id,
                "name": meta["name"], "api_key": key}

    @router.post("/keys/{key_id}/revoke")
    async def revoke_key(key_id: str,
                         x_admin_token: str | None = Header(default=None)):
        require_admin(x_admin_token)
        ok = await ctx.repo.revoke_api_key(key_id)
        if not ok:
            raise HTTPException(status_code=404, detail="key not found")
        return {"api_key_id": key_id, "status": "revoked"}

    # ---- account status & bindings -------------------------------------
    @router.patch("/accounts/{account_id}/status")
    async def set_account_status(account_id: str, req: AccountStatusReq,
                                 x_admin_token: str | None = Header(default=None)):
        require_admin(x_admin_token)
        if req.status not in _OPERATOR_ACCOUNT_STATUSES:
            raise HTTPException(status_code=400, detail="invalid account status")
        ok = await ctx.repo.set_account_status(account_id, req.status)
        if not ok:
            raise HTTPException(status_code=404, detail="account not found")
        return {"account_id": account_id, "status": req.status}

    @router.get("/bindings")
    async def list_bindings(x_admin_token: str | None = Header(default=None)):
        require_admin(x_admin_token)
        return await ctx.repo.list_bindings()

    @router.post("/bindings/{user_id}/release")
    async def release_binding(user_id: str,
                              x_admin_token: str | None = Header(default=None)):
        require_admin(x_admin_token)
        await ctx.repo.release_binding(user_id)
        await ctx.sink.audit({"event": "binding_released", "user": user_id})
        return {"user_id": user_id, "status": "released"}

    @router.post("/accounts/{login}/login/start")
    async def start_login(login: str,
                          x_admin_token: str | None = Header(default=None)):
        """Begin device flow; returns the user_code for a human to authorize."""
        require_admin(x_admin_token)
        if ctx.device_flow is None:
            raise HTTPException(status_code=501, detail="device flow not configured")
        try:
            dc = await ctx.device_flow.request_device_code()
        except DeviceFlowError as exc:
            # Upstream (GitHub) rejected the request — e.g. a misconfigured
            # client_id yields a 404. Surface a clean 502 with a readable
            # detail rather than letting it bubble up as an opaque 500.
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=dc.expires_in)
        account_id, session_id = await ctx.repo.create_device_session(
            login, dc.device_code, dc.user_code, dc.verification_uri,
            dc.interval, expires_at)
        ctx.pending_logins[login] = dc.device_code
        return {"login": login, "account_id": account_id, "session_id": session_id,
                "user_code": dc.user_code,
                "verification_uri": dc.verification_uri,
                "interval": dc.interval, "expires_in": dc.expires_in}

    @router.post("/accounts/{login}/login/poll")
    async def poll_login(login: str,
                         x_admin_token: str | None = Header(default=None)):
        """Poll one device-flow step and store the token once authorized."""
        require_admin(x_admin_token)
        if ctx.device_flow is None:
            raise HTTPException(status_code=501, detail="device flow not configured")
        session = await ctx.repo.get_pending_device_session(login)
        if session is None:
            raise HTTPException(status_code=404, detail="no pending device session")
        try:
            token = await ctx.device_flow.poll_once(session["device_code"])
        except SlowDown:
            return JSONResponse(
                {"login": login, "status": "pending", "reason": "slow_down",
                 "interval": session["interval_s"] + 5},
                status_code=202)
        except AuthorizationPending:
            return JSONResponse(
                {"login": login, "status": "pending",
                 "interval": session["interval_s"]},
                status_code=202)
        except DeviceFlowError as exc:
            await ctx.repo.mark_device_session(session["id"], "denied")
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        account_id = await ctx.repo.complete_device_session(session["id"], token)
        ctx.pending_logins.pop(login, None)
        await ctx.sink.audit({"event": "login_authorized", "account": account_id,
                              "login": login})
        return {"login": login, "account_id": account_id, "status": "idle"}

    return router
