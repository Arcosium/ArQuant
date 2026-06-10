"""슬리브 매도 흐름 — 매니저가 매크로+뉴스로 매도 판단, 사후관리실장이 통합 (사장 지시 2026-06-09).

#1: 비중%가 맞아도 신호로 매도; 사후관리실장이 주식+슬리브 매도를 종합 결정.
"""
from main_swarm import _build_sleeve_prompt, _build_sleeve_sell_orders
from infra.asset_sleeves import BOND_SLEEVE, COMMODITY_SLEEVE


def test_sleeve_prompt_includes_macro_and_news_not_quant():
    p = _build_sleeve_prompt(BOND_SLEEVE, macro="MACRO_X", news="NEWS_Z",
                             pool_txt="148070 KOSEF", weight_ctx="현재 채권 10%",
                             thesis_reminder="")
    assert "MACRO_X" in p and "NEWS_Z" in p
    assert "계량분석팀장 평가" not in p  # 주식 퀀트 제외
    assert "밴드" in p and "보류" in p   # %맞아도 신호로 매도 지시
    assert "채권결정" in p


def test_commodity_prompt_keyword():
    p = _build_sleeve_prompt(COMMODITY_SLEEVE, macro="m", news="n",
                             pool_txt="GLD", weight_ctx="현재 원자재 5%")
    assert "원자재결정" in p
    assert "원자재운용실장 ETF 풀" in p


def test_post_manager_sells_sleeve_code():
    # 사후관리실장 매도결정에 채권 코드가 포함되면 슬리브 매도 주문이 생성된다(주식은 무시).
    decisions = {"005930": "보유", "148070": "전량"}
    holdings = [{"code": "148070", "qty": 7, "cur_price": 50000, "name": "KOSEF10Y"}]
    orders = _build_sleeve_sell_orders(decisions, holdings, price_lookup=lambda c: 50000)
    assert any(o["ticker"] == "148070" and o["side"] == "sell" and o["qty"] == 7 for o in orders)
    assert not any(o["ticker"] == "005930" for o in orders)  # 주식은 슬리브 매도 아님


def test_band_ok_but_signal_sell_not_blocked():
    # 비중 적정이어도 사후관리 매도결정(절반)이 있으면 슬리브 매도 주문이 살아난다.
    decisions = {"148070": "절반"}
    holdings = [{"code": "148070", "qty": 8, "cur_price": 50000, "name": "x"}]
    orders = _build_sleeve_sell_orders(decisions, holdings, price_lookup=lambda c: 50000)
    assert any(o["qty"] == 4 for o in orders)


def test_mixed_bond_and_commodity_sells_get_right_reason():
    decisions = {"148070": "전량", "GLD": "전량"}
    holdings = [{"code": "148070", "qty": 3, "cur_price": 50000},
                {"code": "GLD", "qty": 2, "cur_price": 200}]
    orders = _build_sleeve_sell_orders(decisions, holdings, price_lookup=lambda c: 50000 if c == "148070" else 200)
    reasons = {o["ticker"]: o["reason"] for o in orders}
    assert reasons["148070"] == "채권운용실장 자산배분"
    assert reasons["GLD"] == "원자재운용실장 자산배분"
