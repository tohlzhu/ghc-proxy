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

    async def set_account_status(self, account_id: str, status: str) -> None:
        await self._pool.execute(
            "UPDATE accounts SET status=$2, updated_at=now() WHERE id=$1",
            account_id, status)

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

    async def add_api_key(self, user_id: str, key_hash: bytes, name: str | None = None) -> str:
        row = await self._pool.fetchrow(
            "INSERT INTO api_keys (user_id, key_hash, name) VALUES ($1,$2,$3) RETURNING id",
            user_id, key_hash, name)
        return str(row["id"])

    async def user_for_key_hash(self, key_hash: bytes) -> str | None:
        row = await self._pool.fetchrow(
            "UPDATE api_keys SET last_used_at=now() WHERE key_hash=$1 AND status='active' "
            "RETURNING user_id", key_hash)
        return str(row["user_id"]) if row else None

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
            "SELECT id, login, plan, status, last_error, last_seen_at FROM accounts "
            "ORDER BY created_at")
        return [dict(r) for r in rows]
