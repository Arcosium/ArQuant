"""주간 피드백 루프 메시지 — '로그 참고'가 아니라 점검 내용 자체를 보고 (사장 지시 2026-05-24)."""
from infra import weekly_review


def test_build_review_message_includes_content_not_log_paths():
    summary = {
        "period": "최근 7일 (~ 2026-05-24 06:00 KST)",
        "cycles": 42, "with_orders": 3, "risk_approved": 1, "market_open_cycles": 2,
        "candidates_picked": 30, "targets_final": 6, "candidate_to_target_pct": 20.0,
        "trades_executed": 1, "trades_failed": 0,
        "equity_return_pct_adj": 7.32,
        "news_tuning": {"verdict": "키워드 가중치 양호", "findings": ["KR 분류 편중 없음"]},
    }
    msg = weekly_review.build_review_message(summary, diag="분류 일치율 92%")
    # 사장 요구: 로그 파일 경로로 떠넘기지 말 것
    assert "weekly_review.log" not in msg and "ops_support.log" not in msg
    # 점검 수치가 메시지에 직접 담겨야 한다
    assert "사이클 42회" in msg
    assert "후보 30 → 매수 6" in msg
    assert "+7.32%" in msg
    assert "분류 일치율 92%" in msg
    assert "키워드 가중치 양호" in msg


def test_build_review_message_handles_missing_equity():
    summary = {"period": "최근 7일", "cycles": 0, "equity_return_pct_adj": None}
    msg = weekly_review.build_review_message(summary, diag="")
    assert "데이터 부족" in msg
