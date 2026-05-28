"""ops_support 시스템 프롬프트 — 파라미터 점검 전용으로 재구성됐는지 고정.

버그 2026-05-22: 운용지원실장은 이제 param_overrides 만 조정 가능(소스 자가수정 폐지)인데
시스템 프롬프트 본문은 여전히 소스 search/replace·서버 재시작 중심이라, param-only 강제 규칙과
충돌해 "이상 없음/변경 없음"만 뱉었다. 본문을 '튜닝 파라미터를 하나씩 점검 → param_overrides 제안'
중심으로 재작성하고 죽은 소스수정 framing 을 제거해야 한다.
"""
from infra.ops_support_worker import _build_system_prompt


def test_prompt_directs_systematic_param_review():
    p = _build_system_prompt("ops_support", param_tuning=True)
    assert "param_overrides" in p
    assert "하나씩" in p, "각 튜닝 파라미터를 하나씩 점검하라는 지시가 있어야 한다"


def test_prompt_lists_tunable_keys():
    p = _build_system_prompt("ops_support", param_tuning=True)
    assert "TAKE_PROFIT_PCT" in p and "STOP_LOSS_PCT" in p


def test_prompt_drops_dead_source_edit_framing():
    p = _build_system_prompt("ops_support", param_tuning=True)
    # 본문의 소스 자가수정/재시작 framing 은 제거돼야 한다 (혼선 유발 → "이상 없음")
    assert "서버를 재시작" not in p
    assert "ALLOWED_EDITS" not in p
