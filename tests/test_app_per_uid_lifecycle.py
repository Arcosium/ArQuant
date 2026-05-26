import asyncio


def test_start_stop_are_per_uid(monkeypatch):
    import server.app as app
    from infra import user_paths

    # running_marker 가 실제 data/<uid>/ 를 건드리지 않도록 tmp 로 우회 (테스트 격리)
    import tempfile, pathlib
    _tmp = pathlib.Path(tempfile.mkdtemp())
    monkeypatch.setattr(user_paths, "running_marker", lambda uid: _tmp / f"{uid}.running")

    class FakeSwarm:
        def __init__(self): self.stopped = False
        async def start_continuous(self, directive=None): await asyncio.sleep(3600)
        def stop(self): self.stopped = True

    from infra.user_context import UserContext
    ctxs = {}

    def fake_get_or_create(uid):
        if uid not in ctxs:
            c = UserContext.__new__(UserContext)
            c.uid = uid; c.is_admin = uid == 1; c._swarm = FakeSwarm(); c.task = None
            ctxs[uid] = c
        return ctxs[uid]

    monkeypatch.setattr(app.REGISTRY, "get_or_create", fake_get_or_create)
    monkeypatch.setattr(app.REGISTRY, "get", lambda uid: ctxs.get(uid))

    async def run():
        await app._start_uid(1); await app._start_uid(2)
        assert ctxs[1].task is not None and not ctxs[1].task.done()
        await app._stop_uid(2)
        assert ctxs[2].swarm.stopped is True
        assert not ctxs[1].task.done()
        await app._stop_uid(1)

    asyncio.run(run())
