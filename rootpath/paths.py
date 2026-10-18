"""Where Rootpath's files are. A git checkout keeps everything in the repo, as it always has. A bundle
(the PyInstaller binary, or an installed package such as a `uv tool install`) reads the files it ships
from resources(), which are read-only: a binary unpacks them to a temporary directory that is removed
when it exits. It runs the demo from a copy in the data dir and keeps runtime files in the state dir."""

import hashlib
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PACKAGED = Path(__file__).resolve().parent / "bundle"  # where pyproject.toml puts the resources in a wheel
DEMO_STATE = ("env", "flags/flags.json", "state")  # what the demo writes under demo/: never shipped or replaced
STAMP = ".bundle.json"  # in a copy of the demo: the shipped files it was made from, with their hashes


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False)) and hasattr(sys, "_MEIPASS")


def bundled() -> bool:
    return frozen() or PACKAGED.is_dir()


def resources() -> Path:
    """The files Rootpath ships: prompts/, demo/ and the console in web/dist/."""
    if frozen():
        return Path(sys._MEIPASS)
    return PACKAGED if PACKAGED.is_dir() else REPO


def _xdg(var: str, default: str) -> Path:
    base = os.environ.get(var)
    return Path(base if base and os.path.isabs(base) else Path.home() / default) / "rootpath"


def data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share")


def state_dir() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state")


def root() -> Path:
    """Where Rootpath works: the repo, or for a bundle the data dir, which holds its copy of demo/."""
    return data_dir() if bundled() else REPO


def _is_state(rel: str) -> bool:
    return any(rel == s or rel.startswith(f"{s}/") for s in DEMO_STATE)


def demo_files(demo: Path) -> dict[str, Path]:
    """The files of the demo in `demo` by relative path, without what the demo writes or caches."""
    files = {}
    for path in sorted(demo.rglob("*")):
        rel = path.relative_to(demo).as_posix()
        if path.is_file() and not _is_state(rel) and "__pycache__" not in path.parts:
            files[rel] = path
    return files


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_bytes(data)
    os.replace(tmp, path)  # whole files only, also when two processes copy the demo at once


def sync_demo(target: Path) -> Path:
    """Make `target` a copy of the shipped demo/: on first use, and again when the shipped files change or
    a copy is missing, which replaces the copies and removes those no longer shipped. What the demo wrote
    there is kept."""
    shipped = demo_files(resources() / "demo")
    hashes = {rel: hashlib.sha256(path.read_bytes()).hexdigest() for rel, path in shipped.items()}
    try:
        copied = json.loads((target / STAMP).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        copied = {}
    if copied == hashes and all((target / rel).is_file() for rel in hashes):
        return target
    for rel in set(copied) - set(hashes):
        (target / rel).unlink(missing_ok=True)
    for rel, path in shipped.items():
        _write(target / rel, path.read_bytes())
    _write(target / STAMP, json.dumps(hashes, indent=2, sort_keys=True).encode())
    return target
