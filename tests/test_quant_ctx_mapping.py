"""_quant_ctx_for — 주문 사유에 붙는 퀀트 컨텍스트는 '그 종목'의 분석이어야 한다.

버그 2026-05-22: _build_orders 가 report.find(code) 후 [idx-100:idx+200] 윈도우를
ctx[:90] 로 잘라 썼는데, idx-100 이 직전 종목 섹션의 꼬리를 끌어와
SK텔레콤(017670) 매수 주문 사유에 지아이이노베이션(358570)의 퀀트
("퀀트점수: 358570=5 진입가: 358570=12890")가 붙었다.
종목 섹션을 '퀀트점수: {code}=' 앵커로 정확히 매칭해 인접 종목 오염을 막아야 한다.
"""
from main_swarm import _quant_ctx_for

REPORT = (
    "[계량분석팀장] 지아이이노베이션\n358570(지아이이노베이션) 고변동 바이오, 사이즈 소량.\n"
    "퀀트점수: 358570=5\n진입가: 358570=12890"
    "\n\n---\n\n"
    "[계량분석팀장] SK텔레콤\n017670(SK텔레콤) 정배열 강세, 외인 순매수.\n"
    "퀀트점수: 017670=8\n진입가: 017670=시장가"
)


def test_returns_own_section_not_previous():
    ctx = _quant_ctx_for(REPORT, "017670")
    assert "017670" in ctx or "SK텔레콤" in ctx
    assert "358570" not in ctx
    assert "12890" not in ctx


def test_first_stock_section():
    ctx = _quant_ctx_for(REPORT, "358570")
    assert "지아이이노베이션" in ctx or "358570" in ctx
    assert "SK텔레콤" not in ctx


def test_missing_code_returns_empty():
    assert _quant_ctx_for(REPORT, "999999") == ""


def test_blank_inputs():
    assert _quant_ctx_for("", "017670") == ""
    assert _quant_ctx_for(REPORT, "") == ""
