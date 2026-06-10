"""원자재운용실장(commodity_manager) 페르소나 (사장 지시 2026-06-09).

채권운용실장과 동형 — 실물자산 매크로 전략가. 주식 퀀트·계량분석 무관(매크로+뉴스로만 판단).
"""
import config
from agents.specialists import create_commodity_manager


def test_commodity_model_and_token_registered():
    assert "commodity_manager" in config.MODEL_ASSIGNMENTS
    assert "commodity_manager" in config.AGENT_MAX_TOKENS


def test_commodity_persona_name_role_keyword():
    a = create_commodity_manager(injection={"uid": 1})
    assert a.name == "원자재운용실장"
    assert a.role == "commodity_manager"
    # 마지막 줄 결정표 키워드
    assert "원자재결정" in a.system_prompt


def test_commodity_persona_macro_news_not_quant():
    a = create_commodity_manager(injection={"uid": 1})
    # 실물자산 매크로(인플레·달러·지정학·수급)로 판단; 주식 퀀트는 무관
    assert "인플레" in a.system_prompt or "실물자산" in a.system_prompt
    assert "퀀트" in a.system_prompt  # "주식 퀀트는 무관" 류 명시
