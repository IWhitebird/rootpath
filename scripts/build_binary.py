"""Build the rootpath binary for this OS and CPU: dist/rootpath, one file that carries Python, the package,
its dependencies, the agent's prompts, the demo and the console (rootpath.spec).

    uv run python scripts/build_binary.py            # dist/rootpath
    uv run python scripts/build_binary.py --release  # and dist/rootpath-<os>-<arch> + .sha256, as released

The console is built first (npm in web/) when web/dist has none. PyInstaller works in build/."""

import argparse
import hashlib
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "web"
DIST = REPO / "dist"
BUILD = REPO / "build"
SYSTEMS = {"Linux": "linux", "Darwin": "macos"}
ARCHS = {"x86_64": "x86_64", "amd64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}


def platform_name() -> str:
    """<os>-<arch> as install.sh names the release files: linux-x86_64, macos-arm64, macos-x86_64."""
    system, machine = platform.system(), platform.machine().lower()
    return f"{SYSTEMS.get(system, system.lower())}-{ARCHS.get(machine, machine)}"


def run(cmd: list[str], cwd: Path = REPO) -> None:
    print(f"$ {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def build_console() -> None:
    if (WEB / "dist" / "console.html").is_file():
        return
    npm = shutil.which("npm")
    if not npm:
        sys.exit("web/dist has no console and npm is not on PATH: install Node.js, or run `npm run build` in web/")
    if not (WEB / "node_modules").is_dir():
        run([npm, "ci"], cwd=WEB)
    run([npm, "run", "build"], cwd=WEB)


def build_binary() -> Path:
    pyinstaller = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean"]
    run([*pyinstaller, f"--distpath={DIST}", f"--workpath={BUILD}", str(REPO / "rootpath.spec")])
    return DIST / "rootpath"


def check(binary: Path) -> str:
    """The binary's `--version`, which fails if it can't start."""
    out = subprocess.run([str(binary), "--version"], capture_output=True, text=True, check=True, timeout=120)
    return out.stdout.strip()


def release(binary: Path) -> Path:
    """dist/rootpath-<os>-<arch>, the file a release carries for this platform, and its checksum file."""
    asset = DIST / f"rootpath-{platform_name()}"
    shutil.copy2(binary, asset)
    digest = hashlib.sha256(asset.read_bytes()).hexdigest()
    asset.with_name(f"{asset.name}.sha256").write_text(f"{digest}  {asset.name}\n")
    return asset


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--release", action="store_true", help=f"also write dist/rootpath-{platform_name()} and its .sha256")
    args = p.parse_args()
    started = time.monotonic()
    build_console()
    binary = build_binary()
    version = check(binary)
    size = binary.stat().st_size / 2**20
    print(f"\n{binary.relative_to(REPO)}: {version}, {size:.1f} MB, built in {time.monotonic() - started:.0f}s")
    if args.release:
        print(f"{release(binary).relative_to(REPO)} (+ .sha256)")


if __name__ == "__main__":
    main()
