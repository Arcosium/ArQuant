"""계량분석팀장 프롬프트 — (#9) 가중치 고정 + (#10) 진입가 포맷 일관성.

버그 2026-05-22:
 (#9) 결론의 가중치가 '예: 추세 30% + ...' 라는 예시여서 종목마다 제각각(추세 25/30/35%,
      뉴스 10/15/35%)이 됐다. 운용전략실장은 일괄 6점 컷오프를 적용하는데 점수 산식이
      종목마다 달라 비교 불가. 고정 가중치를 강제해야 한다.
 (#10) 진입가에 '관망 ±X%' 자유서식을 제시했으나(146-150행) 154행에선 '관망 모드 미구현',
      리스크관리실장도 watch_pct 를 의도적으로 버려 항상 시장가 즉시 매수다. 자기모순이며
      운용전략실장 PASS2 가 '현재가>진입가라 추격 보류'로 오작동한다. 진입가는 '시장가' 또는
      숫자(지정가)로만 통일해야 한다.
"""
from agents.specialists import create_quant_analyst


def _prompt():
    return create_quant_analyst().system_prompt


def test_weights_fixed_not_example():
    p = _prompt()
    assert "고정 가중치" in p
    assert ("변경 금지" in p) or ("반드시" in p)


def test_entry_price_drops_watch_mode():
    p = _prompt()
    assert "관망 -1.5%" not in p
    assert "관망 +1.0%" not in p


def test_entry_price_keeps_market_and_limit():
    p = _prompt()
    assert "시장가" in p
    assert "지정가" in p


def test_held_stock_emits_sell_price():
    # 보유 종목은 매수 분석이 아니라 매도가를 제시해야 한다(사장 지시 2026-05-22)
    p = _prompt()
    assert "매도가" in p
