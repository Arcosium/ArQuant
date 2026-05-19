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
        "kis_account_no": "1-1", "kis_app_secret": "ASz"})
    assert r.status_code == 200 and r.json()["username"] == "zoe"
    bad = client.post("/api/recover_id", json={
        "kis_account_no": "1-1", "kis_app_secret": "NOPE"})
    assert bad.status_code == 404

def test_recover_password_endpoint(client):
    ok = client.post("/api/recover_password", json={
        "username": "zoe", "kis_account_no": "1-1", "kis_app_secret": "ASz",
        "new_password": "BrandN3w$pw"})
    assert ok.status_code == 200 and ok.json()["ok"] is True
    assert client.post("/api/login", json={
        "username": "zoe", "password": "BrandN3w$pw"}).status_code == 200

def test_recover_password_policy_fail_is_audited_and_400(client, tmp_path):
    from infra import auth_store as A
    r = client.post("/api/recover_password", json={
        "username": "zoe", "kis_account_no": "1-1", "kis_app_secret": "ASz",
        "new_password": "weak"})
    assert r.status_code == 400
    import json as _j
    lines = (A._AUDIT_PATH).read_text(encoding="utf-8").strip().splitlines()
    rec = _j.loads(lines[-1])
    assert rec["event"] == "recover_password" and rec["outcome"] == "fail" and rec["detail"] == "policy"

def test_recover_password_weak_pw_is_400_regardless_of_factors(client):
    # wrong factors + weak pw → 400 (policy), SAME as right factors + weak pw → 400.
    # No 400/404 split on factor correctness for a weak password ⇒ no stealth oracle.
    bad = client.post("/api/recover_password", json={
        "username": "zoe", "kis_account_no": "WRONG", "kis_app_secret": "WRONG",
        "new_password": "weak"})
    assert bad.status_code == 400
    good_factors_weak = client.post("/api/recover_password", json={
        "username": "zoe", "kis_account_no": "1-1", "kis_app_secret": "ASz",
        "new_password": "weak"})
    assert good_factors_weak.status_code == 400
    # strong pw + wrong factors → 404 generic (no reset happened)
    strong_wrong = client.post("/api/recover_password", json={
        "username": "zoe", "kis_account_no": "WRONG", "kis_app_secret": "WRONG",
        "new_password": "BrandN3w$pw"})
    assert strong_wrong.status_code == 404

def test_client_ip_prefers_cf_connecting_ip(client):
    # CF-Connecting-IP must win over X-Forwarded-For for throttle keying / audit
    r = client.post("/api/recover_id",
                     headers={"CF-Connecting-IP": "9.9.9.9", "X-Forwarded-For": "1.1.1.1"},
                     json={"kis_account_no":"1-1","kis_app_secret":"ASz"})
    assert r.status_code == 200
    from infra import auth_store as A
    import json as _j
    rec = _j.loads((A._AUDIT_PATH).read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["ip"] == "9.9.9.9"
