"""계량분석팀장 '매도가' 지시 → 프롭트레이딩팀장이 그 지정가로 매도 주문.

사장 지시 2026-05-22: 보유 종목은 매수 분석과 구분해 매도 분석을 하고 '매도가'를 제시하며,
프롭트레이딩팀장은 그 매도가로 지정가(limit) 매도 주문을 낸다. 매도가가 '시장가'/미지정이면
종전대로 시장가(즉시 청산). 안전망 자동 익절/손절(사후관리 미언급)은 시장가 유지.
"""
from main_swarm import _parse_sell_price, _assemble_sell_orders

_H = [{"code": "005930", "qty": 10, "name": "삼성전자", "cur_price": 300000, "pnl_pct": 5.0}]


def test_parse_limit_number():
    sp = _parse_sell_price("매도가: 005930=305000", "005930")
    assert sp["mode"] == "limit" and sp["limit_price"] == 305000


def test_parse_market():
    assert _parse_sell_price("매도가: MSFT=시장가", "MSFT")["mode"] == "market"


def test_parse_missing():
    assert _parse_sell_price("퀀트점수: 005930=7", "005930")["mode"] == "market"


def test_sell_uses_limit_when_price_given():
    orders, _ = _assemble_sell_orders(
        _H, {"005930": "전량"}, enable_rebalance=False, take_profit_pct=10, stop_loss_pct=10,
        trim_over_ratio=False, conservative_ratio=0.25, per_stock_cap=0, total=3000000,
        sell_prices={"005930": {"mode": "limit", "limit_price": 305000}})
    assert len(orders) == 1
    o = orders[0]
    assert o["side"] == "sell" and o["qty"] == 10
    assert o.get("price_type") == "limit"
    assert o.get("entry_mode") == "limit" and o.get("entry_limit") == 305000


def test_sell_market_when_no_price():
    orders, _ = _assemble_sell_orders(
        _H, {"005930": "전량"}, enable_rebalance=False, take_profit_pct=10, stop_loss_pct=10,
        trim_over_ratio=False, conservative_ratio=0.25, per_stock_cap=0, total=3000000)
    assert orders[0].get("price_type") == "market"
    assert not orders[0].get("entry_mode")
