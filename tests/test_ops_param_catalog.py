"""사장 지시 2026-06-04: 운용지원실장이 전략 지시를 파라미터로 충실히 번역하도록,
ops 워커 프롬프트에 (1) effect 포함 전체 파라미터 카탈로그 (2) 레짐 플레이북을 주입한다.
"""
from infra.ops_support_worker import _param_tuning_addendum


def test_addendum_includes_new_params_with_effects():
    txt = _param_tuning_addendum()
    assert "MIN_QUANT_SCORE" in txt
    # 결정론 점수 엔진 신규 지표·차원 가중치가 카탈로그에 노출(QW_* 는 폐기되어 없음)
    assert "QIW_VOL" in txt and "QIW_FLOW" in txt and "DW_NEWS" in txt
    assert "QW_VOLATILITY" not in txt
    # 효과(올리면/내리면) 방향 안내 포함 → ops 가 방향을 안다
    assert "효과" in txt and "올리면" in txt


def test_addendum_includes_regime_playbook():
    txt = _param_tuning_addendum()
    assert "급락장" in txt
    assert "추세추종" in txt and "역추세" in txt
    # 플레이북이 신규 결정론 노브를 지목
    assert "MIN_CASH_BUFFER" in txt and "QIW_ADX" in txt and "DETERMINISTIC_SCORING" in txt


def test_addendum_includes_institutional_pipeline_knobs():
    # 제도권 4기능 신규 노브가 카탈로그·플레이북에 노출(ops 가 사이징·유니버스·종목수를 조정 가능)
    txt = _param_tuning_addendum()
    assert "POSITION_SIZING_MODE" in txt and "UNIVERSE_EXCLUDE_LEVERAGED" in txt and "MAX_BUY_NAMES" in txt
    # 플레이북이 사이징/유니버스 노브를 레짐 맥락에서 지목
    assert "SIZING_TILT_STRENGTH" in txt and "UNIVERSE_MIN_TURNOVER" in txt
    # 증거기반 튜닝 안내(스코어카드 참조)
    assert "scorecard" in txt or "성과" in txt
