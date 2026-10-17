"""Web UI + JSON API: the live graph, the agent's investigation path, and demo controls."""
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from . import config, infra, ingest, store

app = FastAPI(title="On-Call Graph")
UI = Path(__file__).resolve().parent.parent / "ui"
_busy = {"task": None, "since": 0.0}

KEY = {"Service": "name", "Team": "name", "Person": "handle", "Flag": "name", "Change": "id",
       "Incident": "id", "Action": "id", "Runbook": "id"}


def _nid(node: dict) -> str:
    labels = node["_labels"]
    label = "Change" if "Change" in labels else labels[0]
    return f"{label}:{node.get(KEY.get(label, 'id'))}"


@app.get("/")
def index():
    return FileResponse(UI / "index.html")


@app.get("/api/graph")
def graph(hours: float = 3.0):
    since = time.time() - hours * 3600
    rows = store.read(
        """MATCH (n) WHERE (n:Service OR n:Team OR n:Flag OR n:Runbook)
              OR (n:Change AND (n.at >= $since OR (n)<-[:ROOT_CAUSE]-(:Incident)))
              OR (n:Incident) OR (n:Action AND n.dry_run = false)
              OR (n:Person AND (n)-[:ON_CALL_FOR]->())
           RETURN n""", {"since": since}, timeout_ms=5000)
    nodes = {}
    for r in rows:
        n = r["n"]
        nid = _nid(n)
        props = {k: v for k, v in n.items() if not k.startswith("_") and k not in ("body", "summary", "symptom")}
        label = nid.split(":", 1)[0]
        name = (n.get("name") or n.get("handle") or
                (f"{n.get('service')} v{n.get('version')}" if n.get("kind") == "deploy" else None) or
                (f"{n.get('flag')} → {n.get('to')}" if n.get("kind") == "flag" else None) or
                (f"{n.get('kind')} {n.get('target')}" if label == "Action" else None) or n.get("id"))
        nodes[nid] = {"id": nid, "type": label, "name": name, "props": props}
    links = store.read(
        """MATCH (a)-[r]->(b) WHERE type(r) IN ['CALLS','OWNS','ON_CALL_FOR','FOR','AFFECTS','CHANGED','TOGGLED',
                                               'IMPACTED','ROOT_CAUSE','RESOLVED_BY','ON','REVERTS','ESCALATES_TO']
           RETURN a, type(r) AS type, b, r.optional AS optional""", timeout_ms=5000)
    out_links = []
    for l in links:
        s, t = _nid(l["a"]), _nid(l["b"])
        if s in nodes and t in nodes:
            out_links.append({"source": s, "target": t, "type": l["type"], "optional": bool(l["optional"])})
    return {"nodes": list(nodes.values()), "links": out_links}


@app.get("/api/investigation")
def investigation(id: str | None = None):
    if id:
        v = store.read("MATCH (v:Investigation {id: $id}) RETURN v", {"id": id})
    else:
        v = store.read("MATCH (v:Investigation) WHERE v.steps > 0 RETURN v ORDER BY v.started_at DESC LIMIT 1")
    if not v:
        return {"investigation": None, "steps": []}
    inv = {k: val for k, val in v[0]["v"].items() if not k.startswith("_")}
    steps = store.read(
        """MATCH (v:Investigation {id: $id})-[s:STEP]->(x)
           RETURN s.n AS n, s.tool AS tool, s.note AS note, s.at AS at, collect(x) AS targets ORDER BY n""",
        {"id": inv["id"]})
    for s in steps:
        s["targets"] = [_nid(t) for t in s["targets"] if "Investigation" not in t["_labels"]]
    return {"investigation": inv, "steps": steps}


@app.get("/api/investigations")
def investigations():
    return store.read("""MATCH (v:Investigation) WHERE v.steps > 0
                         OPTIONAL MATCH (v)-[:FOR]->(i:Incident)
                         RETURN v.id AS id, v.mode AS mode, v.agent AS agent, v.started_at AS started_at,
                                v.steps AS steps, v.duration_s AS duration_s, v.cost_usd AS cost_usd, i.id AS incident
                         ORDER BY started_at DESC LIMIT 20""")


@app.get("/api/health")
def health():
    return {"services": infra.all_stats(window=10), "busy": _busy["task"], "allow_actions": config.ALLOW_ACTIONS}


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
    from .cli import SCENARIOS, chaos as inject
    if scenario not in SCENARIOS:
        raise HTTPException(404, "unknown scenario")
    return _background(f"injecting {scenario}", lambda: (inject(type("A", (), {"scenario": scenario})), ingest.sync_changes()))


@app.post("/api/reset")
def reset():
    from .cli import demo_reset
    return _background("resetting demo", lambda: demo_reset(None))


@app.post("/api/agent")
def agent(mode: str = "graph"):
    from .responder import detect, run_agent
    hit = detect()
    if not hit:
        raise HTTPException(400, "no failing service right now")
    return _background(f"agent investigating {hit[0]} ({mode})", lambda: run_agent(hit[0], hit[1], mode=mode, quiet=True))
