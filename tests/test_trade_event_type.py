"""trade 이벤트 타입이 '확정 체결' 기준이어야 함 — 회귀 테스트.

사장 지시 2026-05-21로 누적 카운트가 '확정 후 증가'로 바뀌면서, 실행부는 즉시 체결이
확인된 주문(filled=True)만 trade_executed 로, 접수 실패(not accepted)만 trade_failed 로
방송한다. 접수만 되고 미확인인 주문은 fill 이벤트를 내지 않고(order_submitted 만),
_poll_fills_until_confirmed 가 체결을 확인한 그 시점에 trade_executed 를 낸다.

요구 동작: _trade_event_type 은 '확정 체결 여부'를 이벤트 타입으로 매핑한다.
  - filled=True  → trade_executed (체결 확인)
  - filled=False → trade_failed   (실패/거부)
"""
from main_swarm import _trade_event_type


def test_event_type_follows_confirmed_fill():
    # 체결 확인된 주문 → executed
    assert _trade_event_type(True) == "trade_executed"
    # 실패/거부 → failed
    assert _trade_event_type(False) == "trade_failed"
