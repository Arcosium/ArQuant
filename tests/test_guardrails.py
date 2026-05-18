"""리스크관리실장 결정론 게이트(`validate_order_draft`) 회귀 테스트.

이 게이트는 LLM 없이 실제 매수 주문을 막는 마지막 결정론 방어선이다.
특히 사장 피드백 2026-05-16 의 **KR(원)/US($) 통화 분리 버그**가 다시
회귀하지 않도록 통화 환산 동작을 명시적으로 고정한다.
"""
import json

import pytest

from agents.guardrails import validate_order_draft

REASON = "정상 매수 — 추세·수급 양호로 진입"


def _bp(cash=1_000_000_000, total=1_000_000_000, pnl=0.0, ok=True):
    return {"cash": cash, "total_eval": total, "pnl_ratio": pnl, "ok": ok}


def _order(ticker="005930", side="buy", qty=1, reason=REASON):
    return {"ticker": ticker, "side": side, "qty": qty, "reason": reason}


# ── 파싱/입력 형태 ────────────────────────────────────────────────────────
def test_unparseable_json_is_rejected():
    r = validate_order_draft("이건 JSON 이 아님 {깨짐")
    assert r["approved"] is False
    assert "파싱 실패" in r["report"]


def test_empty_orders_not_approved():
    r = validate_order_draft({"orders": []})
    assert r["approved"] is False
    assert "검증할 주문 없음" in r["report"]


def test_json_string_wrapper_is_parsed():
    payload = json.dumps({"orders": [_order()]})
    r = validate_order_draft(payload, buying_power=_bp(), price_map={"005930": 70_000})
    assert r["approved"] is True
    assert r["results"][0]["status"] == "APPROVED"


# ── 구조적 검증 ──────────────────────────────────────────────────────────
def test_valid_kr_buy_within_limits_is_approved():
    r = validate_order_draft({"orders": [_order(qty=1)]},
                             buying_power=_bp(), price_map={"005930": 70_000})
    assert r["approved"] is True
    assert r["results"][0]["status"] == "APPROVED"


@pytest.mark.parametrize("qty,frag", [(0, "수량 비정상"), (-3, "수량 비정상"),
                                       (1500, "1회 한도")])
def test_bad_quantity_rejected(qty, frag):
    r = validate_order_draft({"orders": [_order(qty=qty)]},
                             buying_power=_bp(), price_map={"005930": 1_000})
    assert r["results"][0]["status"] == "REJECTED"
    assert frag in "; ".join(r["results"][0]["issues"])


def test_short_reason_rejected():
    r = validate_order_draft({"orders": [_order(reason="x")]},
                             buying_power=_bp(), price_map={"005930": 1_000})
    assert any("사유" in i for i in r["results"][0]["issues"])


def test_missing_price_rejects_buy_conservatively():
    r = validate_order_draft({"orders": [_order()]},
                             buying_power=_bp(), price_map={})  # 현재가 없음
    assert r["results"][0]["status"] == "REJECTED"
    assert any("현재가 조회 실패" in i for i in r["results"][0]["issues"])


def test_balance_unavailable_rejects_buy():
    r = validate_order_draft({"orders": [_order()]},
                             buying_power=_bp(ok=False), price_map={"005930": 1_000})
    assert any("잔고 조회 실패" in i for i in r["results"][0]["issues"])


# ── 보수적 리스크 한도 ───────────────────────────────────────────────────
def test_account_mdd_blocks_all_new_buys():
    r = validate_order_draft({"orders": [_order()]},
                             buying_power=_bp(pnl=-0.06),  # -6% ≤ -5% 한도
                             price_map={"005930": 10_000})
    assert r["results"][0]["status"] == "REJECTED"
    assert any("MDD" in i for i in r["results"][0]["issues"])


def test_single_stock_concentration_rejected():
    # notional 2,000,000원 > total 10,000,000 × 0.15 = 1,500,000
    r = validate_order_draft({"orders": [_order(qty=1)]},
                             buying_power=_bp(cash=50_000_000, total=10_000_000),
                             price_map={"005930": 2_000_000})
    assert r["results"][0]["status"] == "REJECTED"
    assert any("단일 종목 비중" in i for i in r["results"][0]["issues"])


def test_cash_buffer_insufficient_rejected():
    r = validate_order_draft({"orders": [_order()]},
                             buying_power=_bp(cash=1_050_000, total=1_050_000),
                             price_map={"005930": 1_000_000})  # 1.0M × 1.10 > 1.05M
    assert any("예수금 부족" in i for i in r["results"][0]["issues"])


def test_cycle_budget_cap_blocks_second_order():
    # cash 10M, 사이클 예산 = 10M × 0.25 = 2.5M. 1.5M 주문 둘 → 두 번째 초과.
    orders = [_order(ticker="005930"), _order(ticker="000660")]
    r = validate_order_draft({"orders": orders},
                             buying_power=_bp(cash=10_000_000, total=100_000_000),
                             price_map={"005930": 1_500_000, "000660": 1_500_000})
    assert r["results"][0]["status"] == "APPROVED"
    assert r["results"][1]["status"] == "REJECTED"
    assert any("사이클 누적" in i for i in r["results"][1]["issues"])


def test_sell_order_skips_buy_gates():
    # 구조 검증(사유 ≥5자)은 매수·매도 공통이므로 충분한 사유를 준다.
    # 잔고 조회 실패(ok=False)는 *매수* 게이트라 매도는 통과해야 한다.
    r = validate_order_draft({"orders": [_order(side="sell", reason="목표가 도달로 전량 이익 실현")]},
                             buying_power=_bp(ok=False), price_map={})
    assert r["results"][0]["status"] == "APPROVED"
    assert any("매도 주문" in w for w in r["results"][0]["warnings"])


# ── 통화 분리 회귀 (사장 피드백 2026-05-16) ─────────────────────────────
def test_us_notional_converted_to_krw_for_concentration():
    """US 종목 notional 은 ×1500 환산되어 원화 기준 한도와 비교돼야 한다.
    같은 nominal 숫자라도 US 는 환산 후 한도 초과, KR 은 통과 → 통화 인지 증명."""
    us = validate_order_draft({"orders": [_order(ticker="AAPL", qty=10)]},
                              buying_power=_bp(total=15_000_000),
                              price_map={"AAPL": 200.0})  # $2000 → ≈3,000,000원 > 2,250,000
    assert us["results"][0]["status"] == "REJECTED"
    assert any("단일 종목 비중" in i for i in us["results"][0]["issues"])

    kr = validate_order_draft({"orders": [_order(ticker="000660", qty=10)]},
                              buying_power=_bp(total=15_000_000),
                              price_map={"000660": 200.0})  # 2,000원 → 환산 없음
    assert kr["results"][0]["status"] == "APPROVED"


def test_report_uses_correct_currency_symbol():
    r = validate_order_draft({"orders": [_order(ticker="AAPL"), _order(ticker="005930")]},
                             buying_power=_bp(), price_map={"AAPL": 200.0, "005930": 70_000})
    assert "$" in r["report"]   # US 종목 → 달러 표기
    assert "원" in r["report"]  # KR 종목 → 원 표기
