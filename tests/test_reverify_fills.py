"""_reverify_fills US 체결 재확인 회귀 테스트.

버그(2026-05-21 밤 로그): _reverify_fills 가 6자리 숫자(국내)만 보강하고 US 티커는
`continue` 로 전부 스킵했다. 그런데 실행부는 US 주문을 `ok = is_us and accepted` 로
즉시 잠정 체결 카운트하고, 주석은 "5분 후 _reverify_fills 가 보유 변동 없으면 차감"
이라 약속했다 → 약속과 구현이 모순. 결과적으로 미체결 US 주문이 영구히 누적 체결로 남았다.

요구 동작:
  - US 주문도 5분 후 _overseas_holdings() 로 보유 변동을 재확인한다.
  - 보유가 안 늘었으면(매수 미체결) 누적 카운트·trade_log 에서 차감한다.
  - 보유가 늘었으면(체결 확인) 카운트를 유지한다.
"""
import asyncio

import pytest

import main_swarm
from main_swarm import ArquantOrchestrator


class _FakeBroker:
    def __init__(self, kr_after, us_after):
        self._kr = list(kr_after)
        self._us = list(us_after)
        self._acct_snap = None

    async def kr_holdings(self):
        return list(self._kr)

    async def _overseas_holdings(self):
        return list(self._us)


def _orch(broker, *, trades_executed, trade_log):
    o = object.__new__(ArquantOrchestrator)  # __init__ 우회 — 무거운 초기화 회피
    o.broker = broker
    o._trades_executed = trades_executed
    o._trade_log = list(trade_log)
    return o


@pytest.fixture(autouse=True)
def _fast_and_silent(monkeypatch):
    # 5분 대기 seam 제거 + broadcast 무력화 (네트워크/상태 누수 차단)
    monkeypatch.setattr(main_swarm, "_REVERIFY_DELAY_SEC", 0)
    captured = []

    async def _fake_broadcast(ev):
        captured.append(ev)

    monkeypatch.setattr(main_swarm, "_broadcast", _fake_broadcast)
    return captured


def test_unfilled_us_buy_is_decremented():
    # 주문 직전 US 보유 없음 → 5분 후에도 OXY 보유 0 (미체결)
    broker = _FakeBroker(kr_after=[], us_after=[])
    o = _orch(broker, trades_executed=1,
              trade_log=[{"ticker": "OXY", "side": "buy", "qty": 8}])
    pending = [{"ticker": "OXY", "side": "buy", "qty": 8, "accepted": True, "filled": False}]
    baseline = []  # before: OXY 미보유

    asyncio.run(o._reverify_fills(pending, baseline))

    assert o._trades_executed == 0, "미체결 US 매수는 누적에서 차감돼야 한다"
    assert o._trade_log == [], "trade_log 에서도 제거돼야 한다"


def test_filled_us_buy_is_kept():
    # 5분 후 XOM 3주 확인됨 (체결)
    broker = _FakeBroker(kr_after=[], us_after=[{"code": "XOM", "qty": 3}])
    o = _orch(broker, trades_executed=1,
              trade_log=[{"ticker": "XOM", "side": "buy", "qty": 3}])
    pending = [{"ticker": "XOM", "side": "buy", "qty": 3, "accepted": True, "filled": False}]
    baseline = []  # before: XOM 미보유

    asyncio.run(o._reverify_fills(pending, baseline))

    assert o._trades_executed == 1, "체결 확인된 US 매수는 유지돼야 한다"
    assert len(o._trade_log) == 1


def test_pre_existing_us_position_uses_baseline_to_avoid_false_positive():
    # 이미 5주 보유 중 3주 추가 매수가 미체결인 경우: baseline(5) == after(5) → 차감.
    # baseline 을 무시하고 after_qty>0 만 보면 false-positive(체결로 오판)가 난다.
    broker = _FakeBroker(kr_after=[], us_after=[{"code": "XOM", "qty": 5}])
    o = _orch(broker, trades_executed=1,
              trade_log=[{"ticker": "XOM", "side": "buy", "qty": 3}])
    pending = [{"ticker": "XOM", "side": "buy", "qty": 3, "accepted": True, "filled": False}]
    baseline = [{"code": "XOM", "qty": 5}]  # before: 이미 5주 보유

    asyncio.run(o._reverify_fills(pending, baseline))

    assert o._trades_executed == 0, "보유 변동 없으면(미체결) baseline 비교로 차감돼야 한다"
