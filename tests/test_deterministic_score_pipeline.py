"""사장 지시 2026-06-04: 결정론 점수 조립 헬퍼(main_swarm) — 순수 함수.
spec: docs/superpowers/specs/2026-06-04-deterministic-score-engine-design.md
"""
from main_swarm import parse_news_sentiment, assemble_quant_score

QIW = {"rsi": 5, "macd": 10, "adx": 8, "vwap": 8, "vol": 8, "mom": 12, "cmf": 8, "flow": 12, "high52": 8}
DW = {"QUANT": 60, "NEWS": 25, "MACRO": 15}

NEWS = ("[뉴스 감성 분석 리포트]\n"
        "- 📰 에치에프알(230240) (KR 전용): 감성 +0.80\n  - [긍정] AT&T 수혜\n"
        "- 📰 에스제이그룹 (정원오): 감성 -0.95\n  - [부정] 낙마\n"
        "- 📰 신세계(004170): 감성 +0.85\n")


def test_parse_sentiment_by_code():
    assert parse_news_sentiment(NEWS, "230240", "에치에프알") == 0.80


def test_parse_sentiment_by_name_when_code_absent():
    assert parse_news_sentiment(NEWS, "999999", "신세계") == 0.85


def test_parse_sentiment_negative():
    assert parse_news_sentiment(NEWS, "999999", "에스제이그룹") == -0.95


def test_parse_sentiment_none_when_absent():
    assert parse_news_sentiment(NEWS, "111111", "없는회사") is None
    assert parse_news_sentiment("", "230240", "x") is None


def test_assemble_high_when_all_bullish():
    ind = {"rsi14": 20, "macd_hist_pct": 4, "adx": 40, "adx_dir": 1, "mom_1m": 30, "mom_3m": 30,
           "cmf": 0.8, "flow": 0.8, "high52_prox": 1.0, "vwap_dev": -5, "sigma20": 20}
    score, bd = assemble_quant_score(ind, 0.8, 40, QIW, DW)
    assert isinstance(score, int) and score >= 8
    assert "S_quant" in bd


def test_assemble_zero_when_no_indicator_data():
    # 일봉 데이터 부족(빈 지표) → 0 (매수 제외, 레거시 안전 패리티)
    score, _ = assemble_quant_score({}, 0.9, 40, QIW, DW)
    assert score == 0


def test_assemble_quant_only_when_news_macro_none():
    ind = {"rsi14": 20}                      # rsi 신호 +1
    score, _ = assemble_quant_score(ind, None, None, {"rsi": 1}, DW)
    assert score == 10                       # QUANT 차원만 → 10


def test_assemble_low_when_bearish():
    ind = {"rsi14": 85, "macd_hist_pct": -4, "mom_1m": -30, "mom_3m": -30, "cmf": -0.8,
           "flow": -0.8, "high52_prox": 0.2, "vwap_dev": 30, "sigma20": 120}
    score, _ = assemble_quant_score(ind, -0.9, 1, QIW, DW)
    assert score <= 2
