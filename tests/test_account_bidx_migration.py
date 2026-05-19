"""kis_account_no_bidx 컬럼·upsert·부팅 백필 멱등 회귀."""
import pytest


@pytest.fixture
def store(tmp_path, monkeypatch):
    import infra.auth_store as a
    for n, v in [("_DATA_DIR", tmp_path), ("_DB_PATH", tmp_path / "auth.db"),
                 ("_FERNET_KEY_PATH", tmp_path / ".fernet.key"),
                 ("_AUDIT_PATH", tmp_path / "audit.log"),
                 ("_INITED", False), ("_FERNET", None),
                 ("_FERNET_RAW", None), ("_BIDX_KEY", None)]:
        monkeypatch.setattr(a, n, v, raising=False)
    a.init()
    return a


def test_schema_has_kis_account_no_bidx(store):
    with store._connect() as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(users)").fetchall()}
    assert "kis_account_no_bidx" in cols


def test_upsert_sets_account_bidx(store):
    uid = store.upsert_user("u1", "Passw0rd!!xx", "AK", "AS", "OR",
                            "5012345601", "https://openapi.koreainvestment.com:9443")
    with store._connect() as c:
        row = c.execute("SELECT kis_account_no_bidx FROM users WHERE id=?",
                         (uid,)).fetchone()
    assert row["kis_account_no_bidx"] == store.bidx("5012345601")


def test_migration_backfills_blank_account_bidx_idempotent(store):
    uid = store.upsert_user("u2", "Passw0rd!!xx", "AK", "AS", "OR",
                            "777888999", "https://openapi.koreainvestment.com:9443")
    with store._connect() as c:
        c.execute("UPDATE users SET kis_account_no_bidx='' WHERE id=?", (uid,))
    s1 = store.migrate_passwords_and_bidx()
    with store._connect() as c:
        v = c.execute("SELECT kis_account_no_bidx FROM users WHERE id=?",
                       (uid,)).fetchone()["kis_account_no_bidx"]
    assert v == store.bidx("777888999")
    assert s1.get("acct_bidx", 0) >= 1
    s2 = store.migrate_passwords_and_bidx()
    assert s2.get("acct_bidx", 0) == 0
