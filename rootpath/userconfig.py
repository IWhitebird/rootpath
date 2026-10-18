"""The user config file written by `rootpath init`: $XDG_CONFIG_HOME/rootpath/config.toml
(default ~/.config/rootpath/config.toml). It can hold a password, so it is private to the user."""

import contextlib
import json
import os
import tempfile
import tomllib
from pathlib import Path

KEYS = ("falkordb_url", "graph")


def path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    return Path(base if base and os.path.isabs(base) else Path.home() / ".config") / "rootpath" / "config.toml"


def load() -> dict[str, str]:
    """The settings in the file ({} when there is none). Unknown keys and non-string values are ignored."""
    try:
        data = tomllib.loads(path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"{path()} is not valid TOML ({e}); fix it or run `rootpath init` again") from e
    return {k: data[k] for k in KEYS if isinstance(data.get(k), str)}


def toml_str(value: str) -> str:
    """A TOML basic string. JSON escapes the same characters, except DEL, which TOML also requires escaped."""
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def write_atomic(p: Path, text: str, mode: int = 0o600) -> None:
    """Replace p with text in one step: a crash leaves the old file or the new one, never a mix."""
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, p)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def save(values: dict[str, str]) -> Path:
    """Write the known, non-empty settings atomically: directory 0700, file 0600."""
    p = path()
    p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    p.parent.chmod(0o700)
    text = "# Rootpath settings, written by `rootpath init`. FALKORDB_URL and ONCALL_GRAPH override them.\n"
    text += "".join(f"{k} = {toml_str(values[k])}\n" for k in KEYS if values.get(k))
    write_atomic(p, text)
    return p
