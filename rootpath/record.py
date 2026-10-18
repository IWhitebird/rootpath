"""Record a real incident end to end (live health, graph changes, every agent step) into a JSON
file that the static website replays in the browser, without Docker or an API key."""

import json
import threading
import time
from pathlib import Path

from . import config, infra, store
from .cli import SCENARIOS, inject, reset
from .responder import detect, run_agent
from .web import graph as graph_snapshot

REPLAYS = config.ROOT / "web" / "public" / "data" / "replays"


def record(scenario: str, out: str | None = None, mode: str = "graph") -> None:
    """Writes to `out`, by default web/public/data/replays/<scenario>.json."""
    path = Path(out) if out else REPLAYS / f"{scenario}.json"
    reset()
    for _ in range(20):
        if detect() is None:
            break
        time.sleep(2)
    time.sleep(4)
    t0 = time.time()
    rel = lambda: round(time.time() - t0, 2)  # noqa: E731
    nodes, links, health, events = {}, {}, [], []
    state = {"agent": None, "inv": None, "done": False}

    def snap_graph():
        g = graph_snapshot(hours=3)
        for n in g["nodes"]:
            if n["id"] not in nodes:
                nodes[n["id"]] = {**n, "t": rel()}
            else:
                nodes[n["id"]]["props"] = n["props"]
        for link in g["links"]:
            key = f"{link['source']}|{link['type']}|{link['target']}"
            links.setdefault(key, {**link, "t": rel()})

    def snap_health():
        s = infra.all_stats(window=8)
        health.append(
            {"t": rel(), "s": {n: [v["status"], v.get("error_rate", 1.0), v.get("p95_ms", 0)] for n, v in s.items()}}
        )

    snap_graph()
    snap_health()
    events.append({"t": rel(), "kind": "start", "text": "healthy baseline"})
    time.sleep(3)
    snap_health()
    events.append({"t": rel(), "kind": "inject", "text": SCENARIOS[scenario]})
    inject(scenario)

    strikes, alert = 0, None
    while not alert and rel() < 120:
        snap_health()
        hit = detect()
        strikes = strikes + 1 if hit else 0
        if strikes >= 2:
            alert = hit
        time.sleep(2.5)
    if not alert:
        raise SystemExit("alert never fired")
    snap_graph()
    events.append(
        {
            "t": rel(),
            "kind": "alert",
            "text": f"ALERT {alert[0]} error rate {alert[1].get('error_rate', 1):.0%}, p95 {alert[1].get('p95_ms', 0):.0f}ms",
        }
    )

    def agent():
        state["agent"] = run_agent(alert[0], alert[1], mode=mode, quiet=True)
        state["done"] = True

    threading.Thread(target=agent, daemon=True).start()
    agent_t = rel()
    events.append({"t": agent_t, "kind": "agent", "text": f"headless agent started ({mode} mode)"})
    finished_at = None
    while finished_at is None or rel() - finished_at < 12:
        snap_health()
        snap_graph()
        if state["done"] and finished_at is None:
            finished_at = rel()
            events.append({"t": finished_at, "kind": "done", "text": "agent finished"})
        time.sleep(1.2)

    summary = state["agent"]
    inv = summary["investigation"]
    steps = store.read(
        """MATCH (v:Investigation {id: $id})-[s:STEP]->(x)
           RETURN s.n AS n, s.tool AS tool, s.note AS note, s.at AS at, collect(x) AS targets ORDER BY n""",
        {"id": inv},
    )
    from .web import _nid

    step_rows = [
        {
            "t": round(s["at"] - t0, 2),
            "n": s["n"],
            "tool": s["tool"],
            "note": s["note"],
            "targets": [_nid(x) for x in s["targets"] if "Investigation" not in x["_labels"]],
        }
        for s in steps
    ]
    data = {
        "scenario": scenario,
        "description": SCENARIOS[scenario],
        "mode": mode,
        "recorded_at": time.strftime("%Y-%m-%d %H:%M"),
        "duration": rel(),
        "agent": {
            k: summary[k] for k in ("duration_s", "tool_calls", "cost_usd", "input_tokens", "output_tokens", "report")
        }
        | {"start_t": agent_t, "end_t": finished_at},
        "events": events,
        "steps": step_rows,
        "health": health,
        "nodes": list(nodes.values()),
        "links": list(links.values()),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, separators=(",", ":"), default=str))
    print(f"recorded {scenario}: {len(step_rows)} steps, {len(health)} health frames, {len(nodes)} nodes -> {path}")
