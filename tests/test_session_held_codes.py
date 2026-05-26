"""_codes_for_session — 계량분석/매도평가 대상 보유 종목코드는 세션 시장과 일치해야 한다.

버그 2026-05-22: held_kr = [...if c.isdigit() and len(c)==6] 으로 KR(6자리)만 골라
analysis_codes 에 넣어, US 세션에서 보유 미국종목(MSFT 등)이 계량분석을 통째로 못 받았다.
US 세션→US티커, KR 세션→6자리, 장외→전체로 골라야 한다(KR/US 비대칭 방지).
"""
from main_swarm import _codes_for_session

_H = [{"code": "039030", "qty": 1}, {"code": "MSFT", "qty": 1}, {"code": "QUBT", "qty": 49}]


def test_us_session_keeps_us_tickers_only():
    assert set(_codes_for_session(_H, "US_TRADING")) == {"MSFT", "QUBT"}


def test_kr_session_keeps_kr_codes_only():
    assert _codes_for_session(_H, "KR_TRADING") == ["039030"]


def test_off_hours_keeps_all():
    assert set(_codes_for_session(_H, "OFF_HOURS")) == {"039030", "MSFT", "QUBT"}


def test_blank_codes_dropped():
    assert _codes_for_session([{"code": ""}, {"code": "MSFT"}], "US_TRADING") == ["MSFT"]
