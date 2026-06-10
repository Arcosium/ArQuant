"""사장 지시 2026-06-04: 제도권 파이프라인 4기능 — 신규 튜너블 노브 등록 검증.
spec: docs/superpowers/specs/2026-06-04-institutional-pipeline-mimicry-design.md"""
import config

NEW_KEYS = [
    "MAX_BUY_NAMES", "POSITION_SIZING_MODE", "SIZING_TILT_STRENGTH", "SIZING_MAX_TILT",
    "UNIVERSE_MIN_PRICE", "UNIVERSE_MIN_TURNOVER", "UNIVERSE_EXCLUDE_LEVERAGED",
    "SCORECARD_WINDOW_DAYS",
]


def test_constants_exist():
    for k in NEW_KEYS:
        assert hasattr(config, k), f"config.{k} 상수 누락"


def test_registered_in_tunable_keys():
    for k in NEW_KEYS:
        assert k in config.STRATEGY_TUNABLE_KEYS, f"{k} TUNABLE_KEYS 누락"


def test_meta_and_effect_present():
    for k in NEW_KEYS:
        assert k in config.STRATEGY_KEY_META, f"{k} META 누락"
        assert config.STRATEGY_KEY_META[k].get("label"), f"{k} label 누락"
        assert k in config.STRATEGY_KEY_EFFECT, f"{k} EFFECT 누락"


def test_defaults_define_new_keys():
    for k in NEW_KEYS:
        assert k in config.STRATEGY_DEFAULTS, f"기본값에 {k} 누락"


def test_defaults_are_backward_safe():
    # 디폴트는 기존 동작에 가깝게: 사이징 약한 기울임, 유니버스 레버리지만 배제(가격/거래대금 0=off)
    assert config.POSITION_SIZING_MODE in ("equal", "risk_weighted")
    assert 0.0 <= config.SIZING_TILT_STRENGTH <= 1.0
    assert config.SIZING_MAX_TILT >= 1.0
    assert config.MAX_BUY_NAMES >= 1


def test_catalog_text_includes_new_keys():
    txt = config.strategy_param_catalog_text()
    assert "POSITION_SIZING_MODE" in txt and "UNIVERSE_EXCLUDE_LEVERAGED" in txt
    assert "효과:" in txt  # EFFECT 주입 확인
