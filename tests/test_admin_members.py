import pytest
from pathlib import Path
from fastapi.testclient import TestClient

# Repo root (tests/ is one level below)
_REPO_ROOT = Path(__file__).resolve().parent.parent


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
    victim_uid = a.upsert_user("victim", "Passw0rd!!xx", "AK2", "AS2", "OR2", "2",
                               "https://openapivts.koreainvestment.com:29443")
    tok = a.create_session(admin)
    import server.app as app_mod
    c = TestClient(app_mod.app); c.headers.update({"X-Session": tok})
    ms = c.get("/api/admin/members").json()["members"]
    assert {m["username"] for m in ms} == {"hh09080", "victim"}
    assert next(m for m in ms if m["username"] == "victim")["is_mock"] is True
    assert c.post("/api/admin/members/delete",
                  json={"username": "hh09080"}).status_code == 400

    # Create a real profile dir (with sentinel file) so rmtree has something to remove
    profile_dir = _REPO_ROOT / "data" / "profiles" / str(victim_uid)
    profile_dir.mkdir(parents=True, exist_ok=True)
    sentinel = profile_dir / "sentinel.txt"
    sentinel.write_text("test")
    try:
        resp = c.post("/api/admin/members/delete", json={"username": "victim"})
        assert resp.status_code == 200
        assert a.find_user_by_username("victim") is None
        # Route must have removed the profile dir via rmtree
        assert not profile_dir.exists(), "profile dir should have been removed by rmtree"
    finally:
        # Defensive cleanup: remove dir if route failed to do so (e.g. assertion before delete)
        import shutil
        if profile_dir.exists():
            shutil.rmtree(profile_dir, ignore_errors=True)


def test_delete_nonexistent_returns_404(tmp_path, monkeypatch):
    a = _mk(tmp_path, monkeypatch)
    admin = a.upsert_user("hh09080", "Passw0rd!!xx", "AK", "AS", "OR", "1",
                          "https://openapi.koreainvestment.com:9443", is_admin=True)
    tok = a.create_session(admin)
    import server.app as app_mod
    c = TestClient(app_mod.app); c.headers.update({"X-Session": tok})
    assert c.post("/api/admin/members/delete",
                  json={"username": "ghost_nobody"}).status_code == 404
