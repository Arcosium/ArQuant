"""trade 이벤트 타입이 ok(잠정 체결 포함) 기준이어야 함 — 회귀 테스트.

버그(2026-05-21 밤 로그): 실행부가 type 을 `filled` 기준으로 방송해, US 의
'접수만(accepted, ok=True, filled=False)' 주문이 trade_executed 카운트에는
포함되면서도 이벤트는 trade_failed 로 나갔다. get_trade_history 가 두 타입을
모두 거래내역에 넣으므로 "성공으로 카운트됐는데 실패로 표시"되는 불일치가 생겼다.

요구 동작: 누적 체결 카운트(ok) 와 이벤트 타입이 일치해야 한다.
  - ok=True  → trade_executed (체결 또는 US 잠정 접수)
  - ok=False → trade_failed   (진짜 실패/거부)
"""
from main_swarm import _trade_event_type


def test_event_type_follows_ok_not_filled():
    # US 접수만 된 주문: filled=False 라도 ok=True 면 executed
    assert _trade_event_type(True) == "trade_executed"
    # 진짜 실패/거부: ok=False → failed
    assert _trade_event_type(False) == "trade_failed"
