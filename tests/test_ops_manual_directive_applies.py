from infra.ops_support_worker import _gate_overrides_by_data


def test_manual_bypasses_data_gate():
    # 사장 직접 지시(manual)는 cycle 데이터 없어도 통과한다.
    ov, reason = _gate_overrides_by_data({"TAKE_PROFIT_PCT": 8}, has_cycle_data=False, is_manual=True)
    assert ov == {"TAKE_PROFIT_PCT": 8}
    assert reason == ""


def test_autonomous_still_gated_without_data():
    ov, reason = _gate_overrides_by_data({"TAKE_PROFIT_PCT": 8}, has_cycle_data=False, is_manual=False)
    assert ov == {}
    assert "보류" in reason


def test_autonomous_with_data_passes():
    ov, reason = _gate_overrides_by_data({"TAKE_PROFIT_PCT": 8}, has_cycle_data=True, is_manual=False)
    assert ov == {"TAKE_PROFIT_PCT": 8}
    assert reason == ""
