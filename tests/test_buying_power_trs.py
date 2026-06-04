"""KIS 잔고 신뢰성 — 신규 권위조회 6종(Group B):
  매수가능 국내(TTTC8908R)·해외(TTTS3007R), 매도가능(TTTC8408R),
  통합총자산(CTRP6548R, 표시용), 결제기준 해외평가(CTRP6010R, 곡선용), 실현손익 감사(TTTC8494R).
실전 전용 조회(6548/6010/8494)는 is_mock 이면 호출 자체를 skip 한다. (2026-06-01 라이브 필드 검증 반영)
_get_json 을 스텁해 파싱·요청구성(tr_id/params)을 검증."""
import asyncio

from infra.kis_broker import KISBroker


def _creds(mock=False):
    return {
        "kis_app_key": "APPKEY", "kis_app_secret": "SECRET",
        "kis_account_no": "12345678-01",
        "kis_base_url": ("https://openapivts.koreainvestment.com:29443" if mock
                         else "https://openapi.koreainvestment.com:9443"),
    }


def _stub(b, body, cap):
    async def _gj(path, tr_id, params):
        cap.append({"path": path, "tr_id": tr_id, "params": dict(params)})
        return body
    b._get_json = _gj


# ── Task 4: 국내 매수가능 TTTC8908R (ORD_DVSN='01' 필수) ─────────────────────────
def test_kr_psbl_order_parses_buy_qty(tmp_path):
    b = KISBroker(_creds(), token_path=tmp_path / "t.json")
    cap = []
    _stub(b, {"rt_cd": "0", "output": {"nrcvb_buy_qty": "16", "ord_psbl_cash": "6552999"}}, cap)
    r = asyncio.run(b.kr_psbl_order("005930", 55000))
    assert r["ok"] is True and r["buy_qty"] == 16 and r["cash"] == 6552999.0
    assert cap[0]["tr_id"] == "TTTC8908R"
    assert cap[0]["params"]["ORD_DVSN"] == "01", "증거금율 반영 위해 시장가('01') 필수"
    assert cap[0]["params"]["PDNO"] == "005930"


def test_kr_psbl_order_failure_returns_not_ok(tmp_path):
    b = KISBroker(_creds(), token_path=tmp_path / "t.json")
    _stub(b, {"rt_cd": "1", "msg1": "x", "output": {}}, [])
    r = asyncio.run(b.kr_psbl_order("005930", 0))
    assert r["ok"] is False and r["buy_qty"] is None


# ── Task 5: 국내 매도가능 TTTC8408R ────────────────────────────────────────────
def test_kr_psbl_sell_qty(tmp_path):
    b = KISBroker(_creds(), token_path=tmp_path / "t.json")
    cap = []
    _stub(b, {"rt_cd": "0", "output": {"ord_psbl_qty": "3"}}, cap)
    assert asyncio.run(b.kr_psbl_sell_qty("005930")) == 3
    assert cap[0]["tr_id"] == "TTTC8408R" and cap[0]["params"]["PDNO"] == "005930"


def test_kr_psbl_sell_qty_empty_returns_none(tmp_path):
    b = KISBroker(_creds(), token_path=tmp_path / "t.json")
    _stub(b, {"rt_cd": "0", "output": {"ord_psbl_qty": ""}}, [])
    assert asyncio.run(b.kr_psbl_sell_qty("005930")) is None


# ── Task 6: 해외 매수가능 TTTS3007R ────────────────────────────────────────────
def test_us_buying_power(tmp_path):
    b = KISBroker(_creds(), token_path=tmp_path / "t.json")
    cap = []
    _stub(b, {"rt_cd": "0", "output": {"ord_psbl_frcr_amt": "102.51",
                                       "max_ord_psbl_qty": "2", "exrt": "1503.2"}}, cap)
    r = asyncio.run(b.us_buying_power("AAPL", 200.0, "NASD"))
    assert r["ok"] is True and r["usd"] == 102.51 and r["qty"] == 2 and r["exrt"] == 1503.2
    assert cap[0]["tr_id"] == "TTTS3007R"
    assert cap[0]["params"]["ITEM_CD"] == "AAPL" and cap[0]["params"]["OVRS_EXCG_CD"] == "NASD"


# ── Task 7: 통합총자산 CTRP6548R (대시보드 표시용, 실전만) ──────────────────────
def test_kr_account_asset_real(tmp_path):
    b = KISBroker(_creds(mock=False), token_path=tmp_path / "t.json")
    cap = []
    _stub(b, {"rt_cd": "0", "output2": {"tot_asst_amt": "8666418", "tot_dncl_amt": "6552999"}}, cap)
    r = asyncio.run(b.kr_account_asset())
    assert r["ok"] is True and r["tot_asst_amt"] == 8666418.0 and r["tot_dncl_amt"] == 6552999.0
    assert cap[0]["tr_id"] == "CTRP6548R"


def test_kr_account_asset_skips_on_mock(tmp_path):
    b = KISBroker(_creds(mock=True), token_path=tmp_path / "t.json")
    cap = []
    _stub(b, {"rt_cd": "0", "output2": {"tot_asst_amt": "1"}}, cap)
    r = asyncio.run(b.kr_account_asset())
    assert r["ok"] is False and cap == [], "모의는 CTRP6548R 미지원 → 호출 자체 skip"


# ── Task 8: 결제기준 해외평가 CTRP6010R (곡선용, 실전만) ────────────────────────
def test_overseas_settled_krw_real(tmp_path):
    b = KISBroker(_creds(mock=False), token_path=tmp_path / "t.json")
    cap = []
    _stub(b, {"rt_cd": "0", "output3": {"frcr_cblc_wcrc_evlu_amt_smtl": "154093",
                                        "tot_asst_amt2": "7285493"}}, cap)
    r = asyncio.run(b._overseas_settled_krw())
    assert r["ok"] is True and r["krw"] == 154093.0 and r["tot_asst_amt2"] == 7285493.0
    assert cap[0]["tr_id"] == "CTRP6010R" and cap[0]["params"]["WCRC_FRCR_DVSN_CD"] == "01"


def test_overseas_settled_krw_skips_on_mock(tmp_path):
    b = KISBroker(_creds(mock=True), token_path=tmp_path / "t.json")
    cap = []
    _stub(b, {"rt_cd": "0", "output3": {"frcr_cblc_wcrc_evlu_amt_smtl": "1"}}, cap)
    r = asyncio.run(b._overseas_settled_krw())
    assert r["ok"] is False and cap == []


# ── Task 9: 실현손익 감사 TTTC8494R (실전만, 주문 무영향) ───────────────────────
def test_kr_realized_pnl_audit_real(tmp_path):
    # 라이브 검증(2026-06-01): 실현손익 rlzt_pfls 는 output2(요약)에 있다(output1 아님).
    b = KISBroker(_creds(mock=False), token_path=tmp_path / "t.json")
    cap = []
    _stub(b, {"rt_cd": "0", "output2": {"rlzt_pfls": "-8540", "rlzt_erng_rt": "-2.34"}}, cap)
    r = asyncio.run(b.kr_realized_pnl_audit())
    assert r["ok"] is True and r["realized"] == -8540.0 and r["realized_rt"] == -2.34
    assert cap[0]["tr_id"] == "TTTC8494R" and cap[0]["params"]["COST_ICLD_YN"] == "Y"


def test_kr_realized_pnl_audit_skips_on_mock(tmp_path):
    b = KISBroker(_creds(mock=True), token_path=tmp_path / "t.json")
    cap = []
    _stub(b, {"rt_cd": "0", "output1": []}, cap)
    r = asyncio.run(b.kr_realized_pnl_audit())
    assert r["ok"] is False and cap == []
