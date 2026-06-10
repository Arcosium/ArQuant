"""ops_support 프롬프트 잔재 제거 회귀 가드 (사장 지시 2026-06-08).

운용지원실장이 '비관리자라 소스 수정 불가, 관리자(hh09080)가 직접' 따위를 환각하던
옛 프레이밍(소스 변경/changes/restart/admin·비관리자 구분/username 노출)을 프롬프트에서 제거.
ops 의 유일 역할은 param_overrides 튜닝 — 시스템엔 소스 변경 기능이 없다.
"""
from infra import ops_support_worker as O

_FORBIDDEN = [
    "비관리자", "non-ADMIN", "hh09080", "소스 수정", "소스 변경",
    "search/replace", "권한 밖", "사장이 직접", "샌드박스",
]


def test_ops_system_prompt_has_no_source_admin_remnants():
    p = O._build_system_prompt("ops_support", param_tuning=True)
    hit = [w for w in _FORBIDDEN if w in p]
    assert not hit, f"ops 시스템 프롬프트에 잔재 문구 잔존: {hit}"


def test_ops_response_schema_drops_changes_and_restart():
    p = O._build_system_prompt("ops_support", param_tuning=True)
    assert '"changes"' not in p, "응답 스키마에서 changes 필드 제거 안 됨"
    assert '"restart"' not in p, "응답 스키마에서 restart 필드 제거 안 됨"


def test_ops_system_prompt_keeps_param_role():
    p = O._build_system_prompt("ops_support", param_tuning=True)
    assert "param_overrides" in p          # 핵심 역할(파라미터 튜닝)은 유지
