"""라이브 회귀(사장 지시 2026-06-08): 채권 ETF 매수가 *주식용* 예산비율·수량 게이트에 막혀
집행 0건이던 버그. 채권은 자산배분 자산이라 목표비중(예: 31%)까지 채워야 하므로,
- 1회 수량 한도(MAX_ORDER_QTY) 면제 (989주 같은 큰 수량 정상),
- 사이클 누적 매수예산(MAX_CYCLE_BUDGET_RATIO) 면제,
하되 공통 안전장치는 유지:
- 예수금 부족(MIN_CASH_BUFFER)은 채권도 적용(돈 없으면 못 산다),
- 단일종목 편중(BOND_TARGET_MAX_PCT)은 채권 별도 한도로 적용.
또한 채권 매수 notional 이 *주식* 사이클 예산(spent)을 잠식하지 않아야 한다.
"""
import config
import runtime
import pytest
from agents.guardrails import validate_order_draft, _check_single_order

REASON = "채권운용실장 자산배분"


@pytest.fixture
def max_qty_50(monkeypatch):
    """라이브 시나리오 재현: MAX_ORDER_QTY=50 (전략 params 레이어가 config 를 가리므로 runtime.get 패치)."""
    _orig = runtime.get

    def _patched(key, default=None, uid=None):
        if key == "MAX_ORDER_QTY":
            return 50
        return _orig(key, default, uid=uid)

    monkeypatch.setattr(runtime, "get", _patched)


def _bp(cash=1_000_000_000, total=1_000_000_000, pnl=0.0, ok=True):
    return {"cash": cash, "total_eval": total, "pnl_ratio": pnl, "ok": ok}


def _order(ticker, qty, side="buy", reason=REASON):
    return {"ticker": ticker, "side": side, "qty": qty, "reason": reason}


def test_bond_buy_exempt_from_qty_ceiling(max_qty_50):
    # 라이브: IEF buy x989 가 MAX_ORDER_QTY(50) 초과로 반려됐다 → 채권은 수량 면제로 통과해야.
    # notional 은 편중·예수금 한도 안에 들도록: total 1e10, 989주 × $90 ≈ 1.34e8 → 1.3% < 40%·cash 충분
    r = validate_order_draft({"orders": [_order("IEF", 989)]},
                             buying_power=_bp(cash=10_000_000_000, total=10_000_000_000),
                             price_map={"IEF": 90.0})
    res = r["results"][0]
    assert res["status"] == "APPROVED", \
        f"채권은 수량 한도 면제여야 함. issues={res['issues']}"
    assert not any("1회 한도" in i for i in res["issues"])


def test_stock_buy_still_hits_qty_ceiling(max_qty_50):
    # 일반 주식은 여전히 수량 한도 적용 — 989주 > 50 이면 반려.
    r = validate_order_draft(
        {"orders": [_order("005930", 989, reason="정상 매수 — 추세 양호로 진입")]},
        buying_power=_bp(cash=10_000_000_000, total=10_000_000_000),
        price_map={"005930": 100.0})
    res = r["results"][0]
    assert res["status"] == "REJECTED"
    assert any("1회 한도" in i for i in res["issues"])


def test_bond_buy_exempt_from_cycle_budget():
    # 라이브: IEF buy 가 사이클 누적 매수예산(cash × MAX_CYCLE_BUDGET_RATIO) 초과로 반려됐다.
    # cash=1e8, MAX_CYCLE_BUDGET_RATIO=0.25 → 예산 2,500만. 100주 × 30만 = 3,000만 > 예산.
    # 채권은 면제이므로 통과해야 한다(예수금은 충분: 3,000만 × 1.1 < 1e8).
    r = validate_order_draft({"orders": [_order("153130", 100)]},
                             buying_power=_bp(cash=100_000_000, total=1_000_000_000),
                             price_map={"153130": 300_000})
    res = r["results"][0]
    assert res["status"] == "APPROVED", f"채권은 사이클 예산 면제. issues={res['issues']}"
    assert not any("사이클 누적 매수예산" in i for i in res["issues"])


def test_stock_buy_still_hits_cycle_budget():
    # 일반 주식은 여전히 사이클 누적 매수예산 적용.
    r = validate_order_draft(
        {"orders": [_order("005930", 100, reason="정상 매수 — 추세 양호로 진입")]},
        buying_power=_bp(cash=100_000_000, total=1_000_000_000),
        price_map={"005930": 300_000})
    res = r["results"][0]
    assert res["status"] == "REJECTED"
    assert any("사이클 누적 매수예산" in i for i in res["issues"])


def test_bond_buy_still_rejected_on_insufficient_cash():
    # 예수금 부족은 채권도 유지(이중 안전). cash 1e8, IEF 989주 × $90 ≈ 1.34e8 (>cash) → 반려.
    r = validate_order_draft({"orders": [_order("IEF", 989)]},
                             buying_power=_bp(cash=100_000_000, total=10_000_000_000),
                             price_map={"IEF": 90.0})
    res = r["results"][0]
    assert res["status"] == "REJECTED"
    assert any("예수금 부족" in i for i in res["issues"])


def test_bond_buy_still_rejected_on_concentration():
    # 편중 한도(BOND_TARGET_MAX_PCT 0.40)는 채권에도 적용 — 45% notional 이면 반려.
    r = validate_order_draft({"orders": [_order("153130", 100)]},
                             buying_power=_bp(cash=1_000_000_000, total=10_000_000),
                             price_map={"153130": 45_000})
    res = r["results"][0]
    assert res["status"] == "REJECTED"
    assert any("단일 종목 비중" in i for i in res["issues"])


def test_bond_notional_not_added_to_stock_spent():
    # 채권 매수가 같은 사이클 cycle_state["spent"](주식 예산 누적)를 잠식하면 안 된다.
    cs = {"spent": 0.0}
    _check_single_order(_order("153130", 100), _bp(cash=1_000_000_000, total=1_000_000_000),
                        {"153130": 50_000}, cs)
    assert cs["spent"] == 0.0, "채권 매수는 주식 예산(spent) 누적에서 제외돼야 함"
    # 일반 주식은 spent 에 가산된다(대조군).
    cs2 = {"spent": 0.0}
    _check_single_order(_order("005930", 10, reason="정상 매수 — 추세 양호"),
                        _bp(cash=1_000_000_000, total=1_000_000_000),
                        {"005930": 50_000}, cs2)
    assert cs2["spent"] > 0.0


def test_mixed_batch_bond_does_not_consume_stock_budget():
    # 채권 + 주식 혼합 배치: 채권 매수가 spent 를 안 올리므로 주식 매수예산이 보존된다.
    # cash 1e8 → 주식 예산 2,500만. 채권 300만 1건 + 주식 200만 1건 → 주식 spent 는 200만만.
    r = validate_order_draft(
        {"orders": [_order("153130", 10),  # 채권 300만(면제)
                    _order("005930", 100, reason="정상 매수 — 추세 양호로 진입")]},  # 주식 200만
        buying_power=_bp(cash=100_000_000, total=1_000_000_000),
        price_map={"153130": 300_000, "005930": 20_000})
    # 둘 다 승인, spent 는 주식만(2,000,000원).
    assert all(x["status"] == "APPROVED" for x in r["results"]), r["report"]
