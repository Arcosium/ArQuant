"""사장 지시 2026-05-28: equity_curve 가 비어도 '실계좌 총평가' 는 KIS snapshot 으로 폴백 표시.

배경:
  자동 백업 도구가 재시작 시 data/<uid>/equity_curve.json 을 가끔 wipe → 수익률 탭 '현재 평가액'
  ('-' 표시)이 안 떠 사장님이 자산 상태를 모름. 곡선이 비어도 broker 가 KIS 직접 호출로 받는
  실시간 총평가는 항상 표시돼야 한다.

요구 동작:
  - performance_kpis 가 has_equity=False 라도 호출부(/api/performance)는 broker.portfolio_holdings
    에서 buying_power.total_eval 을 끌어와 result["current"] 에 박는다.
  - has_equity 자체는 False 유지 (수익률 KPI 들은 곡선이 있어야 의미가 있으니).
  - broker 호출 실패하면 current 누락(현 동작 유지) — fail-soft.

이 테스트는 server.app._performance_with_current_fallback 헬퍼를 검증 (라우트는 헬퍼 호출).
"""
import asyncio
import pytest


def _perf_empty():
    return {"has_equity": False, "has_trades": False}


def _perf_with_eq(current):
    return {"has_equity": True, "current": current, "has_trades": False}


class _FakeBroker:
    def __init__(self, bp_total_eval, ok=True):
        self._t = bp_total_eval
        self._ok = ok
    async def portfolio_holdings(self):
        if not self._ok:
            raise RuntimeError("KIS down")
        return {"buying_power": {"total_eval": self._t, "cash": 0, "pnl_ratio": 0.0, "ok": True},
                "holdings": []}


def test_fallback_adds_current_when_equity_empty():
    from server.app import _attach_current_fallback
    base = _perf_empty()
    out = asyncio.run(_attach_current_fallback(base, _FakeBroker(8710678.0)))
    assert out["current"] == pytest.approx(8710678.0)
    assert out["has_equity"] is False, "has_equity 는 False 그대로(곡선 없음)"


def test_fallback_does_not_override_existing_current():
    """equity_curve 가 있어 has_equity=True 면 broker 폴백 호출 안 함 (current 그대로 유지)."""
    from server.app import _attach_current_fallback
    base = _perf_with_eq(8000000.0)
    out = asyncio.run(_attach_current_fallback(base, _FakeBroker(9999999.0)))
    assert out["current"] == 8000000.0, "곡선 기반 current 가 우선"


def test_fallback_silently_skips_on_broker_error():
    """broker 호출 실패해도 예외 던지지 말고 base 를 그대로 반환 (fail-soft)."""
    from server.app import _attach_current_fallback
    base = _perf_empty()
    out = asyncio.run(_attach_current_fallback(base, _FakeBroker(0, ok=False)))
    assert "current" not in out
    assert out["has_equity"] is False


def test_fallback_ignores_zero_or_negative_total_eval():
    """KIS가 0/음수 반환(글리치)이면 current 박지 않음 — 호출자 화면이 '-' 유지."""
    from server.app import _attach_current_fallback
    base = _perf_empty()
    out = asyncio.run(_attach_current_fallback(base, _FakeBroker(0.0)))
    assert "current" not in out
