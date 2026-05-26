def test_cycles_are_filtered_by_uid(tmp_path, monkeypatch):
    import infra.cycle_store as cs
    monkeypatch.setattr(cs, "DB_PATH", tmp_path / "cycles.db")
    monkeypatch.setattr(cs, "_conn", None)
    cs.record_cycle({"started_at": "2026-05-26 10:00:00", "session": "KR_TRADING", "uid": 1})
    cs.record_cycle({"started_at": "2026-05-26 10:01:00", "session": "US_TRADING", "uid": 2})
    only1 = cs.list_cycles(uid=1)
    assert len(only1) == 1 and only1[0]["uid"] == 1
    assert len(cs.list_cycles(uid=2)) == 1
    # no filter → both
    assert len(cs.list_cycles()) == 2
