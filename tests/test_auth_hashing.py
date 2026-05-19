from infra import auth_store


def test_hash_and_verify_roundtrip():
    h = auth_store.hash_password("Sup3r$ecret!")
    assert h.startswith("$argon2id$")
    assert auth_store.verify_pw_hash(h, "Sup3r$ecret!") is True
    assert auth_store.verify_pw_hash(h, "wrong") is False
    assert auth_store.verify_pw_hash("", "anything") is False
    assert auth_store.verify_pw_hash("not-a-hash", "x") is False
