"""개장 5분 후 KIS 실시세로 '오늘 개장' 확인 (사장 지시 2026-05-24).

휴일을 하드코딩 목록으로만 추정하지 말고, 주말은 그대로 자명 스킵하되 그 외엔 개장 5분 후
KIS 실데이터(지수/종목 일봉의 최신 봉 날짜)로 '오늘 실제 거래중인지' 한 번 확인한다.
거래 누락 방지가 핵심이므로: 실확인이 '개장'이면 하드코딩 휴장일보다 우선하고,
실확인 불가/시기상조면 기존 하드코딩 로직으로 폴백한다(새로운 거짓 '휴장' 스킵 금지).
"""
import asyncio
from datetime import datetime

import pytest

import main_swarm
from main_swarm import ArquantOrchestrator


class _FakeBroker:
    def __init__(self, kr_rows=None, us_rows=None, raise_kr=False, raise_us=False):
        self.kr_rows, self.us_rows = kr_rows, us_rows
        self.raise_kr, self.raise_us = raise_kr, raise_us
        self.kr_calls = self.us_calls = 0

    async def kr_index_daily(self, index_code="0001", days=5):
        self.kr_calls += 1
        if self.raise_kr:
            raise RuntimeError("KIS down")
        return list(self.kr_rows or [])

    async def us_daily_chart(self, ticker, days=5):
        self.us_calls += 1
        if self.raise_us:
            raise RuntimeError("KIS down")
        return list(self.us_rows or [])


def _orch(broker):
    o = object.__new__(ArquantOrchestrator)  # __init__ 우회
    o.broker = broker
    o._mkt_open_verified = {}
    return o


@pytest.fixture
def at(monkeypatch):
    def _set(dt):
        monkeypatch.setattr(main_swarm, "_now_kst", lambda: dt)
    return _set


def _row(d):
    return {"date": d, "close": 100.0}


# 날짜 기준: 2026-05-21 = 목(평일), 2026-05-23 = 토(주말)
WEEKDAY = "2026-05-21"
PREV = "2026-05-20"


# ── 주말: KIS 호출 없이 자명 스킵 ───────────────────────────────────────────
def test_weekend_kr_skips_without_api(at):
    at(datetime(2026, 5, 23, 9, 10))  # 토요일 09:10
    b = _FakeBroker(kr_rows=[_row("2026-05-22")])
    closed, why = asyncio.run(_orch(b)._market_closed_today("KR_TRADING"))
    assert closed and "주말" in why
    assert b.kr_calls == 0  # 주말이면 실시세 확인 안 함


def test_weekend_us_skips_without_api(at):
    at(datetime(2026, 5, 23, 23, 10))  # 토요일 밤 = 미 동부 토 → 주말
    b = _FakeBroker(us_rows=[_row("2026-05-22")])
    closed, why = asyncio.run(_orch(b)._market_closed_today("US_TRADING"))
    assert closed and "주말" in why
    assert b.us_calls == 0


# ── 개장 5분 후 실확인: 당일 봉 있으면 개장 ─────────────────────────────────
def test_kr_open_when_today_bar_present(at):
    at(datetime(2026, 5, 21, 9, 10))  # 목 09:10 (개장 +10분)
    b = _FakeBroker(kr_rows=[_row(PREV), _row(WEEKDAY)])
    o = _orch(b)
    assert asyncio.run(o._verify_market_open("KR_TRADING")) is True
    closed, why = asyncio.run(o._market_closed_today("KR_TRADING"))
    assert closed is False and why == ""


def test_kr_closed_when_today_bar_missing(at):
    at(datetime(2026, 5, 21, 9, 10))
    b = _FakeBroker(kr_rows=[_row("2026-05-19"), _row(PREV)])  # 최신이 어제
    closed, why = asyncio.run(_orch(b)._market_closed_today("KR_TRADING"))
    assert closed and "실거래 확인" in why


# ── 실확인 '개장'은 하드코딩 휴장일보다 우선 (거짓 휴장 → 거래 누락 방지) ──
def test_real_open_overrides_hardcoded_holiday(at, monkeypatch):
    at(datetime(2026, 5, 21, 9, 10))
    monkeypatch.setattr(main_swarm, "KR_MARKET_HOLIDAYS", {WEEKDAY})  # 목록상 휴장
    b = _FakeBroker(kr_rows=[_row(WEEKDAY)])  # 그러나 실데이터는 거래중
    closed, why = asyncio.run(_orch(b)._market_closed_today("KR_TRADING"))
    assert closed is False  # 실확인이 우선


# ── 실확인 불가 → 하드코딩 폴백 ─────────────────────────────────────────────
def test_inconclusive_falls_back_to_holiday_list(at, monkeypatch):
    at(datetime(2026, 5, 21, 9, 10))
    monkeypatch.setattr(main_swarm, "KR_MARKET_HOLIDAYS", {WEEKDAY})
    b = _FakeBroker(raise_kr=True)  # 실시세 확인 실패
    assert asyncio.run(_orch(b)._verify_market_open("KR_TRADING")) is None
    closed, why = asyncio.run(_orch(b)._market_closed_today("KR_TRADING"))
    assert closed and "KR 휴장일" in why  # 폴백 발동


def test_inconclusive_normal_weekday_proceeds(at, monkeypatch):
    at(datetime(2026, 5, 21, 9, 10))
    monkeypatch.setattr(main_swarm, "KR_MARKET_HOLIDAYS", set())  # 휴장일 아님
    b = _FakeBroker(raise_kr=True)
    closed, why = asyncio.run(_orch(b)._market_closed_today("KR_TRADING"))
    assert closed is False  # 폴백도 '평일 거래일' → 진행


# ── 시기상조(개장 5분 전): 실확인 보류 ──────────────────────────────────────
def test_before_open_plus_5_is_inconclusive(at):
    at(datetime(2026, 5, 21, 9, 2))  # 개장 +2분 (5분 미만)
    b = _FakeBroker(kr_rows=[_row(PREV)])
    assert asyncio.run(_orch(b)._verify_market_open("KR_TRADING")) is None
    assert b.kr_calls == 0  # 시기상조면 KIS 호출조차 안 함


def test_kr_pre_market_is_inconclusive(at):
    at(datetime(2026, 5, 21, 8, 55))  # 프리마켓 — 당일 봉 아직 없음
    b = _FakeBroker(kr_rows=[_row(PREV)])
    assert asyncio.run(_orch(b)._verify_market_open("KR_PRE_MARKET")) is None


# ── US 야간 세션: 당일 봉 있으면 개장 ───────────────────────────────────────
def test_us_open_evening_today_bar(at):
    at(datetime(2026, 5, 21, 23, 10))  # 목 23:10 KST = 미 동부 목 → US 거래일=오늘
    b = _FakeBroker(us_rows=[_row(PREV), _row(WEEKDAY)])
    o = _orch(b)
    assert asyncio.run(o._verify_market_open("US_TRADING")) is True
    closed, _ = asyncio.run(o._market_closed_today("US_TRADING"))
    assert closed is False


def test_us_after_midnight_uses_prev_day(at):
    # KST 금 03:00 = 미 동부 목 14:00 → US 거래일 = 전날(목)
    at(datetime(2026, 5, 22, 3, 0))
    b = _FakeBroker(us_rows=[_row("2026-05-20"), _row(WEEKDAY)])  # 최신 봉 = 목
    assert asyncio.run(_orch(b)._verify_market_open("US_TRADING")) is True


# ── '개장 확정'만 캐시: 휴장/불가는 재확인 (데이터 지연 자기 교정) ──────────
def test_only_open_is_cached(at):
    at(datetime(2026, 5, 21, 9, 10))
    b = _FakeBroker(kr_rows=[_row(WEEKDAY)])
    o = _orch(b)
    assert asyncio.run(o._verify_market_open("KR_TRADING")) is True
    assert asyncio.run(o._verify_market_open("KR_TRADING")) is True
    assert b.kr_calls == 1  # 두 번째는 캐시 hit → 추가 호출 없음


def test_closed_is_not_cached(at):
    at(datetime(2026, 5, 21, 9, 10))
    b = _FakeBroker(kr_rows=[_row(PREV)])  # 당일 봉 없음 → 휴장 추정(캐시 안 함)
    o = _orch(b)
    assert asyncio.run(o._verify_market_open("KR_TRADING")) is False
    assert asyncio.run(o._verify_market_open("KR_TRADING")) is False
    assert b.kr_calls == 2  # 매번 재확인 (지연 자기 교정 여지)
