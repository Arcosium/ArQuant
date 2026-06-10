from agents.specialists import format_sleeve_thesis_reminder


def test_reminder_strong_advisory():
    theses = {"148070": {"entry_ts": "2026-06-08 10:00:00", "entry_price": 50000.0,
                         "planned_hold_hours": 120, "entry_reason": "금리 고점 베팅",
                         "source_agent": "채권운용실장"}}
    holdings = [{"code": "148070", "name": "KOSEF 국고채10년", "cur_price": 50500.0}]
    out = format_sleeve_thesis_reminder(theses, holdings, now_iso="2026-06-08 14:00:00",
                                        manager_name="채권운용실장")
    assert "강력 권고" in out
    assert "채권운용실장" in out      # 포트폴리오기획팀장→채권운용실장
    assert "148070" in out or "국고채" in out
    assert "120" in out              # 계획 보유기간


def test_commodity_manager_name():
    theses = {"GLD": {"entry_ts": "2026-06-08 10:00:00", "entry_price": 200.0,
                      "planned_hold_hours": 72, "entry_reason": "안전자산 선호"}}
    holdings = [{"code": "GLD", "name": "SPDR Gold", "cur_price": 205.0}]
    out = format_sleeve_thesis_reminder(theses, holdings, now_iso="2026-06-08 14:00:00",
                                        manager_name="원자재운용실장")
    assert "원자재운용실장" in out


def test_empty_when_no_match():
    out = format_sleeve_thesis_reminder({}, [], now_iso="2026-06-08 14:00:00")
    assert out == ""
