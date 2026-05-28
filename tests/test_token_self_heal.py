"""KIS 토큰 자가치유 회귀 테스트.

버그(2026-05-27): 디스크 토큰 캐시의 expires_at 은 미래(로컬 시계 기준 유효)인데
KIS 서버가 토큰을 조기 무효화해 모든 잔고/주문 호출이 rt_cd=1 '기간이 만료된 token 입니다.'
로 실패. token() 이 미래 만료시각을 믿고 재발급을 안 해 죽은 토큰을 영구 재사용 → 잔고 0원.

수정: KIS 응답이 '만료 토큰'이면 token(force=True) 로 강제 재발급하고 1회 재시도(_authed_json).
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


def test_resp_token_expired_detects_kis_expiry_message(tmp_path):
    b = KISBroker(_creds(), token_path=tmp_path / "tok.json")
    assert b._resp_token_expired({"rt_cd": "1", "msg1": "기간이 만료된 token 입니다."}) is True
    assert b._resp_token_expired({"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "x"}) is True
    # 정상 응답·다른 거부 사유는 만료로 오탐하지 않는다
    assert b._resp_token_expired({"rt_cd": "0", "msg1": "정상처리"}) is False
    assert b._resp_token_expired({"rt_cd": "1", "msg1": "주문가능금액을 초과합니다"}) is False


def test_authed_json_force_reissues_and_retries_on_expired_token(tmp_path):
    b = KISBroker(_creds(), token_path=tmp_path / "tok.json")
    issued = []

    async def fake_token(force=False):
        issued.append(force)
        return "FRESH" if force else "STALE"

    b.token = fake_token  # type: ignore[assignment]

    calls = []

    async def make_request(tok):
        calls.append(tok)
        # 첫 호출(STALE 토큰)은 만료 거부, 강제 재발급 후 두 번째(FRESH)는 정상
        if tok != "FRESH":
            return {"rt_cd": "1", "msg1": "기간이 만료된 token 입니다."}
        return {"rt_cd": "0", "msg1": "정상", "output1": [{"ok": 1}]}

    d = asyncio.run(b._authed_json(make_request))
    assert d["rt_cd"] == "0"
    assert calls == ["STALE", "FRESH"]      # 죽은 토큰 → 강제 재발급 토큰으로 재시도
    assert issued == [False, True]          # 두 번째는 force=True 재발급


def test_authed_json_no_retry_when_first_call_ok(tmp_path):
    b = KISBroker(_creds(), token_path=tmp_path / "tok.json")
    issued = []

    async def fake_token(force=False):
        issued.append(force)
        return "GOOD"

    b.token = fake_token  # type: ignore[assignment]

    async def make_request(tok):
        return {"rt_cd": "0", "msg1": "정상"}

    d = asyncio.run(b._authed_json(make_request))
    assert d["rt_cd"] == "0"
    assert issued == [False]   # 강제 재발급 없음
