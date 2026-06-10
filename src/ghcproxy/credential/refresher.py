"""Refresher worker — keeps accounts logged in without user traffic.

Because the durable ``gho_`` token is long-lived (no short-exchange needed for
CLI tokens), "refresh" here means **proactive liveness validation**: periodically
call ``GET {api_base}/models`` with each account's token. A healthy response
pushes ``refresh_at`` forward; a login-expiry quarantines the account so an
operator can re-run device flow.

Runs as a separate K8s workload (role=refresher). Per-account work is guarded
by a Redis lock so multiple replicas never validate the same account at once.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import time

from ghcproxy.credential.client import build_upstream_headers, is_login_expired

log = logging.getLogger("ghcproxy.refresher")

HEARTBEAT_PATH = os.environ.get("GHCPROXY_REFRESHER_HEARTBEAT", "/tmp/ghcproxy-refresher.heartbeat")


class Refresher:
    def __init__(self, ctx) -> None:
        self._ctx = ctx
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def _try_lock(self, account_id: str) -> bool:
        try:
            ok = await self._ctx.cache._r.set(
                f"lock:account:{account_id}", "1",
                nx=True, ex=self._ctx.cfg.refresh.lock_ttl_s)
            return bool(ok)
        except Exception:
            return True  # fail-open: still better to validate than skip

    async def validate_account(self, account) -> bool:
        """Return True if the account is healthy."""
        headers = build_upstream_headers(self._ctx.cfg.upstream, account.oauth_token,
                                         anthropic=False)
        url = f"{account.api_base.rstrip('/')}/models"
        try:
            resp = await self._ctx.http.get(url, headers=headers, timeout=20.0)
        except Exception as exc:
            log.warning("liveness check error for %s: %s", account.login, exc)
            return True  # transient network error; don't quarantine
        if is_login_expired(resp.status_code, resp.content):
            await self._ctx.repo.quarantine_account(account.id, "liveness: login expired")
            await self._ctx.sink.audit({"event": "quarantine", "account": account.id,
                                        "reason": "login_expired"})
            return False
        nxt = dt.datetime.now(dt.timezone.utc) + dt.timedelta(
            seconds=self._ctx.cfg.refresh.revalidate_interval_s)
        await self._ctx.repo.mark_seen(account.id, nxt)
        return True

    async def tick(self) -> int:
        """One scan pass. Returns the number of accounts validated."""
        now = dt.datetime.now(dt.timezone.utc)
        due = await self._ctx.repo.due_for_revalidation(now)
        n = 0
        for account in due:
            if not await self._try_lock(account.id):
                continue
            await self.validate_account(account)
            n += 1
        return n

    def _heartbeat(self) -> None:
        try:
            with open(HEARTBEAT_PATH, "w") as fh:
                fh.write(str(time.time()))
        except OSError:  # pragma: no cover - heartbeat best-effort
            pass

    async def run(self) -> None:
        log.info("refresher started")
        self._heartbeat()  # mark alive immediately so health passes at startup
        while not self._stop.is_set():
            try:
                count = await self.tick()
                self._heartbeat()
                if count:
                    log.info("validated %d accounts", count)
            except Exception:  # pragma: no cover - defensive loop guard
                log.exception("refresher tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(),
                                       timeout=self._ctx.cfg.refresh.scan_interval_s)
            except asyncio.TimeoutError:
                pass
