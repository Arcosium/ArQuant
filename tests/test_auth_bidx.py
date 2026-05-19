import pytest
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

def test_bidx_deterministic_and_normalized(fresh_auth):
    a = fresh_auth.bidx("APPKEY-123")
    assert a == fresh_auth.bidx("APPKEY-123")          # deterministic
    assert a == fresh_auth.bidx("  APPKEY-123  ")      # strips like registration
    assert a != fresh_auth.bidx("APPKEY-124")          # collision-free for distinct
    assert len(a) == 64 and all(c in "0123456789abcdef" for c in a)  # sha256 hex
