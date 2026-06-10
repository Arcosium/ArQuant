"""NXT 시간외 세션(프리/애프터) 시각→세션 매핑."""
from datetime import datetime, timezone, timedelta
import main_swarm

KST = timezone(timedelta(hours=9))

def _at(h, m, monkeypatch):
    monkeypatch.setattr(main_swarm, "_now_kst", lambda: datetime(2026, 6, 8, h, m, tzinfo=KST))

def test_pre_market_window(monkeypatch):
    _at(8, 20, monkeypatch)
    assert main_swarm.get_current_session() == "KR_PRE_MARKET"

def test_pre_market_gap_before_open(monkeypatch):
    _at(8, 55, monkeypatch)   # 08:50~09:00 사이 = 장외
    assert main_swarm.get_current_session() == "OFF_HOURS"

def test_regular_session_unchanged(monkeypatch):
    _at(10, 0, monkeypatch)
    assert main_swarm.get_current_session() == "KR_TRADING"

def test_close_review_unchanged(monkeypatch):
    _at(15, 40, monkeypatch)
    assert main_swarm.get_current_session() == "KR_CLOSE_REVIEW"

def test_after_market_window(monkeypatch):
    _at(17, 0, monkeypatch)
    assert main_swarm.get_current_session() == "KR_AFTER_MARKET"

def test_after_market_starts_after_review(monkeypatch):
    _at(15, 48, monkeypatch)   # 리뷰 구간 — 아직 애프터 아님
    assert main_swarm.get_current_session() == "KR_CLOSE_REVIEW"

def test_after_market_ends_2000(monkeypatch):
    _at(20, 1, monkeypatch)
    assert main_swarm.get_current_session() == "OFF_HOURS"

def test_pre_market_start_boundary_inclusive(monkeypatch):
    _at(8, 0, monkeypatch)    # 08:00 정각 = 프리마켓 시작 포함 경계
    assert main_swarm.get_current_session() == "KR_PRE_MARKET"

def test_after_market_start_boundary_inclusive(monkeypatch):
    _at(15, 50, monkeypatch)  # 15:50 정각 = 리뷰 종료(exclusive)·애프터 시작(inclusive)
    assert main_swarm.get_current_session() == "KR_AFTER_MARKET"
