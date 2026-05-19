"""프로필 엔드포인트 — 세션 인증·비번변경·지시 CRUD·탈퇴."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    import infra.auth_store as a
    for n, v in [("_DATA_DIR", tmp_path), ("_DB_PATH", tmp_path / "auth.db"),
                 ("_FERNET_KEY_PATH", tmp_path / ".fernet.key"),
                 ("_AUDIT_PATH", tmp_path / "audit.log"),
                 ("_INITED", False), ("_FERNET", None),
                 ("_FERNET_RAW", None), ("_BIDX_KEY", None)]:
        monkeypatch.setattr(a, n, v, raising=False)
    a.init()
    uid = a.upsert_user("u1", "OldPassw0rd!!", "AK", "AS", "OR", "5012345601",
                         "https://openapi.koreainvestment.com:9443")
    tok = a.create_session(uid)
    import infra.standing_directives as sd
    monkeypatch.setattr(sd, "_PROFILES_DIR", tmp_path / "profiles")
    import server.app as app_mod
    c = TestClient(app_mod.app)
    c.headers.update({"X-Session": tok})
    return c, a, uid


def test_password_change_unauth_401():
    import server.app as app_mod
    c = TestClient(app_mod.app)
    assert c.post("/api/profile/password",
                  json={"current": "x", "new": "y"}).status_code == 401


def test_password_change_flow(client):
    c, a, uid = client
    assert c.post("/api/profile/password",
                  json={"current": "WRONG", "new": "NewPassw0rd!!"}).status_code == 400
    assert c.post("/api/profile/password",
                  json={"current": "OldPassw0rd!!", "new": "short"}).status_code == 400
    assert c.post("/api/profile/password",
                  json={"current": "OldPassw0rd!!", "new": "NewPassw0rd!!"}).status_code == 200
    assert a.verify_password("u1", "NewPassw0rd!!")


def test_directives_crud(client):
    c, a, uid = client
    assert c.get("/api/profile/directives").json()["directives"] == []
    assert c.post("/api/profile/directives",
                  json={"text": "달러 비중 확대"}).status_code == 200
    lst = c.get("/api/profile/directives").json()["directives"]
    assert len(lst) == 1 and lst[0]["text"] == "달러 비중 확대"
    did = lst[0]["id"]
    assert c.delete(f"/api/profile/directives/{did}").status_code == 200
    assert c.get("/api/profile/directives").json()["directives"] == []


def test_delete_account_requires_password(client):
    c, a, uid = client
    assert c.post("/api/profile/delete_account",
                  json={"password": "WRONG"}).status_code == 400
    assert c.post("/api/profile/delete_account",
                  json={"password": "OldPassw0rd!!"}).status_code == 200
    assert a.find_user_by_username("u1") is None
