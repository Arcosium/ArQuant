import pytest
from fastapi.testclient import TestClient


def _mk(tmp_path, monkeypatch):
    import infra.auth_store as a
    for n, v in [("_DATA_DIR", tmp_path), ("_DB_PATH", tmp_path / "auth.db"),
                 ("_FERNET_KEY_PATH", tmp_path / ".fernet.key"),
                 ("_AUDIT_PATH", tmp_path / "audit.log"),
                 ("_INITED", False), ("_FERNET", None),
                 ("_FERNET_RAW", None), ("_BIDX_KEY", None)]:
        monkeypatch.setattr(a, n, v, raising=False)
    a.init()
    return a


def test_members_requires_admin(tmp_path, monkeypatch):
    a = _mk(tmp_path, monkeypatch)
    uid = a.upsert_user("normal", "Passw0rd!!xx", "AK", "AS", "OR", "1",
                         "https://openapi.koreainvestment.com:9443")
    tok = a.create_session(uid)
    import server.app as app_mod
    c = TestClient(app_mod.app); c.headers.update({"X-Session": tok})
    assert c.get("/api/admin/members").status_code == 403


def test_admin_list_and_delete(tmp_path, monkeypatch):
    a = _mk(tmp_path, monkeypatch)
    admin = a.upsert_user("hh09080", "Passw0rd!!xx", "AK", "AS", "OR", "1",
                          "https://openapi.koreainvestment.com:9443", is_admin=True)
    a.upsert_user("victim", "Passw0rd!!xx", "AK2", "AS2", "OR2", "2",
                  "https://openapivts.koreainvestment.com:29443")
    tok = a.create_session(admin)
    import server.app as app_mod
    c = TestClient(app_mod.app); c.headers.update({"X-Session": tok})
    ms = c.get("/api/admin/members").json()["members"]
    assert {m["username"] for m in ms} == {"hh09080", "victim"}
    assert next(m for m in ms if m["username"] == "victim")["is_mock"] is True
    assert c.post("/api/admin/members/delete",
                  json={"username": "hh09080"}).status_code == 400
    assert c.post("/api/admin/members/delete",
                  json={"username": "victim"}).status_code == 200
    assert a.find_user_by_username("victim") is None
