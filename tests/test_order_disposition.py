"""_format_order_disposition — 운용전략실장 리포트 환각 방지.

사장 검토 2026-05-29: #62 final_report 가 리스크 반려된 매수(402340·005380)를
'최종 매수 종목으로 선정'으로 단정해 '선정'과 '체결'을 뭉갰다. 리포트 프롬프트에
리스크 승인/반려 내역을 결정론적으로 주입해 환각을 막는다.
"""
from main_swarm import _format_order_disposition


def test_rejected_buys_are_surfaced_with_reasons():
    rr = {"results": [
        {"ticker": "000430", "side": "sell", "qty": 153, "status": "APPROVED", "issues": []},
        {"ticker": "402340", "side": "buy", "qty": 1, "status": "REJECTED",
         "issues": ["사이클 누적 매수예산(655,300원) 초과"]},
        {"ticker": "005380", "side": "buy", "qty": 1, "status": "REJECTED",
         "issues": ["사이클 누적 매수예산(655,300원) 초과"]},
    ]}
    out = _format_order_disposition(rr)
    assert "402340" in out and "005380" in out
    assert "반려" in out
    assert "예산" in out  # 사유가 포함돼야 한다
    assert "000430" in out  # 승인 매도도 표기


def test_all_approved():
    rr = {"results": [
        {"ticker": "AAPL", "side": "buy", "qty": 2, "status": "APPROVED", "issues": []},
    ]}
    out = _format_order_disposition(rr)
    assert "AAPL" in out
    assert "반려: 없음" in out


def test_empty_results():
    assert "없음" in _format_order_disposition({"results": []})
    assert "없음" in _format_order_disposition({})
    assert "없음" in _format_order_disposition(None)
