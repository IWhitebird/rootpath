"""Builds the graph from real sources: the compose file (services, owners, declared calls),
live traffic stats (observed calls, health), teams.json, runbooks, flag metadata and the
change log. Every sync is idempotent (MERGE on stable keys)."""

import json
import re
import time
from datetime import UTC, datetime

from . import config, infra, store

INDEXES = [
    "CREATE INDEX FOR (s:Service) ON (s.name)",
    "CREATE INDEX FOR (t:Team) ON (t.name)",
    "CREATE INDEX FOR (p:Person) ON (p.handle)",
    "CREATE INDEX FOR (c:Change) ON (c.id)",
    "CREATE INDEX FOR (c:Change) ON (c.at)",
    "CREATE INDEX FOR (f:Flag) ON (f.name)",
    "CREATE INDEX FOR (i:Incident) ON (i.id)",
    "CREATE INDEX FOR (a:Action) ON (a.id)",
    "CREATE INDEX FOR (v:Investigation) ON (v.id)",
    "CALL db.idx.fulltext.createNodeIndex('Incident', 'title', 'symptom', 'summary')",
]


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds")


def ensure_schema() -> None:
    for stmt in INDEXES:
        try:
            store.query(stmt)
        except Exception as e:  # index already exists
            if "already" not in str(e).lower():
                raise


def sync_topology() -> None:
    for svc in infra.services().values():
        store.query(
            """MERGE (s:Service {name: $name})
               SET s.tier = $tier, s.internet_facing = $internet_facing, s.description = $description
               MERGE (t:Team {name: $team})
               MERGE (t)-[:OWNS]->(s)""",
            svc,
        )
    for svc in infra.services().values():
        for dep in svc["calls"]:
            store.query(
                """MATCH (a:Service {name: $a}), (b:Service {name: $b})
                   MERGE (a)-[r:CALLS]->(b)
                   SET r.declared = true, r.optional = $optional""",
                {"a": svc["name"], "b": dep, "optional": dep in svc["optional_calls"]},
            )


def sync_health() -> dict:
    """Overlay live health on Service nodes and record calls actually observed in traffic."""
    now = time.time()
    snapshot = infra.all_stats()
    for name, s in snapshot.items():
        store.query(
            """MATCH (s:Service {name: $name})
               SET s.status = $status, s.error_rate = $error_rate, s.p95_ms = $p95,
                   s.version = $version, s.health_at = $now""",
            {
                "name": name,
                "status": s["status"],
                "error_rate": s.get("error_rate", 1.0),
                "p95": s.get("p95_ms", 0.0),
                "version": s.get("version", infra.read_env(name).get("VERSION")),
                "now": now,
            },
        )
        observed = {} if config.TELEMETRY == "basic" else (s.get("calls_total") or {})
        for dep, count in observed.items():  # observed call edges come from tracing data
            store.query(
                """MATCH (a:Service {name: $a}), (b:Service {name: $b})
                   MERGE (a)-[r:CALLS]->(b)
                   SET r.observed = $count, r.observed_at = $now""",
                {"a": name, "b": dep, "count": count, "now": now},
            )
    return snapshot


def sync_teams() -> None:
    data = json.loads((config.DEMO / "teams.json").read_text())
    for team in data["teams"]:
        store.query(
            """MERGE (t:Team {name: $name}) SET t.channel = $channel
               WITH t
               OPTIONAL MATCH (:Person)-[old:ON_CALL_FOR]->(t) DELETE old""",
            team,
        )
        for handle in team["members"]:
            store.query(
                """MATCH (t:Team {name: $team}) MERGE (p:Person {handle: $h}) MERGE (p)-[:MEMBER_OF]->(t)""",
                {"team": team["name"], "h": handle},
            )
        store.query(
            """MATCH (t:Team {name: $team}), (p:Person {handle: $h}) MERGE (p)-[:ON_CALL_FOR]->(t)""",
            {"team": team["name"], "h": team["oncall"]},
        )
    for src, dst in data.get("escalation", {}).items():
        store.query(
            "MATCH (a:Team {name: $a}), (b:Team {name: $b}) MERGE (a)-[:ESCALATES_TO]->(b)", {"a": src, "b": dst}
        )


def sync_runbooks() -> None:
    for path in sorted((config.DEMO / "runbooks").glob("*.md")):
        text = path.read_text()
        m = re.match(r"---\n(.*?)\n---\n(.*)", text, re.S)
        meta = dict(line.split(": ", 1) for line in m.group(1).splitlines())
        store.query(
            """MERGE (r:Runbook {id: $id}) SET r.title = $title, r.body = $body, r.path = $path
               WITH r MATCH (s:Service {name: $service}) MERGE (r)-[:FOR]->(s)""",
            {**meta, "body": m.group(2).strip(), "path": str(path.relative_to(config.ROOT))},
        )


def sync_flags() -> None:
    current = infra.read_flags()
    for name, meta in infra.flags_meta().items():
        store.query(
            """MERGE (f:Flag {name: $name}) SET f.description = $description, f.enabled = $enabled
               WITH f MATCH (t:Team {name: $team}) MERGE (t)-[:OWNS]->(f)""",
            {
                "name": name,
                "description": meta["description"],
                "enabled": bool(current.get(name)),
                "team": meta["team"],
            },
        )
        for svc in meta["affects"]:
            store.query(
                "MATCH (f:Flag {name: $f}), (s:Service {name: $s}) MERGE (f)-[:AFFECTS]->(s)", {"f": name, "s": svc}
            )


def sync_changes() -> int:
    """Mirror the change log into the graph. The `bug` field stays out: the agent must find defects itself."""
    known = {r["id"] for r in store.query("MATCH (c:Change) RETURN c.id AS id")}
    new = [c for c in infra.read_changes() if c["id"] not in known]
    for c in new:
        base = {
            "id": c["id"],
            "at": c["at"],
            "iso": iso(c["at"]),
            "author": c.get("author", "unknown"),
            "note": c.get("note", ""),
        }
        if c["kind"] == "deploy":
            store.query(
                """MERGE (d:Change:Deploy {id: $id})
                   SET d.at = $at, d.iso = $iso, d.author = $author, d.note = $note,
                       d.service = $service, d.version = $version, d.prev_version = $prev_version, d.kind = 'deploy'
                   WITH d MATCH (s:Service {name: $service}) MERGE (d)-[:CHANGED]->(s)
                   MERGE (p:Person {handle: $author}) MERGE (p)-[:AUTHORED]->(d)""",
                {**base, "service": c["service"], "version": c["version"], "prev_version": c.get("prev_version")},
            )
            if c.get("rollback_of"):
                store.query(
                    "MATCH (a:Deploy {id: $a}), (b:Deploy {id: $b}) MERGE (a)-[:ROLLED_BACK]->(b)",
                    {"a": c["id"], "b": c["rollback_of"]},
                )
        else:
            store.query(
                """MERGE (x:Change:FlagChange {id: $id})
                   SET x.at = $at, x.iso = $iso, x.author = $author, x.note = $note,
                       x.flag = $flag, x.from = $from, x.to = $to, x.kind = 'flag'
                   WITH x MATCH (f:Flag {name: $flag}) MERGE (x)-[:TOGGLED]->(f)
                   MERGE (p:Person {handle: $author}) MERGE (p)-[:AUTHORED]->(x)""",
                {**base, "flag": c["flag"], "from": c.get("from"), "to": c.get("to")},
            )
    if new:
        sync_flags()
    return len(new)


def sync_all(health: bool = True) -> None:
    ensure_schema()
    sync_topology()
    sync_teams()
    sync_runbooks()
    sync_flags()
    sync_changes()
    if health:
        sync_health()
