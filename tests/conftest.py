import time

import pytest


def _stack_up() -> bool:
    try:
        from rootpath import infra, store
        store.query("RETURN 1")
        return infra.stats("frontend")["status"] != "down"
    except Exception:
        return False


@pytest.fixture(scope="session")
def stack():
    """The live demo stack (`rootpath demo up`). Integration tests skip without it."""
    if not _stack_up():
        pytest.skip("demo stack not running (run `uv run rootpath demo up`)")
    return True


@pytest.fixture
def fresh(stack):
    """Hard reset before the test, so every scenario starts from the same healthy baseline."""
    from rootpath.cli import reset
    from rootpath.responder import detect
    reset()
    for _ in range(20):
        if detect() is None:
            break
        time.sleep(2)
    yield
    reset(soft=True)


def wait_for_alert(timeout: float = 60):
    """Same rule as the watcher: two consecutive detections 5s apart; returns the second."""
    from rootpath.responder import detect
    end, strikes = time.time() + timeout, 0
    while time.time() < end:
        hit = detect()
        strikes = strikes + 1 if hit else 0
        if strikes >= 2:
            time.sleep(4)  # let the stats windows fill with the failure
            return hit
        time.sleep(5)
    raise AssertionError("alert never fired")
