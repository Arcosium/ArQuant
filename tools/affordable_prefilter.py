"""후보 사전필터 — 1주 가격이 '사이클 매수예산'(리스크부 기준) 내인가.

리스크부(agents/guardrails.py)가 cash*MAX_CYCLE_BUDGET_RATIO 초과 시 반려하므로, 선정 전에 동일
기준으로 초고가주를 배제해 '선정→무조건 반려' 헛사이클을 막는다(2026-06-05, LG이노텍 1주 1.13M vs
사이클예산 595K 데드존). 시세 조회 실패/현금 정보 없음은 보수적으로 통과(데이터 결손 누락 방지)."""
from __future__ import annotations


def affordable_within_cycle_budget(price: float, cash: float, cycle_ratio: float,
                                   overshoot: float = 1.2) -> bool:
    p = float(price or 0.0); c = float(cash or 0.0)
    if p <= 0:        # 시세 조회 실패 → 통과(보수)
        return True
    if c <= 0:        # 현금 판단 불가 → 통과(보수)
        return True
    budget = c * float(cycle_ratio or 0.0) * float(overshoot or 1.0)
    if budget <= 0:
        return True
    return p <= budget
