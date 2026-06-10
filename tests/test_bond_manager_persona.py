from agents.specialists import create_bond_manager


def test_persona_created():
    a = create_bond_manager(injection={"uid": 1})
    assert a.name == "채권운용실장"
    assert a.role == "bond_manager"


def test_persona_is_active_rate_strategist():
    a = create_bond_manager(injection={"uid": 1})
    assert "금리" in a.system_prompt and "듀레이션" in a.system_prompt
    assert "채권결정" in a.system_prompt          # 출력 형식 명시
    assert "퀀트" not in a.system_prompt or "무관" in a.system_prompt  # 퀀트 비의존
