"""_session_holdings — 매도 평가용 보유종목은 세션 시장에 맞게 합쳐져야 한다.

버그 2026-05-22: 사이클이 holdings = kr_holdings() 로만 채워, US_TRADING 세션에서
미국 보유분이 통째로 빠졌다. 그 결과 세션별 필터(_holdings_this_market)가 항상 비어
"보유 종목 없음 — 분석 생략"으로 빠지고, 실제 미국 포지션은 매도 평가를 못 받았다.
이 테스트는 US 세션엔 해외 보유분이 합쳐지고, KR 세션엔 빠지는지 고정한다.
"""
import asyncio

from main_swarm import ArquantOrchestrator


class _FakeBroker:
    def __init__(self, kr, us):
        self._kr = list(kr)
        self._us = list(us)

    async def kr_holdings(self):
        return list(self._kr)

    async def _overseas_holdings(self):
        return list(self._us)


def _orch(broker):
    o = object.__new__(ArquantOrchestrator)  # __init__ 우회 — 무거운 초기화 회피
    o.broker = broker
    return o


_KR = [{"code": "039030", "name": "이오테크닉스", "qty": 1, "pnl_pct": 1.2}]
_US = [{"code": "XOM", "name": "EXXON", "qty": 3, "pnl_pct": -0.5}]


def test_us_session_includes_overseas_holdings():
    o = _orch(_FakeBroker(kr=_KR, us=_US))
    holdings = asyncio.run(o._session_holdings("US_TRADING"))
    codes = {h["code"] for h in holdings}
    assert "XOM" in codes, "US 세션이면 미국 보유분이 매도 평가 대상에 들어와야 한다"
    assert "039030" in codes, "KR 보유분도 그대로 유지 (반대편 자동보유 처리용)"


def test_kr_session_excludes_overseas_holdings():
    o = _orch(_FakeBroker(kr=_KR, us=_US))
    holdings = asyncio.run(o._session_holdings("KR_TRADING"))
    codes = {h["code"] for h in holdings}
    assert codes == {"039030"}, "KR 세션엔 해외 보유분을 합치지 않는다 (US 사이클에서 처리)"
