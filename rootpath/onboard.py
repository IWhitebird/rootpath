"""`rootpath init`: choose where the graph lives, test the connection, connect coding agents
(Claude Code plugin, Codex, Cursor), optionally start the demo, and save the user config file.
Also `rootpath config show | set`."""

import contextlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import questionary
from falkordb import FalkorDB
from redis import exceptions as rx
from rich.console import Console, Group
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import config, userconfig

LOCAL_URL = "redis://localhost:6380"
CONTAINER = "rootpath-falkordb"
MARKETPLACE = "IWhitebird/rootpath"
AGENTS = {"claude": "Claude Code", "codex": "Codex", "cursor": "Cursor"}
INTEGRATIONS = "Integrations coming soon: Prometheus · Kubernetes · Datadog · Sentry · PagerDuty"

console = Console(highlight=False)
STYLE = questionary.Style(
    [
        ("qmark", "fg:ansicyan bold"),
        ("question", "bold"),
        ("answer", "fg:ansicyan"),
        ("pointer", "fg:ansicyan bold"),
        ("highlighted", "fg:ansicyan"),
        ("selected", "fg:ansicyan"),
        ("instruction", "fg:ansibrightblack"),
    ]
)


def cloud_url(host: str, port: int, username: str = "", password: str = "", tls: bool = True) -> str:
    """redis:// (or rediss:// with TLS) URL with percent-encoded credentials. A username without
    a password means nothing to Redis AUTH, so it is left out."""
    host = host.strip()
    if ":" in host and not host.startswith("["):  # IPv6 literal
        host = f"[{host}]"
    auth = f"{quote(username, safe='')}:{quote(password, safe='')}@" if password else ""
    return f"{'rediss' if tls else 'redis'}://{auth}{host}:{port}"


def mask(url: str) -> str:
    """The URL with its password replaced by ****."""
    u = urlsplit(url)
    if not u.password:
        return url
    return urlunsplit(u._replace(netloc=f"{u.username or ''}:****@{u.netloc.rpartition('@')[2]}"))


def check(url: str, timeout: float = 5.0) -> str | None:
    """None when FalkorDB answers RETURN 1 at url, else what went wrong, in plain words."""
    if (problem := _url_problem(url)) is not True:
        return problem
    c = config.falkordb_connection(url)
    try:
        with contextlib.closing(FalkorDB(**c, socket_timeout=timeout, socket_connect_timeout=timeout)) as db:
            db.select_graph(config.DEFAULTS["graph"]).ro_query("RETURN 1")  # reads no graph: any name will do
    except rx.AuthenticationError as e:
        if "without any password configured" in str(e):
            return "This FalkorDB has no password: leave the username and password empty."
        return "Wrong username or password." if c["password"] else "This FalkorDB needs a username and password."
    except rx.ResponseError as e:
        if "empty key" in str(e):  # FalkorDB answered; the graph just doesn't exist yet
            return None
        if "unknown command" in str(e).lower():
            return f"{c['host']}:{c['port']} answers, but it is not FalkorDB (no GRAPH commands)."
        return f"FalkorDB refused the query: {e}"
    except (rx.ConnectionError, rx.TimeoutError) as e:
        return _connection_problem(c, e)
    return None


def _connection_problem(c: dict, error: Exception) -> str:
    """Tell an unreachable host from a TLS mismatch: does a plain TCP connection get through?"""
    where = f"{c['host']}:{c['port']}"
    try:
        socket.create_connection((c["host"], c["port"]), timeout=3).close()
    except OSError as e:
        return (
            f"Can't reach {where} ({e.strerror or str(e) or type(e).__name__}). Check the host and port, that the "
            "database is running, and that your network (or the instance's IP allow-list) lets you in."
        )
    if "certificate" in str(error).lower():
        return f"{where} uses TLS, but its certificate was not accepted: {error}"
    if c["ssl"]:
        return f"{where} answers, but not over TLS: turn TLS off (redis://)."
    return f"{where} answers, but closed the connection: it probably requires TLS (rediss://)."


def docker_ready() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def _container_running() -> bool:
    ps = subprocess.run(["docker", "ps", "-q", "--filter", f"name=^{CONTAINER}$"], capture_output=True, text=True)
    return bool(ps.stdout.strip())


def start_container(wait: float = 60) -> str | None:
    """Start (or create) the rootpath-falkordb container and wait until it answers; None on success."""
    exists = subprocess.run(["docker", "container", "inspect", CONTAINER], capture_output=True).returncode == 0
    if exists:
        cmd = ["docker", "start", CONTAINER]
    else:
        # no password, so reachable from this machine only; the graph lives in a volume of the same name
        cmd = ["docker", "run", "-d", "--name", CONTAINER, "-p", "127.0.0.1:6380:6379"]
        cmd += ["-v", f"{CONTAINER}:/var/lib/falkordb/data", "--restart", "unless-stopped", "falkordb/falkordb:latest"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return f"`{' '.join(cmd)}` failed: {proc.stderr.strip()[-300:]}"
    deadline = time.monotonic() + wait
    while check(LOCAL_URL, timeout=1) is not None:
        if time.monotonic() > deadline:
            return f"{CONTAINER} did not answer within {wait:.0f}s (see `docker logs {CONTAINER}`)"
        time.sleep(1)
    return None


def rootpath_exe() -> list[str]:
    """The running rootpath, by absolute path, for agents and subprocesses to start."""
    if getattr(sys, "frozen", False):  # the single binary is itself the program, whatever it is named
        return [sys.executable]
    exe = Path(sys.argv[0])
    if exe.name == "rootpath" and exe.is_file():
        return [str(exe.absolute())]
    found = shutil.which("rootpath")
    return [found] if found else [sys.executable, "-m", "rootpath.cli"]


def is_checkout() -> bool:
    """Running from a git checkout of this repo, which can serve as the plugin marketplace."""
    return (config.ROOT / ".git").exists() and (config.ROOT / ".claude-plugin" / "marketplace.json").is_file()


def connect_claude(source: str) -> str:
    marketplace = str(config.ROOT) if source == "local" else MARKETPLACE
    for cmd in (
        ["claude", "plugin", "marketplace", "add", marketplace],
        ["claude", "plugin", "install", "rootpath@rootpath"],
    ):
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            raise RuntimeError(f"`{' '.join(cmd)}` failed: {(proc.stderr or proc.stdout).strip()[-300:]}")
    return f"plugin rootpath@rootpath installed from {_home(config.ROOT) if source == 'local' else MARKETPLACE}"


_TABLE_HEADER = re.compile(r"\s*\[{1,2}([^\[\]#]+)\]{1,2}\s*(#.*)?$")


def codex_config(text: str, command: list[str]) -> str:
    """config.toml with an [mcp_servers.rootpath] table that starts `command`. An existing rootpath
    table (and its sub-tables) is replaced where it stands; everything else is kept byte for byte."""
    try:
        expected = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"not valid TOML ({e}), left as it is") from e
    block = (
        "[mcp_servers.rootpath]\n"
        f"command = {userconfig.toml_str(command[0])}\n"
        f"args = [{', '.join(map(userconfig.toml_str, command[1:]))}]\n"
        'env = { ONCALL_AGENT = "codex" }\n'
    )
    out, inside, placed, gap = [], False, False, []
    for line in text.splitlines(keepends=True):
        if header := _TABLE_HEADER.match(line):
            key = re.sub(r"[\s\"']", "", header[1])
            inside = key == "mcp_servers.rootpath" or key.startswith("mcp_servers.rootpath.")
            if inside and not placed:
                out.append(block)
                placed = True
            if not inside:
                out += gap  # the blank lines and comments above this table belong to it
            gap = []
        if not inside:
            out.append(line)
        elif not line.strip() or line.lstrip().startswith("#"):
            gap.append(line)
        else:
            gap = []
    out += gap
    if not placed:
        out.append(("" if not text.strip() else "\n" if text.endswith("\n") else "\n\n") + block)
    new = "".join(out)

    servers = expected.setdefault("mcp_servers", {})
    if not isinstance(servers, dict):
        raise ValueError("mcp_servers is not a table")
    servers["rootpath"] = {"command": command[0], "args": command[1:], "env": {"ONCALL_AGENT": "codex"}}
    try:
        safe = tomllib.loads(new) == expected
    except tomllib.TOMLDecodeError:
        safe = False
    if not safe:  # e.g. rootpath already defined inline or with dotted keys
        raise ValueError(f"could not add the rootpath server safely; add it yourself:\n{block}")
    return new


def cursor_config(text: str, command: list[str]) -> str:
    """mcp.json with a "rootpath" entry in mcpServers; other entries are kept."""
    try:
        data = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError as e:
        raise ValueError(f"not valid JSON ({e}), left as it is") from e
    servers = data.setdefault("mcpServers", {}) if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        raise ValueError('expected a JSON object with an "mcpServers" object')
    servers["rootpath"] = {"command": command[0], "args": command[1:], "env": {"ONCALL_AGENT": "cursor"}}
    return json.dumps(data, indent=2) + "\n"


def codex_file() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "config.toml"


def cursor_file() -> Path:
    return Path.home() / ".cursor" / "mcp.json"


def _merge_file(path: Path, merge) -> str:
    """Rewrite the agent's file with merge(text, command), in one step and with the mode it had. A
    symlinked file (dotfiles) is written where it points, so the link stays."""
    path = path.resolve()
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    try:
        new = merge(text, [*rootpath_exe(), "mcp"])
    except ValueError as e:
        raise ValueError(f"{_home(path)}: {e}") from e
    if new != text:
        path.parent.mkdir(parents=True, exist_ok=True)
        userconfig.write_atomic(path, new, stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600)
    return f"MCP server in {_home(path)}"


CONNECT = {
    "claude": connect_claude,
    "codex": lambda _: _merge_file(codex_file(), codex_config),
    "cursor": lambda _: _merge_file(cursor_file(), cursor_config),
}


def _connect(agent: str, source: str) -> bool:
    try:
        with console.status(f"connecting {AGENTS[agent]} ..."):
            done = CONNECT[agent](source)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as e:
        _fail(f"{AGENTS[agent]}: {e}")
        return False
    _ok(f"{AGENTS[agent]}: {done}")
    return True


def demo_problem() -> str | None:
    """Why the demo can't start here, or None."""
    if not config.COMPOSE_FILE.is_file():
        return "needs a checkout of the rootpath repo"
    return None if docker_ready() else "needs Docker"


def start_demo(url: str, graph: str) -> bool:
    """`rootpath demo up` against the chosen graph, with its output shown as it runs."""
    if _container_running():  # it holds :6380, which the demo stack's own FalkorDB needs
        subprocess.run(["docker", "stop", CONTAINER], capture_output=True)
        console.print(f"  stopped {CONTAINER}: the demo stack brings its own FalkorDB on localhost:6380")
    console.rule("rootpath demo up", style="dim")
    proc = subprocess.run(
        [*rootpath_exe(), "demo", "up"], env={**os.environ, "FALKORDB_URL": url, "ONCALL_GRAPH": graph}
    )
    console.rule(style="dim")
    if proc.returncode != 0:
        _fail("the demo did not start (see above); run `rootpath demo up` again once it's fixed")
    return proc.returncode == 0


def _ok(message: str) -> None:
    console.print(f"[green]✓[/] {escape(message)}")


def _fail(message: str) -> None:
    console.print(f"[red]✗[/] {escape(message)}")


def _home(path: Path) -> str:
    home = str(Path.home())
    return "~" + str(path)[len(home) :] if str(path).startswith(home + os.sep) else str(path)


def _choice(value: str, title: str, hint: str = "", **kwargs) -> questionary.Choice:
    return questionary.Choice(f"{title:<19}{hint}".rstrip(), value=value, **kwargs)


def _select(message: str, choices: list[questionary.Choice]) -> str:
    return questionary.select(message, choices=choices, style=STYLE).unsafe_ask()


def _text(message: str, default: str = "", **kwargs) -> str:
    return questionary.text(message, default=default, style=STYLE, **kwargs).unsafe_ask().strip()


def _host_problem(value: str) -> bool | str:
    value = value.strip()
    if not value:
        return "enter the host of your FalkorDB Cloud instance"
    if any(c in value for c in "/@ #?"):
        return "only the host name, e.g. r-abc123.falkordb.cloud (or choose 'I have a FalkorDB URL')"
    if value.count(":") == 1:
        return "the port goes in the next question"
    return True


def _port_problem(value: str) -> bool | str:
    return (value.strip().isdecimal() and 0 < int(value) < 65536) or "a port number, e.g. 6379"


def _url_problem(value: str) -> bool | str:
    try:
        config.falkordb_connection(value.strip())
    except ValueError as e:
        if "@" in value:  # urllib's message can quote part of the password
            return (
                "not a valid FalkorDB URL: it starts with redis://, rediss://, falkor:// or falkors://, and special characters in the "
                "username and password are percent-encoded (@ as %40, : as %3A, / as %2F)"
            )
        return str(e)
    return True


def _ask_cloud(answers: dict) -> str:
    answers["host"] = _text("Host", answers.get("host", ""), validate=_host_problem)
    answers["port"] = _text("Port", answers.get("port", "6379"), validate=_port_problem)
    answers["username"] = _text("Username", answers.get("username", "default"))
    answers["password"] = questionary.password(
        "Password", default=answers.get("password", ""), style=STYLE
    ).unsafe_ask()
    # FalkorDB Cloud's connection string says falkor:// (plain) or falkors:// (TLS); the check below tells a mismatch apart.
    answers["tls"] = questionary.confirm("Use TLS?", default=answers.get("tls", False), style=STYLE).unsafe_ask()
    return cloud_url(answers["host"], int(answers["port"]), answers["username"], answers["password"], answers["tls"])


def _ask_url(answers: dict) -> str:
    # hidden like a password: the URL usually carries one
    answers["url"] = questionary.password(
        "FalkorDB URL",
        default=answers.get("url") or "",
        validate=_url_problem,
        instruction="redis:// or falkor://user:password@host:port (rediss:// or falkors:// with TLS)",
        style=STYLE,
    ).unsafe_ask()
    return answers["url"].strip()


def _local(_answers: dict) -> str | None:
    """Local Docker: a FalkorDB already on :6380, else one started in a container."""
    if check(LOCAL_URL, timeout=2) is None:
        console.print("  found FalkorDB on localhost:6380")
        return LOCAL_URL
    if not docker_ready():
        _fail("Nothing answers on localhost:6380 and Docker is not available. Start Docker, or use FalkorDB Cloud.")
        return None
    with console.status(f"starting FalkorDB in Docker ({CONTAINER}, first run pulls the image) ..."):
        problem = start_container()
    if problem:
        _fail(problem)
        return None
    _ok(f"started {CONTAINER} on localhost:6380")
    return LOCAL_URL


ASK = {"local": _local, "cloud": _ask_cloud, "url": _ask_url}


def _test(url: str) -> bool:
    with console.status(f"connecting to {escape(mask(url))} ..."):
        problem = check(url)
    if problem:
        _fail(problem)
    else:
        _ok(f"FalkorDB answers at {mask(url)}")
    return problem is None


def _location(args) -> str | None:
    """A FalkorDB URL that answers. On failure, --yes gives up; otherwise the user can try again,
    change the details or choose another place. --yes keeps the FalkorDB already set, if any."""
    value, source = _in_effect("falkordb_url")
    known = None if source == "default" else value
    url = (args.falkordb_url or "").strip() or (known if args.yes else None)
    kind = "url" if url else "local" if args.yes else None
    answers = {"url": url or known}
    while True:
        if kind is None:
            kind = _select(
                "Where should your graph live?",
                [
                    _choice("local", "Local Docker", LOCAL_URL),
                    _choice("cloud", "FalkorDB Cloud", "managed by FalkorDB"),
                    _choice("url", "I have a FalkorDB URL"),
                ],
            )
        if url is None:
            url = ASK[kind](answers)
        if url and _test(url):
            return url
        if args.yes:
            return None
        options = [_choice("retry", "Try again")]
        if kind != "local":
            options.append(_choice("edit", "Change the connection details"))
        options.append(_choice("back", "Back", "choose where the graph lives"))
        step = _select("What now?", options)
        if step == "back":
            kind = None
        if step != "retry" or kind == "local":
            url = None


def _graph(args) -> str:
    current = _in_effect("graph")[0]
    if args.graph or args.yes:
        return (args.graph or "").strip() or current
    return _text("Graph name", current, validate=lambda v: bool(v.strip()) or "the graph needs a name")


def _agents(args) -> list[str]:
    if args.agents is not None:
        return args.agents
    found = [a for a in AGENTS if shutil.which(a)]
    if args.yes:
        return found
    hints = {
        "claude": "plugin: MCP server, skills, safety hook",
        "codex": _home(codex_file()),
        "cursor": _home(cursor_file()),
    }
    choices = [
        _choice(
            a,
            name,
            hints[a],
            checked=a in found,
            disabled="claude CLI not found" if a == "claude" and a not in found else None,
        )
        for a, name in AGENTS.items()
    ]
    return questionary.checkbox(
        "Connect your agents", choices=choices, style=STYLE, instruction="(space to toggle, enter to confirm)"
    ).unsafe_ask()


def _plugin_source(args, agents: list[str]) -> str:
    if args.plugin_source or args.yes or "claude" not in agents or not is_checkout():
        return args.plugin_source or "github"
    return _select(
        "Install the Claude Code plugin from",
        [_choice("github", "GitHub", MARKETPLACE), _choice("local", "This checkout", _home(config.ROOT))],
    )


def _demo(args) -> bool:
    if args.demo is not None or args.yes:
        return bool(args.demo)
    problem = demo_problem()
    return (
        _select(
            "Start with",
            [
                _choice("demo", "The demo system", "7 services you can break and fix", disabled=problem),
                _choice("map", "Map my own system", "with Claude Code, in your repo"),
            ],
        )
        == "demo"
    )


def run(args) -> int:
    if not args.yes and not (sys.stdin.isatty() and sys.stdout.isatty()):
        _fail("rootpath init asks questions and needs a terminal; in scripts pass --yes (see rootpath init --help)")
        return 2
    if args.plugin_source == "local" and not is_checkout():
        _fail(f"--plugin-source local needs a git checkout of rootpath ({_home(config.ROOT)} is not one)")
        return 2
    console.print(
        Panel.fit(
            "[bold]Rootpath[/] · graph context for on-call agents\n"
            "[dim]Connects your FalkorDB graph and your coding agents, then saves the settings to "
            f"{escape(_home(userconfig.path()))}.[/]",
            border_style="cyan",
        )
    )
    try:
        userconfig.load()
    except ValueError as e:
        where = _home(userconfig.path())
        console.print(f"[yellow]![/] {escape(f'{where} is not valid TOML ({e.__cause__ or e})')}: init replaces it")
    try:
        return _init(args)
    except (KeyboardInterrupt, EOFError):
        console.print("\n[yellow]cancelled[/]")
        return 130


def _init(args) -> int:
    url = _location(args)
    if url is None:
        _fail("no working FalkorDB, nothing saved")
        return 1
    graph = _graph(args)
    agents = _agents(args)
    source = _plugin_source(args, agents)
    demo = _demo(args)

    saved = userconfig.save({"falkordb_url": url, "graph": graph})
    _ok(f"settings saved to {_home(saved)}")
    connected = {a: _connect(a, source) for a in agents}
    if not demo:
        console.print("  to map your own system: open Claude Code in your repo and run [bold]/rootpath:map[/]")
        demo_ok = True
    elif problem := demo_problem():
        _fail(f"the demo {problem}")
        demo_ok = False
    else:
        demo_ok = start_demo(url, graph)
    _summary(url, graph, saved, connected)
    return 0 if all(connected.values()) and demo_ok else 1


def _summary(url: str, graph: str, saved: Path, connected: dict[str, bool]) -> None:
    facts = Table.grid(padding=(0, 2))
    facts.add_column(style="dim")
    facts.add_column()
    facts.add_row("FalkorDB", Text(mask(url)))
    facts.add_row("Graph", Text(graph))
    agents = Text(", ").join(
        Text(f"{AGENTS[a]} {'✓' if ok else '✗'}", style="green" if ok else "red") for a, ok in connected.items()
    )
    facts.add_row("Agents", agents if connected else Text("none yet (run rootpath init again to connect one)"))
    facts.add_row("Settings", Text(_home(saved)))
    steps = Table.grid(padding=(0, 2))
    steps.add_column(style="bold cyan")
    steps.add_column()
    if connected.get("claude"):
        steps.add_row("claude", "/rootpath:map to map your system, /rootpath:oncall <what's broken> to investigate")
    steps.add_row(
        "rootpath start", "the console (http://localhost:7100), MCP over HTTP and graph sync, in the background"
    )
    console.print(
        Panel(
            Group(facts, Text(), Text("Next steps", style="bold"), steps),
            title="[bold]Rootpath is set up[/]",
            title_align="left",
            border_style="green",
            expand=False,
        )
    )
    for key, value in (("falkordb_url", url), ("graph", graph)):
        _warn_if_overridden(key, value)
    console.print(f"[dim]{INTEGRATIONS}[/]")


def _warn_if_overridden(key: str, saved: str) -> None:
    value, source = config.setting(key)
    if source in config.ENV[key] and value != saved:
        console.print(f"[yellow]![/] {source} is set in this shell and takes precedence over the config file")


def _shown(key: str, value: str) -> str:
    return mask(value) if key == "falkordb_url" else value


def _in_effect(key: str) -> tuple[str, str]:
    """config.setting, with a config file that can't be read counting as none: init replaces it."""
    try:
        return config.setting(key)
    except ValueError:
        return config.DEFAULTS[key], "default"


def show_config() -> int:
    p = userconfig.path()
    note = "" if p.exists() else "  (not created yet: run rootpath init)"
    console.print(Text(f"config file   {_home(p)}{note}"))
    try:
        settings = [(key, *config.setting(key)) for key in userconfig.KEYS]
    except ValueError as e:
        _fail(str(e))
        return 1
    for key, value, source in settings:
        where = "config file" if source == str(p) else source
        console.print(Text(f"{key:<13} {_shown(key, value)}  ") + Text(f"({where})", style="dim"))
    return 0


def set_config(key: str, value: str) -> int:
    value = value.strip()
    if key == "falkordb_url" and value and (problem := _url_problem(value)) is not True:
        _fail(problem)
        return 2
    try:
        current = userconfig.load()
    except ValueError as e:
        _fail(str(e))
        return 1
    saved = userconfig.save({**current, key: value})
    _ok(f"{key} = {_shown(key, value)} saved to {_home(saved)}" if value else f"{key} unset in {_home(saved)}")
    _warn_if_overridden(key, value)
    return 0
