"""수익률 환각 수정 (사장 지시 2026-06-11) — 원장 시리즈 우선 KPI/곡선 + US 체결 영속 필드.

uid1 사례: KIS 집계 곡선이 7.2M(해외포함) → 4.1M(해외 증발)로 떨어지며 누적 -43%/MDD -59%
환각 표시. ledger_eval 포인트가 있으면 그 시리즈만으로 KPI/곡선을 계산해야 한다.
"""
import asyncio

import pytest

import main_swarm
from main_swarm import _equity_points, performance_kpis, record_equity


def _pt(ts, total, ledger=None, **kw):
    p = {"ts": ts, "total_eval": total, "cash": 0.0, "pnl_ratio": 0.0, "src": "poll", **kw}
    if ledger is not None:
        p["ledger_eval"] = ledger
    return p


def test_equity_points_prefer_ledger_series_only():
    """ledger_eval 포인트가 있으면 KIS(total_eval) 포인트는 시리즈에서 제외된다."""
    raw = [
        _pt("2026-06-10 17:04:00", 7_227_809),                       # KIS 환각 베이스라인
        _pt("2026-06-10 22:44:22", 4_670_080),                       # 해외평가 증발
        _pt("2026-06-11 09:10:00", 4_101_633, ledger=6_900_000),
        _pt("2026-06-11 09:15:00", 4_101_633, ledger=6_905_000),
    ]
    pts = _equity_points(raw)
    assert len(pts) == 2
    assert [v for _, v, _ in pts] == [6_900_000, 6_905_000]
    assert all(p["adj_total_eval"] == v for _, v, p in pts)


def test_equity_points_legacy_when_no_ledger():
    """ledger 포인트가 하나도 없으면 기존 KIS 곡선 로직 그대로 (회귀 방지)."""
    raw = [_pt("2026-06-10 17:04:00", 1_000_000), _pt("2026-06-10 17:10:00", 1_010_000)]
    pts = _equity_points(raw)
    assert [v for _, v, _ in pts] == [1_000_000, 1_010_000]


def test_kpis_use_ledger_series():
    """KPI 상단(평가금액 변동·MDD·current)이 원장 시리즈 기준으로 계산된다 — -43% 환각 차단."""
    raw = [
        _pt("2026-06-10 17:04:00", 7_227_809),
        _pt("2026-06-10 22:44:22", 4_670_080),
        _pt("2026-06-11 09:10:00", 4_101_633, ledger=6_900_000),
        _pt("2026-06-11 09:15:00", 4_101_633, ledger=6_969_000),
    ]
    k = performance_kpis(raw_equity=raw, trades=[])
    assert k["current"] == 6_969_000
    assert k["start"] == 6_900_000
    assert k["eq_all_pct"] == pytest.approx(1.0)        # +69,000 / 6,900,000
    assert k["mdd_pct"] == pytest.approx(0.0)           # 원장 시리즈엔 낙폭 없음


def test_record_equity_persists_ledger_eval(tmp_path):
    ep = tmp_path / "equity_curve.json"
    record_equity(ep, {"ok": True, "total_eval": 4_000_000, "cash": 1_000_000, "pnl_ratio": 0.0},
                  "poll", ledger_eval=6_900_000.0)
    import json
    data = json.loads(ep.read_text(encoding="utf-8"))
    assert data[-1]["ledger_eval"] == 6_900_000.0
    # ledger_eval 미지정/0 이면 필드 자체가 없다 (폴백 시 KIS 곡선 유지)
    ep2 = tmp_path / "e2.json"
    record_equity(ep2, {"ok": True, "total_eval": 1.0, "cash": 0.0, "pnl_ratio": 0.0}, "poll")
    assert "ledger_eval" not in json.loads(ep2.read_text(encoding="utf-8"))[-1]


def test_poll_confirm_emit_carries_price_fields(monkeypatch):
    """US 폴링 확정 체결의 '영속' 이벤트에 fill_price/avg_cost/fill_currency 가 실려야 한다.
    (기존엔 in-memory _trade_log 에만 있어 실현손익 KPI 가 0 으로 날조됐다 — uid1/2 사례.)"""
    from main_swarm import ArquantOrchestrator

    class _B:
        _acct_snap = None
        async def kr_holdings(self): return []
        async def _overseas_holdings(self):
            return [{"code": "XOM", "qty": 0, "avg_price": 100.0, "cur_price": 0}]
        async def us_last_price(self, tk): return 101.5

    monkeypatch.setattr(main_swarm, "_REVERIFY_DELAY_SEC", 0)
    monkeypatch.setattr(main_swarm, "_POLL_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(main_swarm, "get_current_session", lambda: "US_TRADING")
    captured = []
    async def _fake_broadcast(ev, uid=None): captured.append(ev)
    monkeypatch.setattr(main_swarm, "_broadcast", _fake_broadcast)

    o = object.__new__(ArquantOrchestrator)
    o.broker = _B(); o.uid = 999999; o._trades_executed = 0
    o._trade_log = []; o._stop_event = asyncio.Event()
    baseline = [{"code": "XOM", "qty": 3, "avg_price": 100.0, "cur_price": 100.5}]
    asyncio.run(o._poll_fills_until_confirmed(
        [{"ticker": "XOM", "side": "sell", "qty": 3}], baseline_holdings=baseline))

    fills = [e for e in captured if e.get("type") == "trade_executed"]
    assert len(fills) == 1
    ev = fills[0]
    assert ev["fill_currency"] == "USD"
    assert ev["avg_cost"] == pytest.approx(100.0)       # 매도 직전 평단 (실현손익의 권위 기준)
    assert ev["fill_price"] and ev["fill_price"] > 0
