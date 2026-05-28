"""US 해외주식 '시장가' 주문 버그 회귀 테스트.

버그(2026-05-19, 런타임 입증 claude_response.json L6856):
  price_type:"market" US 매수가 us_buy(price=0)로 호출되어 KIS에
  OVRS_ORD_UNPR="0" + ORD_DVSN="00"(지정가) 전송 → KIS가
  "가격 $0.01 미만시 온라인 주문불가조건입니다" 로 거부.
  (KIS 해외주식 주문 TR은 국내 '01' 시장가가 없고 유효 지정가 단가를 요구.)

요구 동작:
  - limit price 미지정(시장가 의도) 시 현재가 기반 체결가능 지정가
    (marketable limit, 2자리)로 전송 — 절대 "0" 금지.
  - 실시간 시세 실패 시 **주문을 스킵하지 말고** 일봉 종가로 폴백해
    어떻게든 전송한다(사장님 지시 2026-05-19: 다중 폴백).
  - 모든 가격 소스가 비어 물리적으로 가격을 만들 수 없을 때만 명확한
    실패 반환(KIS 지정가 주문은 단가가 필수).
  - OVRS_EXCG_CD 는 시세 프로브가 캐싱한 거래소(NAS/NYS/AMS)를
    주문 거래소코드(NASD/NYSE/AMEX)로 매핑 (UUP=AMS→AMEX).
"""
import asyncio
import pytest

from infra import kis_broker
from infra.kis_broker import KISBroker


def test_excd_to_excg_maps_price_exchange_to_order_exchange():
    f = kis_broker.excd_to_excg
    assert f("NAS") == "NASD"
    assert f("NYS") == "NYSE"
    assert f("AMS") == "AMEX"
    assert f("NASD") == "NASD"      # 이미 주문코드면 통과
    assert f("") == "NASD"          # 미상 → 안전 기본
    assert f(None) == "NASD"


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
    """aiohttp 세션 대역 — post 의 json 바디를 캡처한다."""
    def __init__(self, resp_payload=None):
        self.posted = []
        self._resp = resp_payload or {"rt_cd": "0", "msg1": "정상처리 되었습니다."}
    def post(self, url, headers=None, json=None):
        self.posted.append({"url": url, "json": json})
        return _FakeResp(self._resp)


_TEST_CREDS = {"kis_app_key": "K", "kis_app_secret": "S",
               "kis_account_no": "12345678-01",
               "kis_base_url": "https://openapi.koreainvestment.com:9443"}


def _broker_with_fakes(monkeypatch, *, last_price, daily_rows=None, resp_payload=None):
    b = KISBroker(_TEST_CREDS)
    fake = _FakeSession(resp_payload)

    async def _tok():
        return "TESTTOKEN"

    async def _sess():
        return fake

    async def _last(tk):
        return last_price

    async def _daily(tk, days=100):
        return list(daily_rows or [])

    monkeypatch.setattr(b, "token", _tok)
    monkeypatch.setattr(b, "_s", _sess)
    monkeypatch.setattr(b, "us_last_price", _last)
    monkeypatch.setattr(b, "us_daily_chart", _daily)
    b._us_excd_cache["UUP"] = "AMS"   # 시세 프로브가 발견·캐싱한 거래소
    return b, fake


def test_us_buy_market_sends_marketable_limit_not_zero(monkeypatch):
    b, fake = _broker_with_fakes(monkeypatch, last_price=27.80)

    asyncio.run(b.us_buy("UUP", 2, price=0))

    assert fake.posted, "주문이 KIS 로 전송되어야 한다"
    body = fake.posted[-1]["json"]
    unpr = body["OVRS_ORD_UNPR"]
    assert unpr not in ("0", "0.0", "0.00", 0, ""), (
        f"시장가 주문이 단가 0 으로 전송됨(버그): {unpr!r}")
    assert float(unpr) >= 27.80, f"체결가능 지정가는 현재가 이상이어야: {unpr!r}"
    assert float(unpr) <= 27.80 * 1.05, f"버퍼 과대(슬리피지): {unpr!r}"
    assert body["OVRS_EXCG_CD"] == "AMEX", (
        f"UUP(AMS) → AMEX 매핑이어야: {body['OVRS_EXCG_CD']!r}")
    assert len(str(unpr).split(".")[-1]) <= 2, f"센트 단위 초과: {unpr!r}"


def test_us_buy_falls_back_to_daily_close_and_still_sends(monkeypatch):
    """실시간 시세 실패 → 주문 스킵 금지. 일봉 종가로 폴백해 전송한다."""
    b, fake = _broker_with_fakes(
        monkeypatch, last_price=0.0,
        daily_rows=[{"date": "2026-05-18", "close": 27.50}])

    res = asyncio.run(b.us_buy("UUP", 2, price=0))

    assert fake.posted, f"일봉 폴백으로 주문이 전송돼야 한다 (스킵 금지): {res!r}"
    body = fake.posted[-1]["json"]
    unpr = body["OVRS_ORD_UNPR"]
    assert unpr not in ("0", "0.0", "0.00", 0, ""), f"폴백도 0 금지: {unpr!r}"
    assert 27.50 <= float(unpr) <= 27.50 * 1.05, f"일봉 종가 기반이어야: {unpr!r}"
    assert body["OVRS_EXCG_CD"] == "AMEX"


def test_us_buy_all_price_sources_empty_returns_clear_error(monkeypatch):
    """실시간·일봉 모두 비면 KIS 지정가에 넣을 단가가 없어 전송 불가 —
    0/garbage 전송 대신 명확한 실패(물리적 불가 케이스만)."""
    b, fake = _broker_with_fakes(monkeypatch, last_price=0.0, daily_rows=[])

    res = asyncio.run(b.us_buy("UUP", 2, price=0))

    assert not fake.posted, "단가가 전혀 없으면 0 으로 보내면 안 된다"
    assert "실패" in res or "시세" in res, f"명확한 실패 메시지여야: {res!r}"


def test_us_sell_market_sends_marketable_limit_not_zero(monkeypatch):
    b, fake = _broker_with_fakes(monkeypatch, last_price=27.80)

    asyncio.run(b.us_sell("UUP", 2, price=0))

    body = fake.posted[-1]["json"]
    unpr = body["OVRS_ORD_UNPR"]
    assert unpr not in ("0", "0.0", "0.00", 0, ""), (
        f"시장가 매도가 단가 0 으로 전송됨(버그): {unpr!r}")
    assert float(unpr) <= 27.80, "매도 체결가능 지정가는 현재가 이하여야"
    assert float(unpr) >= 27.80 * 0.95, "매도 버퍼 과대(슬리피지)"
    assert body["OVRS_EXCG_CD"] == "AMEX"


# ── rt_cd 거부 판정 (밤사이 OXY "주문가능금액 초과" 유령 체결 회귀) ──────────
# 버그(2026-05-21 로그): us_buy/us_sell 가 rt_cd 를 안 보고 msg1 만 반환해,
# KIS 가 거부한 주문("주문가능금액을 초과 했습니다", rt_cd≠0)이 성공과 동일 포맷으로
# 돌아왔다. 호출부 accepted 휴리스틱(실패/에러/거부/예외/REJECT 문자열 매칭)이 못 잡아
# US 는 ok=accepted 로 잠정 체결 카운트 → 유령 체결. KR(kr_buy/kr_sell)은 rt_cd 로
# [실패] 프리픽스를 붙여 정상 판정하므로, US 도 동일 대칭이어야 한다.
_ACCEPTED_BLOCKLIST = ("실패", "에러", "거부", "예외", "REJECT", "초당", "거래건수")


def _accepted(res: str) -> bool:
    """main_swarm 실행부의 accepted 휴리스틱 복제 — 회귀 의도 고정용."""
    return all(bad not in res for bad in _ACCEPTED_BLOCKLIST)


def test_us_buy_rejection_is_flagged_as_failure(monkeypatch):
    b, fake = _broker_with_fakes(
        monkeypatch, last_price=57.0,
        resp_payload={"rt_cd": "1", "msg1": "주문가능금액을 초과 했습니다."})

    res = asyncio.run(b.us_buy("OXY", 8, price=0))

    assert "실패" in res, f"rt_cd≠0 거부는 [실패] 로 표기돼야: {res!r}"
    assert not _accepted(res), f"거부 주문이 accepted 로 오판되면 안 됨: {res!r}"


def test_us_sell_rejection_is_flagged_as_failure(monkeypatch):
    b, fake = _broker_with_fakes(
        monkeypatch, last_price=57.0,
        resp_payload={"rt_cd": "1", "msg1": "주문가능수량을 초과 했습니다."})

    res = asyncio.run(b.us_sell("OXY", 8, price=0))

    assert "실패" in res, f"rt_cd≠0 거부는 [실패] 로 표기돼야: {res!r}"
    assert not _accepted(res)


def test_us_buy_success_is_not_flagged_as_failure(monkeypatch):
    b, fake = _broker_with_fakes(
        monkeypatch, last_price=57.0,
        resp_payload={"rt_cd": "0", "msg1": "정상처리 되었습니다."})

    res = asyncio.run(b.us_buy("OXY", 8, price=0))

    assert "실패" not in res, f"정상 주문에 실패 표기되면 안 됨: {res!r}"
    assert _accepted(res), f"정상(rt_cd=0)은 accepted 여야: {res!r}"


def test_us_buy_explicit_marketable_limit_is_honored(monkeypatch):
    """명시 지정가가 체결가능(매수: 시세 이상)이면 그대로 사용. (사장 결정 2026-05-28)"""
    b, fake = _broker_with_fakes(monkeypatch, last_price=27.00)

    asyncio.run(b.us_buy("UUP", 3, price=27.12))

    body = fake.posted[-1]["json"]
    assert float(body["OVRS_ORD_UNPR"]) == pytest.approx(27.12, abs=0.01)
    assert body["ORD_QTY"] == "3"


def test_us_buy_explicit_nonmarketable_limit_is_clamped(monkeypatch):
    """명시 매수 지정가가 시세 아래(미체결)면 체결가능 가격으로 클램프해 전송한다 —
    실계좌 US 미체결 누적 해소(2026-05-28 로그 리뷰: SOFI $15.50 vs 시세 16.13 류)."""
    b, fake = _broker_with_fakes(monkeypatch, last_price=16.13)

    asyncio.run(b.us_buy("UUP", 1, price=15.50))

    body = fake.posted[-1]["json"]
    assert float(body["OVRS_ORD_UNPR"]) >= 16.13, "시세 아래 매수 지정가는 체결가능 가격으로 올려야 한다"
