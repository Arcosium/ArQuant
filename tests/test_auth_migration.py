import sqlite3

def test_new_columns_exist(fresh_auth):
    con = sqlite3.connect(fresh_auth._DB_PATH)
    cols = {r[1] for r in con.execute("PRAGMA table_info(users)").fetchall()}
    con.close()
    assert {"password_hash", "kis_app_key_bidx",
            "kis_app_secret_bidx", "llm_key_bidx"} <= cols

def test_upsert_stores_hash_and_bidx_not_plaintext(fresh_auth):
    uid = fresh_auth.upsert_user(
        username="alice", password="Sup3r$ecret!",
        kis_app_key="AK", kis_app_secret="AS", llm_key="DS",
        kis_account_no="123-01", kis_base_url="", dart_key="", label="")
    import sqlite3
    con = sqlite3.connect(fresh_auth._DB_PATH); con.row_factory = sqlite3.Row
    r = con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone(); con.close()
    assert r["password_enc"] == ""                       # no encrypted password
    assert r["password_hash"].startswith("$argon2id$")
    assert r["kis_app_key_bidx"] == fresh_auth.bidx("AK")
    assert r["kis_app_secret_bidx"] == fresh_auth.bidx("AS")
    assert r["llm_key_bidx"] == fresh_auth.bidx("DS")

def test_one_shot_migration_is_idempotent(fresh_auth):
    uid = fresh_auth.upsert_user("dave", "Migrate$99x", "AK", "AS", "OR",
                                 "1-1", "", "", "")
    import sqlite3
    con = sqlite3.connect(fresh_auth._DB_PATH)
    con.execute("UPDATE users SET password_hash='', password_enc=?, "
                "kis_app_key_bidx='', kis_app_secret_bidx='', llm_key_bidx='' "
                "WHERE id=?", (fresh_auth.encrypt("Migrate$99x"), uid))
    con.commit(); con.close()
    s1 = fresh_auth.migrate_passwords_and_bidx()
    assert s1 == {"pw": 1, "bidx": 1, "acct_bidx": 0}
    s2 = fresh_auth.migrate_passwords_and_bidx()          # idempotent
    assert s2 == {"pw": 0, "bidx": 0, "acct_bidx": 0}
    con = sqlite3.connect(fresh_auth._DB_PATH); con.row_factory = sqlite3.Row
    r = con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone(); con.close()
    assert r["password_hash"].startswith("$argon2id$") and r["password_enc"] == ""
    assert r["kis_app_key_bidx"] == fresh_auth.bidx("AK")

def test_migration_skips_row_with_corrupt_enc_no_data_loss(fresh_auth):
    uid = fresh_auth.upsert_user("eve", "Corrupt$pw9", "AK", "AS", "OR",
                                 "1-1", "", "", "")
    import sqlite3
    con = sqlite3.connect(fresh_auth._DB_PATH)
    # legacy + CORRUPT password_enc (not valid Fernet), bidx cleared
    con.execute("UPDATE users SET password_hash='', password_enc='not-a-valid-fernet-token', "
                "kis_app_key_bidx='', kis_app_secret_bidx='', llm_key_bidx='' "
                "WHERE id=?", (uid,))
    con.commit(); con.close()
    stats = fresh_auth.migrate_passwords_and_bidx()
    # corrupt row must be skipped, NOT counted, NOT mutated
    assert stats == {"pw": 0, "bidx": 0, "acct_bidx": 0}
    con = sqlite3.connect(fresh_auth._DB_PATH); con.row_factory = sqlite3.Row
    r = con.execute("SELECT password_hash, password_enc, kis_app_key_bidx "
                     "FROM users WHERE id=?", (uid,)).fetchone(); con.close()
    # password_enc preserved (NOT blanked), no bogus hash, no bogus bidx written
    assert r["password_enc"] == "not-a-valid-fernet-token"
    assert r["password_hash"] == ""
    assert r["kis_app_key_bidx"] == ""

def test_migration_propagates_fernet_key_lost(fresh_auth, monkeypatch):
    # a user exists (so key-loss is the dangerous case), then key disappears
    fresh_auth.upsert_user("frank", "KeyLost$p9", "AK", "AS", "OR", "1-1", "", "", "")
    # drop the in-memory key + the key file, reset init flag → next crypto op must raise
    (fresh_auth._FERNET_KEY_PATH).unlink(missing_ok=True)
    monkeypatch.setattr(fresh_auth, "_FERNET", None)
    monkeypatch.setattr(fresh_auth, "_FERNET_RAW", None, raising=False)
    monkeypatch.setattr(fresh_auth, "_BIDX_KEY", None, raising=False)
    monkeypatch.setattr(fresh_auth, "_INITED", False)
    import pytest as _pt
    with _pt.raises(fresh_auth.FernetKeyLost):
        fresh_auth.migrate_passwords_and_bidx()


def test_provider_column_migration_preserves_encrypted_key(fresh_auth, monkeypatch):
    uid = fresh_auth.upsert_user(
        "legacy", "Legacy$pw99", "AK", "AS", "DS-SECRET", "1-1", "", "", "")
    old_enc = "open" + "router_key_enc"
    old_bidx = "open" + "router_key_bidx"
    con = sqlite3.connect(fresh_auth._DB_PATH)
    con.execute(f"ALTER TABLE users RENAME COLUMN llm_key_enc TO {old_enc}")
    con.execute(f"ALTER TABLE users RENAME COLUMN llm_key_bidx TO {old_bidx}")
    con.commit()
    con.close()

    monkeypatch.setattr(fresh_auth, "_INITED", False)
    fresh_auth.init()

    con = sqlite3.connect(fresh_auth._DB_PATH)
    cols = {r[1] for r in con.execute("PRAGMA table_info(users)").fetchall()}
    con.close()
    assert "llm_key_enc" in cols
    assert "llm_key_bidx" in cols
    assert old_enc not in cols and old_bidx not in cols
    assert fresh_auth.get_user_credentials(uid)["llm_key"] == "DS-SECRET"
