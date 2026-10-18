"""Settings, all overridable with environment variables."""

import os
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(os.environ.get("ONCALL_ROOT", Path(__file__).resolve().parents[1]))
DEMO = ROOT / "demo"
STATE = Path(os.environ.get("ONCALL_STATE", DEMO / "state"))
COMPOSE_FILE = Path(os.environ.get("ONCALL_COMPOSE_FILE", DEMO / "docker-compose.yml"))


def falkordb_connection(url: str) -> dict:
    """FalkorDB() keyword arguments for a redis:// URL, or rediss:// for TLS (FalkorDB Cloud:
    rediss://user:password@host:port). Percent-encoded credentials are decoded."""
    u = urlparse(url)
    if u.scheme not in ("redis", "rediss"):
        raise ValueError(f"FALKORDB_URL must start with redis:// or rediss://, got {u.scheme or 'no'} scheme")
    return {
        "host": u.hostname or "localhost",
        "port": u.port or 6379,
        "username": unquote(u.username) if u.username else None,
        "password": unquote(u.password) if u.password else None,
        "ssl": u.scheme == "rediss",
    }


FALKORDB_URL = os.environ.get("FALKORDB_URL") or "redis://localhost:6380"  # empty counts as unset
FALKORDB = falkordb_connection(FALKORDB_URL)
GRAPH = os.environ.get("ONCALL_GRAPH", "oncall")

# Stats endpoints are reached on the host via the ports published in the compose file.
STATS_HOST = os.environ.get("ONCALL_STATS_HOST", "localhost")

# Alerting thresholds used by the watcher and by verify_recovery.
ALERT_ERROR_RATE = float(os.environ.get("ONCALL_ALERT_ERROR_RATE", "0.15"))
HEALTHY_ERROR_RATE = float(os.environ.get("ONCALL_HEALTHY_ERROR_RATE", "0.03"))
SLOW_P95_MS = float(os.environ.get("ONCALL_SLOW_P95_MS", "1000"))

# "1" lets action tools change infrastructure. "0" forces every action into dry-run.
ALLOW_ACTIONS = os.environ.get("ONCALL_ALLOW_ACTIONS", "1") == "1"
NOTIFY_WEBHOOK = os.environ.get("ONCALL_NOTIFY_WEBHOOK", "")  # Slack or Discord incoming webhook

QUERY_TIMEOUT_MS = int(os.environ.get("ONCALL_QUERY_TIMEOUT_MS", "2000"))

# "full" = per-service stats include where errors originate (what distributed tracing gives you).
# "basic" = error rate + latency only (no tracing); the agent must infer propagation itself.
TELEMETRY = os.environ.get("ONCALL_TELEMETRY", "full")
