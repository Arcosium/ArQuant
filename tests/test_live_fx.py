"""get_usdkrw/set_usdkrw — 라이브 USD/KRW 환율 (사장 지시 2026-05-22).

환율은 계속 변하므로 하드코딩하지 않고, 5분 지수 크롤(get_index_data)이 USDKRW 를
가져올 때마다 캐시를 갱신한다. 모든 USD↔KRW 환산(예산·리스크 사이징)이 이 값을 읽는다.
"""
from tools.market_data import get_usdkrw, set_usdkrw


def test_set_get_roundtrip():
    set_usdkrw(1515.9)
    assert get_usdkrw(1300.0) == 1515.9


def test_sanity_rejects_garbage():
    set_usdkrw(1512.0)
    set_usdkrw(0)        # 거부 (<=500)
    set_usdkrw(99999)    # 거부 (>=5000)
    set_usdkrw("x")      # 거부 (비숫자)
    assert get_usdkrw(1300.0) == 1512.0


def test_default_is_positive():
    assert get_usdkrw(1500.0) > 0
