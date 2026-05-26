"""kr_account_snapshot — 결제예수금(D+1/D+2) 0 글리치 시 자산 스파이크 방지.

버그 2026-05-22: KIS 잔고는 거래 직후 결제 과도기에 prvs_rcdl_excc_amt(D+2)·nxdy_excc_amt(D+1)를
0으로 깜빡인다. 그러면 `cash = D2 or D1 or D0` 가 D0(dnca_tot_amt, 미결제 매수분이 아직 빠지지
않은 예수금)로 튀고, nass_amt 도 D0 기준이라 통합 총평가에서 매수금액이 이중계상돼 자산곡선이
스파이크 친다(관측: 4.83M→5.65M→5.23M, 한 폴 뒤 복원). settled(D1/D2)=0 & D0>0 은 글리치로 보고
직전 정상 스냅샷(settled 기반 cash/total)을 유지해야 한다 (기존 cash=0·빈보유 글리치 방어와 동형).
"""
import asyncio

from infra.kis_broker import KISBroker


def _broker_with_cached(prev_cash, prev_total, prev_holdings):
    b = object.__new__(KISBroker)  # __init__ 우회 (config/네트워크 불필요)
    b._acct_snap = {"buying_power": {"cash": prev_cash, "total_eval": prev_total,
                                     "pnl_ratio": 0.0, "ok": True},
                    "holdings": prev_holdings, "ok": True, "ts": 0.0}  # ts=0 → TTL 만료
    return b


def _raw(d2, d1, d0, scts, nass, rows=1):
    out1 = [{"pdno": "039030", "prdt_name": "이오테크닉스", "hldg_qty": "1",
             "pchs_avg_pric": "500000", "prpr": str(int(scts)), "evlu_pfls_amt": "0",
             "evlu_pfls_rt": "0"}] * rows
    return {"output1": out1,
            "output2": {"prvs_rcdl_excc_amt": str(d2), "nxdy_excc_amt": str(d1),
                        "dnca_tot_amt": str(d0), "scts_evlu_amt": str(scts),
                        "nass_amt": str(nass), "tot_evlu_amt": str(nass),
                        "evlu_pfls_smtl_amt": "0"},
            "ok": True}


def test_settlement_glitch_keeps_last_good_total():
    # D2=D1=0 글리치, D0=3,564,764 (미결제 매수분 포함) → 직전 정상값(3,693,412) 유지, 스파이크 금지
    b = _broker_with_cached(prev_cash=3_144_412, prev_total=3_693_412,
                            prev_holdings=[{"code": "039030", "qty": 1}])

    async def fake_raw():
        return _raw(d2=0, d1=0, d0=3_564_764, scts=549_000, nass=4_113_764)
    b._raw_balance = fake_raw

    snap = asyncio.run(b.kr_account_snapshot(force=True))
    bp = snap["buying_power"]
    assert bp["total_eval"] == 3_693_412, "글리치 폴은 직전 정상 총평가를 유지해야 한다(스파이크 금지)"
    assert bp["cash"] == 3_144_412, "글리치 폴은 직전 정상 결제예수금을 유지해야 한다(D0로 튀면 안 됨)"
    assert bp.get("total_stale") and bp.get("cash_stale")


def test_normal_settled_cash_unchanged():
    # 정상: D2 결제반영 예수금이 있으면 그대로 사용, stale 플래그 없음
    b = _broker_with_cached(prev_cash=9_999, prev_total=9_999, prev_holdings=[])

    async def fake_raw():
        return _raw(d2=3_144_412, d1=3_144_412, d0=3_564_764, scts=549_000, nass=3_693_412)
    b._raw_balance = fake_raw

    snap = asyncio.run(b.kr_account_snapshot(force=True))
    bp = snap["buying_power"]
    assert bp["cash"] == 3_144_412 and not bp.get("cash_stale")
    assert bp["total_eval"] == 3_693_412 and not bp.get("total_stale")
