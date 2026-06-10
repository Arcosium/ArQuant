from main_swarm import _format_sizing_notes_for_report


def test_skip_notes_formatted():
    notes = ["CVX: 해외 시세 조회 실패(거래소 미확인) → 제외", "AAPL: 1주 매수"]
    out = _format_sizing_notes_for_report(notes)
    assert "CVX" in out and "시세 조회 실패" in out


def test_empty_notes():
    assert _format_sizing_notes_for_report([]) == ""
    assert _format_sizing_notes_for_report(None) == ""
