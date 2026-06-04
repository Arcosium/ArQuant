"""KIS 잔고 신뢰성 — 해외 평가 배선(Group C/D):
  Task 11: _overseas_present_krw 필드 보강(tot_asst_amt·외화예수금) + NATN_CD '000'(전체국가).
  Task 12: portfolio_holdings 곡선 해외분=결제기준(CTRP6010R) 우선, 대시보드 표시=KIS 통합총자산.
"""
import asyncio

from infra.kis_broker import KISBroker


def _creds(mock=False):
    return {
        "kis_app_key": "APPKEY", "kis_app_secret": "SECRET",
        "kis_account_no": "12345678-01",
        "kis_base_url": ("https://openapivts.koreainvestment.com:29443" if mock
                         else "https://openapi.koreainvestment.com:9443"),
    }


# ── Task 11: present-balance 필드 보강 + NATN_CD '000' ─────────────────────────
def test_overseas_present_krw_enriched_and_natn_000(tmp_path):
    b = KISBroker(_creds(mock=False), token_path=tmp_path / "t.json")
    cap = []
    body = {
        "rt_cd": "0",
        "output3": {"frcr_evlu_tota": "154093", "evlu_amt_smtl_amt": "20443",
                    "tot_asst_amt": "7253339", "tot_dncl_amt": "6552999",
                    "tot_evlu_pfls_amt": "38"},
        "output2": [{"frst_bltn_exrt": "1503.2", "frcr_dncl_amt_2": "100"}],
    }

    async def _gj(path, tr_id, params):
        cap.append(dict(params))
        return body

    async def _authed(mr):   # 구현이 _authed_json 경로면 네트워크 대신 body 반환(깨끗한 RED)
        return body

    b._get_json = _gj
    b._authed_json = _authed
    r = asyncio.run(b._overseas_present_krw())
    assert r["ok"] is True and r["krw_value"] == 154093.0 and r["exrt"] == 1503.2
    assert r["tot_asst_amt"] == 7253339.0, "통합총자산 교차검증용 필드 파싱"
    assert r["deposit_krw"] == 100 * 1503.2, "외화예수금(frcr_dncl_amt_2)×환율 직접 산출"
    assert cap and cap[0]["NATN_CD"] == "000", "전체국가(미국 외 보유 누락 방지)"


# ── Task 12: portfolio_holdings — 곡선 해외분=결제기준, 표시=KIS 통합총자산 ──────
def _stub_portfolio(b, *, snap, present, settled, asset):
    async def _snap(*a, **k):
        return snap

    async def _oh():
        return []

    async def _bh():
        return []

    async def _fh():
        return []

    async def _pk():
        return present

    async def _sk():
        return settled

    async def _aa():
        return asset

    b.kr_account_snapshot = _snap
    b._overseas_holdings = _oh
    b._bond_holdings = _bh
    b._fund_holdings = _fh
    b._overseas_present_krw = _pk
    b._overseas_settled_krw = _sk
    b.kr_account_asset = _aa


def test_curve_uses_settled_overseas_and_display_uses_kis_total(tmp_path):
    b = KISBroker(_creds(mock=False), token_path=tmp_path / "t.json")
    _stub_portfolio(
        b,
        snap={"buying_power": {"total_eval": 100.0, "cash": 80.0, "ok": True}, "holdings": [], "ok": True},
        present={"ok": True, "krw_value": 50.0, "stock_value": 20.0, "exrt": 1500.0},   # 실시간 해외=50
        settled={"ok": True, "krw": 40.0, "tot_asst_amt2": 140.0},                       # 결제기준 해외=40
        asset={"ok": True, "tot_asst_amt": 200.0, "tot_dncl_amt": 60.0},                 # KIS 통합총자산=200
    )
    res = asyncio.run(b.portfolio_holdings())
    bp = res["buying_power"]
    assert bp["total_eval"] == 140.0, "곡선식 total_eval = 국내 100 + 결제기준 해외 40 (실시간 50 아님)"
    assert bp.get("overseas_settled") is True
    assert bp["display_total_asset"] == 200.0, "대시보드 현재총자산 = KIS 통합총자산(표시 전용)"


def test_curve_falls_back_to_realtime_when_no_settled(tmp_path):
    b = KISBroker(_creds(mock=False), token_path=tmp_path / "t.json")
    _stub_portfolio(
        b,
        snap={"buying_power": {"total_eval": 100.0, "cash": 80.0, "ok": True}, "holdings": [], "ok": True},
        present={"ok": True, "krw_value": 50.0, "stock_value": 20.0, "exrt": 1500.0},
        settled={"ok": False},        # 결제기준 실패(모의 등)
        asset={"ok": False},
    )
    res = asyncio.run(b.portfolio_holdings())
    bp = res["buying_power"]
    assert bp["total_eval"] == 150.0, "결제기준 없으면 실시간 해외(50)로 폴백"
    assert "display_total_asset" not in bp
