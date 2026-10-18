"""The background server, in the spirit of `herdr server`: `rootpath serve` runs the console + API,
the MCP tools over Streamable HTTP (/mcp) and the ingest loop in one process; `rootpath start` runs
it detached, `stop` ends it and `status` reports on it. Its pid file, info and log live in
$XDG_STATE_HOME/rootpath (default ~/.local/state/rootpath)."""

import asyncio
import contextlib
import errno
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from . import config

HOST = "127.0.0.1"  # the action tools change infrastructure: never listen beyond this machine
INGEST_INTERVAL = 5.0  # seconds between ingest cycles, as `rootpath ingest --watch`
LOG_EVERY = 60.0  # log a steady ingest loop once a minute, and every change between success and failure
START_TIMEOUT = 30.0
STOP_TIMEOUT = 10.0
STOP_GRACE = 3  # seconds for requests in flight to finish on shutdown, then for the loops and the watcher
RESTART_DELAY = 30.0  # before restarting an alert watcher that exited
WATCH_CHECK = 2.0  # seconds between checks that the demo stack runs, well within the watcher's two 5 s strikes


class DaemonError(RuntimeError):
    pass


class AlreadyRunning(DaemonError):
    def __init__(self, info: dict):
        port = f", http://localhost:{info['port']}" if info.get("port") else ""
        super().__init__(f"rootpath is already running (pid {info['pid']}{port}); `rootpath stop` ends it")


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    return Path(base if base and os.path.isabs(base) else Path.home() / ".local" / "state") / "rootpath"


def pid_path() -> Path:
    return state_dir() / "rootpath.pid"


def info_path() -> Path:
    return state_dir() / "server.json"


def log_path() -> Path:
    return state_dir() / "rootpath.log"


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _read_pid() -> int | None:
    try:
        pid = int(pid_path().read_text())
    except (FileNotFoundError, ValueError):
        return None
    return pid if pid > 0 else None  # never 0 or -1: os.kill would signal a whole group


def _read_info() -> dict:
    try:
        return json.loads(info_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_info(info: dict) -> None:
    tmp = info_path().with_name(f".server.json.{os.getpid()}")
    tmp.write_text(json.dumps(info, indent=2))
    os.replace(tmp, info_path())


def _is_server(pid: int) -> bool:
    """True if `pid` is a live `rootpath serve`. A pid file left behind by a crash can name a pid
    that an unrelated process has since been given, and `rootpath stop` must not signal that one."""
    try:
        os.kill(pid, 0)
        cmdline = Path(f"/proc/{pid}/cmdline")
        if cmdline.exists():
            args = cmdline.read_bytes().decode(errors="replace").split("\0")  # empty for a zombie
        else:  # no procfs (macOS)
            ps = subprocess.run(["ps", "-o", "args=", "-p", str(pid)], capture_output=True, text=True)
            args = ps.stdout.split()
    except (ProcessLookupError, PermissionError, FileNotFoundError):  # gone, or another user's process
        return False
    return "serve" in args and any("rootpath" in a for a in args)


def running() -> dict | None:
    """The live server's info (pid, port, started_at, ingest, watch_alerts, last_ingest), or None.
    The files of a server that died without removing them are removed."""
    pid = _read_pid()
    if pid is not None and _is_server(pid):
        info = _read_info()
        return info if info.get("pid") == pid else {"pid": pid}  # just started: info not written yet
    # A stale file, still the same one: with no file at all, a starting server may be creating it now.
    if pid_path().exists() and _read_pid() == pid:
        pid_path().unlink(missing_ok=True)
        info_path().unlink(missing_ok=True)
    return None


def claim(**info) -> dict:
    """Register this process as the server: the pid file, then the info file (`info` + pid and start
    time). Raises AlreadyRunning while another server is alive."""
    if other := running():
        raise AlreadyRunning(other)
    state_dir().mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = pid_path().with_name(f".rootpath.pid.{os.getpid()}")
    tmp.write_text(f"{os.getpid()}\n")
    try:
        os.link(tmp, pid_path())  # atomic, and fails if the file exists: one winner if two start at once
    except FileExistsError:
        raise DaemonError("another rootpath server is starting") from None
    finally:
        tmp.unlink()
    info = {"pid": os.getpid(), "started_at": time.time(), **info}
    _write_info(info)
    return info


def release() -> None:
    """Remove the pid and info files, if this process holds them."""
    if _read_pid() == os.getpid():
        info_path().unlink(missing_ok=True)
        pid_path().unlink(missing_ok=True)


def _check_port(port: int) -> None:
    """Raise DaemonError if the server could not listen on `port`."""
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # as uvicorn binds: TIME_WAIT counts as free
        try:
            s.bind((HOST, port))
        except OSError as e:
            if e.errno == errno.EADDRINUSE:
                raise DaemonError(
                    f"port {port} is already in use (by `rootpath ui`?); choose another with --port"
                ) from None
            raise DaemonError(f"cannot listen on port {port}: {e.strerror}") from None


def _command(*args: str) -> list[str]:
    """`rootpath <args>` for a new process: the same Python (-P: not from a `rootpath/` in the
    current directory), or the same executable for a frozen binary."""
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, "-P", "-m", "rootpath.cli", *args]


def serve(port: int, ingest: bool = True, watch_alerts: bool = False) -> None:
    """The server in the foreground until Ctrl-C or SIGTERM: console + API, MCP at /mcp, and the
    loops asked for, registered in the pid file so that `start`, `stop` and `status` find it."""
    import uvicorn

    from .web import app

    # A one-file binary runs as a launcher plus this Python process: record the parent too, so `start`
    # can recognise the server it launched by either pid.
    claim(port=port, ingest=ingest, watch_alerts=watch_alerts, parent=os.getppid())
    try:
        _check_port(port)
        # uvicorn raises SIGTERM again once it has shut down gracefully. As a KeyboardInterrupt (which
        # uvicorn.run absorbs) it lets this function remove the pid file, instead of ending the process.
        signal.signal(signal.SIGTERM, signal.default_int_handler)
        app.state.loops = {"ingest": ingest, "watch_alerts": watch_alerts}
        loops = [f"ingest every {INGEST_INTERVAL:.0f}s" if ingest else "ingest off"]
        loops += ["alert watcher on"] if watch_alerts else []
        print(
            f"{_now()} rootpath serving http://localhost:{port} (MCP: /mcp, {', '.join(loops)}), pid {os.getpid()}",
            flush=True,
        )
        uvicorn.run(app, host=HOST, port=port, log_level="warning", timeout_graceful_shutdown=STOP_GRACE)
    finally:
        release()
    print(f"{_now()} rootpath stopped", flush=True)


def start(port: int, ingest: bool = True, watch_alerts: bool = False) -> dict:
    """Run `rootpath serve` detached (its own session, no stdin, output to the log) and wait until
    /api/health answers. Returns the server's info."""
    if other := running():
        raise AlreadyRunning(other)
    _check_port(port)
    state_dir().mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = [*(["--no-ingest"] if not ingest else []), *(["--watch-alerts"] if watch_alerts else [])]
    # Only for this user: it gets the headless agent's tool calls and reports.
    with os.fdopen(os.open(log_path(), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "ab") as log:
        offset = log.tell()
        proc = subprocess.Popen(
            _command("serve", "--port", str(port), *flags),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            output = log_path().read_bytes()[offset:].decode(errors="replace").strip()
            raise DaemonError(f"the server exited with code {proc.returncode}:\n{output[-2000:]}")
        info = running()
        if info and proc.pid in (info["pid"], info.get("parent")) and info.get("port") == port and _answers(port):
            return info
        time.sleep(0.2)
    proc.terminate()
    raise DaemonError(f"the server did not answer on port {port} within {START_TIMEOUT:.0f}s; see {log_path()}")


def _answers(port: int) -> bool:
    """Any HTTP response from /api/health: without the demo stack it can be an error, but the server is up."""
    try:
        httpx.get(f"http://{HOST}:{port}/api/health", timeout=10)
    except httpx.HTTPError:
        return False
    return True


def stop(timeout: float = STOP_TIMEOUT) -> dict | None:
    """SIGTERM the server and wait for it to exit. Returns what was running (None: nothing), or
    raises DaemonError if it is still running after `timeout` seconds."""
    info = running()
    if info is None:
        return None
    pid = info["pid"]
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while _is_server(pid):
        if time.monotonic() > deadline:
            raise DaemonError(
                f"rootpath (pid {pid}) is still running {timeout:.0f}s after SIGTERM; "
                f"see {log_path()}, or force it with `kill -9 {pid}`"
            )
        time.sleep(0.1)
    running()  # a server that crashed on the way out leaves its files: clear them
    return info


@contextlib.asynccontextmanager
async def background(ingest: bool = False, watch_alerts: bool = False):
    """For the life of the server (see web.lifespan): the ingest loop and the alert watcher, each
    in a thread that ends when the server stops."""
    stop_event = threading.Event()
    threads = [
        threading.Thread(target=loop, args=(stop_event,), daemon=True)
        for loop, wanted in ((_ingest, ingest), (_watch_alerts, watch_alerts))
        if wanted
    ]
    for t in threads:
        t.start()
    try:
        yield
    finally:
        stop_event.set()
        deadline = time.monotonic() + STOP_GRACE + 1  # enough for the watcher's _end
        for t in threads:
            await asyncio.to_thread(t.join, max(0.0, deadline - time.monotonic()))


def _ingest(stop_event: threading.Event) -> None:
    from .cli import ingest_loop

    last_ok, last_logged = None, 0.0

    def report(line: str, ok: bool) -> None:
        nonlocal last_ok, last_logged
        now = time.time()
        if ok != last_ok or now - last_logged >= LOG_EVERY:
            print(line, flush=True)
            last_logged = now
        last_ok = ok
        if _read_pid() == os.getpid():  # not after a slow last cycle, once the server has removed it
            with contextlib.suppress(OSError):  # only `rootpath status` reads it
                _write_info({**_read_info(), "last_ingest": {"at": now, "line": line, "ok": ok}})

    ingest_loop(INGEST_INTERVAL, wait=stop_event.wait, report=report, cycle=_demo_cycle)


def _demo_running() -> bool:
    """Whether a service of the demo stack answers its stats endpoint."""
    from . import infra

    try:
        return any(s["status"] not in ("down", "unknown") for s in infra.all_stats(window=10).values())
    except Exception:  # no compose file or no Docker: no demo on this machine
        return False


def _demo_cycle(full: bool) -> str:
    """An ingest cycle while the demo stack runs. Its compose file and services are the sources: with
    none of them up, a sync would only add the demo's services, all down, to the graph, and merge them
    into the services of a mapped system that share their names."""
    from . import cli

    if not _demo_running():
        return f"{time.strftime('%H:%M:%S')} idle: the demo stack is not running (`rootpath demo up` starts it)"
    return cli._sync_cycle(full)


def _watch_alerts(stop_event: threading.Event) -> None:
    """Keep `rootpath watch` running while the demo stack runs: with the stack stopped every service is
    down, which the watcher takes for an outage to hand to an agent. It gets its own process group, so
    that ending it also ends the headless agent it may have launched."""
    while not stop_event.wait(WATCH_CHECK):
        if not _demo_running():
            continue
        proc = subprocess.Popen(_command("watch"), stdin=subprocess.DEVNULL, start_new_session=True)
        while proc.poll() is None and _demo_running():
            if stop_event.wait(WATCH_CHECK):
                break
        if proc.poll() is None:
            _end(proc)
            if not stop_event.is_set():
                print(f"{_now()} alert watcher stopped: the demo stack is not running", flush=True)
        else:
            print(
                f"{_now()} alert watcher exited with code {proc.returncode}, restarting in {RESTART_DELAY:.0f}s",
                flush=True,
            )
            stop_event.wait(RESTART_DELAY)


def _end(proc: subprocess.Popen) -> None:
    """SIGTERM a child's process group, and SIGKILL what is left of it STOP_GRACE s later: the child,
    or a headless agent it launched that outlives it."""
    _signal_group(proc.pid, signal.SIGTERM)
    deadline = time.monotonic() + STOP_GRACE
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(STOP_GRACE)
    while time.monotonic() < deadline and _signal_group(proc.pid, 0):
        time.sleep(0.1)
    _signal_group(proc.pid, signal.SIGKILL)
    proc.wait()


def _signal_group(pgid: int, sig: int) -> bool:
    """Send `sig` to a process group (0: none, only check). False if no process is left in it."""
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        return False
    return True


def masked(url: str) -> str:
    """The URL with its password, if any, replaced by ****."""
    u = urlparse(url)
    if not u.password:
        return url
    userinfo, _, host = u.netloc.rpartition("@")
    return u._replace(netloc=f"{userinfo.partition(':')[0]}:****@{host}").geturl()


def _duration(seconds: float) -> str:
    m, s = divmod(int(max(0.0, seconds)), 60)  # not negative if the clock is set back
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m:02d}m"
    return f"{m}m {s:02d}s" if m else f"{s}s"


def graph_status() -> str:
    """Whether FalkorDB answers, and the size of the graph. Short timeouts: a status must not hang."""
    from falkordb import FalkorDB

    try:
        where, connection = masked(config.FALKORDB_URL), config.FALKORDB
    except ValueError as e:  # `rootpath config set falkordb_url ...` fixes it
        return f"not usable: {e}"
    try:
        with contextlib.closing(FalkorDB(**connection, socket_connect_timeout=3, socket_timeout=5)) as db:
            if config.GRAPH not in db.list_graphs():
                return f"reachable at {where}; graph {config.GRAPH!r} does not exist yet (`/rootpath:map` or `rootpath demo up` builds it)"
            graph = db.select_graph(config.GRAPH)
            nodes = graph.ro_query("MATCH (n) RETURN count(n)", timeout=5000).result_set[0][0]
            edges = graph.ro_query("MATCH ()-[r]->() RETURN count(r)", timeout=5000).result_set[0][0]
    except Exception as e:  # any failure is the answer here
        return f"unreachable at {where} ({type(e).__name__}: {e})"
    return f"reachable at {where}, graph {config.GRAPH!r}: {nodes} nodes, {edges} edges"


def _ingest_status(info: dict) -> str:
    if not info.get("ingest"):
        return "off (--no-ingest)"
    last = info.get("last_ingest")
    if not last:
        return "first cycle running"
    return f"{_duration(time.time() - last['at'])} ago: {last['line']}"


def status() -> list[tuple[str, str]]:
    """(label, value) rows for `rootpath status`: the server, its loops, and FalkorDB."""
    info = running()
    if info is None:
        rows = [("server", "stopped (`rootpath start` runs it in the background)")]
    elif "port" not in info:
        rows = [("server", f"starting, pid {info['pid']}")]
    else:
        url = f"http://localhost:{info['port']}"
        rows = [
            (
                "server",
                f"running, pid {info['pid']}, up {_duration(time.time() - info['started_at'])}, port {info['port']}",
            ),
            ("console", url),
            ("mcp", f"{url}/mcp"),
            ("log", str(log_path())),
            ("ingest", _ingest_status(info)),
            ("alerts", "on, while the demo stack runs" if info.get("watch_alerts") else "off"),
        ]
    return [*rows, ("falkordb", graph_status())]
