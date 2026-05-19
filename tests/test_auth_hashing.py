from infra import auth_store


def test_hash_and_verify_roundtrip():
    h = auth_store.hash_password("Sup3r$ecret!")
    assert h.startswith("$argon2id$")
    assert auth_store.verify_pw_hash(h, "Sup3r$ecret!") is True
    assert auth_store.verify_pw_hash(h, "wrong") is False
    assert auth_store.verify_pw_hash("", "anything") is False
    assert auth_store.verify_pw_hash("not-a-hash", "x") is False


import pytest
from infra import auth_store as A

@pytest.fixture
def fresh_auth(tmp_path, monkeypatch):
    for name, val in [("_DATA_DIR", tmp_path), ("_DB_PATH", tmp_path/"a.db"),
                      ("_FERNET_KEY_PATH", tmp_path/".k"),
                      ("_AUDIT_PATH", tmp_path/"au.log"), ("_INITED", False),
                      ("_FERNET", None), ("_FERNET_RAW", None), ("_BIDX_KEY", None)]:
        monkeypatch.setattr(A, name, val, raising=False)
    A.init(); return A

def test_verify_password_argon2_path(fresh_auth):
    fresh_auth.upsert_user("bob", "P@ssword12!", "k", "s", "o", "1-1", "", "", "")
    assert fresh_auth.verify_password("bob", "P@ssword12!")["username"] == "bob"
    assert fresh_auth.verify_password("bob", "nope") is None
    assert fresh_auth.verify_password("ghost", "x") is None

def test_verify_password_legacy_then_migrates(fresh_auth):
    uid = fresh_auth.upsert_user("carol", "Legacy$pw99", "k", "s", "o", "1-1", "", "", "")
    import sqlite3
    con = sqlite3.connect(fresh_auth._DB_PATH)
    con.execute("UPDATE users SET password_hash='', password_enc=? WHERE id=?",
                (fresh_auth.encrypt("Legacy$pw99"), uid)); con.commit(); con.close()
    assert fresh_auth.verify_password("carol", "Legacy$pw99")["username"] == "carol"
    con = sqlite3.connect(fresh_auth._DB_PATH); con.row_factory = sqlite3.Row
    r = con.execute("SELECT password_hash, password_enc FROM users WHERE id=?",
                     (uid,)).fetchone(); con.close()
    assert r["password_hash"].startswith("$argon2id$") and r["password_enc"] == ""
