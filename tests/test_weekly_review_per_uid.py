"""주간 피드백 통계는 per-uid 여야 한다 (Phase 2 멀티테넌트).

버그 2026-06-06: build_review_summary 가 cycle_store.list_cycles 를 uid 없이 호출해
전 계정 사이클이 뒤섞이고, 전역 data/equity_curve.json·claude_response.json(둘 다 폐지·부재)을
읽어 7일 수익률·체결수가 항상 결손이었다. → uid 필터 + per-uid equity + 사이클 기반 체결집계.
"""
import json
from datetime import datetime, timezone, timedelta
from infra import weekly_review, cycle_store

KST = timezone(timedelta(hours=9))


def _recent_ts():
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


def test_summary_filters_by_uid_and_reads_per_uid_equity(tmp_path, monkeypatch):
    ts = _recent_ts()
    fixture = [{
        "started_at": ts, "candidate_codes": '["AAA", "BBB"]', "target_codes": '["AAA"]',
        "orders_executed": '[{"ticker": "AAA", "side": "buy", "filled": true}]',
        "risk_approved": 1, "market_open": 1, "bp_pnl_ratio": 0.0,
    }]
    calls = {}

    def fake_list(limit=50, offset=0, uid=None):
        calls["uid"] = uid
        return fixture

    monkeypatch.setattr(cycle_store, "list_cycles", fake_list)
    monkeypatch.setattr(weekly_review, "PROJECT_ROOT", tmp_path)
    eqdir = tmp_path / "data" / "7"
    eqdir.mkdir(parents=True)
    (eqdir / "equity_curve.json").write_text(json.dumps([
        {"ts": ts, "total_eval": 100.0, "external_flow_cum": 0.0},
        {"ts": ts, "total_eval": 110.0, "external_flow_cum": 0.0},
    ]), encoding="utf-8")

    s = weekly_review.build_review_summary(uid=7)
    assert calls["uid"] == 7                       # 사이클을 uid 로 필터
    assert s["cycles"] == 1
    assert s["candidates_picked"] == 2 and s["targets_final"] == 1
    assert s["trades_executed"] == 1               # 사이클 orders_executed 에서 집계
    assert abs(s["equity_return_pct_adj"] - 10.0) < 1e-6   # per-uid 100→110


def test_summary_counts_failed_orders(tmp_path, monkeypatch):
    ts = _recent_ts()
    fixture = [{
        "started_at": ts, "candidate_codes": '["AAA"]', "target_codes": '["AAA"]',
        "orders_executed": '[{"ticker": "AAA", "side": "buy", "filled": false, "accepted": false}]',
        "risk_approved": 0, "market_open": 1, "bp_pnl_ratio": 0.0,
    }]
    monkeypatch.setattr(cycle_store, "list_cycles", lambda limit=50, offset=0, uid=None: fixture)
    monkeypatch.setattr(weekly_review, "PROJECT_ROOT", tmp_path)
    s = weekly_review.build_review_summary(uid=9)
    assert s["trades_executed"] == 0
    assert s["trades_failed"] == 1
