"""Prometheus metrics exposed at /metrics."""
from prometheus_client import Counter, Gauge, Histogram

REQUESTS = Counter(
    "ghcproxy_requests_total", "Proxied requests", ["protocol", "status_class"])
UPSTREAM_LATENCY = Histogram(
    "ghcproxy_upstream_latency_seconds", "Upstream request latency", ["protocol"])
REBINDS = Counter(
    "ghcproxy_rebinds_total", "Automatic account re-routes after login expiry")
QUARANTINED_ACCOUNTS = Gauge(
    "ghcproxy_quarantined_accounts", "Accounts currently quarantined")
IDLE_ACCOUNTS = Gauge(
    "ghcproxy_idle_accounts", "Accounts currently idle/available")
NO_ACCOUNT = Counter(
    "ghcproxy_no_account_total", "Requests rejected because no account was free")


def status_class(status: int) -> str:
    return f"{status // 100}xx"
