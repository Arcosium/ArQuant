"""수익률 탭 KPI 계산 — 누적·오늘·주·월 수익, MDD(낙폭), 승률·평균 보유일.

equity_curve 의 입출금 보정값(adj total) 기준. raw_equity/trades/now 를 주입해 순수 계산 검증.
"""
from datetime import datetime, timezone, timedelta

import pytest

import main_swarm as ms

KST = timezone(timedelta(hours=9))


def _pt(day, hhmm, total, ext=0.0):
    return {"ts": f"2026-05-{day:02d} {hhmm}:00", "total_eval": total, "cash": 0,
            "pnl_ratio": 0.0, "external_flow_cum": ext}


def test_empty_equity_returns_no_data():
    k = ms.performance_kpis(raw_equity=[], trades=[])
    assert k["has_equity"] is False
    assert k["has_trades"] is False


def test_cumulative_and_mdd():
    # 100만 → 90만(낙폭) → 110만
    raw = [_pt(18, "10:00", 1_000_000), _pt(19, "10:00", 900_000), _pt(20, "10:00", 1_100_000)]
    now = datetime(2026, 5, 20, 15, 0, tzinfo=KST)
    k = ms.performance_kpis(raw_equity=raw, trades=[], now=now)
    assert k["has_equity"] is True
    assert k["current"] == 1_100_000
    assert k["cumulative_pnl"] == 100_000
    assert k["cumulative_pct"] == pytest.approx(10.0, abs=1e-6)
    # 최저점 90만이 직전 고점 100만 대비 -10% → MDD -10%
    assert k["mdd_pct"] == pytest.approx(-10.0, abs=1e-6)


def test_today_pnl_uses_prev_day_close_as_base():
    # 어제 마감 100만, 오늘 105만 → 오늘 +5만(+5%)
    raw = [_pt(19, "14:00", 1_000_000), _pt(20, "10:00", 1_050_000)]
    now = datetime(2026, 5, 20, 11, 0, tzinfo=KST)
    k = ms.performance_kpis(raw_equity=raw, trades=[], now=now)
    assert k["today_pnl"] == pytest.approx(50_000, abs=1e-6)
    assert k["today_pct"] == pytest.approx(5.0, abs=1e-6)


def test_external_flow_excluded_from_pnl():
    # total 100만 → 200만 이지만 100만은 입금(external_flow_cum) → 보정값은 100만으로 변동 없음
    raw = [_pt(19, "10:00", 1_000_000, ext=0.0), _pt(20, "10:00", 2_000_000, ext=1_000_000)]
    now = datetime(2026, 5, 20, 12, 0, tzinfo=KST)
    k = ms.performance_kpis(raw_equity=raw, trades=[], now=now)
    assert k["cumulative_pnl"] == pytest.approx(0.0, abs=1e-6)


def test_win_rate_and_hold_days_from_trades():
    trades = [
        {"side": "sell", "ts": "2026-05-20 10:00:00",
         "detail": {"realized_pnl": 5000.0,
                    "matched": [{"buy_ts": "2026-05-18 10:00:00", "buy_qty": 1}]}},
        {"side": "sell", "ts": "2026-05-20 11:00:00",
         "detail": {"realized_pnl": -2000.0,
                    "matched": [{"buy_ts": "2026-05-19 11:00:00", "buy_qty": 1}]}},
        {"side": "buy", "ts": "2026-05-20 09:00:00", "detail": {"buy_price": 100}},
    ]
    k = ms.performance_kpis(raw_equity=[], trades=trades)
    assert k["has_trades"] is True
    assert k["sell_count"] == 2
    assert k["win_count"] == 1
    assert k["win_rate_pct"] == pytest.approx(50.0, abs=1e-6)
    # 보유일: 2일(05-18→05-20) 과 1일(05-19→05-20) 평균 = 1.5일
    assert k["avg_hold_days"] == pytest.approx(1.5, abs=1e-6)


def test_sell_without_pnl_is_ignored():
    trades = [{"side": "sell", "ts": "2026-05-20 10:00:00", "detail": {"unfilled": True}}]
    k = ms.performance_kpis(raw_equity=[], trades=trades)
    assert k["has_trades"] is False
