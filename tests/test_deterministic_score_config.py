"""사장 지시 2026-06-04: 결정론 점수 엔진 파라미터 — config 정합.
spec: docs/superpowers/specs/2026-06-04-deterministic-score-engine-design.md
"""
import config

QIW = ["QIW_RSI", "QIW_MACD", "QIW_ADX", "QIW_VWAP", "QIW_VOL", "QIW_MOM", "QIW_CMF", "QIW_FLOW", "QIW_HIGH52"]
DW = ["DW_QUANT", "DW_NEWS", "DW_MACRO"]
NEW = QIW + DW + ["DETERMINISTIC_SCORING"]


def test_new_keys_are_constants_and_tunable():
    for k in NEW:
        assert hasattr(config, k), f"config.{k} 상수 없음"
        assert k in config.STRATEGY_TUNABLE_KEYS, f"{k} 미등록(튜너블)"


def test_new_keys_have_metadata_and_effect():
    for k in NEW:
        assert k in config.STRATEGY_KEY_META and config.STRATEGY_KEY_META[k].get("label"), f"{k} 메타 누락"
        assert k in config.STRATEGY_KEY_EFFECT, f"{k} effect 누락"


def test_new_keys_in_all_presets():
    for name, preset in config.STRATEGY_PRESETS.items():
        for k in NEW:
            assert k in preset, f"프리셋 '{name}'에 {k} 누락"


def test_deterministic_scoring_default_on():
    assert config.DETERMINISTIC_SCORING is True
    for name, preset in config.STRATEGY_PRESETS.items():
        assert preset["DETERMINISTIC_SCORING"] is True


def test_qiw_signed_allowed_and_dw_present():
    # 음수 허용 — 타입이 int/float 이면 충분(범위 검증은 runtime clamp 아님, 그대로 사용)
    for k in QIW + DW:
        assert isinstance(config.STRATEGY_PRESETS["balanced"][k], (int, float))
