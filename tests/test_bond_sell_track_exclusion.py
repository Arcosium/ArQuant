"""C2 회귀: 채권 ETF 가 주식 매도 트랙에 중복 노출되던 버그.

채권 ETF 가 (a) 사후관리실장(주식 매도) 입력에 노출돼 '매도결정: 153130=전량'으로
청산되거나, (b) _build_orders→_assemble_sell_orders 의 자동 익절/손절/편중축소 룰에
걸려 주식 룰로 강제 청산/이중 매도되던 문제.

ENABLE_BOND_ETF ON 이면 주식 매도 트랙(사후관리실장 입력·_build_orders holdings·
자동 익절손절)에서 채권 ETF 풀 코드를 제외해야 한다. OFF 면 기존 동작 100% 불변.
"""
import asyncio

import pytest

import main_swarm
from infra.asset_sleeves import all_sleeve_pool_codes  # 채권 함수 일반화(2026-06-09)


def _bp(cash=1_000_000_000, total=1_000_000_000, pnl=0.0, ok=True):
    return {"cash": cash, "total_eval": total, "pnl_ratio": pnl, "ok": ok}


def test_all_sleeve_pool_codes_covers_kr_and_us():
    codes = all_sleeve_pool_codes()
    assert "153130" in codes and "114260" in codes and "148070" in codes  # 채권 KR
    assert "TLT" in codes and "SHY" in codes and "IEF" in codes           # 채권 US


def _mk_orchestrator():
    sm = main_swarm.ArquantOrchestrator.__new__(main_swarm.ArquantOrchestrator)
    sm.uid = "test"

    class _Log:
        def log(self, *a, **k):
            pass
    sm.cycle_log = _Log()

    async def _noop_emit(*a, **k):
        return None
    sm._emit = _noop_emit
    return sm


def _mk_cyc(holdings, stock_holdings=None):
    class _Cyc:
        pass
    cyc = _Cyc()
    cyc.market_open = True
    cyc.target_codes = []
    cyc.candidate_codes = []
    cyc.quant_report = ""
    cyc.news_report = ""
    cyc.holdings = holdings
    if stock_holdings is not None:
        cyc.stock_holdings = stock_holdings
    cyc.sell_directives = {"153130": "전량", "005930": "전량"}
    cyc._entry_dirs = {}
    cyc._sell_prices = {}
    cyc.sleeve_buy_orders = []
    cyc.sleeve_price_map = {}
    cyc.sleeve_sell_proposals = {}
    return cyc


def test_build_orders_sell_track_excludes_bonds_when_enabled(monkeypatch):
    """ENABLE_BOND_ETF ON: _build_orders 의 매도 트랙에 채권(153130)이 들어가면 안 된다.
    cyc.stock_holdings(주식만) 을 _build_orders 에 넘겨야 한다."""
    holdings = [
        {"code": "153130", "name": "KODEX 단기채권", "qty": 100, "cur_price": 100_000, "pnl_pct": 12.0},
        {"code": "005930", "name": "삼성전자", "qty": 10, "cur_price": 70_000, "pnl_pct": 12.0},
    ]
    stock_holdings = [h for h in holdings if h["code"] == "005930"]
    cyc = _mk_cyc(holdings, stock_holdings=stock_holdings)
    sm = _mk_orchestrator()

    seen = {}

    async def _fake_build_orders(target_codes, candidate_codes, quant_report, news_report,
                                 holdings_arg, **k):
        seen["holdings"] = holdings_arg
        return ({"orders": [], "sizing_notes": []}, {}, _bp())
    sm._build_orders = _fake_build_orders

    asyncio.run(sm._cyc_stage_build_orders(cyc))

    passed_codes = {h["code"] for h in seen["holdings"]}
    assert "153130" not in passed_codes, "채권이 주식 매도 트랙(_build_orders)에 노출되면 안 됨"
    assert "005930" in passed_codes


def test_build_orders_sell_track_unchanged_when_disabled():
    """ENABLE_BOND_ETF OFF(stock_holdings 미설정): 기존대로 cyc.holdings 전체를 넘긴다."""
    holdings = [
        {"code": "153130", "name": "KODEX 단기채권", "qty": 100, "cur_price": 100_000, "pnl_pct": 12.0},
        {"code": "005930", "name": "삼성전자", "qty": 10, "cur_price": 70_000, "pnl_pct": 12.0},
    ]
    cyc = _mk_cyc(holdings)  # stock_holdings 미설정 → OFF 동작
    sm = _mk_orchestrator()

    seen = {}

    async def _fake_build_orders(target_codes, candidate_codes, quant_report, news_report,
                                 holdings_arg, **k):
        seen["holdings"] = holdings_arg
        return ({"orders": [], "sizing_notes": []}, {}, _bp())
    sm._build_orders = _fake_build_orders

    asyncio.run(sm._cyc_stage_build_orders(cyc))

    passed_codes = {h["code"] for h in seen["holdings"]}
    # OFF: 채권도 그대로 전체 보유가 넘어간다(기존 동작 불변)
    assert passed_codes == {"153130", "005930"}


def test_assemble_sell_orders_would_liquidate_bond_without_filter():
    """전제 고정: 필터 없이 채권을 _assemble_sell_orders 에 넣으면 자동 익절로 청산된다(=버그 증상)."""
    from main_swarm import _assemble_sell_orders
    holdings = [{"code": "153130", "name": "KODEX 단기채권", "qty": 100,
                 "cur_price": 100_000, "pnl_pct": 12.0}]
    orders, _ = _assemble_sell_orders(
        holdings, {}, enable_rebalance=True, take_profit_pct=10.0, stop_loss_pct=5.0,
        trim_over_ratio=True, conservative_ratio=0.15, per_stock_cap=0, total=100_000_000)
    assert any(o["ticker"] == "153130" and o["side"] == "sell" for o in orders), \
        "필터 없이는 채권이 자동 익절로 청산됨(이래서 매도 트랙에서 제외해야 함)"
