"""유저 피드백/버그 제보 저장소 (사장 지시 2026-05-24).

흐름 검증: 유저 제출 → ADMIN 전체 조회 → 답글 → 유저 미확인 배지 → 확인 처리.
유저 간 격리(자기 항목만 조회)도 확인.
"""
import pytest

from infra import feedback_store as fb


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(fb, "_PATH", tmp_path / "feedback.json")


def test_submit_and_user_isolation():
    fb.submit(2, "alice", "bug", "버그A", "본문A")
    fb.submit(3, "bob", "feature", "기능B", "본문B")
    a = fb.list_for_user(2)
    b = fb.list_for_user(3)
    assert len(a) == 1 and a[0]["title"] == "버그A" and a[0]["uid"] == 2
    assert len(b) == 1 and b[0]["username"] == "bob"
    # ADMIN 은 전체 조회
    assert len(fb.list_all()) == 2


def test_empty_submission_rejected():
    with pytest.raises(ValueError):
        fb.submit(2, "alice", "bug", "", "   ")


def test_invalid_type_coerced():
    e = fb.submit(2, "alice", "weird", "t", "b")
    assert e["type"] == "etc"


def test_reply_flow_and_badges():
    e = fb.submit(5, "carol", "bug", "안 됨", "로그인이 안돼요")
    assert e["status"] == "open"
    assert fb.count_open() == 1
    assert fb.count_unseen_replies(5) == 0

    updated = fb.reply(e["id"], "확인했습니다. 곧 고칠게요.")
    assert updated is not None
    assert updated["status"] == "answered"
    assert updated["reply"] == "확인했습니다. 곧 고칠게요."
    assert updated["reply_seen"] is False
    # 답변되면 미답변 카운트 0, 유저 미확인 답글 1
    assert fb.count_open() == 0
    assert fb.count_unseen_replies(5) == 1

    # 유저가 확인하면 배지 클리어
    fb.mark_replies_seen(5)
    assert fb.count_unseen_replies(5) == 0
    # 다른 유저 배지엔 영향 없음
    assert fb.count_unseen_replies(99) == 0


def test_reply_missing_id_returns_none():
    assert fb.reply("nope", "x") is None
