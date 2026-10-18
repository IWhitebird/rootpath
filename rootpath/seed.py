"""Seeds believable history so incident memory has something to remember:
two weeks of deploys per service and three past incidents with root causes and fixes."""

import json
import time

from . import config, infra, store
from .ingest import iso

DAY = 86400.0

# service -> [(days_ago, version, author)], oldest first; the last entry is the baseline version.
DEPLOYS = {
    "frontend": [(13, "2.7.1", "meera"), (8, "2.7.4", "aarav"), (3, "2.8.0", "meera")],
    "catalog": [(11, "3.0.1", "zoya"), (4, "3.0.4", "zoya")],
    "checkout": [(14, "4.1.2", "kabir"), (9, "4.1.5", "zoya"), (2.5, "4.2.0", "kabir")],
    "payment": [(14, "2.3.4", "dev"), (12, "2.3.5", "riya"), (6, "2.3.7", "dev"), (1.8, "2.4.0", "riya")],
    "fraud-check": [(15, "1.9.0", "omar"), (5, "1.9.2", "ananya")],
    "inventory": [(16, "5.0.9", "li"), (7, "5.1.1", "sam")],
    "notify": [(10, "1.1.8", "li"), (3.2, "1.2.0", "sam")],
}


def seed_changes() -> None:
    if infra.read_changes():
        return
    now = time.time()
    rows = []
    for service, history in DEPLOYS.items():
        prev = None
        for days, version, author in history:
            rows.append(
                {
                    "id": f"chg-seed-{service}-{version}",
                    "kind": "deploy",
                    "service": service,
                    "version": version,
                    "prev_version": prev,
                    "bug": "",
                    "author": author,
                    "note": "",
                    "at": now - days * DAY,
                }
            )
            prev = version
    # INC-031: payment 2.3.5 shipped a tokenizer bug and was rolled back to 2.3.4 by a human.
    rows.append(
        {
            "id": "chg-seed-payment-2.3.4-rb",
            "kind": "deploy",
            "service": "payment",
            "version": "2.3.4",
            "prev_version": "2.3.5",
            "bug": "",
            "author": "riya",
            "note": "rollback: tokenizer TypeError",
            "rollback_of": "chg-seed-payment-2.3.5",
            "at": now - 12 * DAY + 38 * 60,
        }
    )
    # INC-027: fraud_model_v2 enabled, latency spike, turned off again.
    rows.append(
        {
            "id": "chg-seed-flag-fraud-on",
            "kind": "flag",
            "flag": "fraud_model_v2",
            "from": False,
            "to": True,
            "author": "omar",
            "note": "gradual rollout of v2 model",
            "services": ["fraud-check"],
            "at": now - 20 * DAY,
        }
    )
    rows.append(
        {
            "id": "chg-seed-flag-fraud-off",
            "kind": "flag",
            "flag": "fraud_model_v2",
            "from": True,
            "to": False,
            "author": "ananya",
            "note": "disable: payment timeouts",
            "services": ["fraud-check"],
            "at": now - 20 * DAY + 52 * 60,
        }
    )
    rows.sort(key=lambda r: r["at"])
    config.STATE.mkdir(parents=True, exist_ok=True)
    infra.CHANGES.write_text("".join(json.dumps(r) + "\n" for r in rows))


PAST_INCIDENTS = [
    {
        "id": "INC-019",
        "days_ago": 17,
        "mttr_min": 64,
        "severity": "SEV2",
        "title": "Checkout and catalog slow, inventory PoolTimeout",
        "symptom": "checkout 502 and catalog errors; inventory logs psycopg_pool.PoolTimeout connection pool exhausted",
        "summary": "Inventory leaked DB connections until the pool was exhausted. Restart of inventory drained the pool. Follow-up: fix connection leak.",
        "impacted": ["frontend", "checkout", "catalog", "inventory"],
        "root_service": "inventory",
        "root_change": None,
        "action": {"kind": "restart", "target": "inventory", "by": "sam"},
    },
    {
        "id": "INC-027",
        "days_ago": 20,
        "mttr_min": 52,
        "severity": "SEV2",
        "title": "Checkout timeouts after fraud model rollout",
        "symptom": "checkout 504 timeouts; payment timeout calling fraud-check; fraud-check p95 latency above 2s",
        "summary": "Enabling fraud_model_v2 pushed fraud-check latency past payment's 1.5s client timeout. Turning the flag off fixed it.",
        "impacted": ["frontend", "checkout", "payment", "fraud-check"],
        "root_service": "fraud-check",
        "root_change": "chg-seed-flag-fraud-on",
        "action": {"kind": "toggle_flag", "target": "fraud_model_v2", "by": "ananya"},
    },
    {
        "id": "INC-031",
        "days_ago": 12,
        "mttr_min": 38,
        "severity": "SEV1",
        "title": "Checkout failures: payment 500s after release",
        "symptom": "checkout 502; payment 500 TypeError NoneType object is not subscriptable in card_tokenizer",
        "summary": "payment 2.3.5 shipped a card tokenizer regression. Rolled back to 2.3.4. Root cause: missing null check on saved cards.",
        "impacted": ["frontend", "checkout", "payment"],
        "root_service": "payment",
        "root_change": "chg-seed-payment-2.3.5",
        "action": {"kind": "rollback", "target": "payment", "by": "riya"},
    },
]


def seed_incidents() -> None:
    now = time.time()
    for inc in PAST_INCIDENTS:
        opened = now - inc["days_ago"] * DAY
        resolved = opened + inc["mttr_min"] * 60
        store.query(
            """MERGE (i:Incident {id: $id})
               SET i.title = $title, i.symptom = $symptom, i.summary = $summary, i.severity = $severity,
                   i.status = 'resolved', i.opened_at = $opened, i.resolved_at = $resolved,
                   i.opened_iso = $opened_iso, i.mttr_min = $mttr, i.source = 'history'""",
            {**inc, "opened": opened, "resolved": resolved, "opened_iso": iso(opened), "mttr": inc["mttr_min"]},
        )
        for svc in inc["impacted"]:
            store.query(
                "MATCH (i:Incident {id: $id}), (s:Service {name: $s}) MERGE (i)-[:IMPACTED]->(s)",
                {"id": inc["id"], "s": svc},
            )
        if inc["root_change"]:
            store.query(
                "MATCH (i:Incident {id: $id}), (c:Change {id: $c}) MERGE (i)-[:ROOT_CAUSE]->(c)",
                {"id": inc["id"], "c": inc["root_change"]},
            )
        else:
            store.query(
                "MATCH (i:Incident {id: $id}), (s:Service {name: $s}) MERGE (i)-[:ROOT_CAUSE]->(s)",
                {"id": inc["id"], "s": inc["root_service"]},
            )
        act = inc["action"]
        store.query(
            """MATCH (i:Incident {id: $id})
               MERGE (a:Action {id: $aid})
               SET a.kind = $kind, a.target = $target, a.by = $by, a.at = $at, a.result = 'resolved', a.dry_run = false
               MERGE (i)-[:RESOLVED_BY]->(a)""",
            {"id": inc["id"], "aid": f"act-{inc['id']}", **act, "at": resolved},
        )


def write_baseline() -> None:
    """Reset env files and flags to the known-good baseline (no running containers touched)."""
    base = json.loads((config.DEMO / "baseline.json").read_text())
    for service, env in base["services"].items():
        infra.write_env(service, env)
    infra.FLAGS_FILE.write_text(json.dumps(base["flags"], indent=2))
