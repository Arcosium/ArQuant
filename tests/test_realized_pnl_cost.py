"""실현손익 거래비용 모델 + 체결기반 수익률 KPI (사장 지시 2026-05-27).

- 국내(KR) 주식: 거래비용 미적용.
- 해외(US) 주식: 매수·매도 각 leg 0.3% 거래비용 반영.
- 수익률(누적/오늘/주/월)은 잔고 스냅샷이 아닌 '체결 실현손익(비용반영)' 합산 기반 — 글리치에 안 흔들림.
"""
from datetime import datetime, timezone, timedelta
import pytest
import main_swarm as ms

KST = timezone(timedelta(hours=9))


def test_kr_realized_pnl_has_no_cost():
    # 국내: (매도 11,000 - 매수 10,000) × 10주 = 10,000원, 비용 0
    pnl = ms._net_realized_pnl(buy_price=10_000, sell_price=11_000, qty=10, is_us=False)
    assert pnl == pytest.approx(10_000.0, abs=1e-6)


def test_us_realized_pnl_applies_0_3pct_each_leg():
    # 해외: gross (110-100)×10 = 100. 비용 = 0.003×(100+110)×10 = 6.3 → net 93.7
    pnl = ms._net_realized_pnl(buy_price=100.0, sell_price=110.0, qty=10, is_us=True)
    assert pnl == pytest.approx(100.0 - 6.3, abs=1e-6)


def test_us_cost_can_flip_marginal_trade_negative():
    # 거의 동가 매도여도 해외 비용 때문에 실현손익이 (-)가 될 수 있다
    pnl = ms._net_realized_pnl(buy_price=100.0, sell_price=100.2, qty=10, is_us=True)
    gross = (100.2 - 100.0) * 10  # 2.0
    cost = 0.003 * (100.0 + 100.2) * 10  # ≈ 6.006
    assert pnl == pytest.approx(gross - cost, abs=1e-6)
    assert pnl < 0


def _sell(ts, realized, basis_per, qty, currency="KRW"):
    return {"side": "sell", "ts": ts,
            "detail": {"realized_pnl": realized, "cost_basis": basis_per, "qty": qty,
                       "currency": currency}}


def test_kpi_cumulative_is_realized_based_not_equity():
    # 잔고 곡선이 글리치로 튀어도(2백만→8백만), 누적 수익률은 체결 실현손익만 따른다.
    raw = [{"ts": "2026-05-20 10:00:00", "total_eval": 2_000_000, "cash": 0, "pnl_ratio": 0},
           {"ts": "2026-05-20 10:05:00", "total_eval": 8_000_000, "cash": 0, "pnl_ratio": 0}]
    trades = [_sell("2026-05-20 11:00:00", realized=5_000.0, basis_per=10_000, qty=10)]
    now = datetime(2026, 5, 20, 15, 0, tzinfo=KST)
    k = ms.performance_kpis(raw_equity=raw, trades=trades, now=now)
    # 실현손익 5,000원 / 매입원금 100,000원 = +5%  (잔고곡선 +300%와 무관)
    assert k["cumulative_pnl"] == pytest.approx(5_000.0, abs=1e-6)
    assert k["cumulative_pct"] == pytest.approx(5.0, abs=1e-6)


def test_kpi_today_week_month_bucketing():
    trades = [
        _sell("2026-05-01 10:00:00", realized=1_000.0, basis_per=10_000, qty=1),  # 이번달, 이전주
        _sell("2026-05-25 10:00:00", realized=2_000.0, basis_per=10_000, qty=1),  # 이번주(월), 이전날
        _sell("2026-05-27 10:00:00", realized=3_000.0, basis_per=10_000, qty=1),  # 오늘
    ]
    now = datetime(2026, 5, 27, 15, 0, tzinfo=KST)  # 수요일
    k = ms.performance_kpis(raw_equity=[], trades=trades, now=now)
    assert k["cumulative_pnl"] == pytest.approx(6_000.0, abs=1e-6)
    assert k["today_pnl"] == pytest.approx(3_000.0, abs=1e-6)
    assert k["week_pnl"] == pytest.approx(5_000.0, abs=1e-6)   # 05-25(월) + 05-27
    assert k["month_pnl"] == pytest.approx(6_000.0, abs=1e-6)


def test_kpi_no_trades_no_realized():
    k = ms.performance_kpis(raw_equity=[], trades=[], now=datetime(2026, 5, 27, tzinfo=KST))
    assert k["cumulative_pnl"] == 0.0
    assert k["cumulative_pct"] == 0.0
