"""Builds the graph from real sources: the compose file (services, owners, declared calls),
live traffic stats (observed calls, health), teams.json, runbooks, flag metadata and the
change log. Every sync is idempotent (MERGE on stable keys)."""

import json
import re
import time
from datetime import UTC, datetime

from . import config, infra, store

# (label, property) -> the statement that indexes it.
INDEXES = {
    ("Service", "name"): "CREATE INDEX FOR (s:Service) ON (s.name)",
    ("Team", "name"): "CREATE INDEX FOR (t:Team) ON (t.name)",
    ("Person", "handle"): "CREATE INDEX FOR (p:Person) ON (p.handle)",
    ("Change", "id"): "CREATE INDEX FOR (c:Change) ON (c.id)",
    ("Change", "at"): "CREATE INDEX FOR (c:Change) ON (c.at)",
    ("Flag", "name"): "CREATE INDEX FOR (f:Flag) ON (f.name)",
    ("Incident", "id"): "CREATE INDEX FOR (i:Incident) ON (i.id)",
    ("Action", "id"): "CREATE INDEX FOR (a:Action) ON (a.id)",
    ("Investigation", "id"): "CREATE INDEX FOR (v:Investigation) ON (v.id)",
    ("Incident", "title"): "CALL db.idx.fulltext.createNodeIndex('Incident', 'title', 'symptom', 'summary')",
}


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds")


def _unwind(cypher: str, rows: list[dict], **params) -> None:
    """Run `cypher` once for a whole batch (one round trip); it sees each item as `row`."""
    if rows:
        store.query(f"UNWIND $rows AS row {cypher}", {"rows": rows, **params})


def ensure_schema() -> None:
    """Create the missing indexes; a single read when the schema is already in place."""
    # store.query, not store.read: a read-only query fails on a graph that doesn't exist yet.
    indexed = {
        (r["label"], prop)
        for r in store.query("CALL db.indexes() YIELD label, properties RETURN label, properties")
        for prop in r["properties"]
    }
    for key, stmt in INDEXES.items():
        if key in indexed:
            continue
        try:
            store.query(stmt)
        except Exception as e:  # created concurrently by another process
            if "already" not in str(e).lower():
                raise


def sync_topology() -> None:
    services = list(infra.services().values())
    _unwind(
        """MERGE (s:Service {name: row.name})
           SET s.tier = row.tier, s.internet_facing = row.internet_facing, s.description = row.description
           MERGE (t:Team {name: row.team})
           MERGE (t)-[:OWNS]->(s)""",
        services,
    )
    _unwind(
        """MATCH (a:Service {name: row.a}), (b:Service {name: row.b})
           MERGE (a)-[r:CALLS]->(b)
           SET r.declared = true, r.optional = row.optional""",
        [{"a": s["name"], "b": dep, "optional": dep in s["optional_calls"]} for s in services for dep in s["calls"]],
    )


def _observed_calls(stats: dict) -> list[dict]:
    """Calls seen in live traffic. That is tracing data, so there is none in basic telemetry."""
    if config.TELEMETRY == "basic":
        return []
    return [{"dep": dep, "count": count} for dep, count in (stats.get("calls_total") or {}).items()]


def sync_health() -> dict:
    """Overlay live health on Service nodes and record calls actually observed in traffic."""
    now = time.time()
    snapshot = infra.all_stats()
    rows = [
        {
            "name": name,
            "status": s["status"],
            "error_rate": s.get("error_rate", 1.0),
            "p95": s.get("p95_ms", 0.0),
            "version": s.get("version", infra.read_env(name).get("VERSION")),
            "observed": _observed_calls(s),
        }
        for name, s in snapshot.items()
    ]
    _unwind(
        """MATCH (s:Service {name: row.name})
           SET s.status = row.status, s.error_rate = row.error_rate, s.p95_ms = row.p95,
               s.version = row.version, s.health_at = $now
           WITH s, row
           UNWIND row.observed AS o
           MATCH (b:Service {name: o.dep})
           MERGE (s)-[r:CALLS]->(b)
           SET r.observed = o.count, r.observed_at = $now""",
        rows,
        now=now,
    )
    return snapshot


def sync_teams() -> None:
    data = json.loads((config.DEMO / "teams.json").read_text())
    teams = data["teams"]
    # Only rotated-out on-call edges are deleted: a re-sync (every minute under `ingest --watch`)
    # must never leave a team without its on-call while the console reads the graph.
    _unwind(
        """MERGE (t:Team {name: row.name}) SET t.channel = row.channel
           WITH t, row
           OPTIONAL MATCH (p:Person)-[old:ON_CALL_FOR]->(t) WHERE p.handle <> row.oncall DELETE old""",
        teams,
    )
    _unwind(
        """MATCH (t:Team {name: row.team}) MERGE (p:Person {handle: row.h}) MERGE (p)-[:MEMBER_OF]->(t)""",
        [{"team": t["name"], "h": handle} for t in teams for handle in t["members"]],
    )
    _unwind(
        """MATCH (t:Team {name: row.team}), (p:Person {handle: row.h}) MERGE (p)-[:ON_CALL_FOR]->(t)""",
        [{"team": t["name"], "h": t["oncall"]} for t in teams],
    )
    _unwind(
        "MATCH (a:Team {name: row.a}), (b:Team {name: row.b}) MERGE (a)-[:ESCALATES_TO]->(b)",
        [{"a": src, "b": dst} for src, dst in data.get("escalation", {}).items()],
    )


def sync_runbooks() -> None:
    rows = []
    for path in sorted((config.DEMO / "runbooks").glob("*.md")):
        text = path.read_text()
        m = re.match(r"---\n(.*?)\n---\n(.*)", text, re.S)
        meta = dict(line.split(": ", 1) for line in m.group(1).splitlines())
        rows.append({**meta, "body": m.group(2).strip(), "path": str(path.relative_to(config.ROOT))})
    _unwind(
        """MERGE (r:Runbook {id: row.id}) SET r.title = row.title, r.body = row.body, r.path = row.path
           WITH r, row MATCH (s:Service {name: row.service}) MERGE (r)-[:FOR]->(s)""",
        rows,
    )


def sync_flags() -> None:
    current = infra.read_flags()
    meta = infra.flags_meta()
    _unwind(
        """MERGE (f:Flag {name: row.name}) SET f.description = row.description, f.enabled = row.enabled
           WITH f, row MATCH (t:Team {name: row.team}) MERGE (t)-[:OWNS]->(f)""",
        [
            {"name": name, "description": m["description"], "enabled": bool(current.get(name)), "team": m["team"]}
            for name, m in meta.items()
        ],
    )
    _unwind(
        "MATCH (f:Flag {name: row.f}), (s:Service {name: row.s}) MERGE (f)-[:AFFECTS]->(s)",
        [{"f": name, "s": svc} for name, m in meta.items() for svc in m["affects"]],
    )


def sync_changes() -> int:
    """Mirror the change log into the graph. The `bug` field stays out: the agent must find defects itself."""
    known = {r["id"] for r in store.query("MATCH (c:Change) RETURN c.id AS id")}
    new = [c for c in infra.read_changes() if c["id"] not in known]
    write_changes(new)
    if new:
        sync_flags()
    return len(new)


def write_changes(changes: list[dict], source: str | None = None) -> None:
    """MERGE change records (deploys and flag flips, in change-log format) into the graph.
    `source` tags where they came from when it isn't the change log (e.g. "claude-code")."""
    deploys, rollbacks, flips = [], [], []
    for c in changes:
        base = {
            "id": c["id"],
            "at": c["at"],
            "iso": iso(c["at"]),
            "author": c.get("author", "unknown"),
            "note": c.get("note", ""),
            "source": source,
        }
        if c["kind"] == "deploy":
            deploys.append(
                {**base, "service": c["service"], "version": c["version"], "prev_version": c.get("prev_version")}
            )
            if c.get("rollback_of"):
                rollbacks.append({"a": c["id"], "b": c["rollback_of"]})
        else:
            flips.append({**base, "flag": c["flag"], "from": c.get("from"), "to": c.get("to")})
    _unwind(
        """MERGE (d:Change:Deploy {id: row.id})
           SET d.at = row.at, d.iso = row.iso, d.author = row.author, d.note = row.note,
               d.service = row.service, d.version = row.version, d.prev_version = row.prev_version, d.kind = 'deploy',
               d.source = coalesce(row.source, d.source)
           WITH d, row MATCH (s:Service {name: row.service}) MERGE (d)-[:CHANGED]->(s)
           MERGE (p:Person {handle: row.author}) MERGE (p)-[:AUTHORED]->(d)""",
        deploys,
    )
    _unwind("MATCH (a:Deploy {id: row.a}), (b:Deploy {id: row.b}) MERGE (a)-[:ROLLED_BACK]->(b)", rollbacks)
    _unwind(
        """MERGE (x:Change:FlagChange {id: row.id})
           SET x.at = row.at, x.iso = row.iso, x.author = row.author, x.note = row.note,
               x.flag = row.flag, x.from = row.from, x.to = row.to, x.kind = 'flag',
               x.source = coalesce(row.source, x.source)
           WITH x, row MATCH (f:Flag {name: row.flag}) MERGE (x)-[:TOGGLED]->(f)
           MERGE (p:Person {handle: row.author}) MERGE (p)-[:AUTHORED]->(x)""",
        flips,
    )


def sync_all(health: bool = True) -> int:
    """Full sync; returns how many new changes it mirrored."""
    ensure_schema()
    sync_topology()
    sync_teams()
    sync_runbooks()
    sync_flags()
    new = sync_changes()
    if health:
        sync_health()
    return new
