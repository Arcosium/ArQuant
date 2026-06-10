from infra.ops_param_clamp import clamp_overrides
import config


def test_numeric_clamped_to_meta_range():
    clamped, notes = clamp_overrides({"TAKE_PROFIT_PCT": 999})
    assert clamped["TAKE_PROFIT_PCT"] == config.STRATEGY_KEY_META["TAKE_PROFIT_PCT"]["max"]
    assert any("TAKE_PROFIT_PCT" in n for n in notes)


def test_numeric_within_range_unchanged():
    clamped, notes = clamp_overrides({"TAKE_PROFIT_PCT": 8})
    assert clamped["TAKE_PROFIT_PCT"] == 8
    assert notes == []


def test_bool_normalized():
    clamped, _ = clamp_overrides({"MACRO_STOCK_GATE_ENABLED": "true"})
    assert clamped["MACRO_STOCK_GATE_ENABLED"] is True
    clamped, _ = clamp_overrides({"MACRO_STOCK_GATE_ENABLED": 0})
    assert clamped["MACRO_STOCK_GATE_ENABLED"] is False


def test_unknown_key_dropped():
    clamped, notes = clamp_overrides({"NOT_A_REAL_KEY": 5})
    assert "NOT_A_REAL_KEY" not in clamped
    assert any("NOT_A_REAL_KEY" in n for n in notes)


def test_below_min_clamped_up():
    lo = config.STRATEGY_KEY_META["TAKE_PROFIT_PCT"]["min"]
    clamped, _ = clamp_overrides({"TAKE_PROFIT_PCT": -100})
    assert clamped["TAKE_PROFIT_PCT"] == lo


def test_choice_valid_kept():
    clamped, notes = clamp_overrides({"POSITION_SIZING_MODE": "equal"})
    assert clamped["POSITION_SIZING_MODE"] == "equal"
    assert notes == []


def test_choice_invalid_dropped():
    clamped, notes = clamp_overrides({"POSITION_SIZING_MODE": "bogus"})
    assert "POSITION_SIZING_MODE" not in clamped
    assert any("POSITION_SIZING_MODE" in n for n in notes)


def test_tunable_without_meta_passes_through(monkeypatch):
    # 튜너블 화이트리스트엔 있으나 META 엔 범위 정보가 없는 키는 클램프 불가 → 통과(드롭 금지).
    # 실 config 엔 현재 그런 키가 없으므로(모든 tunable 이 META 등록됨) 가짜 키로 분기를 검증한다.
    monkeypatch.setattr(config, "STRATEGY_TUNABLE_KEYS",
                        list(config.STRATEGY_TUNABLE_KEYS) + ["FAKE_TUNABLE_NO_META"])
    clamped, _ = clamp_overrides({"FAKE_TUNABLE_NO_META": 0.05})
    assert clamped["FAKE_TUNABLE_NO_META"] == 0.05
