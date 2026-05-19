import sqlite3, pytest
from infra import auth_store

@pytest.fixture
def fresh_auth(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_store, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(auth_store, "_DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(auth_store, "_FERNET_KEY_PATH", tmp_path / ".fernet.key")
    monkeypatch.setattr(auth_store, "_AUDIT_PATH", tmp_path / "auth_audit.log", raising=False)
    monkeypatch.setattr(auth_store, "_INITED", False)
    monkeypatch.setattr(auth_store, "_FERNET", None)
    monkeypatch.setattr(auth_store, "_FERNET_RAW", None, raising=False)
    monkeypatch.setattr(auth_store, "_BIDX_KEY", None, raising=False)
    auth_store.init()
    return auth_store

def test_new_columns_exist(fresh_auth):
    con = sqlite3.connect(fresh_auth._DB_PATH)
    cols = {r[1] for r in con.execute("PRAGMA table_info(users)").fetchall()}
    con.close()
    assert {"password_hash", "kis_app_key_bidx",
            "kis_app_secret_bidx", "openrouter_key_bidx"} <= cols

def test_upsert_stores_hash_and_bidx_not_plaintext(fresh_auth):
    uid = fresh_auth.upsert_user(
        username="alice", password="Sup3r$ecret!",
        kis_app_key="AK", kis_app_secret="AS", openrouter_key="OR",
        kis_account_no="123-01", kis_base_url="", dart_key="", label="")
    import sqlite3
    con = sqlite3.connect(fresh_auth._DB_PATH); con.row_factory = sqlite3.Row
    r = con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone(); con.close()
    assert r["password_enc"] == ""                       # no encrypted password
    assert r["password_hash"].startswith("$argon2id$")
    assert r["kis_app_key_bidx"] == fresh_auth.bidx("AK")
    assert r["kis_app_secret_bidx"] == fresh_auth.bidx("AS")
    assert r["openrouter_key_bidx"] == fresh_auth.bidx("OR")

def test_one_shot_migration_is_idempotent(fresh_auth):
    uid = fresh_auth.upsert_user("dave", "Migrate$99x", "AK", "AS", "OR",
                                 "1-1", "", "", "")
    import sqlite3
    con = sqlite3.connect(fresh_auth._DB_PATH)
    con.execute("UPDATE users SET password_hash='', password_enc=?, "
                "kis_app_key_bidx='', kis_app_secret_bidx='', openrouter_key_bidx='' "
                "WHERE id=?", (fresh_auth.encrypt("Migrate$99x"), uid))
    con.commit(); con.close()
    s1 = fresh_auth.migrate_passwords_and_bidx()
    assert s1 == {"pw": 1, "bidx": 1}
    s2 = fresh_auth.migrate_passwords_and_bidx()          # idempotent
    assert s2 == {"pw": 0, "bidx": 0}
    con = sqlite3.connect(fresh_auth._DB_PATH); con.row_factory = sqlite3.Row
    r = con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone(); con.close()
    assert r["password_hash"].startswith("$argon2id$") and r["password_enc"] == ""
    assert r["kis_app_key_bidx"] == fresh_auth.bidx("AK")
