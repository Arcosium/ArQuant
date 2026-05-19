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
    time.sleep(0.32)
    assert lim.hit("ip1") is None                   # window slid → allowed
    lim.reset("ip1")
    assert lim.hit("ip1") is None
