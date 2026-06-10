"""C1 회귀: 슬리브(채권·원자재) 매수가 리스크 검증에서 price_map 미주입으로 100% 반려되던 버그.

`_cyc_stage_sleeves` 가 만든 가격조회 dict(`cyc.sleeve_price_map`)가 `_cyc_stage_build_orders`
의 `price_map` 에 합쳐져야 한다. 안 그러면 `validate_order_draft` 가 price<=0 으로 슬리브 매수를
무조건 반려한다(주문 dict 의 price 필드는 안 본다).

US 슬리브 가격은 기존 주식 price_map 의 US 가격 단위(USD, 원시 us_last_price)와 동일 —
guardrails 가 내부에서 USD→KRW 환산하므로.
"""
from agents.guardrails import validate_order_draft


def _bp(cash=1_000_000_000, total=1_000_000_000, pnl=0.0, ok=True):
    return {"cash": cash, "total_eval": total, "pnl_ratio": pnl, "ok": ok}


def test_sleeve_buy_rejected_without_price_in_map():
    """전제 고정: price_map 에 슬리브 가격이 없으면 슬리브 매수는 반려된다(=버그 증상)."""
    order = {"ticker": "153130", "side": "buy", "qty": 100,
             "reason": "채권운용실장 자산배분", "entry_mode": "market"}
    r = validate_order_draft({"orders": [order]}, buying_power=_bp(), price_map={})
    assert r["results"][0]["status"] == "REJECTED"
    assert any("현재가 조회 실패" in i for i in r["results"][0]["issues"])


def test_sleeve_buy_approved_when_price_map_has_price():
    """수정 후: sleeve_price_map 이 price_map 에 합쳐지면 슬리브 매수가 검증 통과한다."""
    order = {"ticker": "153130", "side": "buy", "qty": 100,
             "reason": "채권운용실장 자산배분", "entry_mode": "market"}
    price_map = {"153130": 100_000.0}
    r = validate_order_draft({"orders": [order]}, buying_power=_bp(), price_map=price_map)
    assert r["results"][0]["status"] == "APPROVED"


def test_commodity_buy_approved_with_price():
    """원자재 ETF(GLD) 매수도 슬리브 면제로 검증 통과(가격 주입 시)."""
    order = {"ticker": "GLD", "side": "buy", "qty": 10,
             "reason": "원자재운용실장 자산배분", "entry_mode": "market"}
    r = validate_order_draft({"orders": [order]}, buying_power=_bp(), price_map={"GLD": 200.0})
    assert r["results"][0]["status"] == "APPROVED"


def test_build_orders_merges_sleeve_price_map_before_assigning():
    """`_cyc_stage_build_orders` 가 cyc.sleeve_price_map 을 price_map 에 합쳐 cyc.price_map 에
    실어야 한다(US 가격은 USD 단위 그대로). _build_orders 는 stub 으로 대체."""
    import asyncio
    import main_swarm

    class _Cyc:
        pass

    cyc = _Cyc()
    cyc.market_open = True
    cyc.target_codes = []
    cyc.candidate_codes = []
    cyc.quant_report = ""
    cyc.news_report = ""
    cyc.holdings = []
    cyc.sell_directives = {}
    cyc._entry_dirs = {}
    cyc._sell_prices = {}
    # 슬리브 스테이지가 만든 매수 주문 + 가격맵 (KR 원화, US USD)
    cyc.sleeve_buy_orders = [
        {"ticker": "153130", "side": "buy", "qty": 100, "price": 100_000.0,
         "reason": "채권운용실장 자산배분", "entry_mode": "market"},
        {"ticker": "GLD", "side": "buy", "qty": 5, "price": 200.0,
         "reason": "원자재운용실장 자산배분", "entry_mode": "market"},
    ]
    cyc.sleeve_price_map = {"153130": 100_000.0, "GLD": 200.0}  # US=USD 그대로

    sm = main_swarm.ArquantOrchestrator.__new__(main_swarm.ArquantOrchestrator)
    sm.uid = "test"

    async def _fake_build_orders(*a, **k):
        return ({"orders": [], "sizing_notes": []}, {"AAPL": 200.0}, _bp())

    async def _noop_emit(*a, **k):
        return None

    sm._build_orders = _fake_build_orders
    sm._emit = _noop_emit

    class _Log:
        def log(self, *a, **k):
            pass
    sm.cycle_log = _Log()

    asyncio.run(sm._cyc_stage_build_orders(cyc))

    # 슬리브 가격이 cyc.price_map 에 합쳐졌어야 한다(US 는 USD 그대로 — 주식 price_map 과 동일 단위)
    assert cyc.price_map.get("153130") == 100_000.0
    assert cyc.price_map.get("GLD") == 200.0
    assert cyc.price_map.get("AAPL") == 200.0  # 주식 트랙 가격도 보존
    # 슬리브 매수 주문이 order_obj 에 합류
    tickers = {o["ticker"] for o in cyc.order_obj["orders"]}
    assert {"153130", "GLD"} <= tickers
