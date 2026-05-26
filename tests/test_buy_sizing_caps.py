"""_affordable_buy_qty — 매수 수량은 리스크관리실장이 검증하는 한도(주문당·단일종목·사이클잔여)를
사이징 단계에서 '선반영'해야 한다.

버그 2026-05-22: _build_orders 가 개장 사이클에서 per_stock_cap 을 60%로 완화했으나
리스크 검증(guardrails._check_single_order)은 완화를 모른 채 25%로 반려 →
삼성전자 6주(비중 58.6%)·대한항공 69주(59.9%)가 매번 반려되어, 종목을 골라놓고 매수 0건.
사이징이 세 한도의 최소값 안에서 수량을 산정하면 반려 없이 통과한다.
"""
from main_swarm import _affordable_buy_qty


def test_clamped_by_single_stock_cap():
    # 단일종목 한도 750,000 / 주가 300,000 → 2주
    qty = _affordable_buy_qty(300000, per_order_budget=1500000, per_stock_cap=750000, cycle_remaining=2000000)
    assert qty == 2


def test_clamped_by_cycle_remaining():
    # 사이클 잔여예산 403,000 / 주가 26,550 → 15주
    qty = _affordable_buy_qty(26550, per_order_budget=1500000, per_stock_cap=750000, cycle_remaining=403000)
    assert qty == 15


def test_clamped_by_per_order():
    qty = _affordable_buy_qty(10000, per_order_budget=305000, per_stock_cap=750000, cycle_remaining=2000000)
    assert qty == 30


def test_zero_when_one_share_exceeds_all_caps():
    # 1주가 모든 한도를 넘으면 0 (1주 허용은 호출부의 별도 폴백이 담당)
    qty = _affordable_buy_qty(800000, per_order_budget=750000, per_stock_cap=750000, cycle_remaining=750000)
    assert qty == 0


def test_zero_budget_returns_zero():
    # 사이클 예산 소진(잔여 0) → 0
    qty = _affordable_buy_qty(10000, per_order_budget=305000, per_stock_cap=750000, cycle_remaining=0)
    assert qty == 0


def test_bad_price_returns_zero():
    assert _affordable_buy_qty(0, per_order_budget=1, per_stock_cap=1, cycle_remaining=1) == 0
