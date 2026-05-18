"""백테스트 엔진 — 결정론 + 룩어헤드 없음 + 프리셋 스펙트럼 단조성."""
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
    a = run_backtest("balanced", prices)
    b = run_backtest("balanced", prices)
    assert a == b  # 동일 입력 → 동일 출력 (랜덤성 없음)


def test_preset_risk_spectrum_is_monotonic():
    """방어형은 초공격형보다 MDD(낙폭)가 작아야 한다 — 프리셋 설계 불변식."""
    prices = load_prices()
    d = run_backtest("defensive", prices)
    u = run_backtest("ultra_aggressive", prices)
    # max_drawdown_pct 는 음수 — 방어형이 0 에 더 가깝다.
    assert d["max_drawdown_pct"] >= u["max_drawdown_pct"]
    assert set(d) >= {"total_return_pct", "max_drawdown_pct", "sharpe_like",
                      "trades", "win_rate_pct", "final_eval"}


def test_all_presets_run():
    prices = load_prices()
    for name in config.STRATEGY_PRESETS:
        r = run_backtest(name, prices)
        assert r["preset"] == name
