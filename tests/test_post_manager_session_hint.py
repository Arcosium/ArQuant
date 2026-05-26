"""사후관리실장(매도 판단) 프롬프트의 세션 안내 (버그 2026-05-23 04:47).

버그: 사후관리실장 프롬프트에 '지금 무슨 장인지'가 빠져 있어, US_TRADING 사이클에서
LLM이 '현재 KR 장중이라 미국 시장이 닫혀 매매 불가'로 환각 → QUBT/UUP/DAL 매도 신호를
무시하고 보유. 이 테스트는 세션별 안내문이 '지금 열린 시장 + 즉시 매도 가능 + 닫힘 추측 금지'를
정확히 담는지 고정한다.
"""
from main_swarm import _post_manager_session_hint


def test_us_session_says_us_open_kr_closed():
    h = _post_manager_session_hint("US_TRADING")
    assert "미국 정규장" in h
    assert "한국 장은 마감" in h
    assert "즉시 매도 가능" in h
    # 버그의 환각 문구를 명시적으로 금지하는지
    assert "매매 불가" in h and "사실과 다르" in h


def test_kr_sessions_say_kr_open_us_closed():
    for s in ("KR_TRADING", "KR_PRE_MARKET", "KR_CLOSE_REVIEW"):
        h = _post_manager_session_hint(s)
        assert "한국 장" in h
        assert "미국 장은 마감" in h
        assert "즉시 매도 가능" in h


def test_offhours_no_false_open_claim():
    h = _post_manager_session_hint("OFF_HOURS")
    assert "장외" in h
    # 장외엔 '지금 열려 있다'고 단정하면 안 됨
    assert "열려 있는 시장" not in h
