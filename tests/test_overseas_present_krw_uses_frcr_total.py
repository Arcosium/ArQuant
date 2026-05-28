"""CTRP6504R: 외화평가총액은 'frcr_evlu_tota'(USD 예수금 포함)를 써야 KIS 앱의 총자산과 일치한다.

배경 (2026-05-28 사장 보고·실측):
  - 우리 코드는 output3.evlu_amt_smtl_amt (해외 '주식만' 평가합계)를 더해왔다 → 24,280원.
  - 실제로 사장 계정엔 USD 484.26불 예수금이 있어 KIS의 frcr_evlu_tota=727,164원.
  - 결과적으로 우리 화면 8,006,870원 vs KIS 앱 8,744,275원 → 격차 ~737K.
  - 사장 지시 2026-05-28: '외화평가총액 + D+2 예수금'의 외화평가총액 = USD 예수금 포함값
    (frcr_evlu_tota)이 의도였다.

요구 동작:
  - _overseas_present_krw().krw_value == output3.frcr_evlu_tota.
  - evlu_amt_smtl_amt 만 있을 땐 그것으로 폴백(이전 동작 호환).
  - 둘 다 0이고 USD 예수금도 없으면 0 (변동 없음).
"""
import asyncio
import pytest

from infra.kis_broker import KISBroker


class _FakeResp:
    def __init__(self, payload):
        self._p = payload
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False
    async def json(self):
        return self._p


class _FakeSession:
    def __init__(self, payload):
        self._p = payload
    def get(self, url, headers=None, params=None):
        return _FakeResp(self._p)


_TEST_CREDS = {"kis_app_key": "K", "kis_app_secret": "S",
               "kis_account_no": "12345678-01",
               "kis_base_url": "https://openapi.koreainvestment.com:9443"}


def _broker_with_payload(monkeypatch, payload):
    b = KISBroker(_TEST_CREDS)
    fake = _FakeSession(payload)

    async def _tok():
        return "TESTTOKEN"

    async def _sess():
        return fake

    monkeypatch.setattr(b, "token", _tok)
    monkeypatch.setattr(b, "_s", _sess)
    return b


def test_uses_frcr_evlu_tota_when_present(monkeypatch):
    """USD 예수금 484불·주식 0건 → frcr_evlu_tota(727,164) 가 권위값."""
    payload = {
        "rt_cd": "0", "msg1": "조회되었습니다",
        "output2": [{"frst_bltn_exrt": "1501.6"}],
        "output3": {
            "evlu_amt_smtl_amt": "0",         # 해외 주식 평가합계(0)
            "frcr_evlu_tota": "727164",       # 외화평가총액(USD 예수금 포함)
            "tot_asst_amt": "8503389",
        },
    }
    b = _broker_with_payload(monkeypatch, payload)
    pk = asyncio.run(b._overseas_present_krw())
    assert pk["ok"] is True
    assert pk["krw_value"] == pytest.approx(727164.0), (
        f"frcr_evlu_tota(외화평가총액)을 KRW로 반환해야 KIS 앱과 일치: {pk}")


def test_frcr_evlu_tota_takes_precedence_over_stock_only(monkeypatch):
    """둘 다 0이 아닐 때(USD 예수금+주식 모두 있을 때) 큰 값(frcr_evlu_tota)이 권위값."""
    payload = {
        "rt_cd": "0", "msg1": "조회되었습니다",
        "output2": [{"frst_bltn_exrt": "1501.6"}],
        "output3": {
            "evlu_amt_smtl_amt": "24280",     # 해외 주식 평가(SOFI 1주)
            "frcr_evlu_tota": "727164",       # 외화평가총액(USD 484.26 + 주식 24K)
        },
    }
    b = _broker_with_payload(monkeypatch, payload)
    pk = asyncio.run(b._overseas_present_krw())
    assert pk["krw_value"] == pytest.approx(727164.0)


def test_falls_back_to_evlu_smtl_when_frcr_total_missing(monkeypatch):
    """KIS 응답에 frcr_evlu_tota가 없으면(스키마 변동·모의계좌) 이전 필드로 폴백."""
    payload = {
        "rt_cd": "0", "msg1": "조회되었습니다",
        "output2": [{"frst_bltn_exrt": "1501.6"}],
        "output3": {
            "evlu_amt_smtl_amt": "24280",
            # frcr_evlu_tota 없음
        },
    }
    b = _broker_with_payload(monkeypatch, payload)
    pk = asyncio.run(b._overseas_present_krw())
    assert pk["krw_value"] == pytest.approx(24280.0)


def test_zero_when_both_missing(monkeypatch):
    payload = {
        "rt_cd": "0", "msg1": "조회되었습니다",
        "output2": [{"frst_bltn_exrt": "1501.6"}],
        "output3": {},
    }
    b = _broker_with_payload(monkeypatch, payload)
    pk = asyncio.run(b._overseas_present_krw())
    assert pk["krw_value"] == 0.0


def test_failure_response_returns_not_ok(monkeypatch):
    payload = {"rt_cd": "1", "msg1": "오류"}
    b = _broker_with_payload(monkeypatch, payload)
    pk = asyncio.run(b._overseas_present_krw())
    assert pk["ok"] is False
    assert pk["krw_value"] == 0.0
