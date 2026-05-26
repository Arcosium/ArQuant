"""거래 내역 '비우기' 후에도 승률·통계가 유지돼야 한다 (사장 지시 2026-05-24).

버그: 거래 내역을 비우면 claude_response.json 의 trade 이벤트가 사라져 승률이 '-'로 표시됐다.
요구: 비우기 직전 실현손익 통계를 영속 베이스라인(perf_baseline.json)에 적립하고,
performance_kpis 가 라이브 거래 통계에 이 베이스라인을 더해 승률을 계속 보여준다.
"""
import main_swarm as ms


def test_trade_realized_stats_counts_sells():
    sells = [
        {"side": "sell", "ts": "2026-05-20 10:00:00",
         "detail": {"realized_pnl": 5000.0, "matched": [{"buy_ts": "2026-05-18 10:00:00"}]}},
        {"side": "sell", "ts": "2026-05-20 11:00:00",
         "detail": {"realized_pnl": -2000.0, "matched": [{"buy_ts": "2026-05-19 11:00:00"}]}},
        {"side": "buy", "ts": "2026-05-20 09:00:00", "detail": {"buy_price": 100}},
    ]
    st = ms._trade_realized_stats(sells)
    assert st["sell_count"] == 2 and st["win_count"] == 1
    assert st["hold_days_n"] == 2 and st["hold_days_sum"] == 3.0  # 2일 + 1일


def test_performance_kpis_adds_baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(ms, "_PERF_BASELINE", tmp_path / "perf_baseline.json")
    ms._write_perf_baseline({"sell_count": 4, "win_count": 3, "hold_days_sum": 8.0, "hold_days_n": 4})
    # 라이브 거래가 비어도 베이스라인만으로 승률이 표시돼야 한다
    k = ms.performance_kpis(raw_equity=[], trades=[])
    assert k["has_trades"] is True
    assert k["sell_count"] == 4 and k["win_count"] == 3
    assert k["win_rate_pct"] == 75.0
    assert k["avg_hold_days"] == 2.0


def test_clear_trade_log_folds_into_baseline(tmp_path, monkeypatch):
    # Phase 2 멀티테넌트: 이벤트 로그가 uid 별 파일이 됐으므로 user_paths._DATA_DIR 를 tmp 로 격리.
    from infra import user_paths
    monkeypatch.setattr(ms, "_PERF_BASELINE", tmp_path / "perf_baseline.json")
    monkeypatch.setattr(user_paths, "_DATA_DIR", tmp_path)
    (user_paths.trade_log_path(1)).write_text("[]", encoding="utf-8")  # trade 이벤트 없음 → 백업/제거 0
    sells = [
        {"side": "sell", "ts": "2026-05-20 10:00:00",
         "detail": {"realized_pnl": 5000.0, "matched": [{"buy_ts": "2026-05-18 10:00:00"}]}},
        {"side": "sell", "ts": "2026-05-20 11:00:00",
         "detail": {"realized_pnl": -2000.0, "matched": [{"buy_ts": "2026-05-19 11:00:00"}]}},
    ]
    monkeypatch.setattr(ms, "get_trade_history", lambda limit=500, uid=None: sells)

    ms.clear_trade_log(uid=1)

    base = ms._read_perf_baseline()
    assert base["sell_count"] == 2 and base["win_count"] == 1
    # 비우기 후 거래 리스트가 비어도(=trades=[]) 승률이 유지된다
    k = ms.performance_kpis(raw_equity=[], trades=[])
    assert k["has_trades"] is True and k["win_rate_pct"] == 50.0
