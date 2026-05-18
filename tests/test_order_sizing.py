"""`main_swarm._affordable_one_share` — 최소 1주 매수 허용 판정.

기본 정책(보수형 B): 리스크관리실장 예수금 게이트와 동일하게
`price × MIN_CASH_BUFFER ≤ cash` 일 때만 1주를 살 수 있다고 본다
(주문 초안이 곧바로 리스크 단에서 반려되는 모순 방지).
fixture 가 MIN_CASH_BUFFER=1.10 으로 고정한다.
"""
import pytest

import main_swarm as ms


@pytest.mark.parametrize("price,cash", [(0, 1000), (-1, 1000), (100, 0), (100, -5)])
def test_non_positive_inputs_are_unaffordable(price, cash):
    assert ms._affordable_one_share(price, cash, total=cash) is False


def test_affordable_when_price_times_buffer_within_cash():
    # 100 × 1.10 = 110 ≤ 200  → 매수 가능
    assert ms._affordable_one_share(100.0, 200.0, total=1000.0) is True


def test_not_affordable_inside_buffer_band():
    # price ≤ cash 이지만 price × 1.10 > cash → 보수적으로 거른다 (KIS 거절 예방)
    assert ms._affordable_one_share(100.0, 105.0, total=1000.0) is False


def test_boundary_is_floating_point_conservative():
    # 100 × 1.10 == 110.00000000000001 (IEEE754) 이므로 cash 가 정확히 110 이면
    # 미세하게 거른다 — 경계에서 보수적으로 동작함을 고정한다.
    assert ms._affordable_one_share(100.0, 110.0, total=1000.0) is False
    # cash 에 아주 작은 여유만 있어도 통과.
    assert ms._affordable_one_share(100.0, 110.01, total=1000.0) is True
