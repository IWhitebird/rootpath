"""Entry point of the rootpath binary (see rootpath.spec)."""

import os
import sys

from rootpath.cli import main


def _restore_library_path() -> None:
    """On Linux the binary's loader puts its unpacked libraries first on LD_LIBRARY_PATH, which the dynamic
    linker has already read for this process. The programs it runs (docker, claude and the commands of its
    agent) get the user's own value back, so that they load their system's libraries, not this build's."""
    if os.environ.get("LD_LIBRARY_PATH", "").split(os.pathsep)[0] != sys._MEIPASS:
        return
    original = os.environ.pop("LD_LIBRARY_PATH_ORIG", None)
    if original is None:
        del os.environ["LD_LIBRARY_PATH"]
    else:
        os.environ["LD_LIBRARY_PATH"] = original


if __name__ == "__main__":
    _restore_library_path()
    # A copy of the binary that this one starts (`rootpath start` runs `serve`, which runs the alert watcher,
    # whose agents run `rootpath mcp`) unpacks its own files instead of using this process's: those are
    # removed when this process exits, while the copy keeps running.
    os.environ["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    try:
        main()
    finally:
        # `rootpath mcp` closes stdout when its client hangs up, and the binary's loader then flushes it
        # (and sys.__stdout__) without checking, which prints a traceback.
        if sys.stdout.closed:
            sys.stdout = sys.__stdout__ = open(os.devnull, "w")  # noqa: SIM115
