from tools.account_weight import compute_stock_weight


def test_mock_usd_deposit_not_counted_as_stock():
    # 모의: total 485M(=KR현금100M + 해외USD예수금385M), 해외 주식분 0 → 주식비중 0
    w = compute_stock_weight(total_eval=485_000_000, kr_cash=100_000_000,
                             total_eval_kr=100_000_000, overseas_stock_krw=0)
    assert w == 0.0


def test_kr_stock_counted():
    w = compute_stock_weight(total_eval=100_000_000, kr_cash=50_000_000,
                             total_eval_kr=100_000_000, overseas_stock_krw=0)
    assert abs(w - 0.5) < 1e-9


def test_overseas_stock_counted():
    # total 100M, KR현금10M, KR총평가10M(해외분 90M 중 주식 60M·USD예수금 30M) → 주식 60M/100M
    w = compute_stock_weight(total_eval=100_000_000, kr_cash=10_000_000,
                             total_eval_kr=10_000_000, overseas_stock_krw=60_000_000)
    assert abs(w - 0.6) < 1e-9


def test_zero_total_fail_open():
    assert compute_stock_weight(total_eval=0, kr_cash=0, total_eval_kr=0, overseas_stock_krw=0) == 0.0
    assert compute_stock_weight(total_eval=None, kr_cash=None, total_eval_kr=None, overseas_stock_krw=None) == 0.0
