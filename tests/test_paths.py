"""Where Rootpath's files are, from a checkout and from a bundle (the binary or an installed package),
how a bundle's demo is copied and refreshed, `rootpath --version`, and install.sh's choices."""

import hashlib
import json
import os
import runpy
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from rootpath import cli, config, paths

REPO = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO / "web" / "public" / "install.sh"


@pytest.fixture
def bundle(tmp_path):
    """A bundle's resources: a small demo/ that also holds what a demo writes, and caches."""
    root = tmp_path / "bundle"
    files = {
        "docker-compose.yml": "services: {}\n",
        "service/app.py": "print('v1')\n",
        "runbooks/payment.md": "# payment\n",
        "flags/meta.json": "{}",
        "env/payment.env": "VERSION=9\n",
        "flags/flags.json": '{"f": true}',
        "state/changes.jsonl": "{}\n",
        "service/__pycache__/app.cpython-312.pyc": "",
    }
    for rel, text in files.items():
        (root / "demo" / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / "demo" / rel).write_text(text)
    return root


def test_a_checkout_keeps_everything_in_the_repo():
    from rootpath import responder, web

    assert not paths.bundled()
    assert paths.resources() == paths.root() == REPO
    assert config.ROOT == REPO
    assert (config.DEMO, config.STATE) == (REPO / "demo", REPO / "demo" / "state")
    assert config.COMPOSE_FILE == REPO / "demo" / "docker-compose.yml"
    assert web.DIST == REPO / "web" / "dist"
    assert responder.RUNS == REPO / ".oncall" / "runs"


def test_xdg_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", "relative/is/ignored")
    assert paths.data_dir() == tmp_path / "data" / "rootpath"
    assert paths.state_dir() == tmp_path / ".local" / "state" / "rootpath"


def test_a_frozen_binary_reads_its_unpacked_files_and_works_in_the_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    assert paths.bundled()
    assert paths.resources() == tmp_path
    assert paths.root() == tmp_path / "data" / "rootpath"


def test_demo_files_leave_out_what_the_demo_writes():
    files = paths.demo_files(REPO / "demo")
    assert {"docker-compose.yml", "baseline.json", "teams.json", "flags/meta.json", "service/Dockerfile"} <= set(files)
    assert not [f for f in files if f.startswith(("env/", "state/")) or f == "flags/flags.json"]


def _files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def test_sync_demo_copies_refreshes_and_keeps_state(bundle, tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "resources", lambda: bundle)
    target = tmp_path / "data" / "demo"
    assert paths.sync_demo(target) == target
    assert _files(target) == {
        "docker-compose.yml",
        "service/app.py",
        "runbooks/payment.md",
        "flags/meta.json",
        paths.STAMP,
    }

    # What the demo writes, as `rootpath demo up` does, survives refreshes.
    (target / "env").mkdir()
    (target / "env" / "payment.env").write_text("VERSION=2.4.1\n")
    (target / "flags" / "flags.json").write_text('{"mine": true}')
    stamp = (target / paths.STAMP).stat().st_mtime_ns
    paths.sync_demo(target)
    assert (target / paths.STAMP).stat().st_mtime_ns == stamp  # unchanged bundle: nothing rewritten
    (target / "service" / "app.py").unlink()
    paths.sync_demo(target)
    assert (target / "service" / "app.py").read_text() == "print('v1')\n"  # a copy gone missing comes back

    (bundle / "demo" / "service" / "app.py").write_text("print('v2')\n")
    (bundle / "demo" / "runbooks" / "payment.md").unlink()
    paths.sync_demo(target)
    assert (target / "service" / "app.py").read_text() == "print('v2')\n"
    assert (target / "env" / "payment.env").read_text() == "VERSION=2.4.1\n"
    assert (target / "flags" / "flags.json").read_text() == '{"mine": true}'
    # The runbook no longer shipped is gone, and no temporary file is left.
    assert _files(target) == {
        "docker-compose.yml",
        "service/app.py",
        "flags/meta.json",
        "flags/flags.json",
        "env/payment.env",
        paths.STAMP,
    }


FROZEN = """
import sys
sys.frozen, sys._MEIPASS = True, sys.argv[1]
from rootpath import config, paths
before = paths.data_dir().exists()
print(before, config.ROOT == paths.data_dir(), config.DEMO == paths.data_dir() / "demo", config.COMPOSE_FILE.is_file())
"""


def test_a_binary_copies_its_demo_on_first_use(bundle, tmp_path):
    """In a fresh process, as the binary starts: nothing is written until a command needs the demo."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ONCALL_", "XDG_"))}
    env |= {"HOME": str(tmp_path), "XDG_DATA_HOME": str(tmp_path / "data")}
    out = subprocess.run(
        [sys.executable, "-c", FROZEN, str(bundle)], cwd=REPO, env=env, capture_output=True, text=True, check=True
    )
    assert out.stdout.split() == ["False", "True", "True", "True"]


def test_a_bundle_uses_the_demo_of_an_explicit_oncall_root_as_it_is(bundle, tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    (checkout / "demo").mkdir(parents=True)
    (checkout / "demo" / "docker-compose.yml").write_text("services: {mine: {}}\n")
    monkeypatch.setattr(paths, "bundled", lambda: True)
    monkeypatch.setattr(paths, "resources", lambda: bundle)
    monkeypatch.setenv("ONCALL_ROOT", str(checkout))
    monkeypatch.setattr(config, "ROOT", checkout)
    demo = config._demo()
    assert demo["COMPOSE_FILE"] == checkout / "demo" / "docker-compose.yml"
    assert _files(checkout / "demo") == {"docker-compose.yml"}  # nothing shipped was copied over it
    assert demo["COMPOSE_FILE"].read_text() == "services: {mine: {}}\n"


def test_an_install_without_the_console_does_not_ask_for_npm(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from rootpath import web

    monkeypatch.setattr(web, "DIST", tmp_path)
    assert "npm run build" in TestClient(web.app).get("/").text  # a checkout builds it
    monkeypatch.setattr(paths, "bundled", lambda: True)
    page = TestClient(web.app).get("/").text
    assert "has no console" in page and "npm" not in page


def test_a_bundle_runs_its_agents_mcp_server_with_the_same_program(tmp_path, monkeypatch):
    from rootpath import responder

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths, "bundled", lambda: True)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    path = Path(responder.mcp_config("graph", "inv-test"))
    assert path.parent == tmp_path / "state" / "rootpath"
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600  # it holds the FalkorDB URL and its password
    server = json.loads(path.read_text())["mcpServers"]["oncall"]
    assert [server["command"], *server["args"]] == [sys.executable, "mcp", "--mode", "graph"]


def test_version(monkeypatch, capsys):
    from importlib.metadata import version

    monkeypatch.setattr(sys, "argv", ["rootpath", "--version"])
    with pytest.raises(SystemExit) as raised:
        cli.main()
    assert raised.value.code == 0
    assert capsys.readouterr().out == f"rootpath {version('rootpath')}\n"


def test_the_binary_gives_the_programs_it_runs_the_users_library_path(monkeypatch):
    restore = runpy.run_path(str(REPO / "scripts" / "binary_main.py"))["_restore_library_path"]
    monkeypatch.setattr(sys, "_MEIPASS", "/tmp/_MEI123", raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEI123:/opt/lib")  # as the loader leaves it
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/opt/lib")
    restore()
    assert os.environ["LD_LIBRARY_PATH"] == "/opt/lib"
    assert "LD_LIBRARY_PATH_ORIG" not in os.environ
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEI123")
    restore()
    assert "LD_LIBRARY_PATH" not in os.environ
    monkeypatch.setenv("LD_LIBRARY_PATH", "/usr/local/lib")  # not the loader's (macOS): left as it is
    restore()
    assert os.environ["LD_LIBRARY_PATH"] == "/usr/local/lib"


# ---------- install.sh ----------


def _release_name() -> str:
    """The name scripts/build_binary.py --release gives this platform's binary."""
    return f"rootpath-{runpy.run_path(str(REPO / 'scripts' / 'build_binary.py'))['platform_name']()}"


def _install(tmp_path, release: Path, *args: str, stubs: Path | None = None) -> subprocess.CompletedProcess:
    """install.sh with `release` as the release, and the programs in `stubs` first on PATH."""
    env = {
        "HOME": str(tmp_path / "home"),
        "PATH": f"{stubs}:{os.environ['PATH']}" if stubs else os.environ["PATH"],
        "TMPDIR": str(tmp_path),
        "ROOTPATH_INSTALL_DIR": str(tmp_path / "bin"),
        "ROOTPATH_DOWNLOAD_URL": release.as_uri(),
    }
    return subprocess.run(["sh", str(INSTALL_SH), *args], env=env, capture_output=True, text=True, timeout=60)


def _publish(release: Path, name: str, content: str, checksum: str | None = None) -> None:
    """A file of the release and its checksum file, which has `checksum` instead if given."""
    release.mkdir(exist_ok=True)
    (release / name).write_text(content)
    digest = checksum or hashlib.sha256(content.encode()).hexdigest()
    (release / f"{name}.sha256").write_text(f"{digest}  {name}\n")


def _release(tmp_path, script: str, checksum: str | None = None) -> Path:
    """A release directory with a stand-in binary for this platform and its checksum file."""
    _publish(tmp_path / "release", _release_name(), script, checksum)
    return tmp_path / "release"


needs_download_tool = pytest.mark.skipif(
    not (shutil.which("curl") or shutil.which("wget")), reason="install.sh needs curl or wget"
)
STAND_IN = '#!/bin/sh\n[ "$1" = --version ] && echo "rootpath 9.9.9"\nexit 0\n'
# As `uv tool install`: logs its arguments next to the bin dir, and installs the stand-in there.
STUB_UV = """#!/bin/sh
mkdir -p "$UV_TOOL_BIN_DIR"
echo "$@" > "$UV_TOOL_BIN_DIR/../uv-args"
cp "$(dirname "$0")/rootpath-stand-in" "$UV_TOOL_BIN_DIR/rootpath"
"""


def _stub_uv(tmp_path) -> Path:
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, script in (("uv", STUB_UV), ("rootpath-stand-in", STAND_IN)):
        (stubs / name).write_text(script)
        (stubs / name).chmod(0o755)
    return stubs


def test_install_sh_is_valid_posix_sh():
    subprocess.run(["sh", "-n", str(INSTALL_SH)], check=True)


@needs_download_tool
def test_install_sh_dry_run_falls_back_to_uv_without_a_binary(tmp_path):
    (tmp_path / "release").mkdir()
    out = _install(tmp_path, tmp_path / "release", "--dry-run")
    assert out.returncode == 0, out.stderr
    assert "installing with uv instead" in out.stdout
    assert "installing from GitHub, without the console" in out.stdout
    assert "tool install --force --python 3.12 git+https://github.com/IWhitebird/rootpath\n" in out.stdout
    assert out.stdout.endswith("dry run: nothing was changed\n")
    assert not (tmp_path / "bin").exists()


@needs_download_tool
def test_install_sh_installs_the_release_source_with_uv_without_a_binary(tmp_path):
    """The release's source archive has the console built, which a `uv tool install` from git has not."""
    _publish(tmp_path / "release", "rootpath-source.tar.gz", "the source, with its console")
    out = _install(tmp_path, tmp_path / "release", stubs=_stub_uv(tmp_path))
    assert out.returncode == 0, out.stderr
    *options, source = (tmp_path / "uv-args").read_text().split()
    assert options == ["tool", "install", "--force", "--quiet", "--python", "3.12"]
    assert Path(source).name == "rootpath-source.tar.gz" and Path(source).parent.parent == tmp_path  # downloaded
    assert f"rootpath 9.9.9 is installed: {tmp_path / 'bin' / 'rootpath'}\n" in out.stdout
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bin", "release", "stubs", "uv-args"]


@needs_download_tool
def test_install_sh_refuses_a_source_archive_that_does_not_match_its_checksum(tmp_path):
    _publish(tmp_path / "release", "rootpath-source.tar.gz", "the source", checksum="0" * 64)
    out = _install(tmp_path, tmp_path / "release", stubs=_stub_uv(tmp_path))
    assert out.returncode == 1
    assert "does not match its published SHA-256" in out.stderr
    assert not (tmp_path / "uv-args").exists() and not (tmp_path / "bin").exists()


@needs_download_tool
def test_install_sh_dry_run_downloads_the_release_binary(tmp_path):
    release = _release(tmp_path, STAND_IN)
    out = _install(tmp_path, release, "--dry-run")
    assert out.returncode == 0, out.stderr
    assert f"would download {release.as_uri()}/{_release_name()}\n" in out.stdout
    assert not (tmp_path / "bin").exists()


@needs_download_tool
def test_install_sh_installs_the_binary(tmp_path):
    out = _install(tmp_path, _release(tmp_path, STAND_IN))
    assert out.returncode == 0, out.stderr
    installed = tmp_path / "bin" / "rootpath"
    assert installed.read_text() == STAND_IN and os.access(installed, os.X_OK)
    assert f"rootpath 9.9.9 is installed: {installed}\n" in out.stdout
    assert out.stdout.endswith("run: rootpath init\n")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bin", "release"]  # its temporary files are gone


@needs_download_tool
def test_install_sh_refuses_a_binary_that_does_not_match_its_checksum(tmp_path):
    out = _install(tmp_path, _release(tmp_path, STAND_IN, checksum="0" * 64))
    assert out.returncode == 1
    assert "does not match its published SHA-256" in out.stderr
    assert not (tmp_path / "bin").exists()
