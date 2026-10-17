import time

import pytest


def _stack_up() -> bool:
    try:
        from oncall_graph import infra, store
        store.query("RETURN 1")
        return infra.stats("frontend")["status"] != "down"
    except Exception:
        return False


@pytest.fixture(scope="session")
def stack():
    """The live demo stack (`oncall-graph demo up`). Integration tests skip without it."""
    if not _stack_up():
        pytest.skip("demo stack not running (run `uv run oncall-graph demo up`)")
    return True


@pytest.fixture
def fresh(stack):
    """Hard reset before the test, so every scenario starts from the same healthy baseline."""
    from oncall_graph.cli import demo_reset
    from oncall_graph.responder import detect
    demo_reset(None)
    for _ in range(20):
        if detect() is None:
            break
        time.sleep(2)
    yield
    demo_reset(type("A", (), {"soft": True}))


def wait_for_alert(timeout: float = 60):
    """Same rule as the watcher: two consecutive detections 5s apart; returns the second."""
    from oncall_graph.responder import detect
    end, strikes = time.time() + timeout, 0
    while time.time() < end:
        hit = detect()
        strikes = strikes + 1 if hit else 0
        if strikes >= 2:
            time.sleep(4)  # let the stats windows fill with the failure
            return hit
        time.sleep(5)
    raise AssertionError("alert never fired")
