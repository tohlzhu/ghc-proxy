"""Tests for configuration loading (YAML file + environment overrides)."""
import base64
import os

from ghcproxy.common.config import Settings, load_settings


def test_defaults_match_verified_ghc_client():
    """Upstream defaults must match what the real Copilot CLI sends, as
    captured live: integration id copilot-developer-cli, the enterprise host,
    and the 2026-06-01 api version."""
    s = Settings()
    assert s.upstream.integration_id == "copilot-developer-cli"
    assert s.upstream.api_version == "2026-06-01"
    assert "githubcopilot.com" in s.upstream.default_api_base


def test_device_flow_client_id_is_a_real_public_client_not_placeholder():
    """The device-flow client_id must default to a real, public Copilot OAuth
    client id so Re-login works out of the box. A placeholder like
    'Iv1.<CLIENT_ID>' makes GitHub's POST /login/device/code return 404. The
    id is public (not a secret), so a working default is correct here — just
    like the other upstream defaults above.

    The CLI OAuth App 'Ov23ctDVkRmgkPke0Mmm' mints gho_ tokens used directly as
    Bearer, consistent with integration_id=copilot-developer-cli."""
    s = Settings()
    cid = s.device_flow.client_id
    assert "<" not in cid and ">" not in cid, f"placeholder client_id: {cid!r}"
    assert cid == "Ov23ctDVkRmgkPke0Mmm"


def test_load_from_yaml(tmp_path):
    key = base64.b64encode(b"\x00" * 32).decode()
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "server:\n"
        "  port: 9999\n"
        "postgres:\n"
        "  url: postgres://u:p@h:5432/db\n"
        "redis:\n"
        "  url: redis://h:6379/0\n"
        f"crypto:\n  data_key_b64: {key}\n"
    )
    s = load_settings(str(cfg))
    assert s.server.port == 9999
    assert s.postgres.url == "postgres://u:p@h:5432/db"
    assert s.crypto.data_key_b64 == key


def test_env_overrides_yaml(tmp_path, monkeypatch):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("server:\n  port: 1111\n")
    monkeypatch.setenv("GHCPROXY_SERVER__PORT", "2222")
    s = load_settings(str(cfg))
    assert s.server.port == 2222


def test_env_json_list_is_parsed(monkeypatch):
    # K8s/compose pass list-valued settings as a JSON string
    monkeypatch.setenv("GHCPROXY_KAFKA__BROKERS", '["kafka:9092","kafka2:9092"]')
    s = load_settings()
    assert s.kafka.brokers == ["kafka:9092", "kafka2:9092"]


def test_env_bool_is_parsed(monkeypatch):
    monkeypatch.setenv("GHCPROXY_KAFKA__ENABLED", "false")
    s = load_settings()
    assert s.kafka.enabled is False


def test_data_key_bytes_decoded():
    key = base64.b64encode(b"\x07" * 32).decode()
    s = Settings(crypto={"data_key_b64": key})
    assert s.crypto.data_key_bytes() == b"\x07" * 32
