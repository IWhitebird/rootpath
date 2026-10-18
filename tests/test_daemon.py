"""The background server: state paths, the pid file, start/stop, the loops it runs, and (with
FalkorDB reachable) a real `rootpath serve` answering /api/health and MCP over HTTP."""

import asyncio
import contextlib
import functools
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from rootpath import cli, daemon

REPO = Path(__file__).resolve().parents[1]

# Looks like `rootpath serve` to the pid checks (its arguments include "rootpath" and "serve").
FAKE_SERVER = """
import signal, sys, time
if "--ignore-sigterm" in sys.argv:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
print("ready", flush=True)
time.sleep(60)
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return tmp_path / "state" / "rootpath"


@pytest.fixture
def fake_server(state):
    """Start a fake server process and register it in the pid file, as a running server would."""
    procs = []

    def spawn(*flags: str, **info) -> subprocess.Popen:
        proc = subprocess.Popen(
            [sys.executable, "-c", FAKE_SERVER, "rootpath", "serve", *flags], stdout=subprocess.PIPE, text=True
        )
        proc.stdout.readline()
        procs.append(proc)
        state.mkdir(parents=True, exist_ok=True)
        (state / "rootpath.pid").write_text(f"{proc.pid}\n")
        info = {"pid": proc.pid, "port": 7111, "started_at": time.time(), **info}
        (state / "server.json").write_text(json.dumps(info))
        return proc

    yield spawn
    for proc in procs:
        proc.kill()
        proc.wait()


def test_state_paths_follow_xdg_state_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    assert daemon.state_dir() == tmp_path / "xdg" / "rootpath"
    assert {daemon.pid_path().parent, daemon.info_path().parent, daemon.log_path().parent} == {daemon.state_dir()}
    assert daemon.log_path().name == "rootpath.log"
    for ignored in ("", "relative/state"):  # unset, or not absolute: the XDG spec says to ignore it
        monkeypatch.setenv("XDG_STATE_HOME", ignored)
        assert daemon.state_dir() == tmp_path / "home" / ".local" / "state" / "rootpath"


def test_claim_writes_the_pid_file_and_release_removes_it(state):
    info = daemon.claim(port=7111, ingest=True, watch_alerts=False)
    assert (state / "rootpath.pid").read_text() == f"{os.getpid()}\n"
    assert json.loads((state / "server.json").read_text()) == info
    assert info["pid"] == os.getpid() and info["port"] == 7111 and info["started_at"] <= time.time()
    assert state.stat().st_mode & 0o777 == 0o700
    daemon.release()
    assert list(state.iterdir()) == []


def test_stale_pid_files_are_cleared_and_never_signalled(state):
    exited = subprocess.Popen([sys.executable, "-c", "pass"])
    exited.wait()
    unrelated = subprocess.Popen(["sleep", "30"])  # alive, like a process that was given a crashed server's pid
    try:
        for content in (exited.pid, unrelated.pid, "not a pid", -1, 0):
            state.mkdir(parents=True, exist_ok=True)
            (state / "rootpath.pid").write_text(f"{content}\n")
            (state / "server.json").write_text(json.dumps({"pid": content, "port": 7111}))
            assert daemon.running() is None
            assert list(state.iterdir()) == []
            (state / "rootpath.pid").write_text(f"{content}\n")
            assert daemon.stop() is None
        assert unrelated.poll() is None
    finally:
        unrelated.kill()
        unrelated.wait()
    assert daemon.claim(port=7111)["pid"] == os.getpid()


def test_a_second_server_is_refused(state, fake_server, monkeypatch):
    proc = fake_server()
    monkeypatch.setattr(daemon, "_command", lambda *args: pytest.fail("started a second server"))
    assert daemon.running()["pid"] == proc.pid
    with pytest.raises(daemon.AlreadyRunning, match=f"pid {proc.pid}, http://localhost:7111"):
        daemon.claim(port=7112)
    with pytest.raises(daemon.AlreadyRunning):
        daemon.start(7112)
    assert (state / "rootpath.pid").read_text() == f"{proc.pid}\n"


def test_stop_signals_the_server_and_waits_for_it(state, fake_server):
    proc = fake_server()
    assert daemon.stop(timeout=5)["pid"] == proc.pid
    assert proc.wait(5) == -signal.SIGTERM
    assert daemon.running() is None and list(state.iterdir()) == []


def test_stop_reports_a_server_that_keeps_running(fake_server):
    proc = fake_server("--ignore-sigterm")
    with pytest.raises(daemon.DaemonError, match=f"pid {proc.pid}\\) is still running"):
        daemon.stop(timeout=0.3)
    assert proc.poll() is None and daemon.running()["pid"] == proc.pid


def test_start_and_serve_refuse_a_port_in_use(state, monkeypatch):
    monkeypatch.setattr(daemon, "_command", lambda *args: pytest.fail("spawned a server"))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        with pytest.raises(daemon.DaemonError, match=f"port {port} is already in use"):
            daemon.start(port)
        with pytest.raises(daemon.DaemonError, match=f"port {port} is already in use"):
            daemon.serve(port)
    assert not (state / "rootpath.pid").exists()


def test_a_port_that_is_not_one_is_refused_up_front(state, monkeypatch, capsys):
    monkeypatch.setattr(daemon, "_command", lambda *args: pytest.fail("spawned a server"))
    for cmd in ("start", "serve"):
        monkeypatch.setattr(sys, "argv", ["rootpath", cmd, "--port", "70000"])
        with pytest.raises(SystemExit):
            cli.main()
        assert "--port: must be from 1 to 65535" in capsys.readouterr().err


def test_start_reports_a_server_that_exits(state, monkeypatch):
    state.mkdir(parents=True)
    (state / "rootpath.log").write_text("an earlier run\n")
    exits = [sys.executable, "-c", "print('cannot start: boom'); raise SystemExit(3)"]
    monkeypatch.setattr(daemon, "_command", lambda *args: exits)
    with pytest.raises(daemon.DaemonError, match="exited with code 3:\ncannot start: boom$"):
        daemon.start(_free_port())
    assert (state / "rootpath.log").read_text() == "an earlier run\ncannot start: boom\n"


def test_the_log_is_only_for_its_user(state, monkeypatch):
    monkeypatch.setattr(daemon, "_command", lambda *args: [sys.executable, "-c", "raise SystemExit(1)"])
    with pytest.raises(daemon.DaemonError, match="exited with code 1"):
        daemon.start(_free_port())
    assert (state / "rootpath.log").stat().st_mode & 0o777 == 0o600


def test_server_command_runs_this_package():
    command = daemon._command("serve", "--port", "1")
    assert command[0] == sys.executable and command[-4:] == ["rootpath.cli", "serve", "--port", "1"]
    assert "-P" in command  # not a `rootpath/` that happens to be in the current directory


def test_ingest_loop_runs_while_the_server_is_up(state, monkeypatch, capsys):
    cycles = []

    def cycle(full):
        cycles.append(full)
        if len(cycles) == 1:
            raise ConnectionError("blip")
        return f"cycle {len(cycles)}"

    monkeypatch.setattr(cli, "_sync_cycle", cycle)
    monkeypatch.setattr(daemon, "_demo_running", lambda: True)
    monkeypatch.setattr(daemon, "INGEST_INTERVAL", 0.01)
    daemon.claim(port=7111, ingest=True)

    async def serve_a_while():
        async with daemon.background(ingest=True):
            while len(cycles) < 5:
                await asyncio.sleep(0.01)

    asyncio.run(serve_a_while())
    done = len(cycles)
    time.sleep(0.1)
    assert len(cycles) == done  # stopped with the server
    assert cycles[:3] == [True, True, False]  # the failed first full sync is retried
    last = json.loads((state / "server.json").read_text())["last_ingest"]
    assert last["ok"] and last["line"] == f"cycle {done}"
    # the log gets each change between failing and working, then a steady loop once a minute
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2 and lines[0].endswith("sync failed: ConnectionError: blip") and lines[1] == "cycle 2"


def test_ingest_waits_for_the_demo_stack(state, monkeypatch):
    """Its sources are the demo's: without them, a sync would write the demo's services into a graph
    mapped from another system."""
    monkeypatch.setattr(cli, "_sync_cycle", lambda full: pytest.fail("synced without the demo stack"))
    monkeypatch.setattr(daemon, "INGEST_INTERVAL", 0.01)
    info = daemon.claim(port=7111, ingest=True)

    def no_compose_file(window):
        raise RuntimeError("docker compose -f demo/docker-compose.yml config failed: no such file")

    for all_stats in (lambda window: {"payment": {"status": "down"}}, no_compose_file):
        monkeypatch.setattr("rootpath.infra.all_stats", all_stats)
        (state / "server.json").write_text(json.dumps(info))

        async def serve_a_while():
            async with daemon.background(ingest=True):
                while "last_ingest" not in json.loads((state / "server.json").read_text()):
                    await asyncio.sleep(0.01)

        asyncio.run(asyncio.wait_for(serve_a_while(), 10))
        last = json.loads((state / "server.json").read_text())["last_ingest"]
        assert last["ok"] and last["line"].endswith(
            "idle: the demo stack is not running (`rootpath demo up` starts it)"
        )


def test_alert_watcher_is_restarted_and_stopped_with_the_server(tmp_path, monkeypatch, capsys):
    started = tmp_path / "watchers"
    watcher = (  # the first one exits at once, the next one stays up
        "import os, sys, time; f = open(sys.argv[1], 'a'); f.write(f'{os.getpid()}\\n'); f.close(); "
        "time.sleep(0 if len(open(sys.argv[1]).read().split()) == 1 else 60)"
    )
    monkeypatch.setattr(daemon, "_command", lambda *args: [sys.executable, "-c", watcher, str(started)])
    monkeypatch.setattr(daemon, "_demo_running", lambda: True)
    monkeypatch.setattr(daemon, "WATCH_CHECK", 0.05)
    monkeypatch.setattr(daemon, "RESTART_DELAY", 0.05)

    def pids() -> list[int]:
        return [int(p) for p in started.read_text().split()] if started.exists() else []

    async def serve_until_restarted():
        async with daemon.background(watch_alerts=True):
            while len(pids()) < 2:
                await asyncio.sleep(0.05)
            await asyncio.sleep(0.2)
            assert not _gone(pids()[1])

    asyncio.run(asyncio.wait_for(serve_until_restarted(), 20))
    assert len(pids()) == 2 and all(_gone(pid) for pid in pids())
    assert "alert watcher exited with code 0, restarting" in capsys.readouterr().out


def test_alert_watcher_runs_only_while_the_demo_stack_does(tmp_path, monkeypatch, capsys):
    """With the stack stopped every service is down, which the watcher would hand to an agent as an outage."""
    started, demo = tmp_path / "watcher", {"up": False}
    watcher = "import os, sys, time; print(os.getpid(), file=open(sys.argv[1], 'w'), flush=True); time.sleep(60)"
    monkeypatch.setattr(daemon, "_command", lambda *args: [sys.executable, "-c", watcher, str(started)])
    monkeypatch.setattr(daemon, "_demo_running", lambda: demo["up"])
    monkeypatch.setattr(daemon, "WATCH_CHECK", 0.02)

    async def demo_up_then_down():
        async with daemon.background(watch_alerts=True):
            await asyncio.sleep(0.3)
            assert not started.exists()
            demo["up"] = True
            while not started.exists() or not started.read_text().strip():
                await asyncio.sleep(0.02)
            pid = int(started.read_text())
            demo["up"] = False
            while not _gone(pid):
                await asyncio.sleep(0.02)

    asyncio.run(asyncio.wait_for(demo_up_then_down(), 20))
    assert "alert watcher stopped: the demo stack is not running" in capsys.readouterr().out


def test_an_agent_that_outlives_the_watcher_is_ended_too(tmp_path, monkeypatch):
    agent = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print(flush=True); time.sleep(60)"
    watcher = (  # dies on SIGTERM at once, while the agent it launched ignores it
        "import subprocess, sys; agent = subprocess.Popen([sys.executable, '-c', sys.argv[1]], stdout=subprocess.PIPE); "
        "agent.stdout.readline(); print(agent.pid, flush=True); agent.wait()"
    )
    monkeypatch.setattr(daemon, "STOP_GRACE", 0.5)
    proc = subprocess.Popen(
        [sys.executable, "-c", watcher, agent], stdout=subprocess.PIPE, text=True, start_new_session=True
    )
    agent_pid = int(proc.stdout.readline())
    try:
        started = time.monotonic()
        daemon._end(proc)
        assert time.monotonic() - started < 3
        deadline = time.monotonic() + 5  # killed, then reaped by whoever adopted it
        while not _gone(agent_pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert _gone(agent_pid)
    finally:
        if not _gone(agent_pid):
            os.kill(agent_pid, signal.SIGKILL)


def test_mcp_is_served_at_mcp_on_every_start_of_the_app():
    from fastapi.testclient import TestClient

    from rootpath import web

    for _ in range(2):  # an MCP session manager runs only once: every start of the app gets its own
        with TestClient(web.app, base_url="http://localhost:7100") as c:
            r = c.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"Accept": "application/json, text/event-stream"},
            )
            assert r.status_code == 200 and '"name":"rank_suspects"' in r.text
            assert c.get("/mcp", headers={"Accept": "text/event-stream"}).status_code == 405  # stateless: no stream


def test_http_tools_run_one_at_a_time_off_the_event_loop(monkeypatch):
    """verify_recovery polls for a minute: meanwhile the console and the other clients still get answers."""
    from rootpath import mcp_server, tools

    @functools.wraps(tools.get_service_health)
    def slow(service=None):
        time.sleep(0.3)
        return {"services": {}}

    monkeypatch.setattr(tools, "get_service_health", slow)

    async def two_calls_and_a_ticker():
        mcp, ticks = mcp_server.build("graph", threaded=True), []

        async def tick():
            while True:
                await asyncio.sleep(0.01)
                ticks.append(1)

        ticker = asyncio.create_task(tick())
        started = time.monotonic()
        await asyncio.gather(*(mcp.call_tool("get_service_health", {}) for _ in range(2)))
        ticker.cancel()
        return time.monotonic() - started, len(ticks)

    elapsed, ticks = asyncio.run(two_calls_and_a_ticker())
    assert elapsed >= 0.6 and ticks > 20


def test_console_api_answers_503_without_the_demo_or_the_graph(monkeypatch):
    """Errors the console polls into every few seconds on a fresh install: answers, not tracebacks in the log."""
    from fastapi.testclient import TestClient
    from redis.exceptions import ResponseError

    from rootpath import infra, store, web

    def no_compose_file(window):
        raise RuntimeError("docker compose -f demo/docker-compose.yml config failed: no such file")

    def no_graph(*args, **kwargs):
        raise ResponseError("Invalid graph operation on empty key")

    monkeypatch.setattr(infra, "all_stats", no_compose_file)
    monkeypatch.setattr(store, "read", no_graph)
    c = TestClient(web.app)
    health, graph = c.get("/api/health"), c.get("/api/graph")
    assert health.status_code == 503 and health.json()["detail"].startswith("services unavailable: RuntimeError")
    assert graph.status_code == 503 and graph.json()["detail"].endswith(
        "ResponseError: Invalid graph operation on empty key"
    )


def test_console_api_refuses_other_sites():
    from fastapi.testclient import TestClient

    from rootpath import web

    c = TestClient(web.app, base_url="http://localhost:7100")
    post = "/api/chaos/not-a-scenario"  # 404 once let through: nothing is injected
    assert c.post(post, headers={"Origin": "https://attacker.example"}).status_code == 403
    assert c.post(post, headers={"Origin": "null"}).status_code == 403
    for origin in (None, "http://localhost:7100", "http://localhost:5173", "http://127.0.0.1:7100"):
        assert c.post(post, headers={"Origin": origin} if origin else {}).status_code == 404
    rebound = {"Host": "attacker.example:7100", "Origin": "http://attacker.example:7100"}
    assert c.post(post, headers=rebound).status_code == 421
    assert c.get("/api/scenarios", headers={"Host": "attacker.example:7100"}).status_code == 421
    assert c.get("/api/scenarios", headers={"Origin": "https://attacker.example"}).status_code == 200
    # bound beyond loopback (`rootpath ui --host 0.0.0.0`), the console works from another machine
    lan = TestClient(web.app, base_url="http://192.168.1.5:7100")
    assert lan.post(post, headers={"Origin": "http://192.168.1.5:7100"}).status_code == 404
    assert lan.post(post, headers={"Origin": "https://attacker.example"}).status_code == 403


@pytest.mark.parametrize(
    "url,shown",
    [
        ("redis://localhost:6380", "redis://localhost:6380"),
        ("redis://:pw@db:7000", "redis://:****@db:7000"),
        ("rediss://falkordb:s3cr%40t@r-1.falkordb.cloud:55011", "rediss://falkordb:****@r-1.falkordb.cloud:55011"),
    ],
)
def test_password_is_masked(url, shown):
    assert daemon.masked(url) == shown


def test_status_rows(state, fake_server, monkeypatch):
    monkeypatch.setattr(daemon, "graph_status", lambda: "reachable")
    assert daemon.status() == [
        ("server", "stopped (`rootpath start` runs it in the background)"),
        ("falkordb", "reachable"),
    ]
    last = {"at": time.time() - 65, "line": "12:00:00 live sync: 0 new changes", "ok": True}
    proc = fake_server(started_at=time.time() - 3700, ingest=True, last_ingest=last)
    rows = dict(daemon.status())
    assert rows["server"] == f"running, pid {proc.pid}, up 1h 01m, port 7111"
    assert rows["mcp"] == "http://localhost:7111/mcp" and rows["log"] == str(state / "rootpath.log")
    assert rows["ingest"] == "1m 05s ago: 12:00:00 live sync: 0 new changes"
    assert rows["alerts"] == "off" and rows["falkordb"] == "reachable"


def test_status_reports_a_falkordb_url_that_is_not_one(monkeypatch):
    from rootpath import config

    # forget the resolved settings, so that they come from this environment
    for resolved in {"FALKORDB_URL", "FALKORDB", "GRAPH"} & vars(config).keys():
        monkeypatch.delattr(config, resolved)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_FALKORDB_URL", raising=False)
    monkeypatch.setenv("FALKORDB_URL", "http://admin:s3cret@db:6379")
    status = daemon.graph_status()
    assert status.startswith("not usable: ") and "http" in status and "s3cret" not in status


def _falkordb_reachable() -> bool:
    try:
        socket.create_connection(("localhost", 6380), timeout=1).close()
    except OSError:
        return False
    return True


async def _mcp_session(url: str):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(url) as (read, write, _), ClientSession(read, write) as session:
        await session.initialize()
        tools = {t.name for t in (await session.list_tools()).tools}
        return tools, await session.call_tool("get_service_health", {})


@pytest.mark.skipif(not _falkordb_reachable(), reason="FalkorDB not reachable at localhost:6380")
def test_serve_answers_health_and_mcp_over_http(tmp_path, monkeypatch):
    from falkordb import FalkorDB

    port, graph = _free_port(), "rootpath-test-daemon"  # the tool call's trace goes here, not into the demo graph
    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "FALKORDB_URL": "redis://localhost:6380",
        "ONCALL_GRAPH": graph,
        "ONCALL_INVESTIGATION_ID": "cli-test-daemon",
    }
    monkeypatch.setenv("XDG_STATE_HOME", env["XDG_STATE_HOME"])
    log = tmp_path / "serve.log"
    with log.open("w") as out:
        proc = subprocess.Popen(
            [sys.executable, "-m", "rootpath.cli", "serve", "--port", str(port), "--no-ingest"],
            cwd=REPO,
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
        )
    try:
        deadline = time.monotonic() + 30
        while True:
            assert proc.poll() is None and time.monotonic() < deadline, log.read_text()
            try:
                health = httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=10)
                break
            except httpx.TransportError:
                time.sleep(0.2)
        assert health.status_code == 200 and health.json()["services"]
        info = daemon.running()
        assert (info["pid"], info["port"], info["ingest"]) == (proc.pid, port, False)

        tools, result = asyncio.run(_mcp_session(f"http://localhost:{port}/mcp"))
        assert {"get_service_health", "rank_suspects", "rollback_service", "resolve_incident"} <= tools
        assert not result.isError and json.loads(result.content[0].text)["services"]

        assert daemon.stop()["pid"] == proc.pid
        assert proc.wait(5) == 0, log.read_text()
        assert daemon.running() is None and not daemon.pid_path().exists()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        with contextlib.suppress(Exception):  # absent if the tool call never got that far
            FalkorDB(host="localhost", port=6380).select_graph(graph).delete()
