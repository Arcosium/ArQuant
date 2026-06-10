from main_swarm import _build_cycle_final_report


def test_final_report_never_claims_unexecuted_order_filled():
    report = _build_cycle_final_report([], {"results": []}, [])
    assert "체결 확인:" not in report
    assert "실제 주문과 체결 없이" in report


def test_final_report_separates_filled_pending_and_failed():
    rows = [
        {"ticker": "005930", "side": "buy", "qty": 1, "accepted": True, "filled": True},
        {"ticker": "AAPL", "side": "buy", "qty": 2, "accepted": True, "filled": False},
        {"ticker": "153130", "side": "sell", "qty": 7, "accepted": False,
         "filled": False, "result": "NXT 종목정보 없음"},
    ]
    report = _build_cycle_final_report(rows, {"results": []}, [])
    assert "체결 확인: 005930 매수 1주" in report
    assert "체결 확인 중: AAPL 매수 2주" in report
    assert "주문 실패: 153130 매도 7주" in report


def test_final_report_surfaces_risk_rejections_and_notes():
    risk = {"results": [
        {"ticker": "005380", "status": "REJECTED"},
        {"ticker": "005930", "status": "APPROVED"},
    ]}
    report = _build_cycle_final_report([], risk, ["예산 초과로 제외"])
    assert "리스크 반려: 005380" in report
    assert "예산 초과로 제외" in report
