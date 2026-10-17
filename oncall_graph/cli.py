"""oncall-graph command line: run the demo stack, inject failures, serve MCP / UI, run the watcher."""
import argparse
import json
import subprocess
import sys
import time

from . import config


def demo_up(args) -> None:
    from . import infra, ingest, seed
    if not (infra.ENV_DIR / "payment.env").exists():
        seed.write_baseline()
    print("building demo service image ...")
    subprocess.run(["docker", "build", "-q", "-t", "oncall-demo-service:latest", str(config.DEMO / "service")], check=True)
    infra.compose("up", "-d", "--remove-orphans", timeout=300)
    _wait_for_db()
    seed.seed_changes()
    ingest.sync_all(health=False)
    seed.seed_incidents()
    print("waiting for traffic ...")
    time.sleep(6)
    ingest.sync_health()
    print("demo stack up. UI: `oncall-graph ui` -> http://localhost:7100   FalkorDB browser: http://localhost:3005")


def demo_down(args) -> None:
    from . import infra
    infra.compose("down", *(["-v"] if args.volumes else []), timeout=300)


def demo_reset(args) -> None:
    """Back to a clean, healthy baseline with fresh history."""
    from . import infra, ingest, seed, store
    seed.write_baseline()
    infra.compose("up", "-d", "--remove-orphans", timeout=300)
    for svc in ("inventory",):  # clear in-memory chaos state
        infra.restart(svc)
    store.delete_graph()
    store._graph = None
    if infra.CHANGES.exists():
        infra.CHANGES.unlink()
    seed.seed_changes()
    ingest.sync_all(health=False)
    seed.seed_incidents()
    time.sleep(5)
    ingest.sync_health()
    print("reset to healthy baseline")


SCENARIOS = {
    "bad-deploy": "payment v2.4.1 ships a card tokenizer bug (+ an unrelated catalog deploy as a red herring)",
    "flag": "fraud_model_v2 flag turned on: fraud-check latency breaks payment (+ an unrelated notify deploy)",
    "pool": "inventory's DB connection pool gets exhausted: no change at all, restart needed",
}


def chaos(args) -> None:
    from . import infra
    s = args.scenario
    if s == "bad-deploy":
        infra.deploy("catalog", "3.1.0", author="zoya", note="search ranking tweak")
        time.sleep(2)
        infra.deploy("payment", "2.4.1", bug="null_pointer", author="riya", note="saved-card tokenization refactor")
    elif s == "flag":
        infra.deploy("notify", "1.2.1", author="li", note="email template copy change")
        time.sleep(1)
        infra.set_flag("fraud_model_v2", True, author="omar", note="ramp v2 fraud model to 100%")
    elif s == "pool":
        infra.chaos_pool_exhaust("inventory")
    print(f"injected: {SCENARIOS[s]}")


def status(args) -> None:
    from . import infra
    for name, s in infra.all_stats().items():
        print(f"{name:<12} {s['status']:<9} v{s.get('version', '?'):<7} err {s.get('error_rate', 0):>5.0%}  "
              f"p95 {s.get('p95_ms', 0):>6.0f}ms  req {s.get('requests', 0)}")


def sync(args) -> None:
    from . import ingest
    ingest.sync_all()
    print("graph synced")


def mcp(args) -> None:
    from .mcp_server import main
    main(args.mode)


def ui(args) -> None:
    import uvicorn
    uvicorn.run("oncall_graph.web:app", host=args.host, port=args.port, log_level="warning")


def watch(args) -> None:
    from .responder import watch as run
    run(mode=args.mode, autonomy=args.autonomy, once=args.once)


def bench(args) -> None:
    from .bench import run
    run(scenarios=args.scenario, modes=args.mode, repeats=args.repeats)


def call(args) -> None:
    """Call a tool directly, e.g. `oncall-graph call rank_suspects symptom_service=frontend`."""
    from . import tools
    kwargs = {}
    for kv in args.kwargs:
        k, _, v = kv.partition("=")
        try:
            kwargs[k] = json.loads(v)
        except json.JSONDecodeError:
            kwargs[k] = v
    print(tools.to_json(getattr(tools, args.tool)(**kwargs)))


def _wait_for_db() -> None:
    from . import store
    for _ in range(30):
        try:
            store.query("RETURN 1")
            return
        except Exception:
            store._graph = None
            time.sleep(1)
    sys.exit("FalkorDB did not come up on " + f"{config.FALKORDB_HOST}:{config.FALKORDB_PORT}")


def main() -> None:
    p = argparse.ArgumentParser(prog="oncall-graph")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo", help="manage the demo stack").add_subparsers(dest="demo_cmd", required=True)
    d.add_parser("up").set_defaults(fn=demo_up)
    down = d.add_parser("down")
    down.add_argument("--volumes", action="store_true")
    down.set_defaults(fn=demo_down)
    d.add_parser("reset").set_defaults(fn=demo_reset)
    c = sub.add_parser("chaos", help="inject a failure scenario")
    c.add_argument("scenario", choices=list(SCENARIOS))
    c.set_defaults(fn=chaos)
    sub.add_parser("status", help="live health table").set_defaults(fn=status)
    sub.add_parser("sync", help="rebuild the graph from sources").set_defaults(fn=sync)
    m = sub.add_parser("mcp", help="run the MCP server on stdio")
    m.add_argument("--mode", choices=["graph", "baseline"], default=None)
    m.set_defaults(fn=mcp)
    u = sub.add_parser("ui", help="graph UI + API")
    u.add_argument("--host", default="127.0.0.1")
    u.add_argument("--port", type=int, default=7100)
    u.set_defaults(fn=ui)
    w = sub.add_parser("watch", help="alert watcher that launches a headless agent")
    w.add_argument("--mode", choices=["graph", "baseline"], default="graph")
    w.add_argument("--autonomy", choices=["auto", "propose"], default="auto")
    w.add_argument("--once", action="store_true", help="exit after handling one alert")
    w.set_defaults(fn=watch)
    b = sub.add_parser("bench", help="graph vs baseline benchmark")
    b.add_argument("--scenario", nargs="+", default=list(SCENARIOS), choices=list(SCENARIOS))
    b.add_argument("--mode", nargs="+", default=["graph", "baseline"], choices=["graph", "baseline"])
    b.add_argument("--repeats", type=int, default=1)
    b.set_defaults(fn=bench)
    t = sub.add_parser("call", help="call a tool directly (debugging)")
    t.add_argument("tool")
    t.add_argument("kwargs", nargs="*")
    t.set_defaults(fn=call)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
