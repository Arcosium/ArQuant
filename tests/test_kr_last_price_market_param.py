"""kr_price/kr_last_price 가 market 인자를 FID_COND_MRKT_DIV_CODE 로 전달."""
import asyncio
import pytest
from infra.kis_broker import KISBroker

_CREDS = {
    "kis_app_key": "K", "kis_app_secret": "S",
    "kis_account_no": "12345678-01",
    "kis_base_url": "https://openapi.koreainvestment.com:9443",
}


class _FakeResp:
    def __init__(self, body):
        self._body = body
        self.headers = {"tr_cont": ""}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self):
        return self._body


class _Sess:
    def __init__(self):
        self.params = []

    def get(self, url, headers=None, params=None):
        self.params.append(dict(params or {}))
        return _FakeResp({"output": {"stck_prpr": "70000"}})


def _broker(monkeypatch, sess, tmp_path):
    b = KISBroker(_CREDS, token_path=tmp_path / "tok.json")

    async def _tok(force=False):
        return "T"

    async def _s():
        return sess

    monkeypatch.setattr(b, "token", _tok)
    monkeypatch.setattr(b, "_s", _s)
    b._min_interval = 0.0
    return b


def test_default_market_is_krx(monkeypatch, tmp_path):
    s = _Sess()
    b = _broker(monkeypatch, s, tmp_path)
    asyncio.run(b.kr_price("005930"))
    assert s.params[0]["FID_COND_MRKT_DIV_CODE"] == "J"


def test_nxt_market_param(monkeypatch, tmp_path):
    s = _Sess()
    b = _broker(monkeypatch, s, tmp_path)
    asyncio.run(b.kr_price("005930", market="NX"))
    assert s.params[0]["FID_COND_MRKT_DIV_CODE"] == "NX"


def test_kr_last_price_threads_market(monkeypatch, tmp_path):
    s = _Sess()
    b = _broker(monkeypatch, s, tmp_path)
    px = asyncio.run(b.kr_last_price("005930", market="UN"))
    assert px == 70000.0
    assert s.params[0]["FID_COND_MRKT_DIV_CODE"] == "UN"
