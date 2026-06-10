from infra.ops_support_worker import _task_block_for_trigger


def test_weekly_is_aggressive():
    t = _task_block_for_trigger("weekly")
    assert "매우 적극" in t or "큰 폭" in t
    assert "전" in t  # 전 튜넌키 점검 뉘앙스


def test_cycle_is_active_but_bounded():
    t = _task_block_for_trigger("cycle")
    assert "적극" in t


def test_manual_focuses_directive():
    t = _task_block_for_trigger("manual")
    assert "지시" in t
