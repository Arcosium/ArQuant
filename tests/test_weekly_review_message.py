"""주간 피드백 루프 메시지 — '로그 참고'가 아니라 점검 내용 자체를 보고 (사장 지시 2026-05-24)."""
from infra import weekly_review


def test_build_review_message_includes_content_not_log_paths():
    # 2026-06-04: 뉴스 분류기 폐지 → diag/news_tuning 인자·표시 제거(시그니처: summary 만).
    summary = {
        "period": "최근 7일 (~ 2026-05-24 06:00 KST)",
        "cycles": 42, "with_orders": 3, "risk_approved": 1, "market_open_cycles": 2,
        "candidates_picked": 30, "targets_final": 6, "candidate_to_target_pct": 20.0,
        "trades_executed": 1, "trades_failed": 0,
        "equity_return_pct_adj": 7.32,
    }
    msg = weekly_review.build_review_message(summary)
    # 사장 요구: 로그 파일 경로로 떠넘기지 말 것
    assert "weekly_review.log" not in msg and "ops_support.log" not in msg
    # 점검 수치가 메시지에 직접 담겨야 한다
    assert "사이클 42회" in msg
    assert "후보 30 → 매수 6" in msg
    assert "+7.32%" in msg
    # 뉴스 분류 관련 표시는 사라져야 한다
    assert "뉴스분류" not in msg and "가중치 점검" not in msg


def test_build_review_message_handles_missing_equity():
    summary = {"period": "최근 7일", "cycles": 0, "equity_return_pct_adj": None}
    msg = weekly_review.build_review_message(summary)
    assert "데이터 부족" in msg
