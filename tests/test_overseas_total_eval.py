"""사장 지시 2026-06-03: 사이클/리스크/표시용 총평가(kr_account_snapshot.total_eval)에 해외
외화평가총액(frcr_evlu_tota 원화환산, 캐시값)을 합산한다. KR nass_amt만 쓰면 US 보유가 0으로
사라져 자산곡선이 매수 때마다 계단식 하락하고 pnl 이 0 에 고정되던 문제(실거래 hh09080)를 막는다.

핵심 불변식:
  - total_eval_kr = KR(유가증권 + D+2 예수금)  ← 글리치 carry-forward·곡선 합산의 base
  - total_eval    = total_eval_kr + 해외 캐시   ← 헤드라인(사이클·리스크·표시)
  - 해외 캐시는 '>0' 이면 TTL 무시하고 더한다(일시 조회실패로 US가 사라지지 않게, #2).
"""
import asyncio
import time

from infra.kis_broker import KISBroker


def _make_broker(raw, overseas_krw, overseas_ts=None):
    b = object.__new__(KISBroker)
    b._acct_snap = None
    b._SNAP_TTL = 0
    b._HOLDINGS_CACHE_TTL = 99999
    b._HOLDINGS_GLITCH_MIN_GAP = 100000
    b._OVERSEAS_CACHE_TTL = 7200

    async def _raw():
        return raw
    b._raw_balance = _raw
    b._get_settled_cash_cache = lambda: 0.0
    b._set_settled_cash_cache = lambda v: None
    b._get_holdings_cache = lambda: ([], 0.0, 0.0)
    b._set_holdings_cache = lambda *a, **k: None
    b._clear_holdings_cache = lambda: None
    b._get_overseas_cache = lambda: (overseas_krw, overseas_ts if overseas_ts is not None else time.time())
    return b


def _raw_kr(cash_d2, scts, pnl=0.0, holdings=None):
    return {"ok": True, "output1": holdings or [],
            "output2": {"prvs_rcdl_excc_amt": cash_d2, "nxdy_excc_amt": cash_d2,
                        "scts_evlu_amt": scts, "evlu_pfls_smtl_amt": pnl}}


def test_overseas_added_to_total_eval():
    # KR 예수금 5,000,000 · KR 유가증권 0 (US만 보유) · 해외 캐시 130,000원
    b = _make_broker(_raw_kr(cash_d2=5_000_000, scts=0), overseas_krw=130_000)
    bp = asyncio.run(b.kr_account_snapshot())["buying_power"]
    assert bp["total_eval_kr"] == 5_000_000           # KR 기준
    assert bp["total_eval"] == 5_130_000               # 해외 합산된 헤드라인 (US가 사라지지 않음)
    assert bp["cash"] == 5_000_000                      # 예수금은 KR D+2 그대로
    assert bp["overseas_krw"] == 130_000


def test_no_overseas_when_cache_zero():
    # 해외 캐시 0 (실제 US 전량매도) → total_eval == total_eval_kr
    b = _make_broker(_raw_kr(cash_d2=5_000_000, scts=0), overseas_krw=0)
    bp = asyncio.run(b.kr_account_snapshot())["buying_power"]
    assert bp["total_eval"] == bp["total_eval_kr"] == 5_000_000
    assert "overseas_krw" not in bp


def test_stale_overseas_still_preserved_no_ttl_drop():
    # 캐시가 오래됐어도(>TTL) 0이 아니면 보존해 더한다 — 일시 조회실패로 US 평가가 0으로 떨어지지 않게(#2).
    old_ts = time.time() - 10 * 3600  # 10시간 전 (TTL 2h 초과)
    b = _make_broker(_raw_kr(cash_d2=5_000_000, scts=0), overseas_krw=130_000, overseas_ts=old_ts)
    bp = asyncio.run(b.kr_account_snapshot())["buying_power"]
    assert bp["total_eval"] == 5_130_000


def test_kr_holdings_also_summed_with_overseas():
    # KR 유가증권 2,000,000 + KR 예수금 3,000,000 + 해외 130,000 = 5,130,000
    b = _make_broker(_raw_kr(cash_d2=3_000_000, scts=2_000_000), overseas_krw=130_000)
    bp = asyncio.run(b.kr_account_snapshot())["buying_power"]
    assert bp["total_eval_kr"] == 5_000_000
    assert bp["total_eval"] == 5_130_000
