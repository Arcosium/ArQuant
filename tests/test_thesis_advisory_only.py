"""포트폴리오기획팀장 = 강력 권고 전용 (거부권 폐지) — 회귀 테스트.

사장 지시 2026-06-08: 포트폴리오기획팀장의 '거부권'(사후관리실장 매도결정을 코드가 '보유'로
강제 오버라이드)이 너무 강력하다 → 폐지한다. 대신 사후관리실장 프롬프트에 thesis 를
'강력 권고'로 주입만 하고, 최종 매도 권한은 사후관리실장에게 둔다.

이 테스트는 강제력(거부권)이 되살아나지 못하도록 못박는다:
  - apply_thesis_veto 함수가 코드에서 제거됐다.
  - THESIS_VETO_ENABLED / THESIS_NOISE_BAND_PCT 설정 키가 제거됐다.
  - 권고 채널(format_thesis_reminder / 페르소나)은 살아있고 '강력 권고' 톤이다.
"""
import agents.specialists as specialists
import config
from agents.specialists import create_fund_planner, format_thesis_reminder


def test_veto_function_removed():
    """거부권(강제 오버라이드) 함수는 코드에서 완전히 제거됐다."""
    assert not hasattr(specialists, "apply_thesis_veto"), \
        "apply_thesis_veto 는 폐지됐어야 한다 (강제 거부권 금지, 권고만 허용)"


def test_veto_config_keys_removed():
    """거부권 토글·노이즈밴드 설정 키가 제거됐다 (상수·튜너블 양쪽)."""
    assert not hasattr(config, "THESIS_VETO_ENABLED")
    assert not hasattr(config, "THESIS_NOISE_BAND_PCT")
    assert "THESIS_VETO_ENABLED" not in config.STRATEGY_TUNABLE_KEYS
    assert "THESIS_NOISE_BAND_PCT" not in config.STRATEGY_TUNABLE_KEYS
    # 기본값에도 잔재가 없어야 한다.
    assert "THESIS_VETO_ENABLED" not in config.STRATEGY_DEFAULTS
    assert "THESIS_NOISE_BAND_PCT" not in config.STRATEGY_DEFAULTS


def test_reminder_is_strong_advisory_not_veto():
    """thesis 리마인더는 '강력 권고' 톤이지 강제가 아니다 — 최종 권한은 사후관리실장."""
    theses = {
        "006400": {
            "entry_ts": "2026-06-08 09:00:00",
            "entry_price": 100000.0,
            "target_price": 110000.0,
            "stop_price": 93000.0,
            "planned_hold_hours": 48,
            "entry_reason": "2차전지 비중 확대",
            "source_agent": "포트폴리오기획팀장",
        },
    }
    holdings = [{"code": "006400", "name": "삼성SDI", "qty": 1, "cur_price": 101000.0}]
    out = format_thesis_reminder(theses, holdings, now_iso="2026-06-08 11:00:00")
    assert "강력 권고" in out, f"강력 권고 톤이어야: {out}"
    # 강제가 아니라 권한이 사후관리실장에게 있음을 명시한다.
    assert "사후관리실장" in out


def test_fund_planner_persona_has_no_veto():
    """포트폴리오기획팀장 페르소나에서 '거부권' 표현이 사라지고 '권고'로 재정의됐다."""
    a = create_fund_planner(injection={"uid": 1})
    assert "거부권" not in a.system_prompt, "페르소나에 거부권 잔재가 있으면 안 된다"
    assert "권고" in a.system_prompt
    # plan 모드(목표·손절·계획기간 작성)는 그대로 유지된다.
    assert "목표가" in a.system_prompt and "손절가" in a.system_prompt
