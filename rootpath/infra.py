"""Everything that touches the running system: Docker Compose, service stats, logs,
deploys, restarts and feature flags. The change log (state/changes.jsonl) plays the
role of a CI/CD + flag-service audit trail; the graph is built from it."""

import json
import subprocess
import time
import uuid

import httpx

from . import config

FLAGS_FILE = config.DEMO / "flags" / "flags.json"
FLAGS_META = config.DEMO / "flags" / "meta.json"
CHANGES = config.STATE / "changes.jsonl"
ENV_DIR = config.DEMO / "env"


def compose(*args: str, check: bool = True, timeout: int = 120) -> str:
    cmd = ["docker", "compose", "-f", str(config.COMPOSE_FILE), *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed: {proc.stderr.strip()[-500:]}")
    return proc.stdout


_catalog: dict[str, dict] | None = None


def services() -> dict[str, dict]:
    """Service catalog derived from the compose file (labels + env), keyed by name. Empty when the demo
    stack's compose file or Docker isn't available: then there is no live health source, and the tools
    fall back to what the graph knows. Only a successful read is cached, so a later `demo up` is seen."""
    global _catalog
    if _catalog is None:
        try:
            cfg = json.loads(compose("config", "--format", "json"))
        except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired):
            return {}
        _catalog = _catalog_from(cfg)
    return _catalog


def _catalog_from(cfg: dict) -> dict[str, dict]:
    out = {}
    for name, svc in cfg["services"].items():
        labels = svc.get("labels") or {}
        env = svc.get("environment") or {}
        if name == "falkordb" or labels.get("oncall.ignore") == "true":
            continue
        routes = {"work": [c for c in (env.get("CALLS") or "").split(",") if c]}
        for part in filter(None, (env.get("ROUTES") or "").split(";")):
            route, _, deps = part.partition("=")
            routes[route] = [d for d in deps.split(",") if d]
        port = next((p.get("published") for p in svc.get("ports", []) if p.get("target") == 8000), None)
        out[name] = {
            "name": name,
            "team": labels.get("oncall.team", "unowned"),
            "tier": int(labels.get("oncall.tier", "3")),
            "internet_facing": labels.get("oncall.internet_facing") == "true",
            "description": labels.get("oncall.description", ""),
            "calls": sorted({d for deps in routes.values() for d in deps}),
            "optional_calls": [c for c in (env.get("OPTIONAL_CALLS") or "").split(",") if c],
            "port": int(port) if port else None,
        }
    return out


def stats(service: str, window: float = 20.0) -> dict:
    svc = services().get(service)
    if not svc:
        return {"service": service, "status": "unknown", "error": f"no live health source for '{service}'"}
    try:
        r = httpx.get(f"http://{config.STATS_HOST}:{svc['port']}/_stats", params={"window": window}, timeout=2)
        data = r.json()
    except Exception as e:  # container down or not answering
        return {"service": service, "status": "down", "error": f"stats endpoint unreachable ({type(e).__name__})"}
    data["status"] = classify(data)
    return data


def classify(s: dict) -> str:
    if s.get("requests", 0) == 0:
        return "idle"
    if s["error_rate"] >= config.ALERT_ERROR_RATE:
        return "failing"
    if s["error_rate"] > config.HEALTHY_ERROR_RATE or s.get("p95_ms", 0) > config.SLOW_P95_MS:
        return "degraded"
    return "healthy"


def all_stats(window: float = 20.0) -> dict[str, dict]:
    return {name: stats(name, window) for name in services()}


def logs(service: str, tail: int = 60, since: str | None = None) -> str:
    args = ["logs", "--no-log-prefix", "--tail", str(tail)]
    if since:
        args += ["--since", since]
    return compose(*args, service, check=False)


# ---------- change log (deploys + flag flips) ----------


def read_changes() -> list[dict]:
    if not CHANGES.exists():
        return []
    return [json.loads(line) for line in CHANGES.read_text().splitlines() if line.strip()]


def append_change(change: dict) -> dict:
    CHANGES.parent.mkdir(parents=True, exist_ok=True)
    change.setdefault("id", f"chg-{uuid.uuid4().hex[:8]}")
    change.setdefault("at", time.time())
    with CHANGES.open("a") as f:
        f.write(json.dumps(change) + "\n")
    return change


# ---------- deploys ----------


def read_env(service: str) -> dict:
    path = ENV_DIR / f"{service}.env"
    if not path.exists():
        return {}
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)


def write_env(service: str, env: dict) -> None:
    ENV_DIR.mkdir(parents=True, exist_ok=True)
    (ENV_DIR / f"{service}.env").write_text("".join(f"{k}={v}\n" for k, v in env.items()))


def deploy(
    service: str,
    version: str,
    bug: str = "",
    author: str = "ci",
    note: str = "",
    rollback_of: str | None = None,
    at: float | None = None,
) -> dict:
    """Ship `version` of `service`: rewrite its env file and recreate the container."""
    prev = read_env(service).get("VERSION")
    write_env(service, {"VERSION": version, "BUG": bug})
    compose("up", "-d", "--no-deps", service)
    change = {
        "kind": "deploy",
        "service": service,
        "version": version,
        "prev_version": prev,
        "bug": bug,
        "author": author,
        "note": note,
        "rollback_of": rollback_of,
    }
    if at:
        change["at"] = at
    return append_change(change)


def restart(service: str) -> None:
    compose("restart", service)


# ---------- feature flags ----------


def read_flags() -> dict:
    try:
        return json.loads(FLAGS_FILE.read_text())
    except FileNotFoundError:
        return {}


def flags_meta() -> dict:
    return json.loads(FLAGS_META.read_text())


def set_flag(flag: str, value: bool, author: str = "unknown", note: str = "") -> dict:
    flags = read_flags()
    prev = flags.get(flag)
    flags[flag] = value
    FLAGS_FILE.write_text(json.dumps(flags, indent=2))
    return append_change(
        {
            "kind": "flag",
            "flag": flag,
            "from": prev,
            "to": value,
            "author": author,
            "note": note,
            "services": flags_meta().get(flag, {}).get("affects", []),
        }
    )


def chaos_pool_exhaust(service: str = "inventory") -> None:
    port = services()[service]["port"]
    httpx.post(f"http://{config.STATS_HOST}:{port}/_chaos/pool_exhaust", timeout=3)
