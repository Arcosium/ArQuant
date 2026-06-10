"""Phase 2 멀티테넌트: 통신/에이전트 로그가 유저별로 격리돼야 한다.

버그(root cause): swarm/broker/equity 는 uid 별로 분리됐지만 통신 로그
(claude_response.json)만 전역으로 남아, 동시 운용되는 두 계정(uid=1 hh09080 실거래,
uid=2 hh0908 모의)의 에이전트 메시지가 한 파일에 뒤섞였다. 대시보드가 이를 필터 없이
돌려줘 각 계정이 상대 계정의 에이전트(주식운용실장/사후관리실장/리스크관리실장)까지 봤다.

수정: equity 와 동일 패턴으로 로그를 data/<uid>/trade_log.json 으로 유저별 분리한다.
log_response_event(entry, uid)/get_recent_events(limit, uid) 가 uid 별 파일을 읽고 쓴다.
uid=None 이면 파일 기록을 건너뛴다(WS 로만 전달 — 부팅 IDLE 등 시스템 전역 이벤트).
"""
import main_swarm as ms
from infra import user_paths


def _isolate_data_dir(tmp_path, monkeypatch):
    """user_paths._DATA_DIR 를 tmp 로 돌려 실제 data/<uid>/ 를 건드리지 않게 한다."""
    monkeypatch.setattr(user_paths, "_DATA_DIR", tmp_path)


def test_events_isolated_by_uid(tmp_path, monkeypatch):
    _isolate_data_dir(tmp_path, monkeypatch)

    ms.log_response_event(
        {"source": "system_event", "type": "agent_msg", "agent": "주식운용실장", "message": "u1-hi"},
        uid=1,
    )
    ms.log_response_event(
        {"source": "system_event", "type": "agent_msg", "agent": "주식운용실장", "message": "u2-hi"},
        uid=2,
    )

    ev1 = ms.get_recent_events(uid=1)
    ev2 = ms.get_recent_events(uid=2)

    msgs1 = [e.get("message") for e in ev1]
    msgs2 = [e.get("message") for e in ev2]

    assert "u1-hi" in msgs1 and "u2-hi" not in msgs1
    assert "u2-hi" in msgs2 and "u1-hi" not in msgs2


def test_uid_none_skips_file_logging(tmp_path, monkeypatch):
    _isolate_data_dir(tmp_path, monkeypatch)
    # uid 없는 전역 시스템 이벤트는 파일에 남지 않는다(WS 로만 흐름).
    ms.log_response_event(
        {"source": "system_event", "type": "status", "state": "IDLE", "message": "boot"},
        uid=None,
    )
    assert ms.get_recent_events(uid=1) == []
    assert ms.get_recent_events(uid=None) == []
    # uid=None 은 어떤 유저 로그 파일에도 기록되지 않는다(파일 미생성).
    assert not user_paths.trade_log_path(1).exists()
    assert not user_paths.trade_log_path(2).exists()


def test_get_recent_events_filters_display_types(tmp_path, monkeypatch):
    _isolate_data_dir(tmp_path, monkeypatch)
    ms.log_response_event({"source": "system_event", "type": "agent_msg", "message": "shown"}, uid=7)
    # 표시 대상이 아닌 타입은 replay 에서 제외된다.
    ms.log_response_event({"source": "system_event", "type": "internal_only", "message": "hidden"}, uid=7)
    msgs = [e.get("message") for e in ms.get_recent_events(uid=7)]
    assert "shown" in msgs and "hidden" not in msgs


def test_trade_history_isolated_by_uid(tmp_path, monkeypatch):
    _isolate_data_dir(tmp_path, monkeypatch)
    ms.log_response_event(
        {"source": "system_event", "type": "trade_executed", "side": "buy",
         "code": "005930", "message": "u1 trade"}, uid=1)
    ms.log_response_event(
        {"source": "system_event", "type": "trade_executed", "side": "buy",
         "ticker": "AAPL", "message": "u2 trade"}, uid=2)

    t1 = ms.get_trade_history(uid=1)
    t2 = ms.get_trade_history(uid=2)
    assert any(t.get("message") == "u1 trade" for t in t1)
    assert all(t.get("message") != "u2 trade" for t in t1)
    assert any(t.get("message") == "u2 trade" for t in t2)
    assert all(t.get("message") != "u1 trade" for t in t2)
