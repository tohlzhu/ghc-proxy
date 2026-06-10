"""Redis-backed hot-path caches.

* binding cache: ``binding:{user_id}`` -> account_id (short TTL; PG is source of truth)
* user-for-key cache: ``key:{sha}`` -> user_id
* per-key rate limit: fixed-window counter ``ratelimit:{key_id}:{minute}``

All methods degrade gracefully: a Redis hiccup falls back to the DB rather
than failing the request.
"""
from __future__ import annotations

import redis.asyncio as redis


class RedisCache:
    def __init__(self, url: str) -> None:
        self._r = redis.from_url(url, decode_responses=True)

    async def close(self) -> None:
        await self._r.aclose()

    async def ping(self) -> bool:
        return await self._r.ping()

    # -- binding cache ----------------------------------------------------
    async def get_binding(self, user_id: str) -> str | None:
        try:
            return await self._r.get(f"binding:{user_id}")
        except Exception:
            return None

    async def set_binding(self, user_id: str, account_id: str, ttl: int = 300) -> None:
        try:
            await self._r.set(f"binding:{user_id}", account_id, ex=ttl)
        except Exception:
            pass

    async def drop_binding(self, user_id: str) -> None:
        try:
            await self._r.delete(f"binding:{user_id}")
        except Exception:
            pass

    # -- key -> user cache ------------------------------------------------
    async def get_user_for_key(self, key_sha_hex: str) -> str | None:
        try:
            return await self._r.get(f"key:{key_sha_hex}")
        except Exception:
            return None

    async def set_user_for_key(self, key_sha_hex: str, user_id: str, ttl: int = 300) -> None:
        try:
            await self._r.set(f"key:{key_sha_hex}", user_id, ex=ttl)
        except Exception:
            pass

    # -- rate limiting ----------------------------------------------------
    async def incr_rate(self, user_id: str, window_minute: int) -> int:
        """Return the request count in the current minute window."""
        key = f"ratelimit:{user_id}:{window_minute}"
        try:
            count = await self._r.incr(key)
            if count == 1:
                await self._r.expire(key, 120)
            return count
        except Exception:
            return 0  # fail-open on cache outage
