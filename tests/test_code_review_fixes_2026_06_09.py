"""코드리뷰 후속 수정 회귀 (2026-06-09) — 슬리브 엔진 5건.

#1 양시장 비중, #2 falsy-zero, #3 thesis 오삭제, #4 매도 가격폴백, #5 OFF 슬리브 orphan.
"""
import asyncio

import pytest

import main_swarm
import runtime


def _mk_sm(uid=999):
    sm = main_swarm.ArquantOrchestrator.__new__(main_swarm.ArquantOrchestrator)
    sm.uid = uid

    class _Log:
        def log(self, *a, **k):
            pass
    sm.cycle_log = _Log()

    async def _noop_emit(*a, **k):
        return None
    sm._emit = _noop_emit
    return sm


# ── #2: falsy-zero 마스킹 방지 + 슬리브별 정확한 기본값 ──────────────────────────
def test_sleeve_rt_preserves_legit_zero(monkeypatch):
    sm = _mk_sm()
    monkeypatch.setattr(runtime, "get",
                        lambda k, default=None, uid=None: 0.0 if k == "BOND_TARGET_MAX_PCT" else None)
    # 정당한 0.0(비중 0% 제한)이 0.40 으로 둔갑하지 않는다
    assert sm._sleeve_rt("BOND_TARGET_MAX_PCT") == 0.0


def test_sleeve_rt_none_falls_back_to_per_sleeve_config_default(monkeypatch):
    sm = _mk_sm()
    monkeypatch.setattr(runtime, "get", lambda k, default=None, uid=None: None)
    assert sm._sleeve_rt("BOND_TARGET_MAX_PCT") == 0.40       # 채권 정확값
    assert sm._sleeve_rt("COMMODITY_TARGET_MAX_PCT") == 0.20  # 원자재 정확값(채권 0.40 아님)
    assert sm._sleeve_rt("COMMODITY_PER_CYCLE_RATIO") == 0.10


# ── #5: 활성 슬리브 코드만 / OFF 슬리브 orphan 방지 ─────────────────────────────
def test_enabled_sleeve_codes_only_active(monkeypatch):
    sm = _mk_sm()
    monkeypatch.setattr(runtime, "get",
                        lambda k, default=None, uid=None: k == "ENABLE_BOND_ETF")  # 채권 ON, 원자재 OFF
    codes = sm._enabled_sleeve_codes()
    assert "148070" in codes and "TLT" in codes   # 채권(활성) 포함
    assert "GLD" not in codes and "132030" not in codes  # 원자재(비활성) 제외


def test_build_sleeve_sell_orders_respects_pool():
    from main_swarm import _build_sleeve_sell_orders
    decisions = {"148070": "전량", "GLD": "전량"}
    holdings = [{"code": "148070", "qty": 3, "cur_price": 50000},
                {"code": "GLD", "qty": 2, "cur_price": 200}]
    # pool 을 채권만으로 한정 → 원자재(GLD)는 슬리브 매도 조립서 제외(주식 트랙이 처리)
    orders = _build_sleeve_sell_orders(
        decisions, holdings, lambda c: 50000 if c == "148070" else 200, pool={"148070"})
    assert {o["ticker"] for o in orders} == {"148070"}


# ── #4: 슬리브 매도 가격조회 실패 시 cur_price 폴백(주문 누락 방지) ───────────────
def test_build_orders_sleeve_sell_price_fallback(monkeypatch):
    sm = _mk_sm()

    class _Cyc:
        pass
    cyc = _Cyc()
    cyc.market_open = True
    cyc.target_codes = []
    cyc.candidate_codes = []
    cyc.quant_report = ""
    cyc.news_report = ""
    cyc.holdings = [{"code": "148070", "name": "KOSEF10Y", "qty": 5, "cur_price": 50000}]
    cyc.sell_directives = {"148070": "전량"}
    cyc._entry_dirs = {}
    cyc._sell_prices = {}
    cyc.sleeve_buy_orders = []
    cyc.sleeve_price_map = {}   # 가격조회 실패(빈 맵) — 폴백 없으면 매도 누락
    cyc.sleeve_sell_proposals = {"bond": {"148070": "전량"}}

    async def _fake_build_orders(*a, **k):
        return ({"orders": [], "sizing_notes": []}, {}, {"cash": 1e9, "total_eval": 1e9, "ok": True})
    sm._build_orders = _fake_build_orders

    asyncio.run(sm._cyc_stage_build_orders(cyc))
    sells = [o for o in cyc.order_obj["orders"] if o["ticker"] == "148070" and o["side"] == "sell"]
    assert sells and sells[0]["qty"] == 5, "가격조회 실패해도 cur_price 폴백으로 슬리브 매도가 살아야 함"


# ── #3: 해외조회 비신뢰 시 US thesis 오삭제 방지 ────────────────────────────────
def test_sync_preserves_us_thesis_when_foreign_unreliable(monkeypatch, tmp_path):
    import infra.sleeve_thesis as st
    import infra.position_thesis as pt
    from infra import user_paths
    monkeypatch.setattr(user_paths, "sleeve_thesis_path", lambda uid, key: tmp_path / f"{key}_{uid}.json")
    monkeypatch.setattr(user_paths, "position_thesis_path", lambda uid: tmp_path / f"pos_{uid}.json")
    st._reset_cache_for_tests()
    if hasattr(pt, "_reset_cache_for_tests"):
        pt._reset_cache_for_tests()
    st.record(999, "bond", "TLT", {"entry_price": 90})         # US 채권
    st.record(999, "bond", "148070", {"entry_price": 50000})   # KR 채권
    sm = _mk_sm()

    # 해외조회 실패(drop_foreign=False) → KR 보유만 넘겨도 US thesis(TLT) 보존
    sm._sync_thesis_with_current_holdings([{"code": "148070", "qty": 1}], drop_foreign=False)
    assert "TLT" in st.get_all(999, "bond"), "해외조회 비신뢰 시 US 슬리브 thesis 오삭제되면 안 됨"
    assert "148070" in st.get_all(999, "bond")

    # 해외조회 신뢰(drop_foreign=True) + TLT 미보유 → 정상 정리
    sm._sync_thesis_with_current_holdings([{"code": "148070", "qty": 1}], drop_foreign=True)
    assert "TLT" not in st.get_all(999, "bond")
