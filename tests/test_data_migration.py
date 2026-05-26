def test_migration_moves_global_files_to_backup(tmp_path, monkeypatch):
    import infra.data_migration as dm
    data = tmp_path / "data"; data.mkdir()
    (data / "equity_curve.json").write_text("[1,2,3]")
    (data / "strategy_state.json").write_text("{}")
    monkeypatch.setattr(dm, "_DATA_DIR", data)

    moved = dm.migrate_once()
    assert moved is True
    # globals gone from top level, present in a backup dir
    assert not (data / "equity_curve.json").exists()
    backups = list(data.glob("_migration_backup_*"))
    assert backups and (backups[0] / "equity_curve.json").exists()
    # idempotent: second run does nothing
    assert dm.migrate_once() is False
