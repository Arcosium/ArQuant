"""세션→거래소 결정을 한 곳으로 집약하는 헬퍼군."""
import main_swarm as m

def test_is_kr_session():
    for s in ("KR_TRADING", "KR_PRE_MARKET", "KR_AFTER_MARKET", "KR_CLOSE_REVIEW"):
        assert m.is_kr_session(s) is True
    for s in ("US_TRADING", "OFF_HOURS"):
        assert m.is_kr_session(s) is False

def test_is_kr_tradable_excludes_review():
    assert m.is_kr_tradable("KR_TRADING") is True
    assert m.is_kr_tradable("KR_PRE_MARKET") is True
    assert m.is_kr_tradable("KR_AFTER_MARKET") is True
    assert m.is_kr_tradable("KR_CLOSE_REVIEW") is False   # 리뷰는 매매 X
    assert m.is_kr_tradable("US_TRADING") is False

def test_is_kr_extended_hours():
    assert m.is_kr_extended_hours("KR_PRE_MARKET") is True
    assert m.is_kr_extended_hours("KR_AFTER_MARKET") is True
    assert m.is_kr_extended_hours("KR_TRADING") is False
    assert m.is_kr_extended_hours("KR_CLOSE_REVIEW") is False

def test_kr_exchange_for_session():
    assert m.kr_exchange_for_session("KR_TRADING") == "KRX"
    assert m.kr_exchange_for_session("KR_CLOSE_REVIEW") == "KRX"
    assert m.kr_exchange_for_session("KR_PRE_MARKET") == "NXT"
    assert m.kr_exchange_for_session("KR_AFTER_MARKET") == "NXT"
