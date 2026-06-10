"""사이클 트리거 앵커를 벽시계 시(hour)로 — 진입 즉시 발화 안 함, :00에 발화."""
from datetime import datetime, timezone, timedelta
import main_swarm

KST = timezone(timedelta(hours=9))

def test_current_hour_key_floors_to_hour(monkeypatch):
    monkeypatch.setattr(main_swarm, "_now_kst",
                        lambda: datetime(2026, 6, 8, 10, 37, 12, tzinfo=KST))
    assert main_swarm._current_hour_key() == datetime(2026, 6, 8, 10, 0, 0, tzinfo=KST)
    assert main_swarm._current_hour_key_str() == "2026-06-08 10"

def _orch():
    o = main_swarm.ArquantOrchestrator.__new__(main_swarm.ArquantOrchestrator)
    return o

def test_no_periodic_within_same_hour(monkeypatch):
    monkeypatch.setattr(main_swarm, "_now_kst",
                        lambda: datetime(2026, 6, 8, 10, 37, 0, tzinfo=KST))
    o = _orch()
    o._last_cycle_hour_key = main_swarm._current_hour_key()
    monkeypatch.setattr(main_swarm, "_now_kst",
                        lambda: datetime(2026, 6, 8, 10, 59, 0, tzinfo=KST))
    assert o._should_run_periodic() is False

def test_periodic_fires_on_hour_rollover(monkeypatch):
    monkeypatch.setattr(main_swarm, "_now_kst",
                        lambda: datetime(2026, 6, 8, 10, 37, 0, tzinfo=KST))
    o = _orch()
    o._last_cycle_hour_key = main_swarm._current_hour_key()
    monkeypatch.setattr(main_swarm, "_now_kst",
                        lambda: datetime(2026, 6, 8, 11, 0, 1, tzinfo=KST))
    assert o._should_run_periodic() is True

def test_restart_invariant_first_fire_at_next_hour(monkeypatch):
    monkeypatch.setattr(main_swarm, "_now_kst",
                        lambda: datetime(2026, 6, 8, 8, 37, 0, tzinfo=KST))
    o = _orch()
    o._last_cycle_hour_key = main_swarm._current_hour_key()
    monkeypatch.setattr(main_swarm, "_now_kst",
                        lambda: datetime(2026, 6, 8, 8, 59, 59, tzinfo=KST))
    assert o._should_run_periodic() is False
    monkeypatch.setattr(main_swarm, "_now_kst",
                        lambda: datetime(2026, 6, 8, 9, 0, 0, tzinfo=KST))
    assert o._should_run_periodic() is True
