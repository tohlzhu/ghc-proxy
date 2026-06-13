"""Application context — wires the concrete dependencies together.

Holds the Postgres repo, Redis cache, Kafka sink, httpx upstream client,
binding service, forwarder and device-flow helper. Built once at startup and
shared by the FastAPI handlers and the refresher worker.
"""
from __future__ import annotations

import httpx

from ghcproxy.cache import RedisCache
from ghcproxy.common.config import Settings
from ghcproxy.common.crypto import TokenCipher
from ghcproxy.credential.device_flow import DeviceFlow, DeviceFlowError
from ghcproxy.credential.token_service import CopilotTokenService
from ghcproxy.db.repo import PgRepo
from ghcproxy.observability.sink import KafkaSink, NullSink
from ghcproxy.proxy.forwarder import Forwarder
from ghcproxy.proxy.upstream import HttpxUpstream
from ghcproxy.router.binding import BindingService


class _HttpxForm:
    """Adapter so DeviceFlow can post x-www-form-urlencoded via httpx."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def post_form(self, url, data, headers):
        try:
            resp = await self._client.post(url, data=data, headers=headers)
        except httpx.HTTPError as exc:
            # No egress / DNS / TLS / timeout reaching GitHub. Translate to a
            # DeviceFlowError so the caller (start_login) returns a clean 502
            # with a readable detail instead of an opaque 500.
            raise DeviceFlowError(
                f"could not reach GitHub device-flow endpoint ({url}): "
                f"{type(exc).__name__}: {exc}") from exc
        try:
            return resp.status_code, resp.json()
        except Exception:
            return resp.status_code, {}


class AppContext:
    def __init__(self, settings: Settings) -> None:
        self.cfg = settings
        self.cipher = TokenCipher(settings.crypto.data_key_bytes())
        self.repo: PgRepo | None = None
        self.cache: RedisCache | None = None
        self.sink = None
        self.http: httpx.AsyncClient | None = None
        self.tokens: CopilotTokenService | None = None
        self.upstream: HttpxUpstream | None = None
        self.binding: BindingService | None = None
        self.forwarder: Forwarder | None = None
        self.device_flow: DeviceFlow | None = None
        self.admin_token: str | None = None
        self.pending_logins: dict[str, str] = {}

    async def start(self) -> None:
        self.repo = await PgRepo.connect(
            self.cfg.postgres.url, self.cipher,
            min_size=self.cfg.postgres.min_pool, max_size=self.cfg.postgres.max_pool)
        self.cache = RedisCache(self.cfg.redis.url)
        self.http = httpx.AsyncClient()
        self.tokens = CopilotTokenService(
            self.cfg.upstream, self.http,
            skew_s=self.cfg.refresh.liveness_skew_s)
        self.upstream = HttpxUpstream(self.cfg.upstream, self.http,
                                      token_service=self.tokens)
        self.binding = BindingService(self.repo)
        self.forwarder = Forwarder(self.binding, self.upstream, self.repo)
        self.device_flow = DeviceFlow(self.cfg.device_flow, _HttpxForm(self.http))
        if self.cfg.kafka.enabled:
            self.sink = KafkaSink(self.cfg.kafka.brokers, self.cfg.kafka.topic_prompts,
                                  self.cfg.kafka.topic_usage, self.cfg.kafka.topic_audit)
        else:
            self.sink = NullSink()
        await self.sink.start()

    async def init_schema(self) -> None:
        import importlib.resources as res
        schema = res.files("ghcproxy.db").joinpath("schema.sql").read_text()
        await self.repo.init_schema(schema)

    async def stop(self) -> None:
        if self.sink:
            await self.sink.stop()
        if self.http:
            await self.http.aclose()
        if self.cache:
            await self.cache.close()
        if self.repo:
            await self.repo.close()
