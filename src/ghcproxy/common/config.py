"""Central configuration.

Loaded from a YAML file and overlaid with environment variables
(``GHCPROXY_<SECTION>__<FIELD>``). Secrets (DB/Redis URLs, the crypto data
key) should come from the environment / K8s secrets in production; the YAML
holds non-sensitive defaults and placeholders only.

Upstream defaults reflect what the real GitHub Copilot CLI 1.0.61 sends, as
captured live on the workspace host (see the credential design notes).
"""
from __future__ import annotations

import base64
import json
import os
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080
    max_users_per_account: int = 1  # enforce strict 1:1 binding
    request_timeout_s: float = 600.0


class UpstreamConfig(BaseModel):
    """Headers/identity the proxy presents to the GHC model API.

    Values mirror what a real Copilot client sends; getting them right keeps
    prompt-cache hit-rate high and avoids being flagged as an anomalous client
    (see ghc-proxy-design.md §2.5 and litellm's ``get_copilot_default_headers``).
    """

    default_api_base: str = "https://api.enterprise.githubcopilot.com"
    github_api_base: str = "https://api.github.com"
    # Path on github_api_base that exchanges a durable OAuth token for a
    # short-lived Copilot bearer (token B). 404 here => direct-bearer fallback.
    token_exchange_path: str = "/copilot_internal/v2/token"
    integration_id: str = "copilot-developer-cli"
    editor_version: str = "copilot/1.0.61"
    editor_plugin_version: str = "copilot/1.0.61"
    api_version: str = "2026-06-01"
    anthropic_version: str = "2023-06-01"
    user_agent: str = "GitHubCopilotChat/0.26.7"
    openai_intent: str = "conversation-panel"


class DeviceFlowConfig(BaseModel):
    # Public OAuth client id of a Copilot client (NOT a secret — it is sent in
    # the clear on every device-flow request and is published in litellm,
    # copilot-api, etc.). Which client you use decides the auth shape the token
    # service must take:
    #   * CLI OAuth App (``Ov23ctDVkRmgkPke0Mmm``) mints ``gho_`` → used
    #     DIRECTLY as bearer (token exchange returns 404). This is the default
    #     and matches integration_id=copilot-developer-cli above.
    #   * Editor GitHub App (e.g. ``Iv1.b507a08c87ecfe98``) mints ``ghu_`` →
    #     must be EXCHANGED at copilot_internal/v2/token for a ~30 min token B.
    # The token service supports both (try-exchange, fall back to direct), so
    # either client works; override per deployment via
    # GHCPROXY_DEVICE_FLOW__CLIENT_ID. A placeholder value here would make
    # GitHub's POST /login/device/code return 404 (see device_flow.py guard).
    client_id: str = "Ov23ctDVkRmgkPke0Mmm"
    scope: str = "read:user"
    device_code_url: str = "https://github.com/login/device/code"
    access_token_url: str = "https://github.com/login/oauth/access_token"


class RefreshConfig(BaseModel):
    scan_interval_s: int = 60
    lock_ttl_s: int = 30
    liveness_skew_s: int = 120
    # how often to re-validate a token even if it never errors
    revalidate_interval_s: int = 1800


class PostgresConfig(BaseModel):
    url: str = "postgresql://ghcproxy:ghcproxy@localhost:5432/ghcproxy"
    min_pool: int = 1
    max_pool: int = 10


class RedisConfig(BaseModel):
    url: str = "redis://localhost:6379/0"


class KafkaConfig(BaseModel):
    enabled: bool = True
    brokers: list[str] = Field(default_factory=lambda: ["localhost:9092"])
    topic_prompts: str = "ghcproxy.prompts"
    topic_usage: str = "ghcproxy.usage"
    topic_audit: str = "ghcproxy.audit"


class CryptoConfig(BaseModel):
    # base64 of a 32-byte key. Placeholder default is all-zero (dev only).
    data_key_b64: str = base64.b64encode(b"\x00" * 32).decode()

    def data_key_bytes(self) -> bytes:
        return base64.b64decode(self.data_key_b64)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GHCPROXY_",
        env_nested_delimiter="__",
        extra="ignore",
    )
    server: ServerConfig = Field(default_factory=ServerConfig)
    upstream: UpstreamConfig = Field(default_factory=UpstreamConfig)
    device_flow: DeviceFlowConfig = Field(default_factory=DeviceFlowConfig)
    refresh: RefreshConfig = Field(default_factory=RefreshConfig)
    postgres: PostgresConfig = Field(default_factory=PostgresConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    kafka: KafkaConfig = Field(default_factory=KafkaConfig)
    crypto: CryptoConfig = Field(default_factory=CryptoConfig)


def _env_overlay(prefix: str = "GHCPROXY_", delim: str = "__") -> dict[str, Any]:
    """Build a nested dict from ``GHCPROXY_SECTION__FIELD`` environment vars."""
    out: dict[str, Any] = {}
    for key, value in os.environ.items():
        if not key.startswith(prefix):
            continue
        path = key[len(prefix):].lower().split(delim)
        node = out
        for part in path[:-1]:
            node = node.setdefault(part, {})
        node[path[-1]] = _coerce(value)
    return out


def _coerce(value: str) -> Any:
    """JSON-decode list/object values so e.g. BROKERS='["a","b"]' becomes a list.

    Scalars are left as strings; pydantic coerces them to the field type.
    """
    stripped = value.strip()
    if stripped[:1] in ("[", "{"):
        try:
            return json.loads(stripped)
        except ValueError:
            return value
    return value


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def load_settings(path: str | None = None) -> Settings:
    """Load YAML (if given) then let environment variables override it."""
    file_values: dict[str, Any] = {}
    if path:
        with open(path) as fh:
            file_values = yaml.safe_load(fh) or {}
    merged = _deep_merge(file_values, _env_overlay())
    return Settings(**merged)
