"""ops 파라미터 cycle/weekly tier 강제 구분 (사장 지시 2026-06-09 #7).

매 사이클 조정 가능(cycle) vs 토요일 백테스트+실데이터 검증 후만(weekly) 코드 강제.
"""
import config
from infra.ops_param_clamp import partition_by_tier


# ── 6.1 메타 tier 분류 ──────────────────────────────────────────────────────────
def test_every_tunable_key_has_tier():
    for k in config.STRATEGY_TUNABLE_KEYS:
        m = config.STRATEGY_KEY_META.get(k)
        assert m is not None and m.get("tier") in ("cycle", "weekly"), k


def test_scoring_and_structural_are_weekly():
    for k in ("QIW_RSI", "DW_QUANT", "POSITION_SIZING_MODE", "MAX_BUY_NAMES",
              "UNIVERSE_MIN_PRICE", "SCORECARD_WINDOW_DAYS",
              "BOND_TARGET_MAX_PCT", "COMMODITY_TARGET_MAX_PCT",
              "ENABLE_BOND_ETF", "ENABLE_COMMODITY_ETF"):
        assert config.STRATEGY_KEY_META[k]["tier"] == "weekly", k


def test_tactical_are_cycle():
    for k in ("TAKE_PROFIT_PCT", "STOP_LOSS_PCT", "PER_ORDER_BUDGET_RATIO",
              "MIN_QUANT_SCORE", "CONSERVATIVE_MDD", "MAX_TRADES_PER_CYCLE",
              "MACRO_STOCK_GATE_ENABLED"):
        assert config.STRATEGY_KEY_META[k]["tier"] == "cycle", k


def test_catalog_shows_tier():
    txt = config.strategy_param_catalog_text()
    assert "토요일" in txt or "주간" in txt   # weekly 표기
    assert "사이클" in txt                     # cycle 표기


# ── 6.2 partition_by_tier 강제 ───────────────────────────────────────────────────
def test_cycle_defers_weekly_tier():
    ov = {"TAKE_PROFIT_PCT": 8.0, "QIW_RSI": 3, "POSITION_SIZING_MODE": "equal"}
    apply, defer, notes = partition_by_tier(ov, trigger="cycle")
    assert apply == {"TAKE_PROFIT_PCT": 8.0}
    assert set(defer) == {"QIW_RSI", "POSITION_SIZING_MODE"}
    assert notes  # 회부 사유 메모


def test_weekly_applies_all():
    ov = {"TAKE_PROFIT_PCT": 8.0, "QIW_RSI": 3}
    apply, defer, notes = partition_by_tier(ov, trigger="weekly")
    assert defer == {} and set(apply) == {"TAKE_PROFIT_PCT", "QIW_RSI"}


def test_manual_applies_all():
    apply, defer, notes = partition_by_tier({"QIW_RSI": 3}, trigger="manual")
    assert apply == {"QIW_RSI": 3} and defer == {}
