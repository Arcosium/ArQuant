"""사장 지시 2026-06-04: 순수 파이썬 결정론 점수 엔진.
spec: docs/superpowers/specs/2026-06-04-deterministic-score-engine-design.md

순수 함수만 검증(LLM·IO 없음):
  indicator_signals(ind)        — 지표 raw → 신호 s∈[-1,1] (없는 지표는 생략)
  compute_indicator_score(ind, w) — 신호 가중합 → 0~10 (스케일 안정·음수 반전·폴백)
  news_score(sent) / macro_score(stock_pct) — 0~10 또는 None
  combine_dimensions(scores, dw) — 차원 합성 0~10 (None 차원 제외·DW 음수 반전)
"""
from tools.quant_score import (
    indicator_signals, compute_indicator_score, news_score, macro_score, combine_dimensions,
)


# ── indicator_signals ────────────────────────────────────────────────
def test_rsi_oversold_positive_overbought_negative():
    assert indicator_signals({"rsi14": 20})["rsi"] == 1.0     # (50-20)/30=1
    assert indicator_signals({"rsi14": 80})["rsi"] == -1.0    # (50-80)/30→clamp -1
    assert indicator_signals({"rsi14": 50})["rsi"] == 0.0


def test_adx_direction_applied():
    s_up = indicator_signals({"adx": 40, "adx_dir": 1})["adx"]
    s_dn = indicator_signals({"adx": 40, "adx_dir": -1})["adx"]
    assert s_up == 1.0 and s_dn == -1.0      # (40-20)/20=1, ×dir


def test_vwap_overextension_negative():
    assert indicator_signals({"vwap_dev": 30})["vwap"] == -1.0   # 과이격 위 → 음
    assert indicator_signals({"vwap_dev": -7.5})["vwap"] == 0.5  # 아래 → 양


def test_missing_indicator_omitted():
    sigs = indicator_signals({"rsi14": 50})
    assert "rsi" in sigs and "macd" not in sigs and "cmf" not in sigs


def test_prenormalized_cmf_flow_clamped():
    sigs = indicator_signals({"cmf": 0.3, "flow": -2.0})
    assert sigs["cmf"] == 0.3 and sigs["flow"] == -1.0


# ── compute_indicator_score ──────────────────────────────────────────
def test_score_scale_stable_under_weight_magnitude():
    ind = {"rsi14": 20, "macd_hist_pct": 4, "sigma20": 80}
    a, _ = compute_indicator_score(ind, {"rsi": 1, "macd": 1, "vol": 1})
    b, _ = compute_indicator_score(ind, {"rsi": 10, "macd": 10, "vol": 10})
    assert abs(a - b) < 1e-9          # 가중치 ×10 해도 점수 동일(비율만 의미)


def test_score_negative_weight_inverts():
    ind = {"rsi14": 20}               # rsi 신호 +1 (매수우호)
    pos, _ = compute_indicator_score(ind, {"rsi": 1})
    neg, _ = compute_indicator_score(ind, {"rsi": -1})
    assert pos == 10.0 and neg == 0.0


def test_score_all_zero_weights_is_neutral():
    score, _ = compute_indicator_score({"rsi14": 20}, {"rsi": 0})
    assert score == 5.0


def test_score_missing_indicator_excluded_from_denominator():
    # rsi(+1)만 존재, macd 가중치 있어도 신호 없으면 분모에서 제외 → 만점
    score, _ = compute_indicator_score({"rsi14": 20}, {"rsi": 2, "macd": 5})
    assert score == 10.0


# ── news / macro ─────────────────────────────────────────────────────
def test_news_score_mapping_and_none():
    assert news_score(1.0) == 10.0 and news_score(-1.0) == 0.0 and news_score(0.0) == 5.0
    assert news_score(None) is None


def test_macro_score_neutral_at_15pct():
    assert macro_score(15) == 5.0
    assert macro_score(40) == 10.0       # (40-15)/25=1
    assert macro_score(1) < 5.0
    assert macro_score(None) is None


# ── combine_dimensions ───────────────────────────────────────────────
def test_combine_weighted_blend():
    # QUANT 10, NEWS 0, 가중치 동일 → 5
    out = combine_dimensions({"QUANT": 10.0, "NEWS": 0.0, "MACRO": None},
                             {"QUANT": 1, "NEWS": 1, "MACRO": 1})
    assert abs(out - 5.0) < 1e-9


def test_combine_dw_negative_inverts_dimension():
    # NEWS 10(호재)인데 DW_NEWS 음수 → 감점 방향
    pos = combine_dimensions({"QUANT": 5.0, "NEWS": 10.0}, {"QUANT": 0, "NEWS": 1})
    neg = combine_dimensions({"QUANT": 5.0, "NEWS": 10.0}, {"QUANT": 0, "NEWS": -1})
    assert pos == 10.0 and neg == 0.0


def test_combine_none_dimension_excluded():
    out = combine_dimensions({"QUANT": 8.0, "NEWS": None, "MACRO": None},
                             {"QUANT": 1, "NEWS": 5, "MACRO": 5})
    assert out == 8.0                    # QUANT만 반영


def test_combine_all_none_is_neutral():
    assert combine_dimensions({"QUANT": None, "NEWS": None, "MACRO": None},
                              {"QUANT": 1, "NEWS": 1, "MACRO": 1}) == 5.0
