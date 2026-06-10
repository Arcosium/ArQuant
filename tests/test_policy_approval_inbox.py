"""정책 변경 승인 인박스 — 토요일 ops 제안 → 사장 승인 시 오버라이드 적용."""
import infra.policy_approval_inbox as box
from infra import profile_overrides


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(box, "_PROFILES_DIR", tmp_path)
    return tmp_path


def test_enqueue_then_list(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    box.enqueue(7, "ALLOW_US_STOCKS", True, False, "미국 재개 권고")
    p = box.list_pending(7)
    assert len(p) == 1
    assert p[0]["key"] == "ALLOW_US_STOCKS"
    assert p[0]["proposed_value"] is True
    assert p[0]["current_value"] is False
    assert p[0]["status"] == "pending"


def test_enqueue_dedupes_by_key_updates_value(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    box.enqueue(7, "ALLOW_US_STOCKS", True, False, "v1")
    box.enqueue(7, "ALLOW_US_STOCKS", False, False, "v2")
    p = box.list_pending(7)
    assert len(p) == 1
    assert p[0]["proposed_value"] is False
    assert p[0]["rationale"] == "v2"


def test_approve_applies_override(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    called = {}
    monkeypatch.setattr(profile_overrides, "set_overrides",
                        lambda uid, params: called.setdefault("v", (uid, dict(params))))
    box.enqueue(7, "ALLOW_US_STOCKS", True, False, "x")
    assert box.approve(7, "ALLOW_US_STOCKS") is True
    assert called["v"] == (7, {"ALLOW_US_STOCKS": True})
    assert box.list_pending(7) == []   # 더 이상 pending 아님


def test_reject_removes_without_applying(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(profile_overrides, "set_overrides",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("적용 금지")))
    box.enqueue(7, "ALLOW_DERIVATIVES", True, False, "x")
    assert box.reject(7, "ALLOW_DERIVATIVES") is True
    assert box.list_pending(7) == []


def test_approve_missing_returns_false(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert box.approve(7, "NOPE") is False


def test_list_pending_none_uid_safe(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert box.list_pending(None) == []
