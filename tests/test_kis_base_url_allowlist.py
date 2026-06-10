import asyncio

from server import app


def test_validate_kis_rejects_arbitrary_url_without_network_call(monkeypatch):
    called = False

    class _Session:
        def __init__(self, *args, **kwargs):
            nonlocal called
            called = True

    monkeypatch.setattr(app.aiohttp, "ClientSession", _Session)
    ok, message = asyncio.run(app._validate_kis("key", "secret", "http://127.0.0.1:8080"))
    assert ok is False
    assert "실전투자 또는 모의투자" in message
    assert called is False


def test_kis_allowlist_contains_only_official_real_and_mock_urls():
    assert app.ALLOWED_KIS_BASE_URLS == {
        "https://openapi.koreainvestment.com:9443",
        "https://openapivts.koreainvestment.com:29443",
    }
