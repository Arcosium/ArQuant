import time
from infra.rate_limit import SlidingWindowLimiter

def test_sliding_window_trips_and_recovers():
    lim = SlidingWindowLimiter(max_hits=3, window_sec=0.3)
    assert lim.hit("ip1") is None
    assert lim.hit("ip1") is None
    assert lim.hit("ip1") is None
    retry = lim.hit("ip1")
    assert retry is not None and 0 < retry <= 0.3   # 4th blocked
    assert lim.hit("ip2") is None                   # other key independent
    time.sleep(0.40)
    assert lim.hit("ip1") is None                   # window slid → allowed
    lim.reset("ip1")
    assert lim.hit("ip1") is None

def test_zero_max_hits_blocks_without_crashing():
    lim = SlidingWindowLimiter(max_hits=0, window_sec=0.5)
    r = lim.hit("k")
    assert r is not None and 0 < r <= 0.5     # blocked, no IndexError
    r2 = lim.hit("k")
    assert r2 is not None and 0 < r2 <= 0.5
