"""kr_account_snapshot — 보유목록 글리치 가드의 '재시작 갭' 방어 (디스크 영속).

버그(2026-05-29 사장 제보): 모의계좌(hh0908)에 대한항공(003490)이 분명 보유 중인데
'보유 종목 없음/잔고내역 없음'으로 떴다. 백엔드 조회는 정상이나, KIS가 보유목록을 일시적으로
빈 채로 주는 글리치(기존 알려진 부류)가 **서버 재시작 직후**(in-memory 캐시 소실 = 콜드스타트)
발생하면, 기존 글리치 가드는 in-memory `cached` 가 있을 때만 동작해 막지 못했다.
→ settled_cash·overseas_krw 처럼 보유목록도 디스크에 영속해 재시작 갭을 메운다.
정상 매도로 진짜 평탄해지면(빈 보유 + 총평가≈예수금) 캐시를 즉시 무효화해 유령 보유를 막는다.
"""
import asyncio
import time

from infra.kis_broker import KISBroker


def _cold_broker(tmp_path):
    """__init__ 우회 + 임시 token_path(캐시 파일이 tmp 로 가게). in-memory 스냅샷 없음(콜드스타트)."""
    b = object.__new__(KISBroker)
    b._token_path = tmp_path / "kis_token.json"
    b._acct_snap = None
    return b


def _raw_empty(scts, d2):
    """KIS 글리치 폴: 보유목록(output1) 빈데 총평가(scts)는 포지션 존재를 시사."""
    return {"output1": [],
            "output2": {"prvs_rcdl_excc_amt": str(d2), "nxdy_excc_amt": str(d2),
                        "dnca_tot_amt": str(d2), "scts_evlu_amt": str(scts),
                        "nass_amt": str(scts + d2), "tot_evlu_amt": str(scts + d2),
                        "evlu_pfls_smtl_amt": "0"},
            "ok": True}


def _raw_good(code, qty, avg, prpr, d2):
    return {"output1": [{"pdno": code, "prdt_name": "대한항공", "hldg_qty": str(qty),
                         "pchs_avg_pric": str(avg), "prpr": str(prpr),
                         "evlu_pfls_amt": "0", "evlu_pfls_rt": "0"}],
            "output2": {"prvs_rcdl_excc_amt": str(d2), "nxdy_excc_amt": str(d2),
                        "dnca_tot_amt": str(d2), "scts_evlu_amt": str(qty * prpr),
                        "nass_amt": str(qty * prpr + d2), "tot_evlu_amt": str(qty * prpr + d2),
                        "evlu_pfls_smtl_amt": "0"},
            "ok": True}


def test_cold_start_glitch_restores_from_disk_cache(tmp_path):
    """콜드스타트(in-memory 캐시 없음) + KIS 빈 보유 글리치 + 디스크 last-good 있음
    → 디스크 캐시의 보유목록을 복원하고 holdings_stale 표시."""
    b = _cold_broker(tmp_path)
    # 디스크에 직전 정상 보유목록 영속(재시작 전 저장된 상태 모사)
    b._set_holdings_cache([{"code": "003490", "name": "대한항공", "qty": 185,
                            "avg_price": 22000.0, "cur_price": 23000.0,
                            "pnl_amt": 0.0, "pnl_pct": 0.0}], total=4_255_000.0, ts=time.time())
    b._holdings_cache = None  # in-memory 무효화 → 디스크에서 읽도록(콜드스타트 재현)

    async def fake_raw():
        return _raw_empty(scts=4_200_000, d2=80_000)
    b._raw_balance = fake_raw

    snap = asyncio.run(b.kr_account_snapshot(force=True))
    codes = [h["code"] for h in snap["holdings"]]
    assert "003490" in codes, "재시작 직후 글리치라도 디스크 캐시로 대한항공을 복원해야 한다"
    assert snap.get("holdings_stale") is True


def test_genuinely_flat_clears_cache(tmp_path):
    """진짜 평탄(빈 보유 + 총평가≈예수금) → 디스크 캐시 무효화, 유령 보유 금지."""
    b = _cold_broker(tmp_path)
    b._set_holdings_cache([{"code": "003490", "qty": 185}], total=4_255_000.0, ts=time.time())
    b._holdings_cache = None

    async def fake_raw():
        return _raw_empty(scts=0, d2=4_255_000)  # 총평가≈예수금 = 포지션 없음
    b._raw_balance = fake_raw

    snap = asyncio.run(b.kr_account_snapshot(force=True))
    assert snap["holdings"] == [], "진짜 평탄이면 유령 보유를 띄우면 안 된다"
    hs, tot, ts = b._get_holdings_cache()
    assert hs == [], "평탄 확인 시 디스크 캐시를 무효화해야 한다"


def test_good_read_persists_to_disk(tmp_path):
    """정상 보유 읽기는 디스크 캐시에 last-good 으로 영속된다(다음 재시작 갭 방어)."""
    b = _cold_broker(tmp_path)

    async def fake_raw():
        return _raw_good("003490", 185, 22000, 23000, d2=80_000)
    b._raw_balance = fake_raw

    snap = asyncio.run(b.kr_account_snapshot(force=True))
    assert [h["code"] for h in snap["holdings"]] == ["003490"]
    assert not snap.get("holdings_stale")
    hs, tot, ts = b._get_holdings_cache()
    assert [h["code"] for h in hs] == ["003490"], "정상 읽기는 디스크에 영속돼야 한다"


def test_no_cache_no_glitch_recovery(tmp_path):
    """디스크 캐시도 없으면(첫 가동) 복원할 게 없으니 빈 보유 그대로(오버리커버리 금지)."""
    b = _cold_broker(tmp_path)

    async def fake_raw():
        return _raw_empty(scts=4_200_000, d2=80_000)
    b._raw_balance = fake_raw

    snap = asyncio.run(b.kr_account_snapshot(force=True))
    assert snap["holdings"] == []
