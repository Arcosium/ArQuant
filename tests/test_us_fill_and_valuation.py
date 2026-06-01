"""KR/US 비대칭 수정 (사장 지시 2026-05-30):
  ③ US 체결가/평단 미기록 — 폴링 확정 체결 시 US 보유 평단·현재가가 0/누락이면 체결가가 None 으로
     찍혀 실현손익이 추정가(부정확)로 날조됐다. 라이브 호가(us_last_price)로 체결가를 확보한다.
  ④ 자산곡선 US 세션 동결 — present-balance(권위) 조회가 장중 실패하면 US 평가가 stale 캐시값에
     '동결'됐다. 보유 라이브 현재가로 주식분을 재계산하고 캐시 예수금분을 보존해 곡선이 움직이게 한다.
"""
import asyncio
import time

import pytest

import main_swarm
from main_swarm import ArquantOrchestrator
from infra.kis_broker import KISBroker


# ── ③ 폴링 경로 US 체결가 확보 ────────────────────────────────────────────────
class _USFakeBroker:
    def __init__(self, us_after, last_price):
        self._us_after = us_after
        self._last = last_price
        self._acct_snap = None

    async def kr_holdings(self):
        return []

    async def _overseas_holdings(self):
        return list(self._us_after)

    async def us_last_price(self, tk):
        return self._last


def _orch_poll(broker):
    o = object.__new__(ArquantOrchestrator)
    o.broker = broker
    o.uid = 1
    o._trades_executed = 0
    o._trade_log = []
    o._stop_event = asyncio.Event()
    return o


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(main_swarm, "_REVERIFY_DELAY_SEC", 0)
    monkeypatch.setattr(main_swarm, "_POLL_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(main_swarm, "get_current_session", lambda: "US_TRADING")

    async def _fb(ev, uid=None):
        pass

    monkeypatch.setattr(main_swarm, "_broadcast", _fb)


def test_poll_us_buy_captures_fill_from_quote():
    # US 보유가 잡히지만 평단/현재가 결손(장중 글리치) → 라이브 호가로 체결가·평단 확보
    broker = _USFakeBroker(us_after=[{"code": "FCX", "qty": 8}], last_price=41.5)
    o = _orch_poll(broker)
    asyncio.run(o._poll_fills_until_confirmed([{"ticker": "FCX", "side": "buy", "qty": 8}],
                                              baseline_holdings=[]))
    assert o._trades_executed == 1
    rec = o._trade_log[0]
    assert rec["fill_price"] == 41.5, "US 평단 결손 시 라이브 호가로 체결가를 확보해야 한다"
    assert rec["avg_cost"] == 41.5, "매수 평단 미상이면 체결가로 근사해야 한다(None 금지)"
    assert rec["fill_currency"] == "USD"


def test_poll_us_sell_captures_fill_from_quote():
    broker = _USFakeBroker(us_after=[], last_price=42.0)  # 매도 후 보유 소멸
    o = _orch_poll(broker)
    asyncio.run(o._poll_fills_until_confirmed([{"ticker": "FCX", "side": "sell", "qty": 8}],
                                              baseline_holdings=[{"code": "FCX", "qty": 8}]))
    assert o._trades_executed == 1
    assert o._trade_log[0]["fill_price"] == 42.0, "US 매도 체결가를 라이브 호가로 확보해야 한다"


def test_poll_kr_unchanged():
    # KR 은 평단 역산이 신뢰 가능 — 라이브 호가 폴백을 타면 안 된다(기존 동작 보존)
    broker = _USFakeBroker(us_after=[], last_price=99999)
    broker.kr_holdings = lambda: _coro([{"code": "012330", "qty": 2, "avg_price": 770000, "cur_price": 773000}])
    o = _orch_poll(broker)
    asyncio.run(o._poll_fills_until_confirmed([{"ticker": "012330", "side": "buy", "qty": 1}],
                                              baseline_holdings=[{"code": "012330", "qty": 1, "avg_price": 769000}]))
    assert o._trades_executed == 1
    assert o._trade_log[0]["fill_currency"] == "KRW"
    assert o._trade_log[0]["fill_price"] != 99999, "KR 은 라이브 호가 폴백을 타면 안 된다"


async def _coro(v):
    return v


# ── ④ 평가액 동결 방지 (예수금 보존 라이브 폴백) ───────────────────────────────
class _ValBroker(KISBroker):
    def __init__(self, present, us_holdings, cache):
        self._present = present
        self._us_holdings = us_holdings
        self._overseas_krw_cache = (cache[0], cache[1])  # (총평가, ts)
        self._overseas_stock_krw = cache[2]              # 캐시된 주식분
        self._overseas_exrt = cache[3]                   # 캐시된 기준환율

    async def kr_account_snapshot(self, force=False):
        return {"buying_power": {"total_eval": 7975828.0, "cash": 7975828.0}, "holdings": [], "ok": True}

    async def _overseas_holdings(self):
        return list(self._us_holdings)

    async def _bond_holdings(self):
        return []

    async def _fund_holdings(self):
        return []

    async def _overseas_present_krw(self):
        return self._present

    def _set_overseas_cache(self, krw, ts, stock=None, exrt=None):
        pass  # 테스트: 디스크 쓰기 생략


def test_valuation_unfreezes_with_live_stock_when_present_balance_fails():
    now = time.time()
    # present-balance 실패. 보유 FCX 8주 @ $41.5. 캐시: 총 727,873 / 주식분 600,000 / 환율 1500.
    b = _ValBroker(present={"ok": False, "krw_value": 0.0, "stock_value": 0.0, "exrt": 0.0},
                   us_holdings=[{"code": "FCX", "qty": 8, "cur_price": 41.5, "ccy": "USD"}],
                   cache=(727873.0, now, 600000.0, 1500.0))
    res = asyncio.run(b.portfolio_holdings())
    bp = res["buying_power"]
    live_stock = 8 * 41.5 * 1500          # 498,000
    deposit = 727873 - 600000             # 127,873
    assert bp.get("overseas_krw") == pytest.approx(live_stock + deposit)  # 625,873 (동결 727,873 아님)
    assert bp["total_eval"] == pytest.approx(7975828 + live_stock + deposit)
    assert bp.get("overseas_krw_stale") is True


def test_valuation_uses_authoritative_when_present_balance_ok():
    now = time.time()
    b = _ValBroker(present={"ok": True, "krw_value": 730000.0, "stock_value": 600000.0, "exrt": 1500.0},
                   us_holdings=[{"code": "FCX", "qty": 8, "cur_price": 41.5, "ccy": "USD"}],
                   cache=(727873.0, now, 600000.0, 1500.0))
    res = asyncio.run(b.portfolio_holdings())
    assert res["buying_power"]["total_eval"] == pytest.approx(7975828 + 730000)


def test_valuation_falls_back_to_cache_without_stock_baseline():
    # 캐시 주식분이 0(레거시 캐시) → 예수금 분리 불가 → 기존처럼 캐시 총액 사용(안전, 과대평가 금지)
    now = time.time()
    b = _ValBroker(present={"ok": False, "krw_value": 0.0, "stock_value": 0.0, "exrt": 0.0},
                   us_holdings=[{"code": "FCX", "qty": 8, "cur_price": 41.5, "ccy": "USD"}],
                   cache=(727873.0, now, 0.0, 1500.0))
    res = asyncio.run(b.portfolio_holdings())
    assert res["buying_power"].get("overseas_krw") == pytest.approx(727873.0)
