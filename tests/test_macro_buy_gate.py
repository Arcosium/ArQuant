"""사장 지시 2026-06-04: 매크로 권고 주식비중이 현재 주식 평가비중 이하(=추가 매수 여력 없음)면
신규 매수 후보 선정·평가 자체를 건너뛴다(보유 매도/관리만 진행).

매크로가 '주식 1%'를 외치는데 주식운용실장은 매시간 신규 종목을 사고 사후관리실장은 같은 1%
권고로 -1.5%에 되파는 churn 의 근원(매수엔진 ↔ 매크로 배분 불일치)을 끊는다.
파싱 실패 시 fail-open(매수를 막지 않음 — 잘못된 차단이 거래를 통째로 멈추는 것 방지)."""
from main_swarm import _parse_macro_stock_pct, _macro_blocks_new_buys

# 실제 라이브 매크로 보고에서 그대로 가져온 형태(마크다운 ** 포함)
MACRO = ("📊 매크로 환경 요약\n... 코스피 급락 ...\n"
         "📈 자산 배분 권고: 주식 **1%** / 채권 **4%** / 현금 **95%** (직전: 주식 1% / 채권 4% / 현금 95%) — 변경 없음\n"
         "- 주식 1% (유지): ...\n- 채권 4% (유지): ...")


def test_parse_macro_stock_pct_from_recommendation():
    assert _parse_macro_stock_pct(MACRO) == 0.01


def test_parse_handles_plain_no_markdown():
    assert _parse_macro_stock_pct("자산 배분 권고: 주식 25% / 채권 10% / 현금 65%") == 0.25


def test_parse_returns_none_when_absent():
    assert _parse_macro_stock_pct("주식 비중을 늘립니다(구체 수치 없음)") is None


def test_blocks_when_recommendation_at_or_below_current_weight():
    # 권고 1% ≤ 현재 주식비중 10% → 추가 매수 여력 없음 → 차단
    blocked, pct = _macro_blocks_new_buys(MACRO, equity_weight=0.10)
    assert blocked is True and pct == 0.01


def test_blocks_on_exact_equality():
    blocked, _ = _macro_blocks_new_buys("자산 배분 권고: 주식 10%", equity_weight=0.10)
    assert blocked is True


def test_allows_when_recommendation_above_current_weight():
    # 권고 25% > 현재 5% → 여력 있음 → 허용
    blocked, pct = _macro_blocks_new_buys("자산 배분 권고: 주식 25% / 채권 0% / 현금 75%", equity_weight=0.05)
    assert blocked is False and pct == 0.25


def test_fail_open_on_parse_failure():
    # 매크로에서 주식% 를 못 읽으면 매수를 막지 않는다(거래 전면중단 방지).
    blocked, pct = _macro_blocks_new_buys("수치 없는 매크로 보고", equity_weight=0.50)
    assert blocked is False and pct is None
