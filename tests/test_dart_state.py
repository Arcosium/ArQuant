"""ITEM2a/2b 회귀 테스트: DART 상태 구분 + 재무상태표 결정론 검증.

- QUERY_FAILED vs NO_DISCLOSURE 구분이 명확한지 확인
- 잘못된/불일치 재무 수치가 부채>자산 오판을 유발하지 않는지 확인
- 정상 수치는 올바른 부채비율을 반환하는지 확인
"""
import pytest
from tools.dart_disclosure import (
    DartResult,
    DART_STATE_OK,
    DART_STATE_NO_DISCLOSURE,
    DART_STATE_QUERY_FAILED,
    parse_balance_sheet_sanity,
    _parse_amount,
)


# ─── DartResult 상태 구분 ────────────────────────────────────────────────────

def test_dart_result_ok_properties():
    r = DartResult(state=DART_STATE_OK, text="정상 데이터")
    assert r.ok is True
    assert r.failed is False
    assert r.no_disclosure is False
    assert str(r) == "정상 데이터"


def test_dart_result_no_disclosure_properties():
    r = DartResult(state=DART_STATE_NO_DISCLOSURE, text="공시 없음")
    assert r.ok is False
    assert r.failed is False
    assert r.no_disclosure is True
    # NO_DISCLOSURE는 시스템 리스크가 아님 — risk_text에 '시스템 리스크' 없음
    assert "시스템 리스크" not in r.risk_text()
    assert "특이사항 없음" in r.risk_text()


def test_dart_result_query_failed_properties():
    r = DartResult(state=DART_STATE_QUERY_FAILED, text="API 오류")
    assert r.ok is False
    assert r.failed is True
    assert r.no_disclosure is False
    # QUERY_FAILED는 시스템 리스크 경고 포함
    assert "시스템 리스크" in r.risk_text()


def test_dart_result_str_compatibility():
    """str() 호출이 text 필드만 반환해 기존 f-string 호출자와 호환."""
    r = DartResult(state=DART_STATE_QUERY_FAILED, text="에러 메시지")
    assert f"공시: {r}" == "공시: 에러 메시지"


# ─── _parse_amount ───────────────────────────────────────────────────────────

def test_parse_amount_with_commas():
    assert _parse_amount("1,234,567원") == pytest.approx(1_234_567.0)


def test_parse_amount_negative():
    assert _parse_amount("-5,000,000") == pytest.approx(-5_000_000.0)


def test_parse_amount_empty_returns_none():
    assert _parse_amount("") is None
    assert _parse_amount(None) is None


def test_parse_amount_non_numeric_returns_none():
    assert _parse_amount("해당없음") is None


# ─── parse_balance_sheet_sanity ─────────────────────────────────────────────

def _bs(assets: str, liabilities: str, equity: str) -> dict:
    return {"자산총계": assets, "부채총계": liabilities, "자본총계": equity}


def test_bs_sanity_normal_case():
    """정상 수치: 자산=부채+자본, 부채비율 정상."""
    result = parse_balance_sheet_sanity(_bs("1,000,000", "400,000", "600,000"))
    assert result["state"] == "OK"
    assert result["assets"] == pytest.approx(1_000_000.0)
    assert result["liabilities"] == pytest.approx(400_000.0)
    assert result["equity"] == pytest.approx(600_000.0)
    assert result["debt_ratio"] == pytest.approx(0.4)
    # 정상 수치면 note에 부채비율 정보가 있어야 한다
    assert "부채비율" in result["note"]


def test_bs_sanity_debt_exceeds_assets_flagged_as_impossible():
    """부채>자산인 경우 IMPOSSIBLE — 부채>자산 단정 금지 메시지 포함."""
    result = parse_balance_sheet_sanity(_bs("500,000", "800,000", "-300,000"))
    assert result["state"] == "IMPOSSIBLE"
    # 이 상태에서 debt_ratio는 None (잘못된 수치로 계산 금지)
    assert result["debt_ratio"] is None
    # note에 판정 금지 경고 포함
    assert "판정" in result["note"] or "금지" in result["note"] or "오류" in result["note"]


def test_bs_sanity_inconsistent_data_flagged():
    """자산 ≠ 부채+자본 (5% 초과 불일치) → IMPOSSIBLE."""
    # 자산 1,000,000, 부채 200,000, 자본 200,000 → 합계 400,000 ≠ 1,000,000 (60% 불일치)
    result = parse_balance_sheet_sanity(_bs("1,000,000", "200,000", "200,000"))
    assert result["state"] == "IMPOSSIBLE"
    assert "불일치" in result["note"] or "오류" in result["note"]


def test_bs_sanity_missing_field_returns_parse_failed():
    """자산총계 누락 → PARSE_FAILED."""
    result = parse_balance_sheet_sanity({"부채총계": "400,000", "자본총계": "600,000"})
    assert result["state"] == "PARSE_FAILED"
    assert "자산총계" in result["note"]


def test_bs_sanity_garbage_value_returns_parse_failed():
    """파싱 불가 값(예: '해당없음') → PARSE_FAILED."""
    result = parse_balance_sheet_sanity(_bs("해당없음", "400,000", "600,000"))
    assert result["state"] == "PARSE_FAILED"
    assert result["assets"] is None


def test_bs_sanity_correct_ratio_edge_case_zero_equity():
    """자본=0 (자본잠식 직전): 자산=부채이면 OK."""
    result = parse_balance_sheet_sanity(_bs("1,000,000", "1,000,000", "0"))
    assert result["state"] == "OK"
    assert result["debt_ratio"] == pytest.approx(1.0)


def test_bs_sanity_small_rounding_within_tolerance():
    """1% 이내 반올림 오차는 OK로 처리."""
    # 자산 1,000,000 / 부채 400,001 / 자본 600,000 → 합계 1,000,001 (0.0001% 오차)
    result = parse_balance_sheet_sanity(_bs("1,000,000", "400,001", "600,000"))
    assert result["state"] == "OK"


# ─── 과거 오판 케이스: 잘못된 단위 혼재 (예: 부채는 억원, 자산은 백만원) ──────────

def test_bs_unit_mismatch_detected_as_impossible():
    """부채가 억 단위(미처리), 자산이 원 단위 → 불일치로 IMPOSSIBLE."""
    # 자산 1조원(원 단위) vs 부채 2000(억 단위, 즉 실제 2000억=2000억원)
    # 수치 파싱 결과 부채(2000) > 자산(1000000000) 이 아니면 내적 불일치
    # 여기서는 명백히 부채>자산인 케이스로 테스트
    result = parse_balance_sheet_sanity(_bs("1,000", "2,000", "-1,000"))
    assert result["state"] == "IMPOSSIBLE"
    # 이 판정으로 부채>자산 단정을 하면 안 됨 → debt_ratio=None
    assert result["debt_ratio"] is None
