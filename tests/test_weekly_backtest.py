"""주간 피드백 summary 에 현재-파라미터 단일 백테스트가 포함되는지 (2026-06-09)."""
import infra.weekly_review as wr


def test_summary_has_backtest_section():
    summary = wr.build_review_summary(uid=None)
    assert "backtest" in summary
    bt = summary["backtest"]
    assert "available" in bt
    if bt["available"]:
        assert set(bt) >= {"available", "total_return_pct", "max_drawdown_pct",
                           "sharpe_like", "trades", "win_rate_pct"}


def test_message_includes_backtest_line():
    summary = {"period": "최근 7일", "cycles": 0, "with_orders": 0, "risk_approved": 0,
               "market_open_cycles": 0, "candidate_to_target_pct": 0,
               "candidates_picked": 0, "targets_final": 0,
               "trades_executed": 0, "trades_failed": 0, "equity_return_pct_adj": None,
               "backtest": {"available": True, "total_return_pct": 3.2,
                            "max_drawdown_pct": -4.1, "sharpe_like": 0.8,
                            "trades": 5, "win_rate_pct": 60.0}}
    msg = wr.build_review_message(summary)
    assert "백테스트" in msg
