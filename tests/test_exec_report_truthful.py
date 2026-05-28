"""final_report(전략실장 요약) LLM에 넘기는 실행요약은 '체결/접수대기/실패'를 정직히 구분해야 한다.

배경(2026-05-27 진단): 기존 exec_summary는 `→ OK/실패` 이진값이라, 접수만 되고 아직
체결 미확인인 주문(특히 US — 즉시 확인 불가, 폴링으로 추후 확정 / KR 지정가 미도달)이
'실패'로 LLM에 들어갔다. LLM은 '왜 실패했지'를 채우려 유동성 부족·VWAP 괴리 등 사유를
**지어냈다**(cycle 12·20 환각). 접수·체결대기를 '실패'로 부르지 않으면 환각의 입력이 사라진다.
"""
from main_swarm import _format_exec_for_report


def test_filled_is_labeled_filled_not_failed():
    rows = [{"ticker": "005930", "side": "buy", "qty": 1,
             "accepted": True, "filled": True, "ok": True}]
    s = _format_exec_for_report(rows)
    assert "체결" in s
    assert "실패" not in s


def test_accepted_but_unfilled_is_pending_not_failed():
    """핵심: 접수만 되고 미체결인 주문을 '실패'로 표기하면 안 된다(환각 입력 제거)."""
    rows = [{"ticker": "NVDA", "side": "buy", "qty": 1,
             "accepted": True, "filled": False, "ok": False, "fill_currency": "USD"}]
    s = _format_exec_for_report(rows)
    assert "실패" not in s, "접수·체결대기를 실패로 부르면 LLM이 사유를 지어낸다"
    assert ("접수" in s) or ("체결 대기" in s) or ("체결대기" in s)


def test_rejected_order_is_failed():
    rows = [{"ticker": "AAPL", "side": "buy", "qty": 1,
             "accepted": False, "filled": False, "ok": False}]
    s = _format_exec_for_report(rows)
    assert "실패" in s


def test_empty_returns_none_marker():
    assert _format_exec_for_report([]) == "없음"


def test_mixed_each_classified_independently():
    rows = [
        {"ticker": "005930", "side": "buy", "qty": 1, "accepted": True, "filled": True, "ok": True},
        {"ticker": "NVDA", "side": "buy", "qty": 1, "accepted": True, "filled": False, "ok": False},
        {"ticker": "AAPL", "side": "sell", "qty": 1, "accepted": False, "filled": False, "ok": False},
    ]
    s = _format_exec_for_report(rows)
    # 체결 1, 접수대기 1, 실패 1 — 실패는 거부된 AAPL '하나만'이어야 한다
    assert s.count("실패") == 1
    assert "005930" in s and "NVDA" in s and "AAPL" in s
