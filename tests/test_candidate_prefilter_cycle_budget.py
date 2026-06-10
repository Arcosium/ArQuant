from tools.affordable_prefilter import affordable_within_cycle_budget as ok


def test_high_price_dropped():
    # cash 5.95M, ratio 0.10, overshoot 1.2 → 사이클예산 ~595K*1.2=714K. 1.13M 종목 배제
    assert ok(price=1_130_000, cash=5_952_763, cycle_ratio=0.10, overshoot=1.2) is False


def test_within_budget_kept():
    assert ok(price=500_000, cash=5_952_763, cycle_ratio=0.10, overshoot=1.2) is True


def test_price_fetch_failure_kept():
    # 시세 조회 실패(0/음수)는 통과(보수) — 데이터 결손으로 누락 방지
    assert ok(price=0, cash=5_952_763, cycle_ratio=0.10, overshoot=1.2) is True


def test_no_cash_keeps():
    # 현금 정보 없음(0) 이면 판단 불가 → 통과(보수)
    assert ok(price=1_130_000, cash=0, cycle_ratio=0.10, overshoot=1.2) is True
