"""End-to-end tests against the live demo stack: inject each failure, check the graph reasoning,
take the fix through the real action tools, verify recovery and incident memory."""
import pytest

from oncall_graph import config, infra, store, tools
from oncall_graph.cli import inject as inject_scenario

from .conftest import wait_for_alert

pytestmark = pytest.mark.integration

# scenario -> (root-cause service, kind of change that caused it, red herrings that must be ruled out or scored low)
CASES = {
    "bad-deploy": ("payment", "deploy", ["catalog"]),
    "flag": ("fraud-check", "flag", ["notify"]),
    "pool": ("inventory", None, []),
    "deploy-noise": ("payment", "deploy", ["checkout", "frontend", "catalog", "notify", "inventory"]),
    "flag-collision": ("fraud-check", "flag", ["checkout", "notify"]),
    "pool-after-deploy": ("inventory", None, ["checkout"]),
}


def inject(name):
    inject_scenario(name)
    return wait_for_alert()


def test_agent_reasoning_is_read_only(stack):
    with pytest.raises(Exception):
        store.safe_read("CREATE (:Hacked {x: 1}) RETURN 1")
    assert not store.query("MATCH (h:Hacked) RETURN h")


@pytest.mark.parametrize("scenario", list(CASES))
def test_rank_suspects_finds_the_origin(fresh, scenario):
    service, kind, herrings = CASES[scenario]
    alert_service, _ = inject(scenario)
    assert alert_service == "frontend"  # the alert fires on the user-facing service, not the broken one
    out = tools.rank_suspects(alert_service)
    assert out["top_suspect"] == service, out
    top = out["suspects"][0]
    assert out["path_from_symptom"][0] == "frontend" and out["path_from_symptom"][-1] == service
    if kind:
        assert any(f": {kind} " in c for c in top["recent_changes"]), top
    scores = {s["service"]: s["score"] for s in out["suspects"] + out["other_services_on_the_path"]}
    for h in herrings:
        assert scores.get(h, 0) < top["score"] / 2, (h, scores)


def test_bad_deploy_full_loop_and_memory(fresh):
    inject("bad-deploy")
    inc = tools.open_incident("payment 5xx", "checkout 502 payment TypeError tokenizer", ["frontend", "checkout", "payment"])
    plan = tools.rollback_service("payment", "tokenizer TypeError", inc["incident_id"], dry_run=True)
    assert plan["dry_run"] and plan["plan"]["from_version"] == "2.4.1" and plan["plan"]["to_version"] == "2.4.0"
    assert "checkout" in plan["plan"]["blast_radius"] and "frontend" in plan["plan"]["blast_radius"]
    assert infra.read_env("payment")["VERSION"] == "2.4.1"  # dry run changed nothing
    done = tools.rollback_service("payment", "tokenizer TypeError", inc["incident_id"], dry_run=False)
    assert not done["dry_run"] and infra.read_env("payment")["VERSION"] == "2.4.0"
    assert tools.verify_recovery("frontend", timeout_seconds=60)["recovered"]
    # A second rollback must not go back onto the bad 2.4.1 release.
    again = tools.rollback_service("payment", "test", dry_run=True)
    assert again["plan"]["to_version"] not in ("2.4.1", "2.4.0")
    page = tools.notify_owner("payment", "rolled back", inc["incident_id"])
    assert page["paged"] == "riya" and page["team"] == "payments"
    change_id = plan["plan"]["reverts_change"]["id"]
    res = tools.resolve_incident(inc["incident_id"], "payment", "tokenizer bug in 2.4.1", change_id)
    assert res["status"] == "resolved"
    explained = tools.explain_incident(inc["incident_id"])
    assert any(rc.get("id") == change_id for rc in explained["root_cause"])
    assert any(a["kind"] == "rollback" and a["dry_run"] is False for a in explained["actions"])
    memory = tools.find_similar_incidents("checkout 502 payment TypeError tokenizer", service="payment")
    assert inc["incident_id"] in [m["incident"] for m in memory["matches"]]


def test_flag_fix_and_owner(fresh):
    inject("flag")
    plan = tools.set_feature_flag("fraud_model_v2", False, "latency", dry_run=True)
    assert plan["dry_run"] and infra.read_flags()["fraud_model_v2"] is True
    tools.set_feature_flag("fraud_model_v2", False, "latency", dry_run=False)
    assert infra.read_flags()["fraud_model_v2"] is False
    assert tools.verify_recovery("frontend", timeout_seconds=60)["recovered"]
    owner = tools.get_owner("fraud-check")
    assert owner["on_call"] == "ananya" and owner["runbooks"][0]["id"] == "RB-FRAUD-01"


def test_pool_restart_and_memory_recall(fresh):
    inject("pool")
    memory = tools.find_similar_incidents("checkout 502 inventory PoolTimeout connection pool exhausted", service="inventory")
    assert memory["matches"][0]["incident"] == "INC-019"
    assert memory["matches"][0]["fixed_by"][0]["kind"] == "restart"
    tools.restart_service("inventory", "pool exhausted", dry_run=False)
    assert tools.verify_recovery("frontend", timeout_seconds=60)["recovered"]


def test_actions_disabled_forces_dry_run(fresh, monkeypatch):
    inject("flag")
    monkeypatch.setattr(config, "ALLOW_ACTIONS", False)
    out = tools.set_feature_flag("fraud_model_v2", False, "test", dry_run=False)
    assert out["dry_run"] is True and "disabled" not in out.get("error", "")
    assert infra.read_flags()["fraud_model_v2"] is True


def test_unknown_inputs_are_errors_not_crashes(stack):
    assert "error" in tools.rollback_service("nope", "x")
    assert "error" in tools.restart_service("nope", "x")
    assert "error" in tools.set_feature_flag("nope", True, "x")
    assert "error" in tools.rank_suspects("nope")
    assert "error" in tools.get_owner("nope")
    assert tools.trace_dependency_path("notify", "frontend")["path"] == []  # no call path upstream
    assert "error" in tools.query_graph("THIS IS NOT CYPHER")


def test_blast_radius_and_paths(stack):
    br = tools.get_blast_radius("inventory")
    upstream = {r["service"] for r in br["affected_if_it_fails (upstream callers)"]}
    assert upstream == {"catalog", "checkout", "frontend"} and br["user_facing_impact"]
    assert tools.trace_dependency_path("frontend", "fraud-check")["path"] == ["frontend", "checkout", "payment", "fraud-check"]


def test_web_api(stack):
    from fastapi.testclient import TestClient
    from oncall_graph.web import app
    c = TestClient(app)
    assert c.get("/").status_code == 200
    g = c.get("/api/graph").json()
    types = {n["type"] for n in g["nodes"]}
    assert {"Service", "Team", "Incident", "Flag"} <= types
    ids = {n["id"] for n in g["nodes"]}
    assert all(l["source"] in ids and l["target"] in ids for l in g["links"])
    assert set(c.get("/api/health").json()["services"]) == set(infra.services())
    assert c.post("/api/chaos/does-not-exist").status_code == 404
