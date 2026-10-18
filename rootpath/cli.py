"""rootpath command line: run the demo stack, inject failures, serve MCP / UI, run the watcher."""

import argparse
import json
import subprocess
import sys
import time
from collections import Counter

from . import config


def demo_up(args) -> None:
    from . import infra, ingest, seed

    if not (infra.ENV_DIR / "payment.env").exists():
        seed.write_baseline()
    print("building demo service image ...")
    subprocess.run(
        ["docker", "build", "-q", "-t", "oncall-demo-service:latest", str(config.DEMO / "service")], check=True
    )
    infra.compose("up", "-d", "--remove-orphans", timeout=300)
    _wait_for_db()
    seed.seed_changes()
    ingest.sync_all(health=False)
    seed.seed_incidents()
    print("waiting for traffic ...")
    time.sleep(6)
    ingest.sync_health()
    print("demo stack up. UI: `rootpath ui` -> http://localhost:7100   FalkorDB browser: http://localhost:3005")


def demo_down(args) -> None:
    from . import infra

    infra.compose("down", *(["-v"] if args.volumes else []), timeout=300)


def demo_soft_reset() -> None:
    """Back to healthy, but keep the graph (incident memory survives): differences from the
    baseline are undone through recorded deploys and flag changes, like a real revert."""
    from . import infra, ingest

    base = json.loads((config.DEMO / "baseline.json").read_text())
    for service, env in base["services"].items():
        if infra.read_env(service).get("VERSION") != env["VERSION"] or infra.read_env(service).get("BUG"):
            infra.deploy(service, env["VERSION"], author="release-bot", note="reset to stable release")
    flags = infra.read_flags()
    for flag, value in base["flags"].items():
        if flags.get(flag) != value:
            infra.set_flag(flag, value, author="release-bot", note="reset to default")
    infra.restart("inventory")
    ingest.sync_changes()
    time.sleep(5)
    ingest.sync_health()
    print("soft reset: services healthy, incident memory kept")


def demo_reset(args) -> None:
    reset(soft=args.soft)


def reset(soft: bool = False) -> None:
    """Back to a healthy baseline. Hard (default): fresh graph and history. Soft: keep the graph."""
    if soft:
        return demo_soft_reset()
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
    "deploy-noise": "busy deploy hour (5 healthy deploys, two on the failing path) + payment v2.4.1 tokenizer bug",
    "flag-collision": "fraud-check v1.9.3 deployed 3 min before fraud_model_v2 is ramped: two changes on the slow service",
    "pool-after-deploy": "inventory pool exhaustion right after an unrelated, healthy inventory release",
}


def _busy_hour(deploys: list[tuple]) -> None:
    """Real (healthy) deploys, back-dated into the last hour like a normal busy release day."""
    from . import infra

    for service, version, author, note, minutes_ago in deploys:
        infra.deploy(service, version, author=author, note=note, at=time.time() - minutes_ago * 60)
    time.sleep(15)  # let the restarts settle so they don't look like the incident


def chaos(args) -> None:
    inject(args.scenario)


def inject(s: str) -> None:
    """Inject one of the SCENARIOS into the running demo stack."""
    from . import infra

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
    elif s == "deploy-noise":
        _busy_hour(
            [
                ("inventory", "5.1.2", "sam", "pgbouncer config bump", 41),
                ("frontend", "2.8.1", "meera", "header copy", 35),
                ("checkout", "4.2.1", "kabir", "coupon validation", 22),
                ("catalog", "3.1.0", "zoya", "search ranking tweak", 12),
                ("notify", "1.2.1", "li", "email template copy change", 6),
            ]
        )
        infra.deploy("payment", "2.4.1", bug="null_pointer", author="riya", note="saved-card tokenization refactor")
    elif s == "flag-collision":
        _busy_hour(
            [
                ("checkout", "4.2.1", "kabir", "coupon validation", 25),
                ("notify", "1.2.1", "li", "email template copy change", 10),
                ("fraud-check", "1.9.3", "omar", "feature extraction cleanup", 3),
            ]
        )
        infra.set_flag("fraud_model_v2", True, author="omar", note="ramp v2 fraud model to 100%")
    elif s == "pool-after-deploy":
        _busy_hour(
            [
                ("checkout", "4.2.1", "kabir", "coupon validation", 30),
                ("inventory", "5.1.2", "sam", "pgbouncer config bump", 14),
            ]
        )
        infra.chaos_pool_exhaust("inventory")
    print(f"injected: {SCENARIOS[s]}")


def status(args) -> None:
    from . import infra

    for name, s in infra.all_stats().items():
        print(
            f"{name:<12} {s['status']:<9} v{s.get('version', '?'):<7} err {s.get('error_rate', 0):>5.0%}  "
            f"p95 {s.get('p95_ms', 0):>6.0f}ms  req {s.get('requests', 0)}"
        )


FULL_SYNC_EVERY = 60.0  # seconds between full syncs in `ingest --watch`


def _sync_cycle(full: bool) -> str:
    """One ingest pass: everything, or only what changes between full syncs (change log + health)."""
    from . import infra, ingest

    started = time.monotonic()
    if full:
        infra.services.cache_clear()  # pick up compose-file edits
        new = ingest.sync_all(health=False)
    else:
        new = ingest.sync_changes()
    health = Counter(s["status"] for s in ingest.sync_health().values())
    services = ", ".join(f"{n} {status}" for status, n in health.most_common())
    return (
        f"{time.strftime('%H:%M:%S')} {'full' if full else 'live'} sync: {new} new changes, "
        f"{services} ({time.monotonic() - started:.2f}s)"
    )


def ingest(args) -> None:
    """One full sync, or with --watch keep the graph current until Ctrl-C."""
    if not args.watch:
        print(_sync_cycle(full=True))
        return
    last_full = None
    try:
        while True:
            started = time.monotonic()
            full = last_full is None or started - last_full >= FULL_SYNC_EVERY
            try:
                print(_sync_cycle(full), flush=True)
                if full:
                    last_full = started
            except Exception as e:  # keep running through a dropped connection or a restarting service
                print(f"{time.strftime('%H:%M:%S')} sync failed: {type(e).__name__}: {e}", flush=True)
            time.sleep(max(0.0, args.interval - (time.monotonic() - started)))
    except KeyboardInterrupt:
        print("ingest stopped")


def mcp(args) -> None:
    from .mcp_server import main

    main(args.mode)


def ui(args) -> None:
    import uvicorn

    uvicorn.run("rootpath.web:app", host=args.host, port=args.port, log_level="warning")


def watch(args) -> None:
    from .responder import watch as run

    run(mode=args.mode, autonomy=args.autonomy, once=args.once)


def bench(args) -> None:
    from .bench import run

    run(scenarios=args.scenario, modes=args.mode, repeats=args.repeats)


def call(args) -> None:
    """Call a tool directly, e.g. `rootpath call rank_suspects symptom_service=frontend`."""
    import os

    os.environ.setdefault("ONCALL_INVESTIGATION_ID", "cli-debug")
    os.environ.setdefault("ONCALL_AGENT", "cli")
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
    sys.exit(f"FalkorDB did not come up on {config.FALKORDB['host']}:{config.FALKORDB['port']}")


def _seconds(value: str) -> float:
    seconds = float(value)
    if seconds <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return seconds


def main() -> None:
    p = argparse.ArgumentParser(prog="rootpath")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo", help="manage the demo stack").add_subparsers(dest="demo_cmd", required=True)
    d.add_parser("up").set_defaults(fn=demo_up)
    down = d.add_parser("down")
    down.add_argument("--volumes", action="store_true")
    down.set_defaults(fn=demo_down)
    r = d.add_parser("reset")
    r.add_argument("--soft", action="store_true", help="keep the graph (incident memory), only restore healthy state")
    r.set_defaults(fn=demo_reset)
    c = sub.add_parser("chaos", help="inject a failure scenario")
    c.add_argument("scenario", choices=list(SCENARIOS))
    c.set_defaults(fn=chaos)
    sub.add_parser("status", help="live health table").set_defaults(fn=status)
    ing = sub.add_parser("ingest", help="sync the graph from sources (once, or continuously with --watch)")
    ing.add_argument(
        "--watch",
        action="store_true",
        help=f"keep syncing changes + health, and everything every {FULL_SYNC_EVERY:.0f}s",
    )
    ing.add_argument("--interval", type=_seconds, default=5.0, help="seconds between --watch cycles (default 5)")
    ing.set_defaults(fn=ingest)
    sub.add_parser("sync", help="one full sync (same as `ingest`)").set_defaults(fn=ingest, watch=False)
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
    rec = sub.add_parser("record", help="record a real incident for the website replay")
    rec.add_argument("scenario", choices=list(SCENARIOS))
    rec.add_argument("--out", help="default: web/public/data/replays/<scenario>.json")
    rec.add_argument("--mode", choices=["graph", "baseline"], default="graph")
    rec.set_defaults(fn=lambda a: __import__("rootpath.record", fromlist=["record"]).record(a.scenario, a.out, a.mode))
    t = sub.add_parser("call", help="call a tool directly (debugging)")
    t.add_argument("tool")
    t.add_argument("kwargs", nargs="*")
    t.set_defaults(fn=call)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
