"""사장 지시 2026-06-04: 전략 파라미터 확장 — config 측 정합.
spec: docs/superpowers/specs/2026-06-04-strategy-param-expansion-design.md
갱신(2026-06-04 결정론 점수 엔진): LLM 채점 가중치 QW_* 는 폐기되고 결정론 지표 가중치 QIW_*/차원 DW_* 로 대체.
"""
import config

# 오전 확장 중 살아남은 키(필터·레짐 게이트). QW_* 는 폐기되어 제외.
SURVIVING_KEYS = [
    "MIN_QUANT_SCORE", "MAX_BUY_VOLATILITY_PCT", "RSI_OVERBOUGHT_SKIP",
    "MIN_ADX_FOR_BUY", "REQUIRE_FOREIGN_NET_BUY", "MAX_PRICE_EXTENSION_PCT",
    "MACRO_STOCK_GATE_ENABLED",
]
DEPRECATED_QW = ["QW_TREND", "QW_MEANREV", "QW_VOLATILITY", "QW_FLOW", "QW_NEWS"]


def test_surviving_keys_registered_tunable():
    for k in SURVIVING_KEYS:
        assert k in config.STRATEGY_TUNABLE_KEYS, f"{k} 미등록"
        assert k in config.STRATEGY_KEY_META and config.STRATEGY_KEY_META[k].get("label")


def test_surviving_keys_in_defaults():
    for k in SURVIVING_KEYS:
        assert k in config.STRATEGY_DEFAULTS, f"기본값에 {k} 누락"


def test_qw_category_weights_deprecated():
    # LLM 채점 가중치 QW_* 는 더 이상 튜너블 아님(결정론 QIW_*/DW_* 로 대체).
    for k in DEPRECATED_QW:
        assert k not in config.STRATEGY_TUNABLE_KEYS, f"{k} 가 아직 튜너블(폐기 안 됨)"
        assert k not in config.STRATEGY_DEFAULTS, f"기본값에 폐기 키 {k} 잔존"


def test_catalog_text_includes_tunable_and_effect():
    txt = config.strategy_param_catalog_text()
    assert "MIN_QUANT_SCORE" in txt
    assert "올리면" in txt or "효과" in txt
    assert "STOP_LOSS_PCT" in txt and "MIN_CASH_BUFFER" in txt
    # 폐기된 QW_* 는 카탈로그에 노출되지 않는다
    assert "QW_TREND" not in txt
