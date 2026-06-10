from infra.ops_throttle import ops_due


def test_due_when_never_run():
    assert ops_due(last_ts=0.0, now=10_000.0, throttle_sec=3600) is True


def test_not_due_within_window():
    assert ops_due(last_ts=10_000.0, now=10_000.0 + 1800, throttle_sec=3600) is False


def test_due_after_window():
    assert ops_due(last_ts=10_000.0, now=10_000.0 + 3601, throttle_sec=3600) is True


def test_none_last_is_due():
    assert ops_due(last_ts=None, now=10_000.0, throttle_sec=3600) is True
