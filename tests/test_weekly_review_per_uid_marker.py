"""주간 피드백 중복방지 마커는 per-uid 여야 한다 (Phase 2 멀티테넌트).

버그 2026-06-06: MARKER_FILE 이 전역(data/.weekly_review_last.txt)이라, 토요일 06시 후
uid=1·uid=2 스케줄러가 거의 동시에 틱할 때 먼저 도는 쪽이 마커를 쓰면 should_run_now()가
전역 마커를 읽어 나머지 계정의 weekly 튜닝을 막았다 → 두 계정 중 한 곳만 튜닝. per-uid 마커로 격리.
"""
from datetime import datetime
from infra import weekly_review as wr


def test_marker_is_per_uid(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "PROJECT_ROOT", tmp_path)
    sat = datetime(2026, 6, 6, 7, 0, tzinfo=wr.KST)   # 2026-06-06 = 토요일 07:00 KST
    # uid=1 이 방금 실행됨(마커 기록) → uid=1 재실행 금지
    wr._write_last_run(sat, uid=1)
    assert wr.should_run_now(sat, uid=1) is False
    # uid=2 는 마커 없음 → 실행 가능 (uid=1 마커에 영향받지 않아야 함)
    assert wr.should_run_now(sat, uid=2) is True


def test_per_uid_marker_blocks_only_same_uid_rerun(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "PROJECT_ROOT", tmp_path)
    sat = datetime(2026, 6, 6, 7, 0, tzinfo=wr.KST)
    wr._write_last_run(sat, uid=2)
    assert wr.should_run_now(sat, uid=2) is False     # 같은 uid 재실행 차단
    assert wr.should_run_now(sat, uid=1) is True       # 다른 uid 는 여전히 가능


def test_before_6am_never_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "PROJECT_ROOT", tmp_path)
    early = datetime(2026, 6, 6, 5, 0, tzinfo=wr.KST)  # 토 05:00 KST < 06:00
    assert wr.should_run_now(early, uid=1) is False
