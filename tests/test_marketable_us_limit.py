"""US 명시 지정가가 호가 반대쪽이면 체결가능 가격으로 클램프 — 미체결 누적 해소.

배경(2026-05-28 로그 리뷰): 실계좌(uid1) US 주문이 에이전트 명시 지정가를 그대로 전송해
호가 반대쪽에 걸려 미체결로 남았다(F 매도 $16.00 vs 시세 15.75 = 목표가 위 매도, SOFI 매수
$15.50 vs 16.13 = 시세 아래 매수). 사이클 단위 즉시 집행 결정이므로, 체결가능 범위로 클램프해
실제로 체결되게 한다. 시세 미확보면 원래 지정가 유지(주문 미차단)."""
import math
from infra.kis_broker import marketable_us_limit


def test_buy_limit_below_market_clamps_up():
    px, clamped = marketable_us_limit("buy", 15.50, 16.13)
    assert clamped
    assert px == math.ceil(16.13 * 1.003 * 100) / 100.0
    assert px >= 16.13, "매수 지정가는 시세 이상으로 올라가야 체결된다"


def test_sell_limit_above_market_clamps_down():
    px, clamped = marketable_us_limit("sell", 16.00, 15.75)
    assert clamped
    assert px == math.floor(15.75 * 0.997 * 100) / 100.0
    assert px <= 15.75, "매도 지정가는 시세 이하로 내려가야 체결된다"


def test_buy_limit_at_or_above_market_kept():
    px, clamped = marketable_us_limit("buy", 16.50, 16.13)
    assert not clamped and px == 16.50


def test_sell_limit_at_or_below_market_kept():
    px, clamped = marketable_us_limit("sell", 15.00, 15.75)
    assert not clamped and px == 15.00


def test_no_market_price_keeps_explicit():
    px, clamped = marketable_us_limit("buy", 15.50, 0.0)
    assert not clamped and px == 15.50
