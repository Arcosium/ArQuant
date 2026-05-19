import pytest
from fastapi.testclient import TestClient
from infra import auth_store as A

@pytest.fixture
def client(tmp_path, monkeypatch):
    for n, v in [("_DATA_DIR", tmp_path), ("_DB_PATH", tmp_path/"a.db"),
                 ("_FERNET_KEY_PATH", tmp_path/".k"), ("_AUDIT_PATH", tmp_path/"au.log"),
                 ("_INITED", False), ("_FERNET", None), ("_FERNET_RAW", None),
                 ("_BIDX_KEY", None)]:
        monkeypatch.setattr(A, n, v, raising=False)
    A.init()
    A.upsert_user("zoe", "Orig$pass99", "AKz", "ASz", "ORz", "1-1", "", "", "")
    from server.app import app
    return TestClient(app)

def test_recover_id_endpoint(client):
    r = client.post("/api/recover_id", json={
        "kis_app_key": "AKz", "kis_app_secret": "ASz", "openrouter_key": "ORz"})
    assert r.status_code == 200 and r.json()["username"] == "zoe"
    bad = client.post("/api/recover_id", json={
        "kis_app_key": "AKz", "kis_app_secret": "ASz", "openrouter_key": "NOPE"})
    assert bad.status_code == 404

def test_recover_password_endpoint(client):
    ok = client.post("/api/recover_password", json={
        "username": "zoe", "kis_app_key": "AKz", "kis_app_secret": "ASz",
        "openrouter_key": "ORz", "new_password": "BrandN3w$pw"})
    assert ok.status_code == 200 and ok.json()["ok"] is True
    assert client.post("/api/login", json={
        "username": "zoe", "password": "BrandN3w$pw"}).status_code == 200
