"""timefolio_swarm — 타임폴리오 대회 전용 사이클의 결정론 부품 테스트."""
import sqlite3

import pytest

import timefolio_swarm as tfs


# ─── 전략가 출력 파싱 ────────────────────────────────────────────────────────

def test_parse_sell_and_buy_lines():
    text = ("보유 종목을 검토한 결과...\n"
            "매도결정: 005930=전량, 000660=보유, 035720=절반, 051910=30주\n"
            "매수결정: 035420=9, 068270=5.5%\n")
    sells = tfs.parse_sell_line(text)
    assert sells == {"005930": "전량", "000660": "보유", "035720": "절반", "051910": "30주"}
    buys = tfs.parse_buy_line(text)
    assert buys == {"035420": 9.0, "068270": 5.5}


def test_parse_lines_none_and_missing():
    assert tfs.parse_buy_line("매수결정: 없음") == {}
    assert tfs.parse_sell_line("매도결정: 없음") == {}
    assert tfs.parse_buy_line("아무 결정도 없음") == {}


def test_parse_uses_last_match():
    # reasoning 모델이 중간에 예시를 되뇔 수 있음 — 마지막 줄이 최종 결정
    text = "예시: 매수결정: 111111=9\n...최종:\n매수결정: 035420=7"
    assert tfs.parse_buy_line(text) == {"035420": 7.0}


def test_cross_side_sell_and_rebuy_is_normalized_to_hold():
    sells = {"003230": "절반", "196170": "전량", "008930": "보유"}
    buys = {"003230": 2.0, "008930": 3.0, "051600": 5.0}
    overlap = tfs.normalize_cross_side_decisions(sells, buys)
    assert overlap == ["003230", "008930"]
    assert sells == {"003230": "보유", "196170": "전량", "008930": "보유"}
    assert buys == {"051600": 5.0}


def test_committee_buy_gate_matches_kis_policy():
    assert tfs.committee_buy_allowed("매수") is True
    assert tfs.committee_buy_allowed("보류") is False
    assert tfs.committee_buy_allowed("회피") is False


def test_sell_qty_from_directive():
    assert tfs.sell_qty_from_directive("전량", 10) == 10
    assert tfs.sell_qty_from_directive("절반", 10) == 5
    assert tfs.sell_qty_from_directive("보유", 10) == 0
    assert tfs.sell_qty_from_directive("30주", 10) == 10   # 보유 초과 → 캡
    assert tfs.sell_qty_from_directive("3주", 10) == 3
    assert tfs.sell_qty_from_directive("", 10) == 0


# ─── 대회 적격 스크리닝 ──────────────────────────────────────────────────────

def _meta(**kw):
    base = {"ticker": "005930", "name": "삼성전자", "market": "KOSPI", "is_common_stock": True,
            "listed_business_days": 3000, "avg_5d_trading_value_krw": 5_000_000_000_000,
            "market_cap_krw": 400_000_000_000_000, "flags": [], "last_price": 80000}
    base.update(kw)
    return base


def test_eligibility_pass_and_fail():
    ok, reasons = tfs.eligibility(_meta())
    assert ok and not reasons
    ok, reasons = tfs.eligibility(_meta(market_cap_krw=50_000_000_000))
    assert not ok and any("시총" in r for r in reasons)
    ok, reasons = tfs.eligibility(_meta(avg_5d_trading_value_krw=1_000_000_000))
    assert not ok and any("거래대금" in r for r in reasons)
    ok, reasons = tfs.eligibility(_meta(flags=["투자경고"]))
    assert not ok and any("매수불가" in r for r in reasons)
    ok, reasons = tfs.eligibility(_meta(market="ETF"))
    assert not ok


def test_eligibility_rejects_etf_like_missing_data():
    # 채권/원자재 ETF 류 — 시총·보통주 데이터 미확인 → 부적격 (uid7 사건의 주문 거부를 스크리닝 단계로 앞당김)
    ok, reasons = tfs.eligibility({"ticker": "114260", "name": "KODEX 국고채3년",
                                   "market": "KOSPI", "avg_5d_trading_value_krw": 2_000_000_000})
    assert not ok


# ─── 주문 조립 ───────────────────────────────────────────────────────────────

def _assemble(**over):
    kw = dict(
        sells={}, buys={}, holdings=[], prices={}, total_eval=1_000_000_000, cash=1_000_000_000,
        mcap_map={}, quant_scores={}, min_score=6, max_buys=3,
        smallcap_budget_pct=27.0, cash_floor_pct=2.0)
    kw.update(over)
    return tfs.assemble_orders(**kw)


def test_assemble_buy_weight_clamped_to_order_cap():
    # 일반 종목 요청 12% → 9% 캡 (order_limits)
    sells, buys, notes = _assemble(buys={"068270": 12.0}, prices={"068270": 100_000},
                                   quant_scores={"068270": 8})
    assert len(buys) == 1
    assert buys[0]["qty"] == int(1_000_000_000 * 0.09 // 100_000)


def test_assemble_min_score_gate_and_unknown_score_passes():
    sells, buys, notes = _assemble(buys={"068270": 5.0, "035420": 5.0},
                                   prices={"068270": 100_000, "035420": 100_000},
                                   quant_scores={"068270": 3})   # 미달 / 035420 은 점수 없음(보존)
    codes = [b["code"] for b in buys]
    assert "068270" not in codes and "035420" in codes
    assert any("퀀트점수" in n for n in notes)


def test_assemble_smallcap_budget_enforced():
    # 시총 5000억(소형주) 종목을 9%씩 4개 → 27% 예산에서 3개째까지만/수량 축소
    buys = {c: 9.0 for c in ("111111", "222222", "333333", "444444")}
    prices = {c: 10_000 for c in buys}
    mcaps = {c: 500_000_000_000 for c in buys}
    scores = {c: 9 for c in buys}
    sells, orders, notes = _assemble(buys=buys, prices=prices, mcap_map=mcaps,
                                     quant_scores=scores, max_buys=4)
    total_smallcap = sum(o["qty"] * o["price"] for o in orders)
    assert total_smallcap <= 1_000_000_000 * 0.27 + 1e-6
    assert any("소형주" in n for n in notes)


def test_assemble_cash_floor_and_max_buys():
    sells, orders, notes = _assemble(buys={"068270": 9.0}, prices={"068270": 100_000},
                                     quant_scores={"068270": 9}, cash=50_000_000)
    # 현금 5천만 - 유보 2%(2천만) = 3천만 예산
    assert orders[0]["qty"] == int(30_000_000 // 100_000)
    sells, orders, notes = _assemble(buys={"068270": 5.0, "035420": 5.0}, max_buys=1,
                                     prices={"068270": 100_000, "035420": 100_000},
                                     quant_scores={"068270": 9, "035420": 9})
    assert len(orders) == 1
    assert any("최대 매수" in n for n in notes)


def test_assemble_sells_from_holdings_only():
    holdings = [{"code": "005930", "qty": 100, "cur_price": 80_000, "eval_amt": 8_000_000}]
    sells, buys, notes = _assemble(sells={"005930": "전량", "000660": "전량"},
                                   holdings=holdings, prices={"005930": 80_000})
    assert len(sells) == 1 and sells[0]["qty"] == 100 and sells[0]["side"] == "sell"


def test_timefolio_uses_common_hard_take_profit_even_when_llm_says_hold():
    holdings = [{"code": "214450", "name": "파마리서치", "qty": 2,
                 "cur_price": 408_500, "pnl_pct": 20.4, "eval_amt": 817_000}]
    sells, _, _ = _assemble(
        sells={"214450": "보유"}, holdings=holdings, prices={"214450": 408_500},
        enable_rebalance=True, take_profit_pct=12.0, stop_loss_pct=5.0)
    assert len(sells) == 1 and sells[0]["qty"] == 2
    assert "자동 익절" in sells[0]["reason"]


def test_pending_buy_skips_ledger_when_site_reconciliation_already_adopted_it():
    pending = {"ticker": "003230", "side": "buy", "qty": 57, "before_qty": 0}
    assert tfs.pending_ledger_apply_qty(pending, 56, site_qty=56, ledger_qty=56) == 0


def test_pending_fill_applies_only_unreconciled_quantity():
    buy = {"ticker": "003230", "side": "buy"}
    sell = {"ticker": "214450", "side": "sell"}
    assert tfs.pending_ledger_apply_qty(buy, 56, site_qty=56, ledger_qty=20) == 36
    assert tfs.pending_ledger_apply_qty(sell, 2, site_qty=0, ledger_qty=1) == 1


def test_assemble_skips_already_held():
    holdings = [{"code": "005930", "qty": 100, "cur_price": 80_000, "eval_amt": 8_000_000}]
    sells, buys, notes = _assemble(buys={"005930": 9.0}, holdings=holdings,
                                   prices={"005930": 80_000}, quant_scores={"005930": 9})
    assert not buys and any("이미 보유" in n for n in notes)


# ─── 후보 수집 ───────────────────────────────────────────────────────────────

def test_codes_in_news_matches_code_and_name():
    universe = {"005930": "삼성전자", "068270": "셀트리온"}
    articles = [{"title": "삼성전자, HBM4 공급 확대"}, {"title": "바이오 강세… 068270 신고가"}]
    codes = tfs.codes_in_news(articles, universe)
    assert "005930" in codes and "068270" in codes


def test_movers_from_bars(tmp_path):
    db = tmp_path / "bars.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE bars(code TEXT, ts TEXT, close REAL, vol_cum REAL, PRIMARY KEY(code, ts))")
    from datetime import datetime, timedelta, timezone
    day = datetime.now(timezone(timedelta(hours=9))).strftime("%Y%m%d")
    rows = [("005930", day + "0900", 100.0, 10), ("005930", day + "1000", 110.0, 100),   # +10%
            ("068270", day + "0900", 100.0, 10), ("068270", day + "1000", 95.0, 500)]    # -5%
    conn.executemany("INSERT INTO bars VALUES (?,?,?,?)", rows)
    conn.commit(); conn.close()
    movers = tfs.movers_from_bars({"005930": "삼성전자", "068270": "셀트리온"},
                                  top_n=5, db_path=str(db))
    codes = [m["code"] for m in movers]
    assert codes == ["005930"]           # 상승 종목만
    assert movers[0]["day_ret_pct"] == pytest.approx(10.0)


def test_movers_missing_db_returns_empty(tmp_path):
    assert tfs.movers_from_bars({}, db_path=str(tmp_path / "none.db")) == []


# ─── 사이클 세션 게이트 ──────────────────────────────────────────────────────

def test_cycle_skips_non_kr_sessions():
    """US/장외 세션에선 브라우저 동기화도 LLM 도 없이 즉시 스킵해야 한다."""
    import asyncio

    class _Broker:
        is_timefolio = True

        async def kr_account_snapshot(self, force=False):
            raise AssertionError("비 KR 세션에서 계좌 동기화가 호출되면 안 된다")

    class _Orch:
        uid = 77
        broker = _Broker()
        _cycle_history = []
        statuses = []

        async def _set_status(self, state, msg, force=False):
            self.statuses.append((state, msg))

    orch = _Orch()
    for session in ("US_TRADING", "OFF_HOURS", "KR_PRE_MARKET"):
        asyncio.run(tfs.run_timefolio_cycle(orch, [], None, session, market_open=False))
    assert len(orch.statuses) == 3
    assert all("스킵" in msg for _s, msg in orch.statuses)
    assert orch._cycle_history == []          # 스킵은 사이클 기록을 남기지 않는다
