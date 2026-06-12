"""Async PostgreSQL repository.

Implements the storage contracts used by the binding service, forwarder and
admin API. The idle-account claim is the one piece that must be atomic under
concurrency: it uses ``SELECT ... FOR UPDATE SKIP LOCKED`` plus the double
UNIQUE on ``bindings`` so two proxy replicas can never hand the same account
to two users.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import asyncpg

from ghcproxy.common.crypto import TokenCipher


@dataclass
class Account:
    id: str
    login: str
    oauth_token: str          # decrypted in-memory only
    api_base: str
    plan: str | None
    status: str


class PgRepo:
    def __init__(self, pool: asyncpg.Pool, cipher: TokenCipher) -> None:
        self._pool = pool
        self._cipher = cipher

    @classmethod
    async def connect(cls, dsn: str, cipher: TokenCipher,
                      min_size: int = 1, max_size: int = 10) -> "PgRepo":
        pool = await asyncpg.create_pool(dsn, min_size=min_size, max_size=max_size)
        return cls(pool, cipher)

    async def close(self) -> None:
        await self._pool.close()

    async def init_schema(self, schema_sql: str) -> None:
        async with self._pool.acquire() as con:
            await con.execute(schema_sql)

    # -- accounts ---------------------------------------------------------
    async def get_account(self, account_id: str) -> Account | None:
        row = await self._pool.fetchrow(
            "SELECT id, login, oauth_token_enc, api_base, plan, status "
            "FROM accounts WHERE id = $1", account_id)
        if not row:
            return None
        return self._row_to_account(row)

    def _row_to_account(self, row) -> Account:
        token = self._cipher.decrypt(bytes(row["oauth_token_enc"])) if row["oauth_token_enc"] else ""
        return Account(
            id=str(row["id"]), login=row["login"], oauth_token=token,
            api_base=row["api_base"], plan=row["plan"], status=row["status"])

    async def add_account(self, login: str, oauth_token: str, *,
                          plan: str = "enterprise",
                          api_base: str = "https://api.enterprise.githubcopilot.com",
                          status: str = "idle") -> str:
        enc = self._cipher.encrypt(oauth_token)
        # On re-import we refresh the credential but PRESERVE the existing
        # status: re-importing a token for a bound account is a credential
        # refresh, not a request to free it. A quarantined account is revived
        # to idle (the operator just re-authorized it).
        row = await self._pool.fetchrow(
            "INSERT INTO accounts (login, oauth_token_enc, plan, api_base, status) "
            "VALUES ($1,$2,$3,$4,$5) "
            "ON CONFLICT (host, login) DO UPDATE SET "
            "  oauth_token_enc = EXCLUDED.oauth_token_enc, plan = EXCLUDED.plan, "
            "  api_base = EXCLUDED.api_base, last_error = NULL, updated_at = now(), "
            "  status = CASE WHEN accounts.status = 'quarantined' THEN 'idle' "
            "                ELSE accounts.status END "
            "RETURNING id", login, enc, plan, api_base, status)
        return str(row["id"])

    async def quarantine_account(self, account_id: str, error: str | None = None) -> None:
        await self._pool.execute(
            "UPDATE accounts SET status='quarantined', last_error=$2, updated_at=now() "
            "WHERE id=$1", account_id, error)

    async def set_account_status(self, account_id: str, status: str) -> bool:
        row = await self._pool.fetchrow(
            "UPDATE accounts SET status=$2, updated_at=now() "
            "WHERE id=$1 RETURNING id",
            account_id, status)
        return row is not None

    async def mark_seen(self, account_id: str, refresh_at: dt.datetime) -> None:
        await self._pool.execute(
            "UPDATE accounts SET last_seen_at=now(), refresh_at=$2, last_error=NULL, "
            "updated_at=now() WHERE id=$1", account_id, refresh_at)

    async def due_for_revalidation(self, now: dt.datetime, limit: int = 50) -> list[Account]:
        rows = await self._pool.fetch(
            "SELECT id, login, oauth_token_enc, api_base, plan, status FROM accounts "
            "WHERE status IN ('idle','bound') AND (refresh_at IS NULL OR refresh_at <= $1) "
            "ORDER BY refresh_at NULLS FIRST LIMIT $2", now, limit)
        return [self._row_to_account(r) for r in rows]

    # -- device flow -------------------------------------------------------
    async def create_device_session(
        self, login: str, device_code: str, user_code: str, verification_uri: str,
        interval_s: int, expires_at: dt.datetime, *,
        plan: str = "enterprise",
        api_base: str = "https://api.enterprise.githubcopilot.com",
    ) -> tuple[str, str]:
        enc = self._cipher.encrypt(device_code)
        async with self._pool.acquire() as con:
            async with con.transaction():
                account = await con.fetchrow(
                    "INSERT INTO accounts (login, plan, api_base, status) "
                    "VALUES ($1,$2,$3,'logging_in') "
                    "ON CONFLICT (host, login) DO UPDATE SET "
                    "  plan = EXCLUDED.plan, api_base = EXCLUDED.api_base, "
                    "  status = CASE WHEN accounts.status IN ('idle','quarantined','logging_in') "
                    "                THEN 'logging_in' ELSE accounts.status END, "
                    "  updated_at = now() "
                    "RETURNING id",
                    login, plan, api_base)
                session = await con.fetchrow(
                    "INSERT INTO device_sessions "
                    "(account_id, device_code_enc, user_code, verification_uri, interval_s, expires_at) "
                    "VALUES ($1,$2,$3,$4,$5,$6) RETURNING id",
                    account["id"], enc, user_code, verification_uri, interval_s, expires_at)
                return str(account["id"]), str(session["id"])

    async def get_pending_device_session(self, login: str) -> dict | None:
        row = await self._pool.fetchrow(
            "SELECT ds.id, ds.account_id, ds.device_code_enc, ds.user_code, "
            "       ds.verification_uri, ds.interval_s, ds.expires_at "
            "FROM device_sessions ds JOIN accounts a ON a.id = ds.account_id "
            "WHERE a.login=$1 AND ds.status='pending' AND ds.expires_at > now() "
            "ORDER BY ds.created_at DESC LIMIT 1",
            login)
        if not row:
            return None
        out = dict(row)
        out["id"] = str(out["id"])
        out["account_id"] = str(out["account_id"])
        out["device_code"] = self._cipher.decrypt(bytes(out.pop("device_code_enc")))
        return out

    async def mark_device_session(self, session_id: str, status: str) -> None:
        await self._pool.execute(
            "UPDATE device_sessions SET status=$2 WHERE id=$1",
            session_id, status)

    async def complete_device_session(self, session_id: str, oauth_token: str) -> str:
        enc = self._cipher.encrypt(oauth_token)
        async with self._pool.acquire() as con:
            async with con.transaction():
                row = await con.fetchrow(
                    "UPDATE device_sessions SET status='authorized' "
                    "WHERE id=$1 RETURNING account_id",
                    session_id)
                await con.execute(
                    "UPDATE accounts SET oauth_token_enc=$2, "
                    "status = CASE WHEN EXISTS ("
                    "  SELECT 1 FROM bindings WHERE account_id=$1 AND status='active'"
                    ") THEN 'bound' ELSE 'idle' END, "
                    "last_error=NULL, updated_at=now() WHERE id=$1",
                    row["account_id"], enc)
                return str(row["account_id"])

    # -- bindings ---------------------------------------------------------
    async def get_binding(self, user_id: str) -> str | None:
        row = await self._pool.fetchrow(
            "SELECT account_id FROM bindings WHERE user_id=$1 AND status='active'",
            user_id)
        return str(row["account_id"]) if row else None

    async def claim_idle_account(self, user_id: str) -> str | None:
        """Atomically claim one idle account and bind the user to it."""
        async with self._pool.acquire() as con:
            async with con.transaction():
                row = await con.fetchrow(
                    "SELECT id FROM accounts WHERE status='idle' "
                    "ORDER BY updated_at FOR UPDATE SKIP LOCKED LIMIT 1")
                if not row:
                    return None
                account_id = row["id"]
                await con.execute(
                    "UPDATE accounts SET status='bound', updated_at=now() WHERE id=$1",
                    account_id)
                await con.execute(
                    "INSERT INTO bindings (user_id, account_id) VALUES ($1,$2) "
                    "ON CONFLICT (user_id) DO UPDATE SET account_id=EXCLUDED.account_id, "
                    "  status='active', bound_at=now(), last_active_at=now()",
                    user_id, account_id)
                return str(account_id)

    async def release_binding(self, user_id: str) -> None:
        """Release a user's binding and return its account to the idle pool.

        Done in one transaction. A 'bound' account is reset to 'idle' so it can
        be reused; a 'quarantined' account is left as-is (it must not be handed
        out until an operator re-authorizes it).
        """
        async with self._pool.acquire() as con:
            async with con.transaction():
                row = await con.fetchrow(
                    "DELETE FROM bindings WHERE user_id=$1 RETURNING account_id",
                    user_id)
                if row:
                    await con.execute(
                        "UPDATE accounts SET status='idle', updated_at=now() "
                        "WHERE id=$1 AND status='bound'", row["account_id"])

    # -- users / keys -----------------------------------------------------
    async def create_user(self, external_id: str, display_name: str | None = None) -> str:
        row = await self._pool.fetchrow(
            "INSERT INTO users (external_id, display_name) VALUES ($1,$2) "
            "ON CONFLICT (external_id) DO UPDATE SET display_name=EXCLUDED.display_name "
            "RETURNING id", external_id, display_name)
        return str(row["id"])

    async def add_api_key(self, user_id: str, key_hash: bytes, name: str | None = None,
                          scopes: list[str] | None = None,
                          rate_limit: int | None = None) -> str:
        row = await self._pool.fetchrow(
            "INSERT INTO api_keys (user_id, key_hash, name, scopes, rate_limit) "
            "VALUES ($1,$2,$3,$4,$5) RETURNING id",
            user_id, key_hash, name, scopes or [], rate_limit)
        return str(row["id"])

    async def user_for_key_hash(self, key_hash: bytes) -> str | None:
        row = await self._pool.fetchrow(
            "UPDATE api_keys SET last_used_at=now() WHERE key_hash=$1 AND status='active' "
            "RETURNING user_id", key_hash)
        return str(row["user_id"]) if row else None

    async def set_user_status(self, user_id: str, status: str) -> bool:
        row = await self._pool.fetchrow(
            "UPDATE users SET status=$2 WHERE id=$1 RETURNING id", user_id, status)
        return row is not None

    async def get_api_key_meta(self, key_id: str) -> dict | None:
        row = await self._pool.fetchrow(
            "SELECT id, user_id, name, scopes, rate_limit, status "
            "FROM api_keys WHERE id=$1", key_id)
        if not row:
            return None
        out = dict(row)
        out["id"] = str(out["id"])
        out["user_id"] = str(out["user_id"])
        out["scopes"] = list(out["scopes"] or [])
        return out

    async def revoke_api_key(self, key_id: str) -> bool:
        row = await self._pool.fetchrow(
            "UPDATE api_keys SET status='revoked' WHERE id=$1 RETURNING id", key_id)
        return row is not None

    async def list_users_with_keys(self) -> list[dict]:
        rows = await self._pool.fetch(
            "SELECT u.id AS user_id, u.external_id, u.display_name, u.status, u.created_at, "
            "       k.id AS key_id, k.name, k.scopes, k.status AS key_status, "
            "       k.rate_limit, k.created_at AS key_created_at, k.last_used_at "
            "FROM users u LEFT JOIN api_keys k ON k.user_id = u.id "
            "ORDER BY u.created_at, k.created_at")
        users: dict = {}
        for r in rows:
            uid = str(r["user_id"])
            user = users.get(uid)
            if user is None:
                user = {"id": uid, "external_id": r["external_id"],
                        "display_name": r["display_name"], "status": r["status"],
                        "created_at": r["created_at"], "keys": []}
                users[uid] = user
            if r["key_id"] is not None:
                user["keys"].append({
                    "id": str(r["key_id"]), "name": r["name"],
                    "scopes": list(r["scopes"] or []), "status": r["key_status"],
                    "rate_limit": r["rate_limit"], "created_at": r["key_created_at"],
                    "last_used_at": r["last_used_at"]})
        return list(users.values())

    # -- usage ------------------------------------------------------------
    async def bump_usage(self, user_id: str, account_id: str | None, model: str,
                         prompt_tokens: int, completion_tokens: int) -> None:
        await self._pool.execute(
            "INSERT INTO usage_rollup (user_id, account_id, day, model, "
            "  prompt_tokens, completion_tokens, requests) "
            "VALUES ($1,$2,CURRENT_DATE,$3,$4,$5,1) "
            "ON CONFLICT (user_id, day, model) DO UPDATE SET "
            "  prompt_tokens = usage_rollup.prompt_tokens + EXCLUDED.prompt_tokens, "
            "  completion_tokens = usage_rollup.completion_tokens + EXCLUDED.completion_tokens, "
            "  requests = usage_rollup.requests + 1",
            user_id, account_id, model, prompt_tokens, completion_tokens)

    async def list_accounts(self) -> list[dict]:
        rows = await self._pool.fetch(
            "SELECT id, login, plan, api_base, status, last_error, last_seen_at, "
            "       refresh_at, updated_at "
            "FROM accounts ORDER BY created_at")
        out = []
        for r in rows:
            d = dict(r)
            d["id"] = str(d["id"])
            out.append(d)
        return out

    async def list_bindings(self) -> list[dict]:
        rows = await self._pool.fetch(
            "SELECT b.user_id, u.external_id, b.account_id, a.login, b.status, "
            "       b.bound_at, b.last_active_at "
            "FROM bindings b "
            "JOIN users u ON u.id = b.user_id "
            "JOIN accounts a ON a.id = b.account_id "
            "ORDER BY b.bound_at DESC")
        out = []
        for r in rows:
            d = dict(r)
            d["user_id"] = str(d["user_id"])
            d["account_id"] = str(d["account_id"])
            out.append(d)
        return out

    # -- usage aggregation (read-only over usage_rollup) ------------------
    async def usage_timeseries(self, date_from: dt.date | None,
                               date_to: dt.date | None) -> list[dict]:
        rows = await self._pool.fetch(
            "SELECT day, "
            "       SUM(prompt_tokens)::bigint     AS prompt_tokens, "
            "       SUM(completion_tokens)::bigint AS completion_tokens, "
            "       SUM(requests)::bigint          AS requests "
            "FROM usage_rollup "
            "WHERE ($1::date IS NULL OR day >= $1) AND ($2::date IS NULL OR day <= $2) "
            "GROUP BY day ORDER BY day", date_from, date_to)
        return [{"day": r["day"].isoformat(), "prompt_tokens": int(r["prompt_tokens"]),
                 "completion_tokens": int(r["completion_tokens"]),
                 "requests": int(r["requests"])} for r in rows]

    async def usage_by_user(self, date_from: dt.date | None,
                            date_to: dt.date | None) -> list[dict]:
        rows = await self._pool.fetch(
            "SELECT ur.user_id, u.external_id, u.display_name, "
            "       SUM(ur.prompt_tokens)::bigint     AS prompt_tokens, "
            "       SUM(ur.completion_tokens)::bigint AS completion_tokens, "
            "       SUM(ur.requests)::bigint          AS requests "
            "FROM usage_rollup ur LEFT JOIN users u ON u.id = ur.user_id "
            "WHERE ($1::date IS NULL OR ur.day >= $1) AND ($2::date IS NULL OR ur.day <= $2) "
            "GROUP BY ur.user_id, u.external_id, u.display_name "
            "ORDER BY (SUM(ur.prompt_tokens) + SUM(ur.completion_tokens)) DESC",
            date_from, date_to)
        return [{"user_id": str(r["user_id"]), "external_id": r["external_id"],
                 "display_name": r["display_name"],
                 "prompt_tokens": int(r["prompt_tokens"]),
                 "completion_tokens": int(r["completion_tokens"]),
                 "requests": int(r["requests"])} for r in rows]

    async def usage_by_account(self, date_from: dt.date | None,
                               date_to: dt.date | None) -> list[dict]:
        rows = await self._pool.fetch(
            "SELECT ur.account_id, a.login, "
            "       SUM(ur.prompt_tokens)::bigint     AS prompt_tokens, "
            "       SUM(ur.completion_tokens)::bigint AS completion_tokens, "
            "       SUM(ur.requests)::bigint          AS requests "
            "FROM usage_rollup ur LEFT JOIN accounts a ON a.id = ur.account_id "
            "WHERE ($1::date IS NULL OR ur.day >= $1) AND ($2::date IS NULL OR ur.day <= $2) "
            "GROUP BY ur.account_id, a.login "
            "ORDER BY (SUM(ur.prompt_tokens) + SUM(ur.completion_tokens)) DESC",
            date_from, date_to)
        return [{"account_id": str(r["account_id"]) if r["account_id"] else None,
                 "login": r["login"],
                 "prompt_tokens": int(r["prompt_tokens"]),
                 "completion_tokens": int(r["completion_tokens"]),
                 "requests": int(r["requests"])} for r in rows]

    async def usage_by_model(self, date_from: dt.date | None,
                             date_to: dt.date | None) -> list[dict]:
        rows = await self._pool.fetch(
            "SELECT model, "
            "       SUM(prompt_tokens)::bigint     AS prompt_tokens, "
            "       SUM(completion_tokens)::bigint AS completion_tokens, "
            "       SUM(requests)::bigint          AS requests "
            "FROM usage_rollup "
            "WHERE ($1::date IS NULL OR day >= $1) AND ($2::date IS NULL OR day <= $2) "
            "GROUP BY model "
            "ORDER BY (SUM(prompt_tokens) + SUM(completion_tokens)) DESC",
            date_from, date_to)
        return [{"model": r["model"], "prompt_tokens": int(r["prompt_tokens"]),
                 "completion_tokens": int(r["completion_tokens"]),
                 "requests": int(r["requests"])} for r in rows]
