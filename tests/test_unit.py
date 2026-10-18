"""Fast tests with the graph and Docker mocked out."""
import asyncio
import json

import pytest

from oncall_graph import config, guard, infra, store, tools


def test_safe_read_appends_limit(monkeypatch):
    seen = {}
    monkeypatch.setattr(store, "read", lambda q, p=None, timeout_ms=None: seen.setdefault("q", q) and [])
    store.safe_read("MATCH (n) RETURN n;")
    assert seen["q"].endswith("LIMIT 100")
    seen.clear()
    store.safe_read("MATCH (n) RETURN n LIMIT 5")
    assert seen["q"].endswith("LIMIT 5") and "LIMIT 100" not in seen["q"]


@pytest.mark.parametrize("stats,expected", [
    ({"requests": 0, "error_rate": 0, "p95_ms": 0}, "idle"),
    ({"requests": 50, "error_rate": 0.5, "p95_ms": 30}, "failing"),
    ({"requests": 50, "error_rate": 0.05, "p95_ms": 30}, "degraded"),
    ({"requests": 50, "error_rate": 0.0, "p95_ms": 2200}, "degraded"),
    ({"requests": 50, "error_rate": 0.0, "p95_ms": 40}, "healthy"),
])
def test_classify(stats, expected):
    assert infra.classify(stats) == expected


def _event(tool, **args):
    return {"tool_name": f"mcp__plugin_oncall-graph_oncall__{tool}", "tool_input": args}


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
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps(_event("rollback_service", service="p", dry_run=False))))
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
    deploy = {"id": "chg-1", "kind": "deploy", "service": "payment", "version": "2.4.1", "prev_version": "2.4.0",
              "author": "riya", "at": 0, "iso": "x", "note": "refactor"}
    flag = {"id": "chg-2", "kind": "flag", "_labels": ["Change", "FlagChange"], "flag": "f", "from": False, "to": True,
            "author": "omar", "at": 0, "iso": "x"}
    assert tools._change_line(deploy).startswith("chg-1: deploy payment v2.4.0 -> v2.4.1 by riya")
    assert tools._change_line(flag).startswith("chg-2: flag f False -> True by omar")


def test_health_view_hides_tracing_fields_in_basic_telemetry(monkeypatch):
    s = {"status": "failing", "error_rate": 0.5, "errors_originated_here": 3, "errors_from_dependencies": 1, "p95_ms": 1}
    assert "errors_originated_here" in tools._health_view(s)
    monkeypatch.setattr(config, "TELEMETRY", "basic")
    assert not any(k.startswith("errors_") for k in tools._health_view(s))


def test_mcp_tool_sets():
    from oncall_graph.mcp_server import build

    def names(mode):
        return {t.name for t in asyncio.run(build(mode).list_tools())}
    graph, baseline = names("graph"), names("baseline")
    assert {"rank_suspects", "find_similar_incidents", "open_incident", "resolve_incident"} <= graph
    assert baseline == {"get_service_health", "get_logs", "list_changes_flat", "rollback_service",
                        "restart_service", "set_feature_flag", "verify_recovery"}
    assert not graph & {"list_changes_flat"}


def test_tool_annotations_mark_actions_destructive():
    from oncall_graph.mcp_server import build
    tools_by_name = {t.name: t for t in asyncio.run(build("graph").list_tools())}
    assert tools_by_name["rollback_service"].annotations.destructiveHint is True
    assert tools_by_name["rank_suspects"].annotations.readOnlyHint is True


def test_propose_mode_disables_actions_in_the_server(tmp_path, monkeypatch):
    from oncall_graph import responder
    monkeypatch.setattr(config, "ROOT", tmp_path)
    auto = json.loads(open(responder.mcp_config("graph", "inv-test", "auto")).read())
    propose = json.loads(open(responder.mcp_config("graph", "inv-test", "propose")).read())
    assert "ONCALL_ALLOW_ACTIONS" not in auto["mcpServers"]["oncall"]["env"]
    assert propose["mcpServers"]["oncall"]["env"]["ONCALL_ALLOW_ACTIONS"] == "0"
