"""Settings, all overridable with environment variables."""
import os
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(os.environ.get("ONCALL_ROOT", Path(__file__).resolve().parents[1]))
DEMO = ROOT / "demo"
STATE = Path(os.environ.get("ONCALL_STATE", DEMO / "state"))
COMPOSE_FILE = Path(os.environ.get("ONCALL_COMPOSE_FILE", DEMO / "docker-compose.yml"))

_url = urlparse(os.environ.get("FALKORDB_URL", "redis://localhost:6380"))
FALKORDB_HOST = _url.hostname or "localhost"
FALKORDB_PORT = _url.port or 6379
FALKORDB_PASSWORD = _url.password
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
