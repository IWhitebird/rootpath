"""A tiny configurable microservice for the Rootpath demo stack.

Every service in the demo runs this same file; its behaviour comes from env vars:
  SERVICE_NAME     name of the service
  VERSION          version currently deployed
  BUG              a defect shipped with this version ("null_pointer", "slow", "")
  CALLS            downstream services called by /work (comma separated)
  ROUTES           named routes and their downstreams, e.g. "browse=catalog;checkout=checkout"
  OPTIONAL_CALLS   downstreams whose failure is tolerated
  FLAG_LATENCY     feature flags that add latency, e.g. "fraud_model_v2=2200"
"""
import asyncio
import json
import os
import random
import time
from collections import Counter, deque
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

NAME = os.environ["SERVICE_NAME"]
VERSION = os.environ.get("VERSION", "1.0.0")
BUG = os.environ.get("BUG", "")
CALLS = [c for c in os.environ.get("CALLS", "").split(",") if c]
OPTIONAL = {c for c in os.environ.get("OPTIONAL_CALLS", "").split(",") if c}
ROUTES = {"work": CALLS}
for part in filter(None, os.environ.get("ROUTES", "").split(";")):
    route, _, deps = part.partition("=")
    ROUTES[route] = [d for d in deps.split(",") if d]
FLAG_LATENCY = {}
for part in filter(None, os.environ.get("FLAG_LATENCY", "").split(",")):
    flag, _, ms = part.partition("=")
    FLAG_LATENCY[flag] = float(ms)
FLAGS_FILE = os.environ.get("FLAGS_FILE", "/flags/flags.json")
TIMEOUT = float(os.environ.get("CALL_TIMEOUT", "1.5"))
BASE_LATENCY_MS = float(os.environ.get("BASE_LATENCY_MS", "15"))

app = FastAPI()
client = httpx.AsyncClient(timeout=TIMEOUT)
requests_log: deque = deque(maxlen=20000)  # (ts, status, latency_ms, origin)
calls_log: deque = deque(maxlen=40000)  # (ts, downstream, ok, latency_ms)
calls_total: Counter = Counter()
state = {"pool_exhausted": False}
STARTED = time.time()


class OwnError(Exception):
    pass


def log(level: str, msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    print(f"{ts} {level:<5} [{NAME} v{VERSION}] {msg}", flush=True)


def flags() -> dict:
    try:
        with open(FLAGS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


async def own_work(route: str) -> None:
    latency = BASE_LATENCY_MS * random.uniform(0.6, 1.4)
    active = flags()
    for flag, ms in FLAG_LATENCY.items():
        if active.get(flag):
            latency += ms * random.uniform(0.9, 1.3)
    if BUG == "slow":
        latency += 2000
    await asyncio.sleep(latency / 1000)
    if BUG == "null_pointer" and random.random() < 0.7:
        raise OwnError(
            "Traceback (most recent call last):\n"
            '  File "/app/payments/handler.py", line 41, in charge\n'
            "    token = tokenizer.tokenize(order.card)\n"
            '  File "/app/payments/card_tokenizer.py", line 88, in tokenize\n'
            '    return card["pan"][-4:] + self._vault.put(card)\n'
            "TypeError: 'NoneType' object is not subscriptable"
        )
    if state["pool_exhausted"] and random.random() < 0.9:
        raise OwnError(
            "psycopg_pool.PoolTimeout: couldn't get a connection after 1.00 sec "
            "(pool size 10, 10 in use, 37 requests waiting)"
        )


async def call(dep: str) -> tuple[bool, str]:
    started = time.time()
    try:
        r = await client.get(f"http://{dep}:8000/work")
        ok, detail = r.status_code < 500, f"HTTP {r.status_code}"
    except httpx.TimeoutException:
        ok, detail = False, f"timeout after {TIMEOUT}s"
    except httpx.HTTPError as e:
        ok, detail = False, f"connection error ({type(e).__name__})"
    calls_log.append((time.time(), dep, ok, (time.time() - started) * 1000))
    calls_total[dep] += 1
    return ok, detail


def record(started: float, status: int, origin: str, route: str) -> JSONResponse:
    latency = (time.time() - started) * 1000
    requests_log.append((time.time(), status, latency, origin))
    if status < 500 and random.random() < 0.03:
        log("INFO", f"GET /{route} {status} {latency:.0f}ms")
    return JSONResponse({"service": NAME, "version": VERSION, "status": status}, status_code=status)


async def handle(route: str) -> JSONResponse:
    started = time.time()
    try:
        await own_work(route)
        for dep in ROUTES.get(route, []):
            ok, detail = await call(dep)
            if ok:
                continue
            if dep in OPTIONAL:
                log("WARN", f"/{route}: optional dependency '{dep}' failed ({detail}); continuing without it")
                continue
            log("ERROR", f"/{route}: dependency '{dep}' failed ({detail}); returning error to caller")
            return record(started, 504 if "timeout" in detail else 502, "dependency", route)
        return record(started, 200, "ok", route)
    except OwnError as e:
        log("ERROR", f"/{route}: unhandled exception\n{e}")
        return record(started, 500, "own", route)


@app.get("/work")
async def work():
    return await handle("work")


@app.get("/r/{route}")
async def routed(route: str):
    return await handle(route)


@app.get("/_stats")
def stats(window: float = 20.0):
    now = time.time()
    recent = [r for r in requests_log if now - r[0] <= window]
    n = len(recent)
    errors = [r for r in recent if r[1] >= 500]
    own = sum(1 for r in errors if r[3] == "own")
    latencies = sorted(r[2] for r in recent)
    calls: dict = {}
    for ts, dep, ok, lat in calls_log:
        if now - ts <= window:
            c = calls.setdefault(dep, {"count": 0, "errors": 0})
            c["count"] += 1
            c["errors"] += 0 if ok else 1
    return {
        "service": NAME,
        "version": VERSION,
        "window_s": window,
        "requests": n,
        "errors": len(errors),
        "error_rate": round(len(errors) / n, 3) if n else 0.0,
        "errors_originated_here": own,
        "errors_from_dependencies": len(errors) - own,
        "p50_ms": round(latencies[n // 2], 1) if n else 0.0,
        "p95_ms": round(latencies[int(0.95 * (n - 1))], 1) if n else 0.0,
        "calls": calls,
        "calls_total": dict(calls_total),
        "uptime_s": round(now - STARTED),
    }


@app.get("/health")
def health():
    return {"ok": True, "service": NAME, "version": VERSION}


@app.post("/_chaos/{kind}")
def chaos(kind: str):
    if os.environ.get("CHAOS_ENABLED") != "1":
        return JSONResponse({"error": "chaos disabled"}, status_code=403)
    if kind == "pool_exhaust":
        state["pool_exhausted"] = True
        log("WARN", "connection pool usage at 100% (10/10), requests queueing")
    return {"ok": True, "kind": kind}
