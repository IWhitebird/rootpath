"""Settings, all overridable with environment variables. Where the graph lives (FalkorDB URL and
graph name) also comes from the user config file that `rootpath init` writes."""

import os
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import paths, userconfig

# The repo of a checkout, or the data dir of a binary or an installed package (see paths.py). ONCALL_ROOT
# names a directory with a demo/ of its own instead, such as a checkout, used as it is.
ROOT = Path(os.environ.get("ONCALL_ROOT", paths.root()))


def _demo() -> dict:
    """DEMO, STATE and COMPOSE_FILE. A bundle runs the demo from its copy in the data dir, brought up to
    date the first time a command needs the demo."""
    bundle_copy = paths.bundled() and "ONCALL_ROOT" not in os.environ
    demo = paths.sync_demo(ROOT / "demo") if bundle_copy else ROOT / "demo"
    return {
        "DEMO": demo,
        "STATE": Path(os.environ.get("ONCALL_STATE", demo / "state")),
        "COMPOSE_FILE": Path(os.environ.get("ONCALL_COMPOSE_FILE", demo / "docker-compose.yml")),
    }


def falkordb_connection(url: str) -> dict:
    """FalkorDB() keyword arguments for a redis:// URL, or rediss:// for TLS. FalkorDB Cloud shows
    falkor://user:password@host:port (falkors:// with TLS), the same protocol under another name.
    Percent-encoded credentials are decoded."""
    u = urlparse(url)
    if u.scheme not in ("redis", "rediss", "falkor", "falkors"):
        raise ValueError(
            f"FALKORDB_URL must start with redis://, rediss://, falkor:// or falkors://, got {u.scheme or 'no'} scheme"
        )
    return {
        "host": u.hostname or "localhost",
        "port": u.port or 6379,
        "username": unquote(u.username) if u.username else None,
        "password": unquote(u.password) if u.password else None,
        "ssl": u.scheme in ("rediss", "falkors"),
    }


# The Claude Code plugin's optional overrides reach its MCP server as FALKORDB_URL / ONCALL_GRAPH (empty
# when left unset) and its safety hook as CLAUDE_PLUGIN_OPTION_<KEY>. That one comes first, so the hook
# uses the same graph as the MCP server even when the shell that started Claude Code exports FALKORDB_URL.
ENV = {
    "falkordb_url": ("CLAUDE_PLUGIN_OPTION_FALKORDB_URL", "FALKORDB_URL"),
    "graph": ("CLAUDE_PLUGIN_OPTION_GRAPH", "ONCALL_GRAPH"),
}
DEFAULTS = {"falkordb_url": "redis://localhost:6380", "graph": "oncall"}


def setting(key: str) -> tuple[str, str]:
    """(value, where it came from): the first non-empty environment variable, else the user config
    file, else the built-in default."""
    for var in ENV[key]:
        if os.environ.get(var):
            return os.environ[var], var
    if value := userconfig.load().get(key):
        return value, str(userconfig.path())
    return DEFAULTS[key], "default"


def __getattr__(name: str):
    """DEMO, STATE and COMPOSE_FILE, and FALKORDB_URL, FALKORDB and GRAPH, resolved on first use. A broken
    config file or URL then fails what needs the graph (the safety hook denies), not `rootpath init` /
    `rootpath config`, which fix it."""
    if name in ("DEMO", "STATE", "COMPOSE_FILE"):
        globals().update(_demo())
        return globals()[name]
    if name not in ("FALKORDB_URL", "FALKORDB", "GRAPH"):
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    url, source = setting("falkordb_url")
    try:
        connection = falkordb_connection(url)
    except ValueError as e:
        raise ValueError(f"{e} (from {source})") from e
    globals().update(FALKORDB_URL=url, FALKORDB=connection, GRAPH=setting("graph")[0])
    return globals()[name]


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
