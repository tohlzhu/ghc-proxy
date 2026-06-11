"""Operator/admin API.

Endpoints for managing the account pool and issuing front-end keys. In
production this router must sit behind operator authentication (e.g. mTLS,
SSO, or an admin token) — wired via the ``admin_token`` config and the
``require_admin`` dependency below. Kept minimal and explicit.
"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from ghcproxy.common.keys import generate_api_key, hash_api_key


class ImportAccountReq(BaseModel):
    login: str
    oauth_token: str            # gho_ token (operator supplies)
    plan: str = "enterprise"
    api_base: str = "https://api.enterprise.githubcopilot.com"


class CreateUserReq(BaseModel):
    external_id: str
    display_name: str | None = None


class StartLoginReq(BaseModel):
    login: str


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

    @router.post("/accounts/{login}/login/start")
    async def start_login(login: str,
                          x_admin_token: str | None = Header(default=None)):
        """Begin device flow; returns the user_code for a human to authorize."""
        require_admin(x_admin_token)
        if ctx.device_flow is None:
            raise HTTPException(status_code=501, detail="device flow not configured")
        dc = await ctx.device_flow.request_device_code()
        ctx.pending_logins[login] = dc.device_code
        return {"login": login, "user_code": dc.user_code,
                "verification_uri": dc.verification_uri,
                "interval": dc.interval, "expires_in": dc.expires_in}

    return router
