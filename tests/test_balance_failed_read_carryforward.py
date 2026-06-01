"""kr_account_snapshot — '조회 실패(ok=False)' 시에도 직전 정상값을 유지한다.

버그(2026-05-29 사장 제보 — 모의계좌 hh0908): 모의 KIS 서버가 토큰 rate-limit/일시 오류로
rt_cd≠0(=ok=False)을 뱉으면, 기존 글리치 가드 3종(holdings·cash·total carry-forward)이
**모두 `snap["ok"]` 가 True 일 때만** 동작하므로 하나도 발동하지 않는다.
→ holdings=[] · (콜드스타트 시) cash=0 이 그대로 반환되어:
  (a) 사이클 사전 게이트가 '예수금 부족'으로 사이클을 시작하지 않고,
  (b) 매도 평가가 '보유 종목 없음'으로 매도를 누락/거부한다.
1분 뒤 다음 스냅샷이 ok=True 로 떠 가드가 다시 채워지며 자동 복구된다.

해결: 조회 실패(ok=False)도 글리치의 한 종류로 보고 직전 정상 보유목록·예수금·총평가를
유지한다(in-memory 우선, 없으면 디스크 캐시). 매수는 guardrails 가 ok=False 를 보고 여전히
보수적으로 막으므로, 이 carry-forward 는 사이클 진행·매도 평가만 살리고 오발주를 만들지 않는다.
"""
import asyncio
import time

from infra.kis_broker import KISBroker


def _raw_failed():
    """KIS 조회 실패 폴 — rt_cd≠0, output1/2 비어있음(토큰만료/rate-limit 등)."""
    return {"output1": [], "output2": {}, "ok": False, "rt_cd": "1",
            "msg1": "기간이 만료된 token 입니다."}


def _broker(prev_holdings=None, prev_cash=0.0, prev_total=0.0, settled=None, tmp_path=None):
    b = object.__new__(KISBroker)
    b._acct_snap = None
    # 디스크 캐시 경로(per-uid). tmp_path 주면 디스크 폴백 경로도 검증 가능.
    b._token_path = (tmp_path / "kis_token.json") if tmp_path else None
    b._holdings_cache = None
    b._settled_cash = settled  # None → carry-forward 안 함
    if prev_holdings is not None:
        b._acct_snap = {"buying_power": {"cash": prev_cash, "total_eval": prev_total,
                                         "pnl_ratio": 0.0, "ok": True},
                        "holdings": prev_holdings, "ok": True, "ts": 0.0}  # ts=0 → TTL 만료
    return b


def test_failed_read_keeps_last_good_holdings_inmemory():
    """ok=False + in-memory 직전 정상 스냅샷 → 보유목록·예수금·총평가를 유지(stale)."""
    b = _broker(prev_holdings=[{"code": "003490", "name": "대한항공", "qty": 185,
                                "avg_price": 22000.0, "cur_price": 23000.0}],
                prev_cash=80_000.0, prev_total=4_255_000.0)

    async def fake_raw():
        return _raw_failed()
    b._raw_balance = fake_raw

    snap = asyncio.run(b.kr_account_snapshot(force=True))
    assert [h["code"] for h in snap["holdings"]] == ["003490"], \
        "조회 실패라도 직전 정상 보유목록(대한항공)을 유지해야 한다"
    assert snap.get("holdings_stale") is True
    assert snap["buying_power"]["cash"] == 80_000.0, "조회 실패 시 예수금도 직전값 유지(0원으로 무너지면 사이클 스킵)"
    assert snap["buying_power"]["total_eval"] == 4_255_000.0


def test_failed_read_restores_holdings_from_disk_on_cold_start(tmp_path):
    """콜드스타트(in-memory 스냅샷 없음) + ok=False + 디스크 캐시 → 디스크 보유목록 복원."""
    b = _broker(prev_holdings=None, tmp_path=tmp_path)
    b._set_holdings_cache([{"code": "003490", "name": "대한항공", "qty": 185}],
                          total=4_255_000.0, ts=time.time())
    b._holdings_cache = None  # 디스크에서 읽도록(콜드스타트 재현)

    async def fake_raw():
        return _raw_failed()
    b._raw_balance = fake_raw

    snap = asyncio.run(b.kr_account_snapshot(force=True))
    assert "003490" in [h["code"] for h in snap["holdings"]], \
        "재시작 직후 조회 실패라도 디스크 캐시로 보유목록을 복원해야 한다"
    assert snap.get("holdings_stale") is True


def test_failed_read_no_prior_data_stays_empty():
    """직전 정상값이 아무 데도 없으면(최초 가동) 복원할 게 없으니 빈 채로 — 오버리커버리 금지."""
    b = _broker(prev_holdings=None)

    async def fake_raw():
        return _raw_failed()
    b._raw_balance = fake_raw

    snap = asyncio.run(b.kr_account_snapshot(force=True))
    assert snap["holdings"] == []
    assert snap["ok"] is False, "조회 실패 자체는 ok=False 로 정직하게 표기(매수 가드가 이를 보고 보수적으로 반려)"
