"""ADMIN=게시 / 소비자=대기-수신·폴백 / SHARE off=항상 자체계산."""
import asyncio
from datetime import datetime, timezone, timedelta
import main_swarm
from infra.market_intel import MarketIntelligenceStore

KST = timezone(timedelta(hours=9))

class _StubRuntime:
    def __init__(self, params): self._p = params
    def get(self, k, default=None, uid=None): return self._p.get(k, default)

def _setup(monkeypatch, *, share=True, wait=0.05):
    monkeypatch.setattr(main_swarm, "_now_kst",
                        lambda: datetime(2026, 6, 8, 10, 5, 0, tzinfo=KST))
    monkeypatch.setattr(main_swarm, "runtime", _StubRuntime(
        {"SHARE_MARKET_INTELLIGENCE": share, "SHARE_PRODUCER_WAIT_SEC": wait}))
    store = MarketIntelligenceStore()
    monkeypatch.setattr(main_swarm, "get_intel_store", lambda: store)
    return store

def _orch(is_admin):
    o = main_swarm.ArquantOrchestrator.__new__(main_swarm.ArquantOrchestrator)
    o.uid = 1 if is_admin else 2
    o.is_admin = is_admin
    o._producer_absent_this_cycle = False
    return o

def _counter():
    calls = {"n": 0}
    async def compute():
        calls["n"] += 1
        return "COMPUTED"
    return calls, compute

def test_admin_computes_and_publishes(monkeypatch):
    store = _setup(monkeypatch)
    o = _orch(True); calls, compute = _counter()
    res = asyncio.run(o._shared_or_compute("macro_report", None, compute))
    assert res == "COMPUTED" and calls["n"] == 1
    assert store.peek("macro_report", "2026-06-08 10", None) == "COMPUTED"

def test_admin_empty_result_not_published(monkeypatch):
    store = _setup(monkeypatch)
    o = _orch(True)
    async def empty(): return ""
    res = asyncio.run(o._shared_or_compute("macro_report", None, empty))
    assert res == ""
    assert store.peek("macro_report", "2026-06-08 10", None) is None

def test_consumer_hit_does_not_compute(monkeypatch):
    store = _setup(monkeypatch)
    asyncio.run(store.publish("macro_report", "2026-06-08 10", "SHARED", None, uid=1, now=1.0))
    o = _orch(False); calls, compute = _counter()
    res = asyncio.run(o._shared_or_compute("macro_report", None, compute))
    assert res == "SHARED" and calls["n"] == 0

def test_consumer_miss_falls_back_and_sets_flag(monkeypatch):
    _setup(monkeypatch, wait=0.05)
    o = _orch(False); calls, compute = _counter()
    res = asyncio.run(o._shared_or_compute("macro_report", None, compute))
    assert res == "COMPUTED" and calls["n"] == 1
    assert o._producer_absent_this_cycle is True

def test_consumer_absent_flag_short_circuits(monkeypatch):
    _setup(monkeypatch, wait=10.0)
    o = _orch(False); o._producer_absent_this_cycle = True
    calls, compute = _counter()
    res = asyncio.run(o._shared_or_compute("news_report", None, compute))
    assert res == "COMPUTED" and calls["n"] == 1

def test_share_disabled_always_computes(monkeypatch):
    store = _setup(monkeypatch, share=False)
    o = _orch(True); calls, compute = _counter()
    res = asyncio.run(o._shared_or_compute("macro_report", None, compute))
    assert res == "COMPUTED" and calls["n"] == 1
    assert store.peek("macro_report", "2026-06-08 10", None) is None
