"""4분할 자산배분(주식/채권/원자재/현금) — 매크로 프롬프트 + 파싱 (사장 지시 2026-06-09)."""
from agents.specialists import create_macro_analyst
from infra.asset_sleeves import parse_macro_sleeve_pct

MACRO = ("📊 매크로 환경 요약\n"
         "📈 자산 배분 권고: 주식 45% / 채권 25% / 원자재 20% / 현금 10% "
         "(직전: 주식 50% / 채권 25% / 원자재 15% / 현금 10%)")


def test_macro_prompt_has_four_way_allocation():
    a = create_macro_analyst(injection={"uid": 1})
    assert "원자재" in a.system_prompt
    # 자산배분 권고 형식 줄에 4분할이 모두 들어간다
    assert "주식 X% / 채권 Y% / 원자재 W% / 현금 Z%" in a.system_prompt


def test_four_way_parse_current_not_prev():
    assert parse_macro_sleeve_pct(MACRO, "주식") == 0.45
    assert parse_macro_sleeve_pct(MACRO, "채권") == 0.25
    assert parse_macro_sleeve_pct(MACRO, "원자재") == 0.20  # 직전(15%)이 아닌 현재(20%)
