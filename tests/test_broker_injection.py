from infra.kis_broker import KISBroker


def _creds(mock=False):
    return {
        "kis_app_key": "APPKEY", "kis_app_secret": "SECRET",
        "kis_account_no": "12345678-01",
        "kis_base_url": ("https://openapivts.koreainvestment.com:29443" if mock
                         else "https://openapi.koreainvestment.com:9443"),
    }


def test_broker_reads_injected_creds_not_config(tmp_path):
    b = KISBroker(_creds(mock=True), token_path=tmp_path / "tok.json")
    assert b.app_key == "APPKEY"
    assert b.account_no == "12345678-01"
    assert b.is_mock is True
    assert b._acnt() == ("12345678", "01")


def test_token_file_is_per_uid_path(tmp_path):
    p = tmp_path / "kis_token.json"
    b = KISBroker(_creds(), token_path=p)
    b._save_token_file("TOKEN123", 9999999999.0)
    assert p.exists()
    loaded = b._load_token_file()
    assert loaded["access_token"] == "TOKEN123"


def test_overseas_cache_is_per_uid(tmp_path):
    """해외 원화평가 캐시가 계정(uid)별로 분리돼야 한 계정의 해외평가가 다른 계정에
    누출되지 않는다 (모의 hh0908 에 실전 hh09080 의 78만원이 섞이던 버그)."""
    b1 = KISBroker(_creds(), token_path=tmp_path / "1" / "kis_token.json")
    b2 = KISBroker(_creds(mock=True), token_path=tmp_path / "2" / "kis_token.json")
    assert b1._overseas_cache_path() != b2._overseas_cache_path()
    assert b1._overseas_cache_path().parent == (tmp_path / "1")
    assert b2._overseas_cache_path().parent == (tmp_path / "2")
