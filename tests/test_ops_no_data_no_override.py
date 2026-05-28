"""운용지원실장: 실제 사이클 데이터가 없으면 param_overrides 변경을 거부한다.

배경(2026-05-27 진단): manual 지시("익절 좀 올려줘") 처리 시, 직전 사이클 데이터가 전혀
없는데도 LLM 이 'A종목 +9.2% 익절 후 +14% 상승' 같은 **존재하지 않는 거래를 지어내**
TAKE_PROFIT_PCT 8→12 변경을 정당화하고 profiles/<uid> 에 기록했다. 데이터 유무를 LLM
재량에 맡기지 말고 **코드 게이트**로 막는다 — 데이터 없으면 어떤 override 도 적용 안 함.
"""
from infra.ops_support_worker import _gate_overrides_by_data


def test_no_data_rejects_overrides():
    applied, reason = _gate_overrides_by_data({"TAKE_PROFIT_PCT": 12.0}, has_cycle_data=False)
    assert applied == {}, "실데이터 없으면 어떤 파라미터도 적용하면 안 된다(날조 방지)"
    assert reason, "거부 사유가 있어야 한다"
    assert "데이터" in reason


def test_with_data_passes_overrides_through():
    raw = {"TAKE_PROFIT_PCT": 12.0, "STOP_LOSS_PCT": 3.0}
    applied, reason = _gate_overrides_by_data(raw, has_cycle_data=True)
    assert applied == raw, "실데이터가 있으면 override 를 통과시킨다(후속 화이트리스트/클램프는 set_overrides)"
    assert reason == ""


def test_no_data_empty_overrides_is_noop():
    applied, reason = _gate_overrides_by_data({}, has_cycle_data=False)
    assert applied == {}
