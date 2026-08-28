"""TimefolioBroker 스웜 집행 경로의 대회 룰 게이트 (사장 지시 2026-07-03).

KIS 모의와 동일한 스웜 파이프라인을 타되, 집행 직전 타임폴리오 대회 룰북(check_order)을
하드 게이트로 통과해야 한다: 위반 주문은 사이트 제출 전에 거부, 정상 주문은 제출 후
로컬 장부에도 즉시 반영된다.
"""
import asyncio
import threading
import time

import pytest

import infra.timefolio_broker as tb
from infra.kis_broker import OrderDraft, OrderSide
from Auto_folio.autofolio import contest_store

GOOD_META = {"name": "테스트종목", "market": "KOSPI", "is_common_stock": True,
             "listed_business_days": 3000, "avg_5d_trading_value_krw": 1_000_000_000_000,
             "market_cap_krw": 400_000_000_000_000, "last_price": 70000}


@pytest.fixture
def broker(tmp_path, monkeypatch):
    monkeypatch.setattr(contest_store, "_STORE_PATH", tmp_path / "contest_state.json")
    monkeypatch.setattr(contest_store, "_DATA_DIR", tmp_path)
    contest_store.register(77, initial_cash=100_000_000)
    monkeypatch.setattr(tb, "fetch_security_meta", lambda code, stored=None: dict(GOOD_META))
    calls = []

    def _submit(uid, payload, headless=True):
        calls.append(payload)
        return {"accepted": True, "filled": True}

    monkeypatch.setattr(tb, "submit_order", _submit)
    b = tb.TimefolioBroker({"id": 77})
    b._site_calls = calls
    return b


def test_rule_violating_order_rejected_before_site(broker, monkeypatch):
    # 매수불가 지정(투자경고) 종목 → 사이트 제출 없이 거부. (종목 한도 15% 위반은 2026-07-08
    # order_limits 9% 캡이 수량을 먼저 줄여버려 더는 이 경로로 검증할 수 없다 — 아래 clamp 테스트 참조.)
    bad = {**GOOD_META, "flags": ["투자경고"]}
    monkeypatch.setattr(tb, "fetch_security_meta", lambda code, stored=None: dict(bad))
    draft = OrderDraft(ticker="035420", side=OrderSide.BUY, qty=100, limit_price=200000)
    msg = asyncio.run(broker.place_order(draft))
    assert "대회 룰" in msg and "매수 불가" in msg
    assert broker._site_calls == []


def test_oversized_buy_clamped_to_order_weight_cap(broker):
    # 20% 요청 → 1주문 비중캡(일반 9%)으로 수량 축소 후 제출 (사장 지시 2026-07-08).
    draft = OrderDraft(ticker="035420", side=OrderSide.BUY, qty=100, limit_price=200000)
    res = asyncio.run(broker.place_order_ex(draft))
    assert res["filled"] is True
    assert res["qty"] == int(100_000_000 * 0.09 // 200000)   # 45주 = 9%
    assert len(broker._site_calls) == 1
    assert broker._site_calls[0]["qty"] == res["qty"]


def test_valid_order_submits_and_books_ledger(broker):
    draft = OrderDraft(ticker="005930", side=OrderSide.BUY, qty=100, limit_price=70000)  # 7% 비중
    msg = asyncio.run(broker.place_order(draft))
    assert "체결" in msg and len(broker._site_calls) == 1
    acct = contest_store.get_account(77)
    pos = {p["ticker"]: p for p in acct["portfolio"]["positions"]}
    assert pos["005930"]["qty"] == 100
    assert acct["portfolio"]["cash"] == 100_000_000 - 100 * 70000


def test_site_submissions_are_serialized_per_account(broker, monkeypatch):
    state = {"active": 0, "max_active": 0}
    guard = threading.Lock()

    def _slow_submit(uid, payload, headless=True):
        with guard:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
        time.sleep(0.05)
        with guard:
            state["active"] -= 1
        return {"accepted": True, "filled": False, "pending": True}

    monkeypatch.setattr(tb, "submit_order", _slow_submit)

    async def _run_two():
        return await asyncio.gather(
            broker.place_order_ex(OrderDraft(ticker="005930", side=OrderSide.BUY,
                                             qty=1, limit_price=70000)),
            broker.place_order_ex(OrderDraft(ticker="035420", side=OrderSide.BUY,
                                             qty=1, limit_price=200000)),
        )

    results = asyncio.run(_run_two())
    assert all(r["accepted"] for r in results)
    assert state["max_active"] == 1
