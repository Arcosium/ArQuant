"""계량분석팀장 프롬프트 — (#9) 가중치 종목 간 통일 + (#10) 진입가 포맷 일관성.

버그 2026-05-22:
 (#9) 결론의 가중치가 '예: 추세 30% + ...' 라는 예시여서 종목마다 제각각(추세 25/30/35%,
      뉴스 10/15/35%)이 됐다. 운용전략실장은 일괄 점수 컷오프를 적용하는데 점수 산식이
      종목마다 달라 비교 불가. **모든 종목에 동일 가중치**를 강제해야 한다.
 사장 지시 2026-06-04: 가중치는 이제 운용지원실장이 조정 가능 — 메시지의 전략 블록 값 우선,
      없으면 기본(30/20/15/20/15). 단 '종목마다 다른 가중치 금지'(통일)는 그대로 유지된다.
 (#10) 진입가에 '관망 ±X%' 자유서식을 제시했으나(146-150행) 154행에선 '관망 모드 미구현',
      리스크관리실장도 watch_pct 를 의도적으로 버려 항상 시장가 즉시 매수다. 자기모순이며
      운용전략실장 PASS2 가 '현재가>진입가라 추격 보류'로 오작동한다. 진입가는 '시장가' 또는
      숫자(지정가)로만 통일해야 한다.
"""
from agents.specialists import create_quant_analyst


def _prompt():
    return create_quant_analyst().system_prompt


def test_commentator_mode_when_deterministic_score_given():
    # 2026-06-04 결정론 점수 엔진: 점수가 시스템 확정값으로 주어지면 LLM은 바꾸지 말고 해설만.
    p = _prompt()
    assert "결정론 점수" in p
    assert ("바꾸지" in p) or ("그대로" in p)        # 점수 변경 금지·그대로 echo
    assert "해설" in p


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
