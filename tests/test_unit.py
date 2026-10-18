"""Fast tests with the graph and Docker mocked out."""

import argparse
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from rootpath import config, guard, infra, ingest, store, tools


@pytest.mark.parametrize(
    "url,expected",
    [
        ("redis://localhost:6380", ("localhost", 6380, None, None, False)),
        ("redis://db", ("db", 6379, None, None, False)),
        ("redis://:pw@db:7000", ("db", 7000, None, "pw", False)),
        (
            "rediss://falkordb:s3cr%40t@r-1.falkordb.cloud:55011",
            ("r-1.falkordb.cloud", 55011, "falkordb", "s3cr@t", True),
        ),
    ],
)
def test_falkordb_url(url, expected):
    c = config.falkordb_connection(url)
    assert (c["host"], c["port"], c["username"], c["password"], c["ssl"]) == expected


def test_falkordb_url_rejects_other_schemes():
    with pytest.raises(ValueError, match="redis://"):
        config.falkordb_connection("https://user:secret@db:6379")


def test_store_connects_with_parsed_url(monkeypatch):
    seen = {}

    class FakeDB:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def select_graph(self, name):
            return name

    monkeypatch.setattr(config, "FALKORDB", config.falkordb_connection("rediss://u:p@cloud:1234"))
    monkeypatch.setattr(store, "FalkorDB", FakeDB)
    monkeypatch.setattr(store, "_graph", None)
    assert store.graph() == config.GRAPH
    assert seen == {"host": "cloud", "port": 1234, "username": "u", "password": "p", "ssl": True}


def test_safe_read_appends_limit(monkeypatch):
    seen = {}
    monkeypatch.setattr(store, "read", lambda q, p=None, timeout_ms=None: seen.setdefault("q", q) and [])
    store.safe_read("MATCH (n) RETURN n;")
    assert seen["q"].endswith("LIMIT 100")
    seen.clear()
    store.safe_read("MATCH (n) RETURN n LIMIT 5")
    assert seen["q"].endswith("LIMIT 5") and "LIMIT 100" not in seen["q"]


@pytest.mark.parametrize(
    "stats,expected",
    [
        ({"requests": 0, "error_rate": 0, "p95_ms": 0}, "idle"),
        ({"requests": 50, "error_rate": 0.5, "p95_ms": 30}, "failing"),
        ({"requests": 50, "error_rate": 0.05, "p95_ms": 30}, "degraded"),
        ({"requests": 50, "error_rate": 0.0, "p95_ms": 2200}, "degraded"),
        ({"requests": 50, "error_rate": 0.0, "p95_ms": 40}, "healthy"),
    ],
)
def test_classify(stats, expected):
    assert infra.classify(stats) == expected


def _event(tool, **args):
    return {"tool_name": f"mcp__plugin_rootpath_oncall__{tool}", "tool_input": args}


def test_guard_allows_dry_runs_without_touching_graph(monkeypatch):
    monkeypatch.setattr(store, "read", lambda *a, **k: pytest.fail("graph should not be queried"))
    assert guard.decide(_event("rollback_service", service="payment", dry_run=True)) is None
    assert guard.decide(_event("rollback_service", service="payment")) is None  # dry_run defaults to true
    assert guard.decide(_event("get_logs", service="payment")) is None


def test_guard_requires_prior_dry_run(monkeypatch):
    monkeypatch.setattr(store, "read", lambda *a, **k: [{"n": 0}])
    verdict = guard.decide(_event("restart_service", service="inventory", dry_run=False))
    assert verdict[0] == "deny" and "dry_run=true" in verdict[1]
    monkeypatch.setattr(store, "read", lambda *a, **k: [{"n": 1}])
    assert guard.decide(_event("restart_service", service="inventory", dry_run=False)) is None


def test_guard_change_freeze(monkeypatch):
    monkeypatch.setenv("ONCALL_CHANGE_FREEZE", "1")
    monkeypatch.setattr(store, "read", lambda *a, **k: [{"n": 5}])
    verdict = guard.decide(_event("set_feature_flag", flag="x", enabled=False, dry_run=False))
    assert verdict[0] == "deny" and "freeze" in verdict[1].lower()


def test_guard_fails_closed_when_graph_is_down(monkeypatch, capsys):
    def boom(*a, **k):
        raise ConnectionError("down")

    monkeypatch.setattr(store, "read", boom)
    monkeypatch.setattr(
        "sys.stdin", __import__("io").StringIO(json.dumps(_event("rollback_service", service="p", dry_run=False)))
    )
    with pytest.raises(SystemExit):
        guard.main()
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


SAMPLE_LOGS = """2026-10-17T21:02:09.044+00:00 ERROR [payment v2.4.1] /work: unhandled exception
Traceback (most recent call last):
TypeError: 'NoneType' object is not subscriptable
2026-10-17T21:02:09.262+00:00 ERROR [payment v2.4.1] /work: unhandled exception
Traceback (most recent call last):
TypeError: 'NoneType' object is not subscriptable
2026-10-17T21:02:10.001+00:00 INFO  [payment v2.4.1] GET /work 200 12ms
"""


def test_get_logs_groups_multiline_entries(monkeypatch):
    monkeypatch.setattr(infra, "logs", lambda service, tail=200: SAMPLE_LOGS)
    monkeypatch.setattr(tools, "_trace", lambda *a, **k: None)
    out = tools.get_logs("payment")
    assert out["entries_scanned"] == 3
    top = out["distinct_messages"][0]
    assert top["count"] == 2 and "TypeError" in top["message"]
    assert tools.get_logs("payment", contains="info")["entries_scanned"] == 1


def test_fulltext_query_is_sanitized(monkeypatch):
    queries = []

    def fake_read(q, params=None, timeout_ms=None):
        if "fulltext" in q:
            queries.append(params["q"])
        return []

    monkeypatch.setattr(store, "read", fake_read)
    monkeypatch.setattr(tools, "_trace", lambda *a, **k: None)
    tools.find_similar_incidents("fraud-check p95 2.3s; payment-service TIMEOUT timeout")
    q = queries[0]
    assert "-" not in q and "." not in q and ";" not in q
    assert q.split("|") == ["fraud", "check", "payment", "service", "timeout"]


def test_change_line_formats():
    deploy = {
        "id": "chg-1",
        "kind": "deploy",
        "service": "payment",
        "version": "2.4.1",
        "prev_version": "2.4.0",
        "author": "riya",
        "at": 0,
        "iso": "x",
        "note": "refactor",
    }
    flag = {
        "id": "chg-2",
        "kind": "flag",
        "_labels": ["Change", "FlagChange"],
        "flag": "f",
        "from": False,
        "to": True,
        "author": "omar",
        "at": 0,
        "iso": "x",
    }
    assert tools._change_line(deploy).startswith("chg-1: deploy payment v2.4.0 -> v2.4.1 by riya")
    assert tools._change_line(flag).startswith("chg-2: flag f False -> True by omar")


def test_health_view_hides_tracing_fields_in_basic_telemetry(monkeypatch):
    s = {
        "status": "failing",
        "error_rate": 0.5,
        "errors_originated_here": 3,
        "errors_from_dependencies": 1,
        "p95_ms": 1,
    }
    assert "errors_originated_here" in tools._health_view(s)
    monkeypatch.setattr(config, "TELEMETRY", "basic")
    assert not any(k.startswith("errors_") for k in tools._health_view(s))


def test_mcp_tool_sets():
    from rootpath.mcp_server import build

    def names(mode):
        return {t.name for t in asyncio.run(build(mode).list_tools())}

    graph, baseline = names("graph"), names("baseline")
    assert {"rank_suspects", "find_similar_incidents", "open_incident", "resolve_incident"} <= graph
    assert baseline == {
        "get_service_health",
        "get_logs",
        "list_changes_flat",
        "rollback_service",
        "restart_service",
        "set_feature_flag",
        "verify_recovery",
    }
    assert not graph & {"list_changes_flat"}


def test_tool_annotations_mark_actions_destructive():
    from rootpath.mcp_server import build

    tools_by_name = {t.name: t for t in asyncio.run(build("graph").list_tools())}
    assert tools_by_name["rollback_service"].annotations.destructiveHint is True
    assert tools_by_name["rank_suspects"].annotations.readOnlyHint is True


def test_propose_mode_disables_actions_in_the_server(tmp_path, monkeypatch):
    from rootpath import responder

    monkeypatch.setattr(config, "ROOT", tmp_path)
    auto = json.loads(Path(responder.mcp_config("graph", "inv-test", "auto")).read_text())
    propose = json.loads(Path(responder.mcp_config("graph", "inv-test", "propose")).read_text())
    assert "ONCALL_ALLOW_ACTIONS" not in auto["mcpServers"]["oncall"]["env"]
    assert propose["mcpServers"]["oncall"]["env"]["ONCALL_ALLOW_ACTIONS"] == "0"


def _fake_sources(monkeypatch, n: int) -> list[str]:
    """A demo of n services in a chain, each deployed once, with every write to the graph recorded."""
    names = [f"svc{i}" for i in range(n)]
    services = {
        name: {
            "name": name,
            "team": "web",
            "tier": 1,
            "internet_facing": i == 0,
            "description": "",
            "calls": names[i + 1 : i + 2],
            "optional_calls": [],
            "port": None,
        }
        for i, name in enumerate(names)
    }
    stats = {
        name: {"status": "healthy", "error_rate": 0.0, "p95_ms": 5.0, "version": "1", "calls_total": {}}
        for name in names
    }
    changes = [
        {"id": f"chg-{name}", "kind": "deploy", "at": 0, "service": name, "version": "1", "author": "ci"}
        for name in names
    ]
    changes.append(
        {"id": "chg-rb", "kind": "deploy", "at": 1, "service": "svc0", "version": "0", "rollback_of": "chg-svc0"}
    )
    changes.append({"id": "chg-flag", "kind": "flag", "at": 2, "flag": "f", "from": False, "to": True})
    monkeypatch.setattr(infra, "services", lambda: services)
    monkeypatch.setattr(infra, "all_stats", lambda window=20.0: stats)
    monkeypatch.setattr(infra, "read_changes", lambda: changes)
    monkeypatch.setattr(infra, "read_flags", lambda: {"f": True})
    monkeypatch.setattr(infra, "flags_meta", lambda: {"f": {"affects": names, "team": "web", "description": ""}})
    queries = []
    monkeypatch.setattr(store, "query", lambda q, p=None: queries.append(q) or [])
    return queries


def test_full_sync_round_trips_do_not_grow_with_the_graph(monkeypatch):
    counts = []
    for n in (2, 6):
        queries = _fake_sources(monkeypatch, n)
        ingest.sync_all()
        counts.append(len(queries))
    assert counts[0] == counts[1]
    writes = [q for q in queries if "MERGE" in q or "SET" in q]
    assert writes and all(q.startswith("UNWIND $rows AS row") for q in writes)


def test_trace_writes_one_query_per_label(monkeypatch):
    queries = []
    monkeypatch.setattr(store, "query", lambda q, p=None: queries.append((q, p)) or [])
    monkeypatch.setattr(tools, "_inv", {"id": "inv-test", "step": 0, "created": True})
    tools._trace("rank_suspects", "note", [("Service", "a"), ("Service", "b"), ("Service", "a"), ("Action", "act-1")])
    assert [p["keys"] for _, p in queries] == [["a", "b"], ["act-1"]]
    assert ":Service {name: key}" in queries[0][0] and ":Action {id: key}" in queries[1][0]
    queries.clear()
    tools._trace("query_graph", "no targets")
    assert len(queries) == 1 and queries[0][1]["keys"] == ["inv-test"]


def test_ingest_watch_schedules_full_syncs_and_survives_errors(monkeypatch, capsys):
    from rootpath import cli

    clock, cycles = {"t": 0.0}, []

    def cycle(full):
        cycles.append(full)
        if len(cycles) == 1:
            raise ConnectionError("blip")
        return "synced"

    def sleep(seconds):
        clock["t"] += seconds
        if len(cycles) == 14:
            raise KeyboardInterrupt

    fake_time = SimpleNamespace(monotonic=lambda: clock["t"], sleep=sleep, strftime=lambda fmt: "12:00:00")
    monkeypatch.setattr(cli, "time", fake_time)
    monkeypatch.setattr(cli, "_sync_cycle", cycle)
    cli.ingest(argparse.Namespace(watch=True, interval=5.0))
    # the failed first full sync is retried, then live cycles every 5 s and a full one 60 s later
    assert cycles == [True, True] + [False] * 11 + [True]
    out = capsys.readouterr().out
    assert "sync failed: ConnectionError: blip" in out and out.rstrip().endswith("ingest stopped")


def test_console_is_served_from_web_dist(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from rootpath import web

    monkeypatch.setattr(web, "DIST", tmp_path)
    c = TestClient(web.app)
    hint = c.get("/")
    assert hint.status_code == 200 and "npm run build" in hint.text
    (tmp_path / "console.html").write_text("<!doctype html><title>console</title>")
    built = c.get("/")
    assert built.status_code == 200 and "<title>console</title>" in built.text
    assert c.get("/console.js").status_code == 404
