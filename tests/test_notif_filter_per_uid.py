"""모바일 알림 필터 회귀 — per-uid 송신(send_to_uid)도 알림설정을 존중해야 한다.

버그(Phase 2 멀티테넌트 회귀): cycle_complete 등 per-uid 사이클 이벤트는 _emit→_broadcast(uid)→
ws_mgr.send_to_uid 로 라우팅되는데, send_to_uid 가 _should_send 필터를 거치지 않아 모바일이
'사이클 완료' 알림을 껐는데도 계속 받았다. broadcast(전체) 경로만 필터를 적용하던 비대칭.
웹은 설정과 무관하게 전부 수신(통신로그), 모바일은 켜진 종류만 수신해야 한다.
"""
import asyncio

import runtime
from server.app import WS


class _FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, m):
        self.sent.append(m)


def _mk(monkeypatch, settings):
    monkeypatch.setattr(runtime, "notif_settings", lambda uid=None: dict(settings))
    ws = WS()
    web, mob = _FakeWS(), _FakeWS()
    ws.conns = [web, mob]
    ws.meta = {web: {"uid": 1, "client": "web"}, mob: {"uid": 1, "client": "mobile"}}
    return ws, web, mob


def test_send_to_uid_suppresses_cycle_for_mobile_when_off(monkeypatch):
    ws, web, mob = _mk(monkeypatch, {"order_submitted": True, "trade": True,
                                     "cycle": False, "market_close": True})
    msg = {"type": "cycle_complete", "trades_total": 3}
    asyncio.run(ws.send_to_uid(1, msg))
    assert msg in web.sent, "웹 대시보드는 설정 무관 전부 수신(통신로그)"
    assert mob.sent == [], "모바일은 cycle=False 면 '사이클 완료'를 받지 않아야 한다"


def test_send_to_uid_delivers_enabled_type_to_mobile(monkeypatch):
    ws, web, mob = _mk(monkeypatch, {"order_submitted": True, "trade": True,
                                     "cycle": False, "market_close": True})
    msg = {"type": "trade_executed", "ticker": "005930", "qty": 3}
    asyncio.run(ws.send_to_uid(1, msg))
    assert msg in mob.sent, "trade=True 면 체결 알림은 모바일도 수신"
    assert msg in web.sent
