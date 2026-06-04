"""사장 지시 2026-06-04: 단일 저가주에 사이클 예산 전액이 몰리면 budget/단가 = 큰 수량이
산출되는데(예: KX하이텍 052900 @1,873원 → 10,227주), 가드레일의 1회 주문 한도
(MAX_ORDER_QTY 미설정 시 config.HARD_MAX_ORDER_QTY=1000주)에 걸려 매수가 '전량 반려'되어
매수 자체가 증발하던 버그(실거래 인접 모의 uid=2 사이클 #140)를 막는다.

핵심 불변식:
  - 매수 수량이 1회 한도를 넘으면 '반려'가 아니라 한도까지 'clamp' 한다 — 주문 절대 드롭 금지(사장 원칙).
  - 한도 = MAX_ORDER_QTY(런타임, >0일 때) 아니면 config.HARD_MAX_ORDER_QTY(=1000).
  - 매도는 위험회피(전량청산)이고 보유수량에 의해 이미 한정되므로 이 상한을 적용하지 않는다.
"""
import config
from main_swarm import ArquantOrchestrator
from agents.guardrails import validate_order_draft


def _orch(uid=None):
    o = object.__new__(ArquantOrchestrator)
    o.uid = uid
    return o


def _bp():
    return {"cash": 1_000_000_000, "total_eval": 1_000_000_000, "pnl_ratio": 0.0, "ok": True}


def test_low_price_buy_clamped_not_dropped():
    o = _orch()
    orders = [{"ticker": "052900", "side": "buy", "qty": 10227, "market": "KR",
               "reason": "운용전략실장 지정 · 10227주 · 진입가:시장가"}]
    out = o._clamp_orders_to_max_qty(orders)
    assert len(out) == 1, "매수가 살아 있어야 한다 — 반려/드롭 금지"
    assert out[0]["qty"] == config.HARD_MAX_ORDER_QTY == 1000
    assert out[0].get("maxqty_clamped") is True


def test_buy_within_ceiling_unchanged():
    o = _orch()
    orders = [{"ticker": "036800", "side": "buy", "qty": 10, "market": "KR", "reason": "정상 매수"}]
    out = o._clamp_orders_to_max_qty(orders)
    assert out[0]["qty"] == 10
    assert out[0].get("maxqty_clamped") is not True


def test_sell_not_capped_by_ceiling():
    # 대량 보유 전량청산은 상한을 넘어도 통과해야 한다(위험회피 우선·보유수량으로 이미 한정).
    o = _orch()
    orders = [{"ticker": "005930", "side": "sell", "qty": 5000, "market": "KR", "reason": "전량 매도"}]
    out = o._clamp_orders_to_max_qty(orders)
    assert out[0]["qty"] == 5000
    assert out[0].get("maxqty_clamped") is not True


def test_clamped_buy_passes_guardrail():
    # clamp(10227→1000) 후엔 리스크관리실장 1회 한도 검증을 통과해야 한다(예전엔 전량 반려됨).
    o = _orch()
    orders = o._clamp_orders_to_max_qty(
        [{"ticker": "052900", "side": "buy", "qty": 10227, "market": "KR",
          "reason": "운용전략실장 지정 정상 매수"}])
    r = validate_order_draft({"orders": orders}, buying_power=_bp(), price_map={"052900": 1873})
    assert r["results"][0]["status"] == "APPROVED"


def test_guardrail_exempts_large_sell():
    # 매도는 1회 한도 반려 대상이 아니다(위험회피·보유수량 한정).
    r = validate_order_draft(
        {"orders": [{"ticker": "005930", "side": "sell", "qty": 5000, "reason": "전량 매도 위험회피"}]},
        buying_power=_bp(), price_map={"005930": 70000})
    assert not any("1회 한도" in i for i in r["results"][0]["issues"]), "매도는 한도 반려 제외"
