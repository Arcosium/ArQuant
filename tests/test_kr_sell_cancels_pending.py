"""KR 매도 진입 시 같은 종목 펜딩 매도 주문 자동 취소.

배경 (2026-05-28 사장 보고·실측):
  - 003490 1주를 사후관리실장이 28,000 지정가 매도로 KIS에 넣었고(odno=0024338900,
    10:37:49), 이후 두 번 후속 사이클(14:09 시장가·15:20 지정가 29,000)이
    재매도 시도 → 모두 "주문 가능한 수량을 초과했습니다"로 거부됨.
  - 원인: 살아있는 펜딩 매도가 1주를 잠가 ord_psbl_qty=0 → 신규 매도는 0주를 초과.

요구 동작:
  - kr_sell(code, qty) 진입 직전에 inquire-psbl-rvsecncl 로 같은 code 의 펜딩 매도
    (sll_buy_dvsn_cd='01') 를 조회하고, 있으면 order-rvsecncl(TTTC0803U, RVSE_CNCL_DVSN_CD='02')
    로 모두 취소 후 새 매도를 전송한다.
  - 펜딩이 없으면 종전과 동일.
  - 펜딩 취소 자체가 실패해도 새 매도 전송은 시도(다중 폴백 — 사장 룰).
  - 매수(kr_buy)는 이 로직 무관(매수 펜딩이 신규 매수를 잠그지 않음).
"""
import asyncio
import pytest

from infra import kis_broker
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
    """get/post 모두 캡처. payload 는 (method, path, headers, json/params) → response."""
    def __init__(self):
        self.calls = []
        self._responders = []  # list of (predicate, payload)
    def respond(self, predicate, payload):
        self._responders.append((predicate, payload))
    def _match(self, method, url, headers, body_or_params):
        for pred, payload in self._responders:
            if pred(method, url, headers, body_or_params):
                return payload
        return {"rt_cd": "0", "msg1": "정상"}
    def get(self, url, headers=None, params=None):
        p = self._match("GET", url, headers or {}, params or {})
        self.calls.append({"method": "GET", "url": url, "headers": headers, "params": params})
        return _FakeResp(p)
    def post(self, url, headers=None, json=None):
        p = self._match("POST", url, headers or {}, json or {})
        self.calls.append({"method": "POST", "url": url, "headers": headers, "json": json})
        return _FakeResp(p)


_TEST_CREDS = {"kis_app_key": "K", "kis_app_secret": "S",
               "kis_account_no": "12345678-01",
               "kis_base_url": "https://openapi.koreainvestment.com:9443"}


def _broker(monkeypatch, fake):
    b = KISBroker(_TEST_CREDS)

    async def _tok():
        return "TESTTOKEN"

    async def _sess():
        return fake

    monkeypatch.setattr(b, "token", _tok)
    monkeypatch.setattr(b, "_s", _sess)
    return b


def _is_psbl_rvsecncl(method, url, headers, body):
    return method == "GET" and "inquire-psbl-rvsecncl" in url


def _is_cancel(method, url, headers, body):
    return method == "POST" and "order-rvsecncl" in url


def _is_new_order(method, url, headers, body):
    return method == "POST" and url.endswith("/order-cash")


def test_kr_sell_cancels_existing_pending_sell_before_new_order(monkeypatch):
    """003490 펜딩 매도 1건 → 취소 1번 + 신규 주문 1건 전송."""
    fake = _FakeSession()
    # inquire-psbl-rvsecncl: 003490 매도 1건 펜딩
    fake.respond(_is_psbl_rvsecncl, {
        "rt_cd": "0", "msg1": "정상",
        "output": [{
            "odno": "0024338900",
            "ord_gno_brno": "00950",
            "pdno": "003490",
            "ord_qty": "1",
            "ord_unpr": "28000",
            "ord_dvsn_cd": "00",
            "sll_buy_dvsn_cd": "01",        # 01=매도
            "ord_tmd": "103749",
        }],
        "ctx_area_fk100": "", "ctx_area_nk100": "",
    })
    fake.respond(_is_cancel, {"rt_cd": "0", "msg1": "취소되었습니다"})
    fake.respond(_is_new_order, {"rt_cd": "0", "msg1": "정상처리 되었습니다"})

    b = _broker(monkeypatch, fake)
    res = asyncio.run(b.kr_sell("003490", 1, price=29000))

    posts = [c for c in fake.calls if c["method"] == "POST"]
    cancels = [c for c in posts if "order-rvsecncl" in c["url"]]
    new_orders = [c for c in posts if c["url"].endswith("/order-cash")]

    assert len(cancels) == 1, f"펜딩 매도 1건이 있으면 취소 1회 호출돼야: {fake.calls}"
    cancel_body = cancels[0]["json"]
    assert cancel_body["ORGN_ODNO"] == "0024338900"
    assert cancel_body["KRX_FWDG_ORD_ORGNO"] == "00950"
    assert cancel_body["RVSE_CNCL_DVSN_CD"] == "02"
    assert cancel_body["QTY_ALL_ORD_YN"] == "Y"

    assert len(new_orders) == 1, "신규 매도 1건 전송돼야"
    assert new_orders[0]["json"]["PDNO"] == "003490"
    assert "실패" not in res


def test_kr_sell_skips_cancel_when_no_pending(monkeypatch):
    """펜딩 없음 → 취소 0건 + 신규 주문 1건."""
    fake = _FakeSession()
    fake.respond(_is_psbl_rvsecncl, {"rt_cd": "0", "msg1": "조회할 내용이 없습니다",
                                       "output": [], "ctx_area_fk100": "", "ctx_area_nk100": ""})
    fake.respond(_is_new_order, {"rt_cd": "0", "msg1": "정상처리 되었습니다"})

    b = _broker(monkeypatch, fake)
    asyncio.run(b.kr_sell("005380", 1, price=666000))

    posts = [c for c in fake.calls if c["method"] == "POST"]
    cancels = [c for c in posts if "order-rvsecncl" in c["url"]]
    new_orders = [c for c in posts if c["url"].endswith("/order-cash")]
    assert len(cancels) == 0
    assert len(new_orders) == 1


def test_kr_sell_ignores_pending_buy_orders_on_same_code(monkeypatch):
    """같은 종목 펜딩 매수가 있어도 매도엔 영향 없음 → 취소 안 함."""
    fake = _FakeSession()
    fake.respond(_is_psbl_rvsecncl, {
        "rt_cd": "0", "msg1": "정상",
        "output": [{
            "odno": "9999999999",
            "ord_gno_brno": "00950",
            "pdno": "003490",
            "ord_qty": "5", "ord_unpr": "25000",
            "ord_dvsn_cd": "00",
            "sll_buy_dvsn_cd": "02",        # 02=매수
            "ord_tmd": "100000",
        }],
        "ctx_area_fk100": "", "ctx_area_nk100": "",
    })
    fake.respond(_is_new_order, {"rt_cd": "0", "msg1": "정상"})

    b = _broker(monkeypatch, fake)
    asyncio.run(b.kr_sell("003490", 1, price=0))

    posts = [c for c in fake.calls if c["method"] == "POST"]
    cancels = [c for c in posts if "order-rvsecncl" in c["url"]]
    assert len(cancels) == 0, "같은 종목 펜딩 매수는 매도와 무관 — 취소하지 말 것"


def test_kr_sell_ignores_pending_sell_of_different_ticker(monkeypatch):
    """다른 종목 펜딩 매도는 절대 건드리지 말 것."""
    fake = _FakeSession()
    fake.respond(_is_psbl_rvsecncl, {
        "rt_cd": "0", "msg1": "정상",
        "output": [{
            "odno": "1111111111",
            "ord_gno_brno": "00950",
            "pdno": "005380",                # 다른 종목!
            "ord_qty": "1", "ord_unpr": "666000",
            "ord_dvsn_cd": "00",
            "sll_buy_dvsn_cd": "01",
            "ord_tmd": "100000",
        }],
        "ctx_area_fk100": "", "ctx_area_nk100": "",
    })
    fake.respond(_is_new_order, {"rt_cd": "0", "msg1": "정상"})

    b = _broker(monkeypatch, fake)
    asyncio.run(b.kr_sell("003490", 1, price=0))

    posts = [c for c in fake.calls if c["method"] == "POST"]
    cancels = [c for c in posts if "order-rvsecncl" in c["url"]]
    assert len(cancels) == 0, "다른 종목 펜딩은 절대 건드리지 말 것"


def test_kr_sell_still_sends_when_cancel_lookup_fails(monkeypatch):
    """펜딩 조회 자체가 실패해도 신규 매도는 전송한다 (다중 폴백 — 사장 룰)."""
    fake = _FakeSession()
    fake.respond(_is_psbl_rvsecncl, {"rt_cd": "1", "msg1": "오류"})
    fake.respond(_is_new_order, {"rt_cd": "0", "msg1": "정상"})

    b = _broker(monkeypatch, fake)
    res = asyncio.run(b.kr_sell("003490", 1, price=0))

    posts = [c for c in fake.calls if c["method"] == "POST"]
    new_orders = [c for c in posts if c["url"].endswith("/order-cash")]
    assert len(new_orders) == 1, "펜딩 조회 실패해도 매도 자체는 전송 (다중 폴백)"
    assert "실패" not in res


def test_kr_buy_does_not_query_pending(monkeypatch):
    """매수는 펜딩 조회·취소 안 함 (매수 펜딩이 신규 매수를 잠그지 않음)."""
    fake = _FakeSession()
    fake.respond(_is_new_order, {"rt_cd": "0", "msg1": "정상"})

    b = _broker(monkeypatch, fake)
    asyncio.run(b.kr_buy("005380", 1, price=666000))

    gets = [c for c in fake.calls if c["method"] == "GET" and "inquire-psbl-rvsecncl" in c["url"]]
    assert len(gets) == 0, "매수 경로엔 펜딩 조회 없어야"
