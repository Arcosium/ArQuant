"""NXT 시간외 창에도 평가곡선 기록이 열리되, 주말/휴장은 닫힌다."""
from datetime import datetime, timezone, timedelta
import main_swarm as m

KST = timezone(timedelta(hours=9))
def _dt(y, mo, d, h, mi): return datetime(y, mo, d, h, mi, tzinfo=KST)

def test_pre_market_weekday_open(monkeypatch):
    monkeypatch.setattr(m, "_market_day_verified_closed", lambda *a, **k: False)
    assert m.is_market_session_now(_dt(2026, 6, 8, 8, 20)) is True   # 월요일 08:20

def test_after_market_weekday_open(monkeypatch):
    monkeypatch.setattr(m, "_market_day_verified_closed", lambda *a, **k: False)
    assert m.is_market_session_now(_dt(2026, 6, 8, 17, 0)) is True

def test_after_market_weekend_closed(monkeypatch):
    monkeypatch.setattr(m, "_market_day_verified_closed", lambda *a, **k: False)
    assert m.is_market_session_now(_dt(2026, 6, 6, 17, 0)) is False   # 토요일

def test_pre_market_holiday_closed(monkeypatch):
    monkeypatch.setattr(m, "_market_day_verified_closed", lambda mkt, dt=None: True)
    assert m.is_market_session_now(_dt(2026, 6, 8, 8, 20)) is False

def test_gap_between_review_and_after_still_closed(monkeypatch):
    monkeypatch.setattr(m, "_market_day_verified_closed", lambda *a, **k: False)
    # 08:50~09:00, 15:30~15:50 등 비거래 구간은 기록 안 함
    assert m.is_market_session_now(_dt(2026, 6, 8, 8, 55)) is False
