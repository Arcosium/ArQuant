import inspect
import asyncio
import time

import config
import main_swarm
import timefolio_swarm
from main_swarm import (ArquantOrchestrator, SAFETY_WATCH_INTERVAL_SEC,
                        _safety_sell_reason, _safety_watch_active)


def test_continuous_cycle_choice_is_removed_from_strategy_surface():
    removed = {"CONTINUOUS_CYCLES", "CONTINUOUS_MIN_GAP_SEC", "CONTINUOUS_MIN_CYCLE_SEC"}
    assert removed.isdisjoint(config.STRATEGY_TUNABLE_KEYS)
    assert removed.isdisjoint(config.STRATEGY_KEY_META)
    assert all(not hasattr(config, key) for key in removed)


def test_brain_is_hourly_and_guard_is_fixed_60_seconds():
    assert config.PERIODIC_CYCLE_SEC == 3600
    assert SAFETY_WATCH_INTERVAL_SEC == 60
    source = inspect.getsource(ArquantOrchestrator.start_continuous)
    assert "if periodic_due and is_trading_hours()" in source
    assert "market_open or periodic_due" not in source


def test_safety_watch_only_triggers_hard_stops():
    holding = {"cur_price": 98.0, "pnl_pct": -2.0}
    thesis = {"stop_price": 93.0, "target_price": 105.0}
    assert _safety_sell_reason(holding, thesis, 5.0) is None
    assert "계획 손절가" in _safety_sell_reason(
        {"cur_price": 92.0, "pnl_pct": -3.0}, thesis, 5.0)
    assert "하드 손절" in _safety_sell_reason(
        {"cur_price": 95.0, "pnl_pct": -5.1}, thesis, 5.0)


def test_safety_watch_does_not_take_profit_or_rebalance():
    assert _safety_sell_reason(
        {"cur_price": 120.0, "pnl_pct": 20.0}, {"stop_price": 90.0}, 5.0) is None


def test_timefolio_safety_watch_is_regular_session_only():
    assert _safety_watch_active("KR_TRADING", is_timefolio=True) is True
    assert _safety_watch_active("KR_PRE_MARKET", is_timefolio=True) is False
    assert _safety_watch_active("KR_AFTER_MARKET", is_timefolio=True) is False
    assert _safety_watch_active("US_TRADING", is_timefolio=True) is False


def test_kis_safety_watch_keeps_supported_sessions():
    assert _safety_watch_active("KR_TRADING") is True
    assert _safety_watch_active("US_TRADING") is True


def _timefolio_safety_orch(result):
    class _Broker:
        is_timefolio = True

        async def place_order_ex(self, order):
            return dict(result)

    o = ArquantOrchestrator.__new__(ArquantOrchestrator)
    o.uid = 77
    o.broker = _Broker()
    o._safety_last_order_at = {}
    o._safety_retry_after = {}
    o._events = []

    async def _emit(msg):
        o._events.append(msg)

    async def _finalize(order, session):
        return order, None

    o._emit = _emit
    o._finalize_kr_order_for_session = _finalize
    return o


def test_timefolio_safety_acceptance_persists_pending_order(tmp_path, monkeypatch):
    monkeypatch.setattr(main_swarm, "LIVE_TRADING", True)
    monkeypatch.setattr(main_swarm.metrics, "incr", lambda *args, **kwargs: None)
    monkeypatch.setattr(timefolio_swarm, "_pending_path", lambda uid: tmp_path / "pending.json")
    o = _timefolio_safety_orch({"accepted": True, "filled": False, "qty": 3,
                                "price": 10000, "result": "접수/대기"})
    holding = {"code": "005930", "qty": 3, "cur_price": 10000, "pnl_pct": -4}
    accepted = asyncio.run(o._submit_safety_sell(holding, "하드 손절", [holding]))
    assert accepted is True
    pending = timefolio_swarm._load_pending(77)
    assert pending == [{"ticker": "005930", "side": "sell", "qty": 3,
                        "price": 10000.0, "before_qty": 3,
                        "ts": pending[0]["ts"], "age": 0}]


def test_timefolio_safety_failure_sets_retry_backoff(monkeypatch):
    monkeypatch.setattr(main_swarm, "LIVE_TRADING", True)
    monkeypatch.setattr(main_swarm.metrics, "incr", lambda *args, **kwargs: None)
    o = _timefolio_safety_orch({"accepted": False, "filled": False,
                                "result": "매도 선택 실패"})
    before = time.time()
    holding = {"code": "005930", "qty": 3, "cur_price": 10000, "pnl_pct": -4}
    accepted = asyncio.run(o._submit_safety_sell(holding, "하드 손절", [holding]))
    assert accepted is False
    assert o._safety_retry_after["005930"] >= before + main_swarm.SAFETY_FAILURE_BACKOFF_SEC - 1
