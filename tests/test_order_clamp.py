"""KIS 잔고 신뢰성 — 주문 사이징 clamp(Group E, Task 14-16).
매수/매도 직전 KIS 권위 '주문가능' 조회(kr_psbl_order·us_buying_power·kr_psbl_sell_qty)로
수량을 clamp 한다. 조회 실패/글리치 시 현행 수량 유지(주문 절대 드롭 금지)."""
import asyncio

from main_swarm import ArquantOrchestrator


class _FakeClampBroker:
    def __init__(self, kr_buy=None, us=None, sell=None):
        self._kr_buy = kr_buy or {}   # {code: nrcvb_buy_qty}
        self._us = us or {}           # {ticker: max_ord_psbl_qty}
        self._sell = sell or {}       # {code: ord_psbl_qty}  (없으면 조회 None)
        self.calls = []

    async def kr_psbl_order(self, code, unpr):
        self.calls.append(("kr_buy", code))
        if code in self._kr_buy:
            return {"ok": True, "buy_qty": self._kr_buy[code], "cash": 0.0}
        return {"ok": False, "buy_qty": None, "cash": 0.0}

    async def us_buying_power(self, ticker, unpr, excg=None):
        self.calls.append(("us", ticker))
        if ticker in self._us:
            return {"ok": True, "qty": self._us[ticker], "usd": 0.0, "exrt": 1500.0}
        return {"ok": False, "qty": 0, "usd": 0.0, "exrt": 0.0}

    async def kr_psbl_sell_qty(self, code):
        self.calls.append(("sell", code))
        return self._sell.get(code)


def _orch(broker):
    o = object.__new__(ArquantOrchestrator)
    o.broker = broker
    return o


def test_kr_buy_clamped_to_psbl_qty():
    b = _FakeClampBroker(kr_buy={"005930": 16})
    o = _orch(b)
    orders = [{"ticker": "005930", "side": "buy", "qty": 20, "market": "KR"}]
    out = asyncio.run(o._clamp_orders_to_psbl(orders, {"005930": 55000}))
    assert out[0]["qty"] == 16 and out[0].get("psbl_clamped") is True


def test_kr_buy_unchanged_when_query_fails():
    b = _FakeClampBroker(kr_buy={})   # ok=False
    o = _orch(b)
    orders = [{"ticker": "005930", "side": "buy", "qty": 20, "market": "KR"}]
    out = asyncio.run(o._clamp_orders_to_psbl(orders, {"005930": 55000}))
    assert out[0]["qty"] == 20, "조회 실패 시 현행 수량 유지(주문 드롭 금지)"


def test_us_buy_clamped_to_usd_qty():
    b = _FakeClampBroker(us={"AAPL": 2})
    o = _orch(b)
    orders = [{"ticker": "AAPL", "side": "buy", "qty": 10, "market": "US"}]
    out = asyncio.run(o._clamp_orders_to_psbl(orders, {"AAPL": 200.0}))
    assert out[0]["qty"] == 2 and out[0].get("psbl_clamped") is True


def test_buy_dropped_when_psbl_zero():
    b = _FakeClampBroker(us={"ZZZ": 0})
    o = _orch(b)
    orders = [{"ticker": "ZZZ", "side": "buy", "qty": 5, "market": "US"}]
    out = asyncio.run(o._clamp_orders_to_psbl(orders, {"ZZZ": 10.0}))
    assert out == [], "매수가능 0(살 수 없음) → 0주 주문 금지로 제거"


def test_kr_sell_clamped_to_psbl_qty():
    b = _FakeClampBroker(sell={"005930": 3})
    o = _orch(b)
    orders = [{"ticker": "005930", "side": "sell", "qty": 5, "market": "KR"}]
    out = asyncio.run(o._clamp_orders_to_psbl(orders, {}))
    assert out[0]["qty"] == 3 and out[0].get("psbl_clamped") is True


def test_kr_sell_glitch_keeps_qty_no_drop():
    # 매도가능 0(글리치 의심: 보유 있는데 0) → 재조회 후에도 0이면 현행 유지(매도 드롭 금지)
    b = _FakeClampBroker(sell={"005930": 0})
    o = _orch(b)
    orders = [{"ticker": "005930", "side": "sell", "qty": 5, "market": "KR"}]
    out = asyncio.run(o._clamp_orders_to_psbl(orders, {}))
    assert out[0]["qty"] == 5, "매도가능 0 글리치 시 전량 유지(주문 절대 드롭 금지)"


def test_us_sell_not_clamped_by_kr_tr():
    # US 매도는 국내 매도가능(TTTC8408R) 대상이 아니다 — 현행 유지
    b = _FakeClampBroker()
    o = _orch(b)
    orders = [{"ticker": "TSLA", "side": "sell", "qty": 4, "market": "US"}]
    out = asyncio.run(o._clamp_orders_to_psbl(orders, {}))
    assert out[0]["qty"] == 4
    assert ("sell", "TSLA") not in b.calls
