"""장 마감 알림(_maybe_market_close_alert) + 실현손익 버킷(_realized_perf_buckets) 수정 회귀 테스트.

사장 지시 2026-05-30:
  ① +0%/+0% 버그: 알림이 performance_kpis 를 uid 없이 호출해 거래내역이 빈 배열로 로드 →
     당일·누적이 항상 0 으로 찍혔다. uid 를 넘겨 실제 실현손익이 반영돼야 한다.
  ② 통화 혼합 버그: 누적/당일 손익이 KR(원) 과 US(달러) 실현손익을 '그대로 더해' 무의미한
     숫자였다. 해외분은 환율로 원화 환산 후 합산해야 한다.
  ③ 비운영일 알림: 한국 휴장일/주말인데도 시간대 기반 세션 전환만으로 '한국 장 마감' 알림이
     발송됐다. 실제 그 시장이 거래일이었을 때만 알림을 보내야 한다.
"""
import asyncio
from datetime import datetime, timezone, timedelta

import pytest

import main_swarm
from main_swarm import ArquantOrchestrator

KST = timezone(timedelta(hours=9))


def _orch(uid=1):
    o = object.__new__(ArquantOrchestrator)  # __init__ 우회 — 무거운 초기화 회피
    o.uid = uid
    o.equity_path = f"data/{uid}/equity_curve.json"
    o._trades_executed = 5
    return o


@pytest.fixture
def cap(monkeypatch):
    events = []

    async def _fake_broadcast(ev, uid=None):
        events.append(ev)

    monkeypatch.setattr(main_swarm, "_broadcast", _fake_broadcast)
    return events


# ── ② 통화 혼합 ──────────────────────────────────────────────────────────────
def test_realized_buckets_converts_usd_to_krw():
    now = datetime(2026, 5, 30, 12, 0, tzinfo=KST)
    trades = [
        {"side": "sell", "ts": "2026-05-30 10:00:00",
         "detail": {"realized_pnl": 1500.0, "cost_basis": 772000.0, "qty": 1, "currency": "KRW"}},
        {"side": "sell", "ts": "2026-05-30 11:00:00",
         "detail": {"realized_pnl": 10.0, "cost_basis": 100.0, "qty": 1, "currency": "USD"}},
    ]
    b = main_swarm._realized_perf_buckets(trades, now, fx=1500.0)
    # KRW 그대로 1500 + USD 10×1500=15000 = 16500 원 (원/달러 그대로 더하지 않음)
    assert b["cumulative_pnl"] == pytest.approx(1500 + 15000)
    # 매입원금도 환산: 772000 + 100×1500=150000 = 922000
    assert b["cumulative_pct"] == pytest.approx(16500 / 922000 * 100)


def test_realized_buckets_krw_only_unchanged():
    """통화 필드 없으면(레거시) 원화로 간주 — 환산하지 않음 (기존 동작 보존)."""
    now = datetime(2026, 5, 30, 12, 0, tzinfo=KST)
    trades = [{"side": "sell", "ts": "2026-05-30 10:00:00",
               "detail": {"realized_pnl": 5000.0, "cost_basis": 100000.0, "qty": 1}}]
    b = main_swarm._realized_perf_buckets(trades, now, fx=1500.0)
    assert b["cumulative_pnl"] == pytest.approx(5000.0)


# ── ① +0% (uid 전달) ─────────────────────────────────────────────────────────
def test_kr_close_alert_passes_uid_and_real_values(cap, monkeypatch):
    o = _orch(uid=1)
    monkeypatch.setattr(main_swarm, "_now_kst", lambda: datetime(2026, 5, 29, 15, 30, tzinfo=KST))  # 금요일=거래일
    seen = {}

    def _fake_kpis(equity_path=None, **kw):
        seen.update(kw)
        return {"today_pct": 1.0, "today_pnl": 100.0, "cumulative_pct": 2.0, "cumulative_pnl": 200.0}

    monkeypatch.setattr(main_swarm, "performance_kpis", _fake_kpis)
    asyncio.run(o._maybe_market_close_alert("KR_TRADING", "KR_CLOSE_REVIEW"))

    mc = [e for e in cap if e.get("type") == "market_close"]
    assert len(mc) == 1, "거래일엔 한국 장 마감 알림이 1건 나가야 한다"
    assert seen.get("uid") == 1, "performance_kpis 에 uid 가 전달돼야 한다 (+0% 버그의 근본원인)"
    assert mc[0]["cumulative_pnl"] == 200.0 and mc[0]["today_pnl"] == 100.0


# ── ③ 비운영일 알림 게이트 ────────────────────────────────────────────────────
def test_kr_close_alert_suppressed_on_weekend(cap, monkeypatch):
    o = _orch()
    monkeypatch.setattr(main_swarm, "_now_kst", lambda: datetime(2026, 5, 30, 15, 30, tzinfo=KST))  # 토요일
    monkeypatch.setattr(main_swarm, "performance_kpis",
                        lambda *a, **k: {"today_pct": 1.0, "today_pnl": 1, "cumulative_pct": 1.0, "cumulative_pnl": 1})
    asyncio.run(o._maybe_market_close_alert("KR_TRADING", "KR_CLOSE_REVIEW"))
    assert [e for e in cap if e.get("type") == "market_close"] == [], "주말엔 한국 장 마감 알림이 나가면 안 된다"


def test_kr_close_alert_suppressed_on_holiday(cap, monkeypatch):
    # 사장 지시 2026-06-03: 하드코딩 휴장일 목록 폐지 → 휴장은 거래량 검증으로 확정한 캐시
    # (_VERIFIED_CLOSED)로만 판정한다. 평일이라도 당일 거래가 없었던 것으로 확정되면 마감 알림 금지.
    o = _orch()
    # 2026-06-03 지방선거 임시공휴일 (수요일·평일이지만 휴장) — 옛 하드코딩 목록엔 없던 날.
    monkeypatch.setattr(main_swarm, "_now_kst", lambda: datetime(2026, 6, 3, 15, 30, tzinfo=KST))
    monkeypatch.setattr(main_swarm, "performance_kpis",
                        lambda *a, **k: {"today_pct": 1.0, "today_pnl": 1, "cumulative_pct": 1.0, "cumulative_pnl": 1})
    main_swarm._VERIFIED_TRADED.clear(); main_swarm._VERIFIED_CLOSED.clear()
    main_swarm._VERIFIED_CLOSED.add("KR:2026-06-03")  # 개장 후 당일 봉 없음 → 휴장 확정됨
    try:
        asyncio.run(o._maybe_market_close_alert("KR_TRADING", "KR_CLOSE_REVIEW"))
        assert [e for e in cap if e.get("type") == "market_close"] == [], "휴장 확정일엔 한국 장 마감 알림이 나가면 안 된다"
    finally:
        main_swarm._VERIFIED_CLOSED.clear()


def test_us_close_alert_sent_for_friday_session(cap, monkeypatch):
    """금요일 밤(KST) US 세션은 토요일 새벽 05:00 에 마감 — 정상 거래일이므로 알림 발송."""
    o = _orch()
    monkeypatch.setattr(main_swarm, "_now_kst", lambda: datetime(2026, 5, 30, 5, 0, tzinfo=KST))  # 토 05:00 = 금 US장 마감
    monkeypatch.setattr(main_swarm, "performance_kpis",
                        lambda *a, **k: {"today_pct": 1.0, "today_pnl": 1, "cumulative_pct": 1.0, "cumulative_pnl": 1})
    asyncio.run(o._maybe_market_close_alert("US_TRADING", "OFF_HOURS"))
    assert len([e for e in cap if e.get("type") == "market_close"]) == 1, "금요일 US 세션 마감 알림은 정상 발송돼야 한다"


def test_us_close_alert_suppressed_for_weekend_session(cap, monkeypatch):
    """토요일 밤(KST) 비거래 세션이 일요일 새벽 05:00 에 '마감'으로 잡혀도 알림 금지."""
    o = _orch()
    monkeypatch.setattr(main_swarm, "_now_kst", lambda: datetime(2026, 5, 31, 5, 0, tzinfo=KST))  # 일 05:00 = 토 야간(주말)
    monkeypatch.setattr(main_swarm, "performance_kpis",
                        lambda *a, **k: {"today_pct": 1.0, "today_pnl": 1, "cumulative_pct": 1.0, "cumulative_pnl": 1})
    asyncio.run(o._maybe_market_close_alert("US_TRADING", "OFF_HOURS"))
    assert [e for e in cap if e.get("type") == "market_close"] == [], "주말 US 비거래일엔 알림이 나가면 안 된다"
