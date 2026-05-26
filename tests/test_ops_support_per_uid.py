"""운용지원실장 워커 멀티테넌트 격리 (사장 지시 2026-05-26 / Phase 2).

fetch_cycle_context 가 actor uid 의 사이클·이벤트만 읽어야 한다(타 계정 누출 금지).
- list_cycles / get_recent_events 가 uid 인자로 호출되는지 (호출 기록)
- 실제 two-uid cycles.db 에서 uid 필터가 먹는지
- uid=None 이면 deny-by-default 로 빈 컨텍스트 (다른 계정 데이터 안 읽음)
- get_cycle 이 타 uid 소유 사이클이면 채택 안 함
"""
import infra.ops_support_worker as w


def test_fetch_cycle_context_threads_uid_into_reads(monkeypatch):
    """list_cycles 와 get_recent_events 가 actor uid 로 호출되는지 (인자 캡처)."""
    from infra import cycle_store
    import main_swarm

    seen = {}

    def fake_list_cycles(limit=50, offset=0, uid=None):
        seen["list_cycles_uid"] = uid
        return [{"id": 7, "uid": uid, "session": "KR_TRADING"}]

    def fake_get_recent_events(limit=500, uid=None):
        seen["events_uid"] = uid
        return [{"type": "error", "component": "x", "message": "boom"}]

    monkeypatch.setattr(cycle_store, "list_cycles", fake_list_cycles)
    monkeypatch.setattr(main_swarm, "get_recent_events", fake_get_recent_events)

    ctx = w.fetch_cycle_context(None, uid=1)
    assert seen["list_cycles_uid"] == 1
    assert seen["events_uid"] == 1
    assert ctx["recent_cycles"][0]["uid"] == 1
    assert ctx["recent_errors_skips"] and ctx["recent_errors_skips"][0]["type"] == "error"


def test_fetch_cycle_context_none_uid_is_empty_deny_by_default(monkeypatch):
    """uid=None 이면 어느 계정 데이터도 읽지 않는다 (default-deny)."""
    from infra import cycle_store
    import main_swarm

    called = {"list": False, "events": False}

    def fake_list_cycles(*a, **k):
        called["list"] = True
        return [{"id": 1, "uid": 99}]

    def fake_get_recent_events(*a, **k):
        called["events"] = True
        return [{"type": "error"}]

    monkeypatch.setattr(cycle_store, "list_cycles", fake_list_cycles)
    monkeypatch.setattr(main_swarm, "get_recent_events", fake_get_recent_events)

    ctx = w.fetch_cycle_context(5, uid=None)
    assert ctx == {"target_cycle": None, "recent_cycles": [], "recent_errors_skips": []}
    assert not called["list"] and not called["events"], "uid 없으면 어떤 계정도 읽지 않아야 함"


def test_fetch_cycle_context_real_db_filters_by_uid(tmp_path, monkeypatch):
    """실제 two-uid cycles.db: uid 의 사이클만 보이고 타 계정 사이클은 안 보인다."""
    import infra.cycle_store as cs
    import main_swarm

    monkeypatch.setattr(cs, "DB_PATH", tmp_path / "cycles.db")
    monkeypatch.setattr(cs, "_conn", None)
    # events 는 이 테스트의 관심사가 아니므로 빈 리스트로 고정.
    monkeypatch.setattr(main_swarm, "get_recent_events", lambda limit=500, uid=None: [])

    cs.record_cycle({"started_at": "2026-05-26 10:00:00", "session": "KR_TRADING", "uid": 1})
    cs.record_cycle({"started_at": "2026-05-26 10:01:00", "session": "US_TRADING", "uid": 2})

    ctx1 = w.fetch_cycle_context(None, uid=1)
    assert len(ctx1["recent_cycles"]) == 1
    assert ctx1["recent_cycles"][0]["uid"] == 1
    assert ctx1["target_cycle"]["uid"] == 1

    ctx2 = w.fetch_cycle_context(None, uid=2)
    assert len(ctx2["recent_cycles"]) == 1 and ctx2["recent_cycles"][0]["uid"] == 2


def test_fetch_cycle_context_rejects_foreign_cycle_id(tmp_path, monkeypatch):
    """지정 cycle_id 가 타 uid 소유면 target 으로 채택하지 않고 본인 최신으로 대체."""
    import infra.cycle_store as cs
    import main_swarm

    monkeypatch.setattr(cs, "DB_PATH", tmp_path / "cycles.db")
    monkeypatch.setattr(cs, "_conn", None)
    monkeypatch.setattr(main_swarm, "get_recent_events", lambda limit=500, uid=None: [])

    id1 = cs.record_cycle({"started_at": "2026-05-26 10:00:00", "session": "KR_TRADING", "uid": 1})
    id2 = cs.record_cycle({"started_at": "2026-05-26 10:01:00", "session": "US_TRADING", "uid": 2})

    # uid=1 이 uid=2 의 cycle_id 를 가리켜도, 그 사이클을 target 으로 쓰면 안 된다.
    ctx = w.fetch_cycle_context(id2, uid=1)
    assert ctx["target_cycle"] is None or ctx["target_cycle"].get("uid") == 1
