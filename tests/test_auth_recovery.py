import pytest
from infra import auth_store as A

@pytest.fixture
def fresh_auth(tmp_path, monkeypatch):
    for n, v in [("_DATA_DIR", tmp_path), ("_DB_PATH", tmp_path/"a.db"),
                 ("_FERNET_KEY_PATH", tmp_path/".k"), ("_AUDIT_PATH", tmp_path/"au.log"),
                 ("_INITED", False), ("_FERNET", None), ("_FERNET_RAW", None),
                 ("_BIDX_KEY", None)]:
        monkeypatch.setattr(A, n, v, raising=False)
    A.init(); return A

def test_find_username_by_factors(fresh_auth):
    fresh_auth.upsert_user("erin", "P@ss12345!", "AK1", "AS1", "OR1", "1-1", "", "", "")
    assert fresh_auth.find_username_by_factors("AK1", "AS1", "OR1") == "erin"
    assert fresh_auth.find_username_by_factors(" AK1 ", "AS1", "OR1") == "erin"  # norm
    assert fresh_auth.find_username_by_factors("AK1", "AS1", "WRONG") is None
    assert fresh_auth.find_username_by_factors("AK1", "AS1", "") is None

def test_reset_password_by_factors(fresh_auth):
    fresh_auth.upsert_user("fred", "OldP@ss123", "AK2", "AS2", "OR2", "1-1", "", "", "")
    assert fresh_auth.reset_password_by_factors(
        "fred", "AK2", "AS2", "OR2", "N3wP@ssword!") is True
    assert fresh_auth.verify_password("fred", "N3wP@ssword!")["username"] == "fred"
    assert fresh_auth.verify_password("fred", "OldP@ss123") is None
    assert fresh_auth.reset_password_by_factors(
        "fred", "AK2", "AS2", "BAD", "Another1!") is False
    with pytest.raises(ValueError):
        fresh_auth.reset_password_by_factors("fred", "AK2", "AS2", "OR2", "weak")
