"""Web UI + JSON API: the live graph, the agent's investigation path, and demo controls. The same
app serves the MCP tools over Streamable HTTP at /mcp, and runs the loops `rootpath serve` asks for."""

import ipaddress
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from redis.exceptions import RedisError
from starlette.datastructures import Headers

from . import config, daemon, infra, ingest, mcp_server, paths, store


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.mcp = mcp_server.http_app()  # a new one per start: its session manager runs only once
    async with app.state.mcp.router.lifespan_context(app.state.mcp), daemon.background(**app.state.loops):
        yield


class _MCP:
    """ASGI app for /mcp: hands each request to the MCP server of the running app."""

    async def __call__(self, scope, receive, send):
        await scope["app"].state.mcp(scope, receive, send)


def _this_machine(host: str | None) -> bool:
    try:
        return host == "localhost" or ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


def _hostname(netloc: str) -> str | None:
    try:
        return urlsplit(f"//{netloc}").hostname
    except ValueError:  # a malformed IPv6 literal
        return None


def _refusal(scope) -> tuple[int, str] | None:
    """Why to refuse a request that a page of another site can make a browser send, or None. On a
    loopback socket, a Host that names another site is DNS rebinding (its name, resolving to 127.0.0.1);
    a POST from a page that is neither this console nor on this machine is cross-site request forgery."""
    headers = Headers(scope=scope)
    host, origin = headers.get("host", ""), headers.get("origin")
    server = scope.get("server") or ("", 0)
    if _this_machine(server[0]) and not _this_machine(_hostname(host)):
        return 421, "Invalid Host header"
    if scope["method"] not in ("GET", "HEAD") and origin is not None:
        netloc = origin.partition("://")[2]
        if netloc != host and not _this_machine(_hostname(netloc)):
            return 403, "Cross-site request refused"
    return None


class _SameMachine:
    """ASGI middleware: the API changes infrastructure, and `rootpath start` keeps it running in the
    background, so it refuses what other sites' pages can send it (/mcp checks the same for itself)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and (refusal := _refusal(scope)):
            await PlainTextResponse(refusal[1], refusal[0])(scope, receive, send)
        else:
            await self.app(scope, receive, send)


async def _graph_unavailable(request, exc: RedisError) -> JSONResponse:
    """FalkorDB is down, or the graph was never written to: a 503, not a traceback in the server's log
    at every poll of the console."""
    return JSONResponse({"detail": f"graph unavailable: {type(exc).__name__}: {exc}"}, status_code=503)


app = FastAPI(title="Rootpath", lifespan=lifespan, exception_handlers={RedisError: _graph_unavailable})
app.add_middleware(_SameMachine)
app.state.loops = {}  # `rootpath serve` turns on the ingest loop and the alert watcher here
app.add_route("/mcp", _MCP(), include_in_schema=False)
DIST = paths.resources() / "web" / "dist"  # the console, built by `npm run build` in web/
NOT_BUILT = """<!doctype html><meta charset="utf-8"><title>Rootpath</title>
<p>The console is not built yet. Run <code>npm install &amp;&amp; npm run build</code> in <code>web/</code>, then reload.</p>
"""
NOT_SHIPPED = """<!doctype html><meta charset="utf-8"><title>Rootpath</title>
<p>This install of Rootpath has no console: it was installed from source on GitHub, where the console is not
built. The API and MCP at <code>/mcp</code> work. install.sh installs a release, which has the console.</p>
"""
_busy = {"task": None, "since": 0.0}

KEY = {
    "Service": "name",
    "Team": "name",
    "Person": "handle",
    "Flag": "name",
    "Change": "id",
    "Incident": "id",
    "Action": "id",
    "Runbook": "id",
}


def _nid(node: dict) -> str:
    labels = node["_labels"]
    label = "Change" if "Change" in labels else labels[0]
    return f"{label}:{node.get(KEY.get(label, 'id'))}"


@app.get("/", response_class=HTMLResponse)
def index():
    console = DIST / "console.html"
    if console.exists():
        return FileResponse(console)
    return NOT_SHIPPED if paths.bundled() else NOT_BUILT


# check_dir=False: a build made while the server runs is picked up without a restart.
app.mount("/assets", StaticFiles(directory=DIST / "assets", check_dir=False), name="assets")


@app.get("/api/graph")
def graph(hours: float = 3.0):
    since = time.time() - hours * 3600
    rows = store.read(
        """MATCH (n) WHERE (n:Service OR n:Team OR n:Flag OR n:Runbook)
              OR (n:Change AND (n.at >= $since OR (n)<-[:ROOT_CAUSE]-(:Incident)))
              OR (n:Incident) OR (n:Action AND n.dry_run = false)
              OR (n:Person AND (n)-[:ON_CALL_FOR]->())
           RETURN n""",
        {"since": since},
        timeout_ms=5000,
    )
    nodes = {}
    for r in rows:
        n = r["n"]
        nid = _nid(n)
        props = {k: v for k, v in n.items() if not k.startswith("_") and k not in ("body", "summary", "symptom")}
        label = nid.split(":", 1)[0]
        name = (
            n.get("name")
            or n.get("handle")
            or (f"{n.get('service')} v{n.get('version')}" if n.get("kind") == "deploy" else None)
            or (f"{n.get('flag')} → {n.get('to')}" if n.get("kind") == "flag" else None)
            or (f"{n.get('kind')} {n.get('target')}" if label == "Action" else None)
            or n.get("id")
        )
        nodes[nid] = {"id": nid, "type": label, "name": name, "props": props}
    links = store.read(
        """MATCH (a)-[r]->(b) WHERE type(r) IN ['CALLS','OWNS','ON_CALL_FOR','FOR','AFFECTS','CHANGED','TOGGLED',
                                               'IMPACTED','ROOT_CAUSE','RESOLVED_BY','ON','REVERTS','ESCALATES_TO']
           RETURN a, type(r) AS type, b, r.optional AS optional""",
        timeout_ms=5000,
    )
    out_links = []
    for link in links:
        s, t = _nid(link["a"]), _nid(link["b"])
        if s in nodes and t in nodes:
            out_links.append({"source": s, "target": t, "type": link["type"], "optional": bool(link["optional"])})
    return {"nodes": list(nodes.values()), "links": out_links}


@app.get("/api/investigation")
def investigation(id: str | None = None):
    if id:
        v = store.read("MATCH (v:Investigation {id: $id}) RETURN v", {"id": id})
    else:
        v = store.read(
            "MATCH (v:Investigation) WHERE v.steps > 0 AND NOT v.id STARTS WITH 'cli-' "
            "RETURN v ORDER BY v.started_at DESC LIMIT 1"
        )
    if not v:
        return {"investigation": None, "steps": []}
    inv = {k: val for k, val in v[0]["v"].items() if not k.startswith("_")}
    steps = store.read(
        """MATCH (v:Investigation {id: $id})-[s:STEP]->(x)
           RETURN s.n AS n, s.tool AS tool, s.note AS note, s.at AS at, collect(x) AS targets ORDER BY n""",
        {"id": inv["id"]},
    )
    for s in steps:
        s["targets"] = [_nid(t) for t in s["targets"] if "Investigation" not in t["_labels"]]
    return {"investigation": inv, "steps": steps}


@app.get("/api/investigations")
def investigations():
    return store.read("""MATCH (v:Investigation) WHERE v.steps > 0 AND NOT v.id STARTS WITH 'cli-'
                         OPTIONAL MATCH (v)-[:FOR]->(i:Incident)
                         RETURN v.id AS id, v.mode AS mode, v.agent AS agent, v.started_at AS started_at,
                                v.steps AS steps, v.duration_s AS duration_s, v.cost_usd AS cost_usd, i.id AS incident
                         ORDER BY started_at DESC LIMIT 20""")


@app.get("/api/health")
def health():
    try:
        services = infra.all_stats(window=10)
    except (OSError, RuntimeError, subprocess.SubprocessError) as e:  # no demo stack here: no compose file or Docker
        raise HTTPException(503, f"services unavailable: {type(e).__name__}: {e}") from None
    return {"services": services, "busy": _busy["task"], "allow_actions": config.ALLOW_ACTIONS}


def _background(name: str, fn) -> dict:
    if _busy["task"]:
        raise HTTPException(409, f"busy: {_busy['task']}")
    _busy.update(task=name, since=time.time())

    def run():
        try:
            fn()
        finally:
            _busy.update(task=None)

    threading.Thread(target=run, daemon=True).start()
    return {"started": name}


@app.post("/api/chaos/{scenario}")
def chaos(scenario: str):
    from .cli import SCENARIOS, inject

    if scenario not in SCENARIOS:
        raise HTTPException(404, "unknown scenario")
    return _background(f"injecting {scenario}", lambda: (inject(scenario), ingest.sync_changes()))


@app.post("/api/reset")
def reset(hard: bool = False):
    from .cli import reset as do_reset

    if hard:
        return _background("resetting demo (fresh graph)", do_reset)
    return _background("restoring healthy state", lambda: do_reset(soft=True))


@app.post("/api/agent")
def agent(mode: str = "graph"):
    from .responder import detect, run_agent

    hit = detect()
    if not hit:
        raise HTTPException(400, "no failing service right now")
    return _background(
        f"agent investigating {hit[0]} ({mode})", lambda: run_agent(hit[0], hit[1], mode=mode, quiet=True)
    )


@app.get("/api/scenarios")
def scenarios():
    from .cli import SCENARIOS

    return SCENARIOS


@app.get("/api/incidents")
def incidents():
    rows = store.read(
        """MATCH (i:Incident)
           OPTIONAL MATCH (i)-[:ROOT_CAUSE]->(rc)
           OPTIONAL MATCH (i)-[:RESOLVED_BY]->(a:Action)
           RETURN i.id AS id, i.title AS title, i.status AS status, i.severity AS severity, i.opened_at AS opened_at,
                  i.mttr_min AS mttr_min, i.source AS source, i.summary AS summary,
                  collect(DISTINCT coalesce(rc.name, rc.version, rc.flag)) AS root_cause,
                  collect(DISTINCT {kind: a.kind, target: a.target, by: a.by}) AS fixed_by
           ORDER BY opened_at DESC"""
    )
    for r in rows:
        r["fixed_by"] = [a for a in r["fixed_by"] if a.get("kind")]
        r["root_cause"] = [x for x in r["root_cause"] if x]
    return rows


@app.get("/api/changes")
def changes(hours: float = 24.0):
    rows = store.read(
        """MATCH (c:Change) WHERE c.at >= $since
           OPTIONAL MATCH (c)-[:CHANGED]->(s:Service)
           OPTIONAL MATCH (c)-[:TOGGLED]->(:Flag)-[:AFFECTS]->(fs:Service)
           RETURN c, coalesce(s.name, fs.name) AS service ORDER BY c.at DESC LIMIT 40""",
        {"since": time.time() - hours * 3600},
    )
    return [{**{k: v for k, v in r["c"].items() if not k.startswith("_")}, "service": r["service"]} for r in rows]
