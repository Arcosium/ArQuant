"""모바일 알림 설정(프로필별) + 서버측 푸시 필터링 — 사장 지시 2026-05-21.

웹 대시보드 연결은 모든 이벤트를 받아 통신로그에 전부 표시한다(설정 무관).
모바일 네이티브 연결은 알림 4종(order_submitted/trade/cycle/market_close)만, 그것도
해당 프로필의 설정이 ON 인 것만 받아 푸시한다.
"""
import json

import runtime


def test_notif_defaults_all_on(monkeypatch):
    monkeypatch.setattr(runtime, "_notif_settings", {"_default": dict(runtime._NOTIF_DEFAULT)})
    assert runtime.notif_settings(123) == {
        "order_submitted": True, "trade": True, "cycle": True, "market_close": True}


def test_notif_partial_update_persists_per_profile(tmp_path, monkeypatch):
    f = tmp_path / "notif.json"
    monkeypatch.setattr(runtime, "_NOTIF_FILE", f)
    monkeypatch.setattr(runtime, "_notif_settings", {"_default": dict(runtime._NOTIF_DEFAULT)})

    runtime.set_notif_settings({"trade": False}, uid=7)

    assert runtime.notif_settings(7)["trade"] is False
    assert runtime.notif_settings(7)["cycle"] is True       # 부분 갱신 — 나머지는 ON 유지
    assert runtime.notif_settings(8)["trade"] is True        # 다른 프로필 영향 없음
    assert json.loads(f.read_text())["7"]["trade"] is False  # 디스크 반영


def test_should_send_web_gets_everything(monkeypatch):
    monkeypatch.setattr(runtime, "_notif_settings", {"_default": dict(runtime._NOTIF_DEFAULT)})
    from server.app import _should_send
    assert _should_send({"client": "web", "uid": 5}, {"type": "status"}) is True
    assert _should_send({"client": "web", "uid": 5}, {"type": "trade_executed"}) is True


def test_should_send_mobile_filtered_by_settings(monkeypatch):
    monkeypatch.setattr(runtime, "_notif_settings", {
        "_default": dict(runtime._NOTIF_DEFAULT),
        "5": {"order_submitted": True, "trade": False, "cycle": True, "market_close": True}})
    from server.app import _should_send
    # 비알림 이벤트는 모바일에 안 보냄 (잡음 차단)
    assert _should_send({"client": "mobile", "uid": 5}, {"type": "status"}) is False
    # trade OFF → 체결완료/실패 차단
    assert _should_send({"client": "mobile", "uid": 5}, {"type": "trade_executed"}) is False
    assert _should_send({"client": "mobile", "uid": 5}, {"type": "trade_failed"}) is False
    # 켜진 알림은 통과
    assert _should_send({"client": "mobile", "uid": 5}, {"type": "order_submitted"}) is True
    assert _should_send({"client": "mobile", "uid": 5}, {"type": "cycle_complete"}) is True
    assert _should_send({"client": "mobile", "uid": 5}, {"type": "market_close"}) is True
