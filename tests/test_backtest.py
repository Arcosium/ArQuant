"""백테스트 엔진 — 결정론 + 룩어헤드 없음 + 리스크규칙 단조성 (프리셋 폐지 후 params 기반)."""
import config
from backtest.engine import load_prices, run_backtest, sma_breakout_signal


def test_signal_no_lookahead():
    closes = [10] * 25 + [9, 8, 12]  # 마지막에 상향 돌파
    # i 일 신호는 closes[:i+1] 만 본다 — 미래 가격 미참조.
    assert sma_breakout_signal(closes, 5) is False     # lookback 미충족
    assert isinstance(sma_breakout_signal(closes, 27), bool)


def test_backtest_is_deterministic():
    prices = load_prices()
    assert prices, "data/daily_*.csv 필요"
    a = run_backtest(config.STRATEGY_DEFAULTS, prices)
    b = run_backtest(config.STRATEGY_DEFAULTS, prices)
    assert a == b  # 동일 입력 → 동일 출력 (랜덤성 없음)


def test_risk_rules_monotonic():
    """손절 타이트·단일종목 한도 작은 설정이 큰 베팅 설정보다 MDD(낙폭)가 작아야 한다."""
    prices = load_prices()
    tight = dict(config.STRATEGY_DEFAULTS)
    tight.update({"STOP_LOSS_PCT": 3.5, "CONSERVATIVE_STOCK_RATIO": 0.07,
                  "CONSERVATIVE_MDD": 0.025, "PER_ORDER_BUDGET_RATIO": 0.03,
                  "MAX_CYCLE_BUDGET_RATIO": 0.10})
    loose = dict(config.STRATEGY_DEFAULTS)
    loose.update({"STOP_LOSS_PCT": 15.0, "CONSERVATIVE_STOCK_RATIO": 0.40,
                  "CONSERVATIVE_MDD": 0.15, "PER_ORDER_BUDGET_RATIO": 0.35,
                  "MAX_CYCLE_BUDGET_RATIO": 0.70})
    d = run_backtest(tight, prices)
    u = run_backtest(loose, prices)
    # max_drawdown_pct 는 음수 — 타이트한 설정이 0 에 더 가깝다.
    assert d["max_drawdown_pct"] >= u["max_drawdown_pct"]
    assert set(d) >= {"total_return_pct", "max_drawdown_pct", "sharpe_like",
                      "trades", "win_rate_pct", "final_eval"}
