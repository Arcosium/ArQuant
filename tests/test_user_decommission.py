"""유저 삭제/탈퇴 시 실행 주체 완전 종료 — _decommission_uid.

배경(2026-05-27 진단): 기존 삭제 경로(auth_store.delete_user + rmtree(profiles))는
**실행 중인 매매 루프를 멈추지 않았다**. 단일 프로세스·per-uid asyncio 모델에서 DB 행만
지우면 그 uid 의 asyncio.Task(스왐)는 메모리에서 계속 돌아 — 삭제된 유저 명의로 KIS 실주문이
나가고, 살아있는 루프가 rmtree 된 profiles/<uid> 를 재생성(고아 부활)한다.
_decommission_uid 는 루프를 '먼저' 멈추고(정지+컨텍스트 제거) 그다음 디렉터리를 정리한다.
"""
import asyncio
import pathlib

import pytest


def _setup(monkeypatch, tmp_path):
    import server.app as app
    from infra import user_paths

    profiles = tmp_path / "profiles"
    data = tmp_path / "data"
    (profiles / "7").mkdir(parents=True)
    (profiles / "7" / "overrides.json").write_text("{}", encoding="utf-8")
    (data / "7").mkdir(parents=True)
    (data / "7" / "trade_log.json").write_text("[]", encoding="utf-8")

    monkeypatch.setattr(app, "_PROFILES_DIR", profiles)
    monkeypatch.setattr(user_paths, "user_dir", lambda uid: data / str(uid))
    monkeypatch.setattr(user_paths, "running_marker", lambda uid: tmp_path / f"{uid}.running")

    class FakeSwarm:
        def __init__(self): self.stopped = False
        def stop(self): self.stopped = True

    class FakeTask:
        def __init__(self): self._cancelled = False
        def done(self): return self._cancelled
        def cancel(self): self._cancelled = True
        def cancelled(self): return self._cancelled

    class FakeCtx:
        def __init__(self, uid):
            self.uid = uid; self.swarm = FakeSwarm()
            self.task = FakeTask()

    ctxs = {7: FakeCtx(7)}
    monkeypatch.setattr(app.REGISTRY, "get", lambda uid: ctxs.get(uid))
    monkeypatch.setattr(app.REGISTRY, "drop", lambda uid: ctxs.pop(uid, None))
    return app, profiles, data, ctxs


def test_decommission_stops_loop_first(monkeypatch, tmp_path):
    app, profiles, data, ctxs = _setup(monkeypatch, tmp_path)
    swarm = ctxs[7].swarm
    task = ctxs[7].task

    asyncio.run(app._decommission_uid(7))

    assert swarm.stopped is True, "삭제 시 매매 루프를 멈춰야 한다 (계속 거래 방지)"
    assert task.cancelled() or task.done(), "실행 중 task 가 취소돼야 한다"


def test_decommission_drops_registry_context(monkeypatch, tmp_path):
    app, profiles, data, ctxs = _setup(monkeypatch, tmp_path)
    asyncio.run(app._decommission_uid(7))
    assert app.REGISTRY.get(7) is None, "컨텍스트가 레지스트리에서 제거돼야 부활하지 않는다"


def test_decommission_removes_profile_and_data_dirs(monkeypatch, tmp_path):
    app, profiles, data, ctxs = _setup(monkeypatch, tmp_path)
    asyncio.run(app._decommission_uid(7))
    assert not (profiles / "7").exists(), "profiles/<uid> 정리돼야 한다"
    assert not (data / "7").exists(), "data/<uid> 정리돼야 한다"


def test_rmtree_guard_blocks_live_paths_under_pytest(tmp_path):
    """2026-06-11 참사 재발 방지 — pytest 중 라이브 저장소 하위 경로 rmtree 는 차단돼야 한다.
    (test_admin_members 가 tmp 격리 없이 _decommission_uid 를 태워 실 data/1·data/2 가
    통째로 삭제됐고, 과거 '거래기록 소실' 사건들의 근본 원인이었다.)"""
    import server.app as app
    live_dir = pathlib.Path(__file__).resolve().parent / ".guard_probe_tmp"
    live_dir.mkdir(exist_ok=True)
    (live_dir / "x.txt").write_text("probe", encoding="utf-8")
    try:
        app._rmtree_pytest_guarded(live_dir)
        assert live_dir.exists(), "pytest 중 라이브 경로는 삭제되면 안 된다"
    finally:
        import shutil
        shutil.rmtree(live_dir, ignore_errors=True)
    # tmp 경로(저장소 밖)는 정상 삭제
    victim = tmp_path / "victim"
    victim.mkdir()
    app._rmtree_pytest_guarded(victim)
    assert not victim.exists(), "tmp(저장소 밖) 경로는 정상 삭제돼야 한다"
