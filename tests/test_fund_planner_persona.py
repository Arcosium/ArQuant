"""펀드기획팀장 페르소나 — plan 출력 파서 + remind 포맷터.

사장 확정 2026-05-28 (우선순위 3 단독 적용):
  사후관리실장 산하에 펀드기획팀장 페르소나를 두고
  - 매수 시점에 진입 thesis (목표가/손절가/계획 보유기간/진입 사유) 작성
  - 사후관리실장 매도 판단 직전 thesis 를 그대로 상기

플랜 출력은 정형 4줄이므로 deterministic regex 파서를 쓴다.
리마인더는 LLM 호출 없이 stored thesis 를 포맷팅 (비용 절감 + deterministic).
"""
from agents.specialists import create_fund_planner, parse_fund_plan, format_thesis_reminder


def test_persona_can_be_created():
    a = create_fund_planner(injection={"uid": 1})
    assert a.name == "펀드기획팀장"
    assert a.role == "fund_planner"
    assert "목표가" in a.system_prompt
    assert "손절가" in a.system_prompt
    assert "계획 보유기간" in a.system_prompt
    assert "진입 사유" in a.system_prompt


def test_parse_fund_plan_extracts_four_fields():
    text = """매수 사유를 종합한 결과:

목표가: 700000
손절가: 620000
계획 보유기간: 48h
진입 사유 요약: 전략리서치팀장 2차전지 비중 확대 + 정배열 ADX 25
"""
    out = parse_fund_plan(text)
    assert out["target_price"] == 700000.0
    assert out["stop_price"] == 620000.0
    assert out["planned_hold_hours"] == 48
    assert "2차전지" in out["entry_reason"]


def test_parse_fund_plan_tolerates_commas_and_full_width_colons():
    text = "목표가: 700,000\n손절가： 620,000\n계획 보유기간: 48h\n진입 사유 요약: 테스트"
    out = parse_fund_plan(text)
    assert out["target_price"] == 700000.0
    assert out["stop_price"] == 620000.0


def test_parse_fund_plan_partial_fields_returns_nones():
    """필드 누락 시 None — 호출부가 폴백 결정."""
    text = "목표가: 700000"
    out = parse_fund_plan(text)
    assert out["target_price"] == 700000.0
    assert out["stop_price"] is None
    assert out["planned_hold_hours"] is None


def test_parse_fund_plan_accepts_decimal_hours():
    text = "목표가: 700\n손절가: 620\n계획 보유기간: 1.5h\n진입 사유 요약: x"
    out = parse_fund_plan(text)
    assert out["planned_hold_hours"] == 1.5


def test_format_reminder_lists_holdings_with_thesis():
    theses = {
        "006400": {
            "entry_ts": "2026-05-28 14:09:14",
            "entry_price": 655500.0,
            "target_price": 700000.0,
            "stop_price": 620000.0,
            "planned_hold_hours": 48,
            "entry_reason": "2차전지 비중 확대",
            "source_agent": "펀드기획팀장",
        },
    }
    holdings = [{"code": "006400", "name": "삼성SDI", "qty": 1, "cur_price": 678000.0}]
    out = format_thesis_reminder(theses, holdings, now_iso="2026-05-29 09:00:00")
    assert "펀드기획팀장" in out
    assert "삼성SDI" in out or "006400" in out
    assert "655" in out          # entry price 표시
    assert "700" in out          # target
    assert "620" in out          # stop
    assert "48" in out           # planned hours
    assert "2차전지 비중 확대" in out


def test_format_reminder_marks_holding_over_planned_period():
    theses = {
        "006400": {
            "entry_ts": "2026-05-26 14:00:00",
            "entry_price": 655500.0,
            "target_price": 700000.0,
            "stop_price": 620000.0,
            "planned_hold_hours": 24,
            "entry_reason": "x",
            "source_agent": "펀드기획팀장",
        },
    }
    holdings = [{"code": "006400", "name": "삼성SDI", "qty": 1, "cur_price": 678000.0}]
    # 2026-05-28 14:00 - 2026-05-26 14:00 = 48h, 계획 24h → '초과'
    out = format_thesis_reminder(theses, holdings, now_iso="2026-05-28 14:00:00")
    assert "초과" in out, f"계획 보유 초과는 명시돼야: {out}"


def test_format_reminder_marks_target_reached():
    theses = {
        "006400": {
            "entry_ts": "2026-05-28 14:00:00",
            "entry_price": 655500.0,
            "target_price": 700000.0,
            "stop_price": 620000.0,
            "planned_hold_hours": 48,
            "entry_reason": "x",
            "source_agent": "펀드기획팀장",
        },
    }
    # cur_price >= target → 목표 도달 표시
    holdings = [{"code": "006400", "name": "삼성SDI", "qty": 1, "cur_price": 702000.0}]
    out = format_thesis_reminder(theses, holdings, now_iso="2026-05-28 16:00:00")
    assert "목표" in out and ("도달" in out or "✅" in out)


def test_format_reminder_marks_stop_hit():
    theses = {
        "006400": {
            "entry_ts": "2026-05-28 14:00:00",
            "entry_price": 655500.0,
            "target_price": 700000.0,
            "stop_price": 620000.0,
            "planned_hold_hours": 48,
            "entry_reason": "x",
            "source_agent": "펀드기획팀장",
        },
    }
    holdings = [{"code": "006400", "name": "삼성SDI", "qty": 1, "cur_price": 615000.0}]
    out = format_thesis_reminder(theses, holdings, now_iso="2026-05-28 16:00:00")
    assert "손절" in out and ("터치" in out or "❗" in out)


def test_format_reminder_empty_when_no_thesis_for_holdings():
    """저장된 thesis 가 보유 종목과 매칭 안 되면 빈 문자열 (외부 매수·기존 잔고 등)."""
    theses = {"005930": {"entry_price": 70000.0, "entry_ts": "...", "target_price": 75000.0,
                          "stop_price": 65000.0, "planned_hold_hours": 48,
                          "entry_reason": "x", "source_agent": "펀드기획팀장"}}
    holdings = [{"code": "006400", "name": "삼성SDI", "qty": 1, "cur_price": 678000.0}]
    out = format_thesis_reminder(theses, holdings, now_iso="2026-05-29 09:00:00")
    assert out == "" or "삼성SDI" not in out  # 매칭 없으면 출력 X


def test_format_reminder_empty_input_returns_empty_string():
    assert format_thesis_reminder({}, [], now_iso="2026-05-29 09:00:00") == ""
