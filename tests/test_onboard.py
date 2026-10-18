"""`rootpath init` and the user config file. No network, no Docker: HOME and XDG_CONFIG_HOME
point at a temporary directory and the database, socket and subprocess calls are faked."""

import argparse
import io
import json
import os
import stat
import subprocess
import sys
import tomllib

import pytest
from redis import exceptions as rx

from rootpath import cli, config, onboard, userconfig

SECRET = "s3cr@t:pw/%"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """A throwaway home with no Rootpath settings in the environment."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    for var in (*config.ENV["falkordb_url"], *config.ENV["graph"]):
        monkeypatch.delenv(var, raising=False)
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


def test_config_file_location(tmp_path, monkeypatch):
    assert userconfig.path() == tmp_path / "xdg" / "rootpath" / "config.toml"
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/dir")  # relative paths are invalid in the XDG spec
    assert userconfig.path() == tmp_path / "home" / ".config" / "rootpath" / "config.toml"


def test_precedence_env_then_file_then_default(monkeypatch):
    assert config.setting("falkordb_url") == ("redis://localhost:6380", "default")
    assert config.setting("graph") == ("oncall", "default")
    userconfig.save({"falkordb_url": "rediss://u:p@cloud:1", "graph": "prod"})
    assert config.setting("falkordb_url") == ("rediss://u:p@cloud:1", str(userconfig.path()))
    assert config.setting("graph")[0] == "prod"
    # the plugin's MCP server gets FALKORDB_URL="" when its override is left empty
    monkeypatch.setenv("FALKORDB_URL", "")
    monkeypatch.setenv("ONCALL_GRAPH", "")
    assert config.setting("falkordb_url")[0] == "rediss://u:p@cloud:1"
    assert config.setting("graph")[0] == "prod"
    monkeypatch.setenv("FALKORDB_URL", "redis://env:7000")
    monkeypatch.setenv("ONCALL_GRAPH", "env-graph")
    assert config.setting("falkordb_url") == ("redis://env:7000", "FALKORDB_URL")
    assert config.setting("graph") == ("env-graph", "ONCALL_GRAPH")
    # how the safety hook sees the plugin's override: it wins, as it does for the plugin's MCP server
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_GRAPH", "from-plugin")
    assert config.setting("graph") == ("from-plugin", "CLAUDE_PLUGIN_OPTION_GRAPH")


def test_module_settings_read_the_config_file(tmp_path):
    """FALKORDB_URL / GRAPH are what the MCP server and the guard hook import."""
    userconfig.save({"falkordb_url": "rediss://u:p@cloud:1", "graph": "prod"})
    code = "from rootpath import config; print(config.FALKORDB_URL, config.GRAPH, config.FALKORDB['ssl'])"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=os.environ, check=True)
    assert out.stdout.split() == ["rediss://u:p@cloud:1", "prod", "True"]


GUARD_EVENT = '{"tool_name": "mcp__oncall__restart_service", "tool_input": {"service": "x", "dry_run": false}}'


def _broken_config_file() -> None:
    userconfig.path().parent.mkdir(parents=True)
    userconfig.path().write_text('falkordb_url = "redis://h:1"\ngraph = \n')


def _first_use(name: str) -> str:
    """What a fresh process prints when it first reads config.<name> and that fails."""
    code = f"from rootpath import config\ntry:\n    config.{name}\nexcept ValueError as e:\n    print(e)"
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=os.environ).stdout


def test_a_broken_config_file_fails_what_needs_the_graph(capsys):
    _broken_config_file()
    assert "config.toml is not valid TOML" in _first_use("GRAPH")
    guard = [sys.executable, "-m", "rootpath.guard"]
    out = subprocess.run(guard, input=GUARD_EVENT, capture_output=True, text=True, env=os.environ).stdout
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"  # fails closed
    assert onboard.show_config() == 1 and onboard.set_config("graph", "prod") == 1
    assert "not valid TOML" in capsys.readouterr().out


def test_init_replaces_a_broken_config_file(monkeypatch, capsys, fake_run):
    _broken_config_file()
    assert _init(monkeypatch, "--falkordb-url", "redis://h:2", "--agents", "none", "--yes") == 0
    assert "is not valid TOML" in capsys.readouterr().out
    assert userconfig.load() == {"falkordb_url": "redis://h:2", "graph": "oncall"}


def test_a_bad_saved_url_names_where_it_comes_from():
    userconfig.save({"falkordb_url": "http://h:1"})
    out = _first_use("FALKORDB")
    assert "must start with redis://" in out and str(userconfig.path()) in out


def test_save_is_private_and_round_trips():
    p = userconfig.path()
    p.parent.mkdir(parents=True)
    p.write_text('graph = "old"\n')
    p.chmod(0o644)
    tricky = 'redis://:a"b\\c\x7fd@h:1'
    userconfig.save({"falkordb_url": tricky, "graph": "g", "other": "x"})
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert stat.S_IMODE(p.parent.stat().st_mode) == 0o700
    assert userconfig.load() == {"falkordb_url": tricky, "graph": "g"}
    assert [f.name for f in p.parent.iterdir()] == ["config.toml"]  # no temp file left behind
    userconfig.save({"falkordb_url": tricky, "graph": ""})  # empty means unset
    assert userconfig.load() == {"falkordb_url": tricky}


def test_load_ignores_unknown_keys_and_reports_bad_toml():
    p = userconfig.path()
    p.parent.mkdir(parents=True)
    p.write_text('graph = 3\nfalkordb_url = "redis://h"\nextra = "x"\n')
    assert userconfig.load() == {"falkordb_url": "redis://h"}
    p.write_text("graph = \n")
    with pytest.raises(ValueError, match="config.toml is not valid TOML"):
        userconfig.load()


@pytest.mark.parametrize(
    "args,url",
    [
        (
            ("r-1.falkordb.cloud", 55011, "falkordb", SECRET, True),
            "rediss://falkordb:s3cr%40t%3Apw%2F%25@r-1.falkordb.cloud:55011",
        ),
        (("db.internal", 6379, "default", "pw", False), "redis://default:pw@db.internal:6379"),
        (("db.internal", 6379, "default", "", True), "rediss://db.internal:6379"),
        (("::1", 6380, "", "pw", False), "redis://:pw@[::1]:6380"),
    ],
)
def test_cloud_url_encodes_credentials(args, url):
    assert onboard.cloud_url(*args) == url
    c = config.falkordb_connection(url)
    host, port, username, password, tls = args
    assert (c["port"], c["password"], c["ssl"]) == (port, password or None, tls)
    assert c["username"] == ((username or None) if password else None)


def test_mask_hides_only_the_password():
    assert onboard.mask("rediss://falkordb:s3cr%40t@r-1.cloud:55011") == "rediss://falkordb:****@r-1.cloud:55011"
    assert onboard.mask("redis://:pw@h:1") == "redis://:****@h:1"
    assert onboard.mask("redis://localhost:6380") == "redis://localhost:6380"


def _db_raising(error):
    class FakeDB:
        def __init__(self, **kwargs):
            pass

        def select_graph(self, name):
            return self

        def ro_query(self, q):
            if error:
                raise error

        def close(self):
            pass

    return FakeDB


def _tcp(ok: bool):
    def connect(address, timeout):
        if not ok:
            raise ConnectionRefusedError(111, "Connection refused")
        return type("Sock", (), {"close": lambda self: None})()

    return connect


def test_check_never_echoes_the_password(monkeypatch):
    monkeypatch.setattr(onboard, "FalkorDB", _db_raising(None))
    problem = onboard.check("rediss://default:hunter/secret@h:1")  # "/" not percent-encoded
    assert "percent-encoded" in problem and "hunter" not in problem


@pytest.mark.parametrize(
    "url,error,tcp,expected",
    [
        ("redis://h:1", None, True, None),
        ("redis://h:1", rx.ResponseError("Invalid graph operation on empty key"), True, None),
        (
            "redis://u:bad@h:1",
            rx.AuthenticationError("invalid username-password pair"),
            True,
            "Wrong username or password",
        ),
        ("redis://h:1", rx.AuthenticationError("Authentication required."), True, "needs a username and password"),
        ("rediss://h:1", rx.TimeoutError("Timeout connecting to server"), True, "not over TLS"),
        ("redis://h:1", rx.ConnectionError("Connection closed by server."), True, "requires TLS"),
        (
            "rediss://h:1",
            rx.TimeoutError("Timeout connecting to server"),
            False,
            "Can't reach h:1 (Connection refused)",
        ),
        ("redis://h:1", rx.ResponseError("unknown command 'GRAPH.RO_QUERY'"), True, "not FalkorDB"),
        ("http://h:1", None, True, "must start with redis://"),
    ],
)
def test_check_explains_failures(monkeypatch, url, error, tcp, expected):
    monkeypatch.setattr(onboard, "FalkorDB", _db_raising(error))
    monkeypatch.setattr(onboard.socket, "create_connection", _tcp(tcp))
    problem = onboard.check(url)
    assert problem is None if expected is None else expected in problem


CODEX = """# my codex settings
model = "o3"   # keep this comment

[mcp_servers.other]
command = "other"
args = ["serve"]

[profiles.fast]
model = "o4-mini"
"""


def test_codex_config_appends_and_is_idempotent():
    cmd = ["/opt/root path/bin/rootpath", "mcp"]
    once = onboard.codex_config(CODEX, cmd)
    assert once.startswith(CODEX) and once == onboard.codex_config(once, cmd)
    data = tomllib.loads(once)
    assert data["mcp_servers"]["rootpath"] == {"command": cmd[0], "args": ["mcp"], "env": {"ONCALL_AGENT": "codex"}}
    assert data["mcp_servers"]["other"] == {"command": "other", "args": ["serve"]}
    assert tomllib.loads(onboard.codex_config("", cmd))["mcp_servers"]["rootpath"]["command"] == cmd[0]


def test_codex_config_replaces_an_existing_block_in_place():
    old = CODEX.replace(
        "[profiles.fast]",
        '[mcp_servers.rootpath]\ncommand = "/old/rootpath"\nargs = ["mcp"]\n\n'
        '[mcp_servers.rootpath.env]\nFALKORDB_URL = "redis://x"\n\n[profiles.fast]',
    )
    new = onboard.codex_config(old, ['/new/"rootpath"', "mcp"])
    before, _, after = old.partition("[mcp_servers.rootpath]")
    assert new.startswith(before) and new.endswith('[profiles.fast]\nmodel = "o4-mini"\n')
    assert "/old/rootpath" not in new and "FALKORDB_URL" not in new
    assert tomllib.loads(new)["mcp_servers"]["rootpath"]["command"] == '/new/"rootpath"'


def test_codex_config_keeps_the_comments_above_the_next_table():
    old = CODEX.replace(
        "[profiles.fast]", '[mcp_servers.rootpath]\ncommand = "/old"\n\n# fast profile\n[profiles.fast]'
    )
    new = onboard.codex_config(old, ["/bin/rootpath", "mcp"])
    assert '"codex" }\n\n# fast profile\n[profiles.fast]\n' in new and "/old" not in new


def test_codex_config_refuses_what_it_cannot_merge_safely():
    with pytest.raises(ValueError, match="add it yourself"):
        onboard.codex_config('[mcp_servers]\nrootpath = { command = "x" }\n', ["/bin/rootpath", "mcp"])
    with pytest.raises(ValueError, match="not valid TOML"):
        onboard.codex_config("model = \n", ["/bin/rootpath", "mcp"])


def test_cursor_config_keeps_other_servers_and_is_idempotent():
    text = json.dumps({"mcpServers": {"github": {"command": "gh-mcp"}}, "theme": "dark"})
    once = onboard.cursor_config(text, ["/bin/rootpath", "mcp"])
    assert once == onboard.cursor_config(once, ["/bin/rootpath", "mcp"])
    data = json.loads(once)
    assert data["theme"] == "dark" and data["mcpServers"]["github"] == {"command": "gh-mcp"}
    assert data["mcpServers"]["rootpath"] == {
        "command": "/bin/rootpath",
        "args": ["mcp"],
        "env": {"ONCALL_AGENT": "cursor"},
    }
    with pytest.raises(ValueError, match="mcpServers"):
        onboard.cursor_config("[1, 2]", ["/bin/rootpath", "mcp"])
    with pytest.raises(ValueError, match="not valid JSON"):
        onboard.cursor_config("{", ["/bin/rootpath", "mcp"])


def test_agent_files_keep_their_symlink_and_mode(monkeypatch, tmp_path, home):
    monkeypatch.setattr(onboard, "rootpath_exe", lambda: ["/opt/rootpath/bin/rootpath"])
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    dotfile = tmp_path / "dotfiles" / "codex.toml"
    dotfile.parent.mkdir()
    dotfile.write_text(CODEX)
    dotfile.chmod(0o640)
    (tmp_path / "codex").mkdir()
    (tmp_path / "codex" / "config.toml").symlink_to(dotfile)
    assert onboard.CONNECT["codex"]("github") == f"MCP server in {dotfile.resolve()}"
    assert (tmp_path / "codex" / "config.toml").is_symlink() and stat.S_IMODE(dotfile.stat().st_mode) == 0o640
    assert tomllib.loads(dotfile.read_text())["mcp_servers"]["rootpath"]["command"] == "/opt/rootpath/bin/rootpath"
    onboard.CONNECT["cursor"]("github")
    assert stat.S_IMODE((home / ".cursor" / "mcp.json").stat().st_mode) == 0o600


def test_the_local_container_is_loopback_only_and_keeps_its_data(monkeypatch):
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 1 if "inspect" in cmd else 0, stdout="", stderr="")

    monkeypatch.setattr(onboard.subprocess, "run", run)
    monkeypatch.setattr(onboard, "check", lambda url, timeout=5.0: None)
    assert onboard.start_container() is None
    docker_run = calls[1]
    assert docker_run[docker_run.index("-p") + 1] == "127.0.0.1:6380:6379"
    assert docker_run[docker_run.index("-v") + 1] == "rootpath-falkordb:/var/lib/falkordb/data"


def test_rootpath_exe_is_the_running_executable(tmp_path, monkeypatch):
    exe = tmp_path / "bin" / "rootpath"
    exe.parent.mkdir()
    exe.write_text("")
    monkeypatch.setattr(sys, "argv", [str(exe), "init"])
    assert onboard.rootpath_exe() == [str(exe)]
    monkeypatch.setattr(sys, "argv", ["-c"])
    monkeypatch.setattr(onboard.shutil, "which", lambda name: None)
    assert onboard.rootpath_exe() == [sys.executable, "-m", "rootpath.cli"]


def test_agent_list_argument():
    assert cli._agent_list("claude, codex,claude") == ["claude", "codex"]
    assert cli._agent_list("none") == []
    with pytest.raises(argparse.ArgumentTypeError, match="vim"):
        cli._agent_list("claude,vim")


@pytest.fixture
def fake_run(monkeypatch):
    """Records subprocess.run calls; set .returncode / .stderr to change what the claude CLI answers.
    The connection test passes and agents get a fixed rootpath executable."""

    def run(cmd, **kwargs):
        run.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, run.returncode, stdout="", stderr=run.stderr)

    run.calls, run.returncode, run.stderr = [], 0, ""
    monkeypatch.setattr(onboard.subprocess, "run", run)
    monkeypatch.setattr(onboard, "check", lambda url, timeout=5.0: None)
    monkeypatch.setattr(onboard, "rootpath_exe", lambda: ["/opt/rootpath/bin/rootpath"])
    return run


def _init(monkeypatch, *argv) -> int:
    monkeypatch.setattr(sys, "argv", ["rootpath", "init", *argv])
    with pytest.raises(SystemExit) as exit_:
        cli.main()
    return exit_.value.code


def test_noninteractive_init(monkeypatch, capsys, fake_run, home):
    url = onboard.cloud_url("r-1.falkordb.cloud", 55011, "falkordb", SECRET)
    code = _init(monkeypatch, "--falkordb-url", url, "--graph", "prod", "--agents", "claude,codex,cursor", "--yes")
    assert code == 0
    assert userconfig.load() == {"falkordb_url": url, "graph": "prod"}
    assert fake_run.calls == [
        ["claude", "plugin", "marketplace", "add", "IWhitebird/rootpath"],
        ["claude", "plugin", "install", "rootpath@rootpath"],
    ]
    codex = tomllib.loads((home / ".codex" / "config.toml").read_text())["mcp_servers"]["rootpath"]
    cursor = json.loads((home / ".cursor" / "mcp.json").read_text())["mcpServers"]["rootpath"]
    assert codex["command"] == cursor["command"] == "/opt/rootpath/bin/rootpath"
    assert codex["args"] == cursor["args"] == ["mcp"]
    out = capsys.readouterr().out
    assert "falkordb:****@r-1.falkordb.cloud" in out and "Next steps" in out and "/rootpath:map" in out
    assert "s3cr" not in out and "Integrations coming soon" in out


def test_init_again_with_yes_keeps_the_saved_falkordb(monkeypatch, fake_run):
    userconfig.save({"falkordb_url": "rediss://u:p@cloud:1", "graph": "prod"})
    assert _init(monkeypatch, "--agents", "none", "--yes") == 0
    assert userconfig.load() == {"falkordb_url": "rediss://u:p@cloud:1", "graph": "prod"}


def test_init_installs_the_plugin_from_the_checkout(monkeypatch, fake_run):
    monkeypatch.setattr(onboard, "is_checkout", lambda: True)
    code = _init(monkeypatch, "--falkordb-url", "redis://h:1", "--agents", "claude", "--plugin-source", "local", "-y")
    assert code == 0
    assert fake_run.calls[0] == ["claude", "plugin", "marketplace", "add", str(config.ROOT)]


def test_init_reports_a_failed_agent_but_keeps_the_settings(monkeypatch, capsys, fake_run):
    fake_run.returncode, fake_run.stderr = 1, "marketplace not found"
    assert _init(monkeypatch, "--falkordb-url", "redis://h:1", "--agents", "claude", "--yes") == 1
    assert "marketplace not found" in capsys.readouterr().out
    assert userconfig.load()["falkordb_url"] == "redis://h:1"


def test_init_saves_nothing_without_a_working_database(monkeypatch, capsys, fake_run):
    monkeypatch.setattr(onboard, "check", lambda url, timeout=5.0: "Wrong username or password.")
    assert _init(monkeypatch, "--falkordb-url", f"rediss://u:{SECRET}@h:1", "--agents", "none", "--yes") == 1
    out = capsys.readouterr().out
    assert "Wrong username or password" in out and "nothing saved" in out and "s3cr" not in out
    assert not userconfig.path().exists() and not fake_run.calls


def test_interactive_init_needs_a_terminal(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    assert _init(monkeypatch) == 2
    assert "--yes" in capsys.readouterr().out


def test_config_set_and_show_mask_the_password(monkeypatch, capsys):
    url = onboard.cloud_url("h", 1, "u", SECRET)
    assert onboard.set_config("falkordb_url", url) == 0
    assert onboard.set_config("graph", "prod") == 0
    onboard.show_config()
    out = capsys.readouterr().out
    assert "rediss://u:****@h:1" in out and "s3cr" not in out and "(config file)" in out
    monkeypatch.setenv("ONCALL_GRAPH", "staging")
    assert onboard.set_config("graph", "prod") == 0
    assert "ONCALL_GRAPH is set in this shell" in capsys.readouterr().out
    assert onboard.set_config("falkordb_url", "https://h") == 2
    assert onboard.set_config("falkordb_url", f"rediss://u:{SECRET}@h:1") == 2  # not percent-encoded
    out = capsys.readouterr().out
    assert "percent-encoded" in out and "pw" not in out
    assert userconfig.load()["falkordb_url"] == url
