def test_bidx_deterministic_and_normalized(fresh_auth):
    a = fresh_auth.bidx("APPKEY-123")
    assert a == fresh_auth.bidx("APPKEY-123")          # deterministic
    assert a == fresh_auth.bidx("  APPKEY-123  ")      # strips like registration
    assert a != fresh_auth.bidx("APPKEY-124")          # collision-free for distinct
    assert len(a) == 64 and all(c in "0123456789abcdef" for c in a)  # sha256 hex
