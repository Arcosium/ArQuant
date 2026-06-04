"""사장 지시 2026-06-04 ③: 유니버스 스크리닝(순수). 레버리지/인버스·저가·거래대금 미달 후보 배제.
임계 0/False=해당 기준 off. 거른 내역은 사유 동반(무음 금지). 후보 풀만 거름(최종 주문 불간섭)."""
from tools.universe_screen import screen_universe


def _items():
    return [
        {"code": "005930", "name": "삼성전자", "price": 70000, "turnover": 5_000_000_000},
        {"code": "251340", "name": "KODEX 코스닥150 인버스", "price": 3000, "turnover": 9_000_000_000},
        {"code": "900001", "name": "동전주식", "price": 300, "turnover": 8_000_000_000},
        {"code": "000001", "name": "저거래기업", "price": 50000, "turnover": 1_000_000},
    ]


def test_excludes_leveraged_by_name():
    kept, dropped = screen_universe(_items(), exclude_leveraged=True)
    assert "251340" not in kept
    assert any(c == "251340" for c, _ in dropped)


def test_excludes_low_price():
    kept, dropped = screen_universe(_items(), min_price=1000, exclude_leveraged=False)
    assert "900001" not in kept and "005930" in kept


def test_excludes_low_turnover():
    kept, dropped = screen_universe(_items(), min_turnover=100_000_000, exclude_leveraged=False)
    assert "000001" not in kept


def test_thresholds_off_keep_all_nonleveraged():
    kept, _ = screen_universe(_items(), min_price=0, min_turnover=0, exclude_leveraged=False)
    assert set(kept) == {"005930", "251340", "900001", "000001"}


def test_dropped_carries_reason():
    _, dropped = screen_universe(_items(), min_price=1000, min_turnover=100_000_000, exclude_leveraged=True)
    reasons = dict(dropped)
    assert "레버리지" in reasons.get("251340", "") or "인버스" in reasons.get("251340", "")
    assert "저가" in reasons.get("900001", "")
    assert "거래대금" in reasons.get("000001", "")


def test_missing_fields_kept():
    # price/turnover 결측 종목은 데이터 없음 → 보존(평가불가 드롭 금지)
    kept, _ = screen_universe([{"code": "111111", "name": "데이터없음"}], min_price=1000, min_turnover=100)
    assert kept == ["111111"]
