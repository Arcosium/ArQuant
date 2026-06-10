"""I1 회귀: 채권 ETF 가 단일종목 편중 게이트(CONSERVATIVE_STOCK_RATIO)에 반려되던 버그.

채권은 BOND_TARGET_MAX_PCT(0.40)까지 배분 가능해야 자산배분이 작동한다. 그런데
validate_order_draft 가 채권 매수에 CONSERVATIVE_STOCK_RATIO(보통 0.15) 단일종목 한도를
적용해, 15%를 넘는 채권 ETF 매수가 반려됐다. 채권은 안전자산이라 별도 한도(BOND_TARGET_MAX_PCT)
로 분기해야 한다(사장 지시 2026-06-08).
"""
import config
from agents.guardrails import validate_order_draft

REASON = "채권운용실장 자산배분"


def _bp(cash=1_000_000_000, total=1_000_000_000, pnl=0.0, ok=True):
    return {"cash": cash, "total_eval": total, "pnl_ratio": pnl, "ok": ok}


def _order(ticker, qty, side="buy", reason=REASON):
    return {"ticker": ticker, "side": side, "qty": qty, "reason": reason}


def test_kr_bond_buy_above_stock_ratio_but_below_bond_max_is_approved():
    # notional 25%: CONSERVATIVE_STOCK_RATIO(0.15) 초과지만 BOND_TARGET_MAX_PCT(0.40) 이내
    # total 10,000,000 × 0.25 = 2,500,000 → 단가 25,000 × 100주
    r = validate_order_draft({"orders": [_order("153130", 100)]},
                             buying_power=_bp(cash=1_000_000_000, total=10_000_000),
                             price_map={"153130": 25_000})
    assert r["results"][0]["status"] == "APPROVED", \
        "채권은 BOND_TARGET_MAX_PCT(0.40) 한도라 25%는 통과해야 함"


def test_kr_bond_buy_above_bond_max_is_rejected():
    # notional 45% > BOND_TARGET_MAX_PCT(0.40) → 반려
    r = validate_order_draft({"orders": [_order("153130", 100)]},
                             buying_power=_bp(cash=1_000_000_000, total=10_000_000),
                             price_map={"153130": 45_000})
    assert r["results"][0]["status"] == "REJECTED"
    assert any("단일 종목 비중" in i for i in r["results"][0]["issues"])


def test_us_bond_buy_uses_bond_max_after_krw_conversion():
    # TLT 25% (USD→KRW 환산 후) → BOND_TARGET_MAX_PCT 이내 통과
    # total 15,000,000 × 0.40 = 6,000,000. $90 × 25주 = $2,250 ≈ 3.4M (환율 1500 가정) < 6M
    r = validate_order_draft({"orders": [_order("TLT", 25)]},
                             buying_power=_bp(cash=1_000_000_000, total=15_000_000),
                             price_map={"TLT": 90.0})
    assert r["results"][0]["status"] == "APPROVED"


def test_non_bond_stock_still_uses_conservative_stock_ratio():
    # 일반 주식(삼성전자)은 여전히 CONSERVATIVE_STOCK_RATIO(0.15) 적용 — 25%면 반려
    r = validate_order_draft({"orders": [_order("005930", 100, reason="정상 매수 — 추세 양호로 진입")]},
                             buying_power=_bp(cash=1_000_000_000, total=10_000_000),
                             price_map={"005930": 25_000})
    assert r["results"][0]["status"] == "REJECTED"
    assert any("단일 종목 비중" in i for i in r["results"][0]["issues"])


def test_bond_pool_codes_recognized():
    # 풀에 정의된 코드만 채권으로 인지(예: 임의의 6자리 코드는 일반 주식 한도)
    assert "153130" in {c for c, *_ in config.BOND_ETF_POOL_KR}
    assert "TLT" in {c for c, *_ in config.BOND_ETF_POOL_US}
