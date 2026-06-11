"""대체 후보(ENABLE_CHEAP_FALLBACK) 발동 조건 — 사장 지시 2026-06-03.

버그(2026-06-03 cycle 118, 실거래 hh09080): 사후관리실장이 NU 매도 후 트레이더(주식운용실장)가
'최종종목 없음'으로 매수 보류했는데도, 대체 후보 폴백이 트레이더가 퀀트 4.2점·약세로 배제한
SCHW 를 후보군 최저가로 무단 매수 → 매도 직후 더 나쁜 종목 재매수(처닝).

근본 원인: 폴백 발동 조건이 `not affordable_buy_found` 뿐이라 '지정했으나 예산초과로 못 산' 경우와
'트레이더가 의도적으로 안 산(target_codes 비어있음)' 경우를 구분하지 못했다.
수정: target_set 가 비어 있으면(의도적 매수 보류) 폴백을 발동하지 않는다.
"""
import asyncio

import pytest

import main_swarm
from main_swarm import ArquantOrchestrator


class _StubRuntime:
    def __init__(self, params):
        self.params = params

    def get(self, key, uid=None, default=None):
        return self.params.get(key, default)


class _StubBroker:
    def __init__(self, prices):
        self.prices = prices  # {code: price}

    async def kr_account_snapshot(self):
        return {"buying_power": {"cash": 10_000_000.0, "total_eval": 10_000_000.0}, "holdings": []}

    async def kr_last_price(self, code):
        return float(self.prices.get(str(code), 0.0))

    async def us_last_price(self, tk):
        return float(self.prices.get(str(tk).upper(), 0.0))

    async def kr_psbl_order(self, code, price):
        return {"ok": False}  # 권위 조회 미가용 → clamp 안 함(현행 유지)


_PARAMS = {
    "PER_ORDER_BUDGET_RATIO": 0.2, "CONSERVATIVE_STOCK_RATIO": 0.25,
    "PER_ORDER_BUDGET_OVERSHOOT": 1.3, "MAX_ORDER_QTY": 0, "MAX_TRADES_PER_CYCLE": 2,
    "ENABLE_SELL_REBALANCE": True, "TAKE_PROFIT_PCT": 12.0, "STOP_LOSS_PCT": 5.0,
    "TRIM_OVER_RATIO": True, "ENABLE_CHEAP_FALLBACK": True, "ALLOW_US_STOCKS": True,
    "ALLOW_DERIVATIVES": False, "MAX_CYCLE_BUDGET_RATIO": 0.1,
}


def _orch(broker):
    o = object.__new__(ArquantOrchestrator)
    o.broker = broker
    o.uid = 1
    o.equity_path = "data/1/equity_curve.json"
    return o


@pytest.fixture(autouse=True)
def _patch(monkeypatch):
    monkeypatch.setattr(main_swarm, "runtime", _StubRuntime(_PARAMS))
    monkeypatch.setattr(main_swarm, "get_current_session", lambda: "KR_TRADING")
    monkeypatch.setattr(main_swarm, "is_market_session_now", lambda *a, **k: False)  # equity 기록 경로 우회


def test_no_buy_when_trader_picks_nothing():
    # target 비어있음(의도적 매수 보류) + 저가 후보 존재 → 폴백이 발동하면 안 된다.
    b = _StubBroker({"000660": 200_000.0})
    obj, _px, _bp = asyncio.run(_orch(b)._build_orders(
        target_codes=[], candidate_codes=["000660"], quant_report="", news_report="",
        holdings=[], sell_directives={}))
    buys = [o for o in obj["orders"] if o.get("side") == "buy"]
    assert buys == [], "트레이더가 최종종목 없음으로 보류했으면 후보 최저가를 무단 매수하면 안 된다"
    assert any("의도적 매수 보류" in n for n in obj["sizing_notes"])


def test_fallback_still_fires_when_designated_target_unaffordable():
    # 트레이더가 지정은 했으나(005930) 1주조차 예수금 초과로 못 삼 → 후보군 최저가(000660) 대체는 유지.
    # (실거래에선 ENABLE_CHEAP_FALLBACK override=false 로 꺼두지만, 코드 로직 자체는 보존됨)
    b = _StubBroker({"005930": 15_000_000.0, "000660": 200_000.0})
    obj, _px, _bp = asyncio.run(_orch(b)._build_orders(
        target_codes=["005930"], candidate_codes=["000660"], quant_report="", news_report="",
        holdings=[], sell_directives={}))
    buys = [o for o in obj["orders"] if o.get("side") == "buy"]
    assert len(buys) == 1 and buys[0]["ticker"] == "000660", "지정종목 예산초과 시 대체 후보 폴백은 유지돼야 한다"
    assert any("대체 후보" in n for n in obj["sizing_notes"])


def test_two_targets_both_bought_with_split_budget():
    # 사장 지시 2026-06-03: 두 종목 이상 결정되면 하나만 사지 말고 예산을 나눠 둘 다 매수해야 한다.
    # cash/total=1,000만, MAX_CYCLE_BUDGET_RATIO=0.1 → 사이클 예산 100만, 2종목 → 종목당 50만씩 분배.
    b = _StubBroker({"005930": 70_000.0, "000660": 100_000.0})
    obj, _px, _bp = asyncio.run(_orch(b)._build_orders(
        target_codes=["005930", "000660"], candidate_codes=["005930", "000660"],
        quant_report="", news_report="", holdings=[], sell_directives={}))
    buys = {o["ticker"]: o for o in obj["orders"] if o.get("side") == "buy"}
    assert set(buys) == {"005930", "000660"}, "두 종목이 모두 매수돼야 한다(하나만 X)"
    # 각 종목은 분배된 예산(≈50만) 안에서 사이징 — 한 종목이 예산을 독식하지 않는다.
    assert buys["005930"]["qty"] * 70_000 <= 500_000 + 1  # 50만/7만 ≈ 7주
    assert buys["000660"]["qty"] * 100_000 <= 500_000 + 1  # 50만/10만 = 5주
    assert buys["005930"]["qty"] >= 1 and buys["000660"]["qty"] >= 1
