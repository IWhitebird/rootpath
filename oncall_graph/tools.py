"""The on-call agent's tools. Plain functions returning JSON-able dicts, shared by the
MCP server, the web UI and the benchmark.

Reasoning tools query FalkorDB (multi-hop Cypher + graph algorithms). Action tools change
real infrastructure, default to dry-run, and write (:Action)-[:BECAUSE]->(:Evidence) to the
graph so every change the agent makes is explainable later. Every call is also recorded as
a STEP of the current (:Investigation), which the UI replays."""
import json
import os
import re
import time
import uuid
from collections import Counter

import httpx

from . import config, infra, ingest, store

KEYS = {"Service": "name", "Team": "name", "Person": "handle", "Flag": "name", "Change": "id",
        "Deploy": "id", "FlagChange": "id", "Incident": "id", "Action": "id", "Runbook": "id"}
UNHEALTHY = {"failing", "degraded", "down"}
MAX_HOPS = 6

# ---------------------------------------------------------------- investigation trace

_inv = {"id": os.environ.get("ONCALL_INVESTIGATION_ID"), "step": 0, "created": False}


def investigation_id() -> str:
    if not _inv["id"]:
        _inv["id"] = f"inv-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
    if not _inv["created"]:
        store.query(
            """MERGE (v:Investigation {id: $id})
               ON CREATE SET v.started_at = $now, v.agent = $agent, v.mode = $mode, v.steps = 0""",
            {"id": _inv["id"], "now": time.time(), "agent": os.environ.get("ONCALL_AGENT", "interactive"),
             "mode": os.environ.get("ONCALL_MODE", "graph")},
        )
        _inv["created"] = True
    return _inv["id"]


def _trace(tool: str, note: str, touched: list[tuple[str, str]] = ()) -> None:
    """Record one agent step and the graph nodes it looked at."""
    try:
        inv = investigation_id()
        _inv["step"] += 1
        n, now = _inv["step"], time.time()
        store.query("MATCH (v:Investigation {id: $id}) SET v.steps = $n, v.updated_at = $now",
                    {"id": inv, "n": n, "now": now})
        targets = list(dict.fromkeys(touched)) or [("Investigation", inv)]
        for label, key in targets:
            prop = KEYS.get(label, "id")
            store.query(
                f"""MATCH (v:Investigation {{id: $inv}}), (x:{label} {{{prop}: $key}})
                    CREATE (v)-[:STEP {{n: $n, tool: $tool, note: $note, at: $now}}]->(x)""",
                {"inv": inv, "key": key, "n": n, "tool": tool, "note": note[:300], "now": now},
            )
    except Exception:
        pass  # tracing must never break a tool


def _ago(ts: float) -> str:
    s = max(0, time.time() - ts)
    if s < 120:
        return f"{s:.0f}s ago"
    if s < 7200:
        return f"{s / 60:.0f} min ago"
    if s < 172800:
        return f"{s / 3600:.1f} h ago"
    return f"{s / 86400:.0f} days ago"


def _fresh() -> dict:
    """Pull the latest changes and live health into the graph before reasoning over it."""
    ingest.sync_changes()
    return ingest.sync_health()


def _change_view(c: dict) -> dict:
    if c.get("kind") == "flag" or "FlagChange" in c.get("_labels", []):
        return {"id": c["id"], "kind": "flag", "flag": c.get("flag"), "from": c.get("from"), "to": c.get("to"),
                "author": c.get("author"), "when": _ago(c["at"]), "at": c.get("iso"), "note": c.get("note") or None}
    return {"id": c["id"], "kind": "deploy", "service": c.get("service"), "version": c.get("version"),
            "prev_version": c.get("prev_version"), "author": c.get("author"), "when": _ago(c["at"]),
            "at": c.get("iso"), "note": c.get("note") or None}


def _health_view(s: dict) -> dict:
    keys = ("status", "version", "requests", "error_rate", "errors_originated_here", "errors_from_dependencies",
            "p95_ms", "error")
    if config.TELEMETRY == "basic":
        keys = tuple(k for k in keys if not k.startswith("errors_"))
    return {k: s[k] for k in keys if k in s}


# ---------------------------------------------------------------- read tools

def get_service_health(service: str | None = None) -> dict:
    """Live health from the services' stats endpoints (last 20s)."""
    snapshot = infra.all_stats() if service is None else {service: infra.stats(service)}
    order = {"down": 0, "failing": 1, "degraded": 2, "healthy": 3, "idle": 4, "unknown": 5}
    rows = sorted(snapshot.items(), key=lambda kv: order.get(kv[1]["status"], 9))
    _trace("get_service_health", f"checked health of {service or 'all services'}",
           [("Service", n) for n, s in rows if s["status"] in UNHEALTHY and n in infra.services()])
    return {"window": "last 20s", "services": {n: _health_view(s) for n, s in rows}}


def get_blast_radius(service: str) -> dict:
    """Who is affected if `service` misbehaves (upstream callers) and what it depends on (downstream)."""
    upstream = store.read(
        """MATCH p = (u:Service)-[:CALLS*1..6]->(s:Service {name: $s})
           WITH u, min(length(p)) AS hops
           MATCH (t:Team)-[:OWNS]->(u)
           RETURN u.name AS service, hops, u.tier AS tier, u.internet_facing AS internet_facing,
                  u.status AS status, t.name AS team ORDER BY hops, service""",
        {"s": service},
    )
    downstream = store.read(
        """MATCH p = (s:Service {name: $s})-[:CALLS*1..6]->(d:Service)
           WITH d, min(length(p)) AS hops
           MATCH (t:Team)-[:OWNS]->(d)
           RETURN d.name AS service, hops, d.tier AS tier, d.status AS status, t.name AS team ORDER BY hops, service""",
        {"s": service},
    )
    teams = sorted({r["team"] for r in upstream} | {r["team"] for r in downstream})
    user_facing = [r["service"] for r in upstream if r["internet_facing"]]
    _trace("get_blast_radius", f"blast radius of {service}: {len(upstream)} upstream, {len(downstream)} downstream",
           [("Service", service)] + [("Service", r["service"]) for r in upstream + downstream])
    return {
        "service": service,
        "affected_if_it_fails (upstream callers)": upstream,
        "depends_on (downstream, possible causes)": downstream,
        "user_facing_impact": bool(user_facing) or bool(infra.services().get(service, {}).get("internet_facing")),
        "teams_involved": teams,
    }


def rank_suspects(symptom_service: str, window_minutes: int = 60) -> dict:
    """Rank root-cause suspects for a symptom by walking the dependency graph downstream from it,
    combining live health, recent changes (deploys + flag flips), and betweenness centrality."""
    snapshot = _fresh()
    since = time.time() - window_minutes * 60
    candidates = store.read(
        """MATCH p = (s:Service {name: $s})-[:CALLS*0..6]->(d:Service)
           RETURN d.name AS service, min(length(p)) AS hops""",
        {"s": symptom_service},
    )
    if not candidates:
        return {"error": f"unknown service '{symptom_service}'", "known": sorted(infra.services())}
    names = [c["service"] for c in candidates]
    deps = {r["service"]: r["deps"] for r in store.read(
        """MATCH (a:Service)-[r:CALLS]->(b:Service) WHERE a.name IN $names AND coalesce(r.optional, false) = false
           RETURN a.name AS service, collect(b.name) AS deps""", {"names": names})}
    deploys = store.read(
        """MATCH (c:Deploy)-[:CHANGED]->(d:Service) WHERE d.name IN $names AND c.at >= $since
           RETURN d.name AS service, c AS change ORDER BY c.at DESC""", {"names": names, "since": since})
    flips = store.read(
        """MATCH (x:FlagChange)-[:TOGGLED]->(f:Flag)-[:AFFECTS]->(d:Service) WHERE d.name IN $names AND x.at >= $since
           RETURN d.name AS service, x AS change, f.enabled AS enabled_now ORDER BY x.at DESC""",
        {"names": names, "since": since})
    centrality = {r["service"]: r["score"] for r in store.read(
        """CALL algo.betweenness({nodeLabels: ['Service'], relationshipTypes: ['CALLS']}) YIELD node, score
           RETURN node.name AS service, score""")}
    top_c = max(centrality.values() or [1]) or 1

    changes: dict[str, list] = {}
    for r in deploys + flips:
        changes.setdefault(r["service"], []).append(r["change"])

    suspects = []
    for c in candidates:
        name, st = c["service"], snapshot.get(c["service"], {"status": "unknown"})
        unhealthy = st["status"] in UNHEALTHY
        deepest = unhealthy and all(snapshot.get(d, {}).get("status") not in UNHEALTHY for d in deps.get(name, []))
        score, reasons = 0.0, []
        if unhealthy:
            if deepest:
                score += 0.4
                reasons.append("deepest unhealthy service on this path (its own dependencies are healthy)")
            else:
                bad = [d for d in deps.get(name, []) if snapshot.get(d, {}).get("status") in UNHEALTHY]
                reasons.append(f"unhealthy, but its dependencies {bad} are unhealthy too (likely propagated)")
        if st["status"] == "down":
            score += 0.3
            reasons.append("not responding at all")
        n, own = st.get("requests", 0), st.get("errors_originated_here", 0)
        if config.TELEMETRY == "basic":
            own = 0  # no tracing: we don't know where errors originate, only the topology tells us
        if n and own / n > 0.05:
            score += 0.2 * min(1.0, 2 * own / n)
            reasons.append(f"{own}/{n} requests failed inside this service itself")
        if st.get("p95_ms", 0) > config.SLOW_P95_MS:
            score += 0.15 if (deepest or not unhealthy) else 0.0
            reasons.append(f"p95 latency {st['p95_ms']:.0f}ms (threshold {config.SLOW_P95_MS:.0f}ms)"
                           + ("" if deepest or not unhealthy else ", inherited from its dependencies"))
        recent = changes.get(name, [])
        if recent:
            newest = max(recent, key=lambda x: x["at"])
            freshness = max(0.0, 1 - (time.time() - newest["at"]) / (window_minutes * 60))
            weight = 0.35 if deepest or st["status"] == "down" else 0.1 if unhealthy else 0.05
            score += weight * (0.5 + 0.5 * freshness)
            for ch in recent:
                v = _change_view(ch)
                what = (f"deployed v{v['version']} (from v{v['prev_version']})" if v["kind"] == "deploy"
                        else f"flag '{v['flag']}' set {v['from']} -> {v['to']}")
                reasons.append(f"{what} {v['when']} by {v['author']}" + (
                    "" if deepest else " (but its errors are inherited)" if unhealthy else " (but service looks healthy)"))
        score += 0.05 * centrality.get(name, 0) / top_c
        suspects.append({
            "service": name, "score": round(score, 3), "hops": c["hops"], "status": st["status"],
            "error_rate": st.get("error_rate"),
            **({} if config.TELEMETRY == "basic" else {"errors_originated_here": st.get("errors_originated_here")}),
            "p95_ms": st.get("p95_ms"), "recent_changes": [_change_line(x) for x in recent],
            "betweenness": centrality.get(name, 0), "reasons": reasons or ["healthy, no recent changes"],
            "_changes": recent,
        })
    suspects.sort(key=lambda s: -s["score"])
    if all(s["status"] not in UNHEALTHY for s in suspects):
        _trace("rank_suspects", f"everything downstream of {symptom_service} is healthy", [("Service", symptom_service)])
        return {"symptom_service": symptom_service, "top_suspect": None,
                "note": "every service on this dependency path is healthy right now; nothing to rank",
                "services_checked": [s["service"] for s in suspects]}

    outside = store.read(
        """MATCH (c:Change) WHERE c.at >= $since
           OPTIONAL MATCH (c)-[:CHANGED]->(s:Service)
           OPTIONAL MATCH (c)-[:TOGGLED]->(:Flag)-[:AFFECTS]->(fs:Service)
           WITH c, coalesce(s.name, fs.name) AS target WHERE NOT target IN $names
           RETURN c AS change, target""", {"since": since, "names": names})
    top = suspects[0]
    path = _path(symptom_service, top["service"])["path"] if top["service"] != symptom_service else [symptom_service]

    touched = [("Service", symptom_service)] + [("Service", s["service"]) for s in suspects[:3]]
    touched += [("Change", ch["id"]) for s in suspects[:2] for ch in s["_changes"]]
    _trace("rank_suspects", f"top suspect {top['service']} (score {top['score']}) via path {' -> '.join(path)}", touched)
    for s in suspects:
        del s["_changes"]
    detailed = [s for s in suspects if s["score"] >= 0.1][:3] or suspects[:1]
    rest = [{"service": s["service"], "score": s["score"], "status": s["status"], "why_low": s["reasons"][0]}
            for s in suspects if s not in detailed]
    return {
        "symptom_service": symptom_service,
        "top_suspect": top["service"],
        "path_from_symptom": path,
        "suspects": detailed,
        "other_services_on_the_path": rest,
        "ruled_out_changes (not in the dependency graph of the symptom)": [
            f"{_change_line(r['change'])} -> {r['target']}" for r in outside],
        "scoring": "deepest unhealthy +0.4, down +0.3, own errors +0.2, slow +0.15 and fresh change +0.35 (only where the failure starts)",
    }


def _change_line(c: dict) -> str:
    v = _change_view(c)
    if v["kind"] == "deploy":
        return f"{v['id']}: deploy {v['service']} v{v['prev_version']} -> v{v['version']} by {v['author']} {v['when']}" + (
            f" ({v['note']})" if v.get("note") else "")
    return f"{v['id']}: flag {v['flag']} {v['from']} -> {v['to']} by {v['author']} {v['when']}" + (
        f" ({v['note']})" if v.get("note") else "")


def recent_changes(service: str | None = None, minutes: int = 120, include_dependencies: bool = True) -> dict:
    """Deploys and feature-flag flips in the time window, scoped through the graph to `service`
    (plus everything it depends on, and flags that affect those services)."""
    ingest.sync_changes()
    since = time.time() - minutes * 60
    if service:
        hops = "*0..6" if include_dependencies else "*0..0"
        rows = store.read(
            f"""MATCH (s:Service {{name: $s}})-[:CALLS{hops}]->(d:Service)
                WITH DISTINCT d
                OPTIONAL MATCH (c1:Deploy)-[:CHANGED]->(d) WHERE c1.at >= $since
                OPTIONAL MATCH (c2:FlagChange)-[:TOGGLED]->(:Flag)-[:AFFECTS]->(d) WHERE c2.at >= $since
                WITH d, collect(DISTINCT c1) + collect(DISTINCT c2) AS cs
                UNWIND cs AS c
                RETURN DISTINCT c AS change, d.name AS service ORDER BY c.at DESC""",
            {"s": service, "since": since})
    else:
        rows = store.read(
            """MATCH (c:Change) WHERE c.at >= $since
               OPTIONAL MATCH (c)-[:CHANGED]->(s:Service)
               OPTIONAL MATCH (c)-[:TOGGLED]->(:Flag)-[:AFFECTS]->(fs:Service)
               RETURN c AS change, coalesce(s.name, fs.name) AS service ORDER BY c.at DESC""", {"since": since})
    out = [{**_change_view(r["change"]), "affects_service": r["service"]} for r in rows]
    _trace("recent_changes", f"{len(out)} changes in last {minutes} min around {service or 'all services'}",
           [("Change", c["id"]) for c in out[:6]] + ([("Service", service)] if service else []))
    return {"scope": service or "all services", "minutes": minutes, "changes": out}


def trace_dependency_path(from_service: str, to_service: str) -> dict:
    """Shortest call path between two services (algo.SPpaths), with live health per hop."""
    out = _path(from_service, to_service)
    if out["path"]:
        _trace("trace_dependency_path", " -> ".join(out["path"]), [("Service", n) for n in out["path"]])
    return out


def _path(from_service: str, to_service: str) -> dict:
    rows = store.read(
        """MATCH (a:Service {name: $a}), (b:Service {name: $b})
           CALL algo.SPpaths({sourceNode: a, targetNode: b, relTypes: ['CALLS'], pathCount: 1})
           YIELD path RETURN [n IN nodes(path) | n.name] AS names,
                             [n IN nodes(path) | coalesce(n.status, 'unknown')] AS statuses""",
        {"a": from_service, "b": to_service})
    if not rows:
        return {"path": [], "note": f"no call path from {from_service} to {to_service}"}
    names, statuses = rows[0]["names"], rows[0]["statuses"]
    return {"path": names, "hops": [{"service": n, "status": s} for n, s in zip(names, statuses)]}


_TS = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]+\+00:00 ")


def get_logs(service: str, lines: int = 200, contains: str | None = None) -> dict:
    """Recent log entries, de-duplicated: distinct messages with counts, plus the latest entries."""
    raw = infra.logs(service, tail=lines)
    entries, current = [], []
    for line in raw.splitlines():
        if _TS.match(line) and current:
            entries.append("\n".join(current))
            current = []
        current.append(line)
    if current:
        entries.append("\n".join(current))
    if contains:
        entries = [e for e in entries if contains.lower() in e.lower()]
    counts = Counter(_TS.sub("", e) for e in entries)
    _trace("get_logs", f"read {len(entries)} log entries of {service}", [("Service", service)])
    return {
        "service": service,
        "entries_scanned": len(entries),
        "distinct_messages": [{"count": n, "message": m[:900]} for m, n in counts.most_common(6)],
        "latest_timestamps": [e.split(" ", 1)[0] + " " + e.split("\n", 1)[0].split("] ", 1)[-1][:160] for e in entries[-3:]],
    }


def get_owner(service: str) -> dict:
    """Owning team, who is on call, escalation path and runbooks for a service."""
    rows = store.read(
        """MATCH (t:Team)-[:OWNS]->(s:Service {name: $s})
           OPTIONAL MATCH (p:Person)-[:ON_CALL_FOR]->(t)
           OPTIONAL MATCH (t)-[:ESCALATES_TO]->(e:Team)
           OPTIONAL MATCH (ep:Person)-[:ON_CALL_FOR]->(e)
           OPTIONAL MATCH (r:Runbook)-[:FOR]->(s)
           RETURN t.name AS team, t.channel AS channel, p.handle AS on_call, e.name AS escalates_to,
                  ep.handle AS escalation_on_call, collect(DISTINCT {id: r.id, title: r.title, steps: r.body}) AS runbooks""",
        {"s": service})
    if not rows:
        return {"error": f"no owner found for '{service}'"}
    row = rows[0]
    row["runbooks"] = [r for r in row["runbooks"] if r.get("id")]
    _trace("get_owner", f"{service} is owned by {row['team']} (on call: {row['on_call']})",
           [("Service", service), ("Team", row["team"])] + ([("Person", row["on_call"])] if row["on_call"] else [])
           + [("Runbook", r["id"]) for r in row["runbooks"]])
    return {"service": service, **row}


def find_similar_incidents(symptom: str, service: str | None = None, limit: int = 3) -> dict:
    """Search incident memory: full-text match on past symptoms, boosted when the past incident
    involved the same service. Returns root causes and what fixed them."""
    # RediSearch syntax: '-' negates and '.' separates, so query on plain alphanumeric words only.
    words = list(dict.fromkeys(w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9]{3,}", symptom)))[:12]
    hits = {}
    if words:
        for r in store.read("CALL db.idx.fulltext.queryNodes('Incident', $q) YIELD node, score RETURN node.id AS id, score",
                            {"q": "|".join(words)}):
            hits[r["id"]] = r["score"]
    if service:
        for r in store.read(
                """MATCH (i:Incident)-[:IMPACTED|ROOT_CAUSE]->(s:Service {name: $s}) RETURN DISTINCT i.id AS id""",
                {"s": service}):
            hits[r["id"]] = hits.get(r["id"], 0) + 0.5
    ids = [i for i, _ in sorted(hits.items(), key=lambda kv: -kv[1])][:limit]
    rows = store.read(
        """MATCH (i:Incident) WHERE i.id IN $ids AND i.status = 'resolved'
           OPTIONAL MATCH (i)-[:ROOT_CAUSE]->(rc)
           OPTIONAL MATCH (i)-[:RESOLVED_BY]->(a:Action)
           RETURN i.id AS id, i.title AS title, i.summary AS summary, i.mttr_min AS mttr_min, i.opened_iso AS opened,
                  collect(DISTINCT rc) AS root_causes, collect(DISTINCT {kind: a.kind, target: a.target, by: a.by}) AS fixed_by""",
        {"ids": ids}) if ids else []
    out = []
    for r in sorted(rows, key=lambda r: -hits.get(r["id"], 0)):
        causes = []
        for rc in r["root_causes"]:
            if "Change" in rc.get("_labels", []):
                causes.append(_change_view(rc))
            elif rc.get("name"):
                causes.append({"service": rc["name"]})
        out.append({"incident": r["id"], "title": r["title"], "when": r["opened"], "match_score": round(hits[r["id"]], 2),
                    "root_cause": causes, "fixed_by": [a for a in r["fixed_by"] if a.get("kind")],
                    "mttr_min": r["mttr_min"], "summary": r["summary"]})
    _trace("find_similar_incidents", f"{len(out)} similar past incidents" + (f", best {out[0]['incident']}" if out else ""),
           [("Incident", o["incident"]) for o in out])
    return {"query": symptom, "matches": out}


def query_graph(cypher: str) -> dict:
    """Escape hatch: run your own read-only Cypher (RO_QUERY, 2s timeout, max 100 rows)."""
    try:
        rows = store.safe_read(cypher)
    except Exception as e:
        return {"error": str(e)[:500]}
    _trace("query_graph", cypher[:200])
    return {"rows": rows}


# ---------------------------------------------------------------- incident lifecycle

def open_incident(title: str, symptom: str, impacted_services: list[str], severity: str = "SEV2") -> dict:
    """Open an incident in the graph (or return the one already open for these services)."""
    existing = store.read("MATCH (i:Incident {status: 'open'}) RETURN i.id AS id, i.title AS title ORDER BY i.opened_at DESC LIMIT 1")
    if existing:
        inc_id = existing[0]["id"]
    else:
        n = store.read("MATCH (i:Incident) RETURN count(i) AS n")[0]["n"]
        inc_id = f"INC-{40 + n:03d}"
        now = time.time()
        store.query(
            """CREATE (i:Incident {id: $id, title: $title, symptom: $symptom, severity: $sev, status: 'open',
                                   opened_at: $now, opened_iso: $iso, source: 'agent'})""",
            {"id": inc_id, "title": title, "symptom": symptom, "sev": severity, "now": now, "iso": ingest.iso(now)})
        for s in impacted_services:
            store.query("MATCH (i:Incident {id: $id}), (s:Service {name: $s}) MERGE (i)-[:IMPACTED]->(s)", {"id": inc_id, "s": s})
    store.query("MATCH (v:Investigation {id: $v}), (i:Incident {id: $i}) MERGE (v)-[:FOR]->(i)",
                {"v": investigation_id(), "i": inc_id})
    _trace("open_incident", f"{inc_id}: {title}", [("Incident", inc_id)])
    return {"incident_id": inc_id, "status": "open", "reused_existing": bool(existing)}


def _record_action(kind: str, target: str, reason: str, incident_id: str | None, dry_run: bool,
                   result: str, links: list[tuple[str, str, str]] = ()) -> str:
    action_id = f"act-{uuid.uuid4().hex[:8]}"
    store.query(
        """CREATE (a:Action {id: $id, kind: $kind, target: $target, reason: $reason, dry_run: $dry,
                             result: $result, at: $now, by: $by, investigation: $inv})
           CREATE (e:Evidence {id: $eid, summary: $reason, at: $now})
           CREATE (a)-[:BECAUSE]->(e)""",
        {"id": action_id, "kind": kind, "target": target, "reason": reason, "dry": dry_run, "result": result,
         "now": time.time(), "by": os.environ.get("ONCALL_AGENT", "oncall-agent"), "inv": investigation_id(),
         "eid": f"ev-{action_id[4:]}"})
    for rel, label, key in links:
        prop = KEYS.get(label, "id")
        store.query(f"MATCH (a:Action {{id: $a}}), (x:{label} {{{prop}: $k}}) MERGE (a)-[:{rel}]->(x)", {"a": action_id, "k": key})
    if incident_id:
        store.query("MATCH (a:Action {id: $a}), (i:Incident {id: $i}) MERGE (i)-[:HAS_ACTION]->(a)", {"a": action_id, "i": incident_id})
    return action_id


def _blocked(dry_run: bool) -> bool:
    return dry_run or not config.ALLOW_ACTIONS


def rollback_service(service: str, reason: str, incident_id: str | None = None, dry_run: bool = True) -> dict:
    """Redeploy the version that ran before the latest deploy of `service`."""
    ingest.sync_changes()
    current = store.read(
        """MATCH (d:Deploy)-[:CHANGED]->(:Service {name: $s}) RETURN d ORDER BY d.at DESC LIMIT 1""", {"s": service})
    if not current or not current[0]["d"].get("prev_version"):
        return {"error": f"no previous version recorded for {service}; cannot roll back"}
    cur = current[0]["d"]
    target = cur["prev_version"]
    plan = {"service": service, "from_version": cur["version"], "to_version": target,
            "reverts_change": _change_view(cur), "blast_radius": [r["service"] for r in get_blast_radius(service)["affected_if_it_fails (upstream callers)"]]}
    if _blocked(dry_run):
        act = _record_action("rollback", service, reason, incident_id, True, "planned", [("REVERTS", "Change", cur["id"]), ("ON", "Service", service)])
        _trace("rollback_service", f"DRY RUN rollback {service} v{cur['version']} -> v{target}", [("Service", service), ("Change", cur["id"])])
        return {"dry_run": True, "action_id": act, "plan": plan,
                "note": "Nothing changed. Call again with dry_run=false to execute." if config.ALLOW_ACTIONS
                else "Actions are disabled on this server (ONCALL_ALLOW_ACTIONS=0)."}
    # Re-ship the target version exactly as it was originally shipped (from the change log).
    shipped = [c for c in infra.read_changes() if c["kind"] == "deploy" and c["service"] == service and c["version"] == target]
    bug = shipped[-1].get("bug", "") if shipped else ""
    change = infra.deploy(service, target, bug=bug, author="oncall-agent", note=f"rollback: {reason}", rollback_of=cur["id"])
    ingest.sync_changes()
    act = _record_action("rollback", service, reason, incident_id, False, "executed",
                         [("REVERTS", "Change", cur["id"]), ("ON", "Service", service), ("PRODUCED", "Change", change["id"])])
    _trace("rollback_service", f"ROLLED BACK {service} v{cur['version']} -> v{target}", [("Service", service), ("Change", cur["id"]), ("Action", act)])
    return {"dry_run": False, "action_id": act, "executed": plan, "new_deploy_id": change["id"],
            "next": "call verify_recovery on the user-facing service to confirm the fix"}


def restart_service(service: str, reason: str, incident_id: str | None = None, dry_run: bool = True) -> dict:
    """Restart a service's container (clears in-memory state such as stuck connection pools)."""
    if service not in infra.services():
        return {"error": f"unknown service '{service}'"}
    if _blocked(dry_run):
        act = _record_action("restart", service, reason, incident_id, True, "planned", [("ON", "Service", service)])
        _trace("restart_service", f"DRY RUN restart {service}", [("Service", service)])
        return {"dry_run": True, "action_id": act, "note": "Nothing changed. Call again with dry_run=false to execute."}
    infra.restart(service)
    act = _record_action("restart", service, reason, incident_id, False, "executed", [("ON", "Service", service)])
    _trace("restart_service", f"RESTARTED {service}", [("Service", service), ("Action", act)])
    return {"dry_run": False, "action_id": act, "executed": f"restarted {service}",
            "next": "call verify_recovery on the user-facing service to confirm the fix"}


def set_feature_flag(flag: str, enabled: bool, reason: str, incident_id: str | None = None, dry_run: bool = True) -> dict:
    """Turn a feature flag on or off (takes effect within a second, no deploy)."""
    meta = infra.flags_meta()
    if flag not in meta:
        return {"error": f"unknown flag '{flag}'", "known": sorted(meta)}
    current = infra.read_flags().get(flag)
    plan = {"flag": flag, "from": current, "to": enabled, "affects": meta[flag]["affects"]}
    if _blocked(dry_run):
        act = _record_action("toggle_flag", flag, reason, incident_id, True, "planned", [("ON", "Flag", flag)])
        _trace("set_feature_flag", f"DRY RUN {flag} -> {enabled}", [("Flag", flag)])
        return {"dry_run": True, "action_id": act, "plan": plan, "note": "Nothing changed. Call again with dry_run=false to execute."}
    change = infra.set_flag(flag, enabled, author="oncall-agent", note=f"incident fix: {reason}")
    ingest.sync_changes()
    act = _record_action("toggle_flag", flag, reason, incident_id, False, "executed",
                         [("ON", "Flag", flag), ("PRODUCED", "Change", change["id"])])
    _trace("set_feature_flag", f"SET {flag} {current} -> {enabled}", [("Flag", flag), ("Action", act)])
    return {"dry_run": False, "action_id": act, "executed": plan,
            "next": "call verify_recovery on the user-facing service to confirm the fix"}


def verify_recovery(service: str, timeout_seconds: int = 60) -> dict:
    """Poll live health until `service` is healthy for two consecutive samples (or time out)."""
    samples, good, started = [], 0, time.time()
    time.sleep(4)
    while time.time() - started < timeout_seconds:
        s = infra.stats(service, window=6)
        samples.append({"t": round(time.time() - started), "status": s["status"], "error_rate": s.get("error_rate"),
                        "p95_ms": s.get("p95_ms")})
        good = good + 1 if s["status"] == "healthy" else 0
        if good >= 2:
            break
        time.sleep(3)
    recovered = good >= 2
    ingest.sync_health()
    _trace("verify_recovery", f"{service} {'RECOVERED' if recovered else 'NOT recovered'} after {samples[-1]['t'] if samples else 0}s",
           [("Service", service)])
    return {"service": service, "recovered": recovered, "samples": samples[-6:]}


def notify_owner(service: str, message: str, incident_id: str | None = None) -> dict:
    """Page the on-call person of the owning team (Slack/Discord webhook if configured)."""
    owner = get_owner(service)
    if "error" in owner:
        return owner
    text = f"[{incident_id or 'oncall-graph'}] @{owner['on_call']} ({owner['channel']}): {message}"
    delivered = "recorded in graph (no webhook configured)"
    if config.NOTIFY_WEBHOOK:
        try:
            httpx.post(config.NOTIFY_WEBHOOK, json={"text": text, "content": text}, timeout=5).raise_for_status()
            delivered = "sent to webhook"
        except Exception as e:
            delivered = f"webhook failed ({type(e).__name__}); recorded in graph"
    store.query(
        """MATCH (p:Person {handle: $h})
           CREATE (n:Notification {id: $id, text: $text, at: $now, delivered: $d})-[:TO]->(p)
           WITH n OPTIONAL MATCH (i:Incident {id: $inc}) FOREACH (_ IN CASE WHEN i IS NULL THEN [] ELSE [1] END | MERGE (i)-[:NOTIFIED]->(n))""",
        {"h": owner["on_call"], "id": f"ntf-{uuid.uuid4().hex[:8]}", "text": text, "now": time.time(), "d": delivered, "inc": incident_id})
    _trace("notify_owner", f"paged {owner['on_call']} ({owner['team']})", [("Person", owner["on_call"]), ("Team", owner["team"])])
    return {"paged": owner["on_call"], "team": owner["team"], "channel": owner["channel"], "message": text, "delivery": delivered}


def resolve_incident(incident_id: str, root_cause_service: str, summary: str, root_cause_change_id: str | None = None) -> dict:
    """Close the incident and write what was learned into incident memory."""
    now = time.time()
    rows = store.query(
        """MATCH (i:Incident {id: $id}) SET i.status = 'resolved', i.resolved_at = $now, i.summary = $summary,
               i.mttr_min = round(($now - i.opened_at) / 6.0) / 10.0
           RETURN i.mttr_min AS mttr""", {"id": incident_id, "now": now, "summary": summary})
    if not rows:
        return {"error": f"no incident {incident_id}"}
    if root_cause_change_id:
        store.query("MATCH (i:Incident {id: $id}), (c:Change {id: $c}) MERGE (i)-[:ROOT_CAUSE]->(c)", {"id": incident_id, "c": root_cause_change_id})
    store.query("MATCH (i:Incident {id: $id}), (s:Service {name: $s}) MERGE (i)-[:ROOT_CAUSE]->(s)", {"id": incident_id, "s": root_cause_service})
    store.query(
        """MATCH (i:Incident {id: $id})-[:HAS_ACTION]->(a:Action {dry_run: false}) MERGE (i)-[:RESOLVED_BY]->(a)""",
        {"id": incident_id})
    store.query("MATCH (v:Investigation {id: $v}) SET v.finished_at = $now, v.outcome = 'resolved'", {"v": investigation_id(), "now": now})
    _trace("resolve_incident", f"{incident_id} resolved in {rows[0]['mttr']} min, root cause {root_cause_service}",
           [("Incident", incident_id), ("Service", root_cause_service)] + ([("Change", root_cause_change_id)] if root_cause_change_id else []))
    return {"incident_id": incident_id, "status": "resolved", "mttr_min": rows[0]["mttr"],
            "memory": "stored: future find_similar_incidents calls will return this incident and its fix"}


def explain_incident(incident_id: str) -> dict:
    """The full evidence chain for an incident: impacted services, root cause, actions and why."""
    rows = store.read(
        """MATCH (i:Incident {id: $id})
           OPTIONAL MATCH (i)-[:IMPACTED]->(s:Service)
           OPTIONAL MATCH (i)-[:ROOT_CAUSE]->(rc)
           OPTIONAL MATCH (i)-[:HAS_ACTION|RESOLVED_BY]->(a:Action)-[:BECAUSE]->(e:Evidence)
           OPTIONAL MATCH (v:Investigation)-[:FOR]->(i)
           RETURN i, collect(DISTINCT s.name) AS impacted, collect(DISTINCT rc) AS root_causes,
                  collect(DISTINCT {kind: a.kind, target: a.target, dry_run: a.dry_run, result: a.result, why: e.summary}) AS actions,
                  collect(DISTINCT v.id) AS investigations""", {"id": incident_id})
    if not rows:
        return {"error": f"no incident {incident_id}"}
    r = rows[0]
    inc = {k: v for k, v in r["i"].items() if not k.startswith("_")}
    steps = []
    if r["investigations"]:
        steps = store.read(
            """MATCH (v:Investigation {id: $v})-[s:STEP]->(x)
               RETURN s.n AS n, s.tool AS tool, s.note AS note, collect(coalesce(x.name, x.id, x.handle)) AS looked_at
               ORDER BY n""", {"v": r["investigations"][0]})
    _trace("explain_incident", f"explained {incident_id}", [("Incident", incident_id)])
    return {"incident": inc, "impacted": r["impacted"],
            "root_cause": [_change_view(c) if "Change" in c.get("_labels", []) else {"service": c.get("name")} for c in r["root_causes"]],
            "actions": [a for a in r["actions"] if a.get("kind")], "investigation_steps": steps}


# ---------------------------------------------------------------- baseline-only tool

def list_changes_flat(minutes: int = 120) -> dict:
    """(Baseline mode) Every deploy and flag flip in the window, straight from the change log,
    with no knowledge of dependencies or which services a flag affects."""
    since = time.time() - minutes * 60
    out = []
    for c in infra.read_changes():
        if c["at"] < since:
            continue
        v = {k: c.get(k) for k in ("id", "kind", "service", "version", "prev_version", "flag", "from", "to", "author", "note") if c.get(k) is not None}
        v["when"] = _ago(c["at"])
        out.append(v)
    _trace("list_changes_flat", f"{len(out)} changes in last {minutes} min")
    return {"minutes": minutes, "changes": out[::-1]}


def to_json(value) -> str:
    return json.dumps(value, indent=1, default=str)
