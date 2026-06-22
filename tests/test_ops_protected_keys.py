"""운용지원실장(ops) 자율 튜닝의 정책 플래그 봉인 가드레일.

회사 운영 거버넌스(사장 지시 2026-06-05): ops 는 전술 파라미터는 적극 튜닝하되,
자산군·엔진 같은 '정책 플래그'(ALLOW_US_STOCKS / ALLOW_DERIVATIVES /
ENABLE_CHEAP_FALLBACK / DETERMINISTIC_SCORING)는 사장 전용으로 봉인한다.
ops 가 자율(cycle/weekly)로 미국 주식을 꺼버려 실거래 계정 매수가 막혔던 사고 재발 방지.
사장 직접 지시(manual)는 권위이므로 면제한다.
"""
import config
from infra.ops_param_clamp import partition_protected


def test_protected_keys_defined():
    prot = set(getattr(config, "OPS_PROTECTED_KEYS", ()))
    assert {"ALLOW_US_STOCKS", "ALLOW_DERIVATIVES",
            "ENABLE_CHEAP_FALLBACK", "DETERMINISTIC_SCORING"} <= prot


def test_nxt_toggles_protected():
    # 사장 지시 2026-06-11: NXT 시간외 매매 마스터/구간 토글은 사장 전용 정책 플래그.
    # ops 자율 사이클이 NXT 를 꺼 profiles/1 override(우선순위 ①)가 사장 전략탭 설정(②)을
    # 가려 '재시작마다 OFF 로 복귀'하던 사고 재발 방지(2026-06-11).
    prot = set(getattr(config, "OPS_PROTECTED_KEYS", ()))
    assert {"ENABLE_NXT_EXTENDED_HOURS", "ENABLE_NXT_PRE_MARKET",
            "ENABLE_NXT_AFTER_MARKET"} <= prot


def test_macro_stock_gate_protected():
    # 사장 지시 2026-06-12: 매크로 주식비중 매수게이트는 사장 방어 설정.
    # ops 자율 사이클이 게이트를 꺼(MACRO_STOCK_GATE_ENABLED=False) 매크로 '주식 0%'
    # 권고에도 1·2·3시 후보선정이 돌던 사고(uid1 cyc305 OFF→cyc312 ON) 재발 방지.
    prot = set(getattr(config, "OPS_PROTECTED_KEYS", ()))
    assert "MACRO_STOCK_GATE_ENABLED" in prot


def test_cycle_drops_macro_gate_toggle():
    kept, review, notes = partition_protected(
        {"MACRO_STOCK_GATE_ENABLED": False, "MIN_QUANT_SCORE": 5}, trigger="cycle")
    assert kept == {"MIN_QUANT_SCORE": 5}
    assert any("MACRO_STOCK_GATE_ENABLED" in n for n in notes)


def test_cycle_drops_nxt_toggle():
    kept, review, notes = partition_protected(
        {"ENABLE_NXT_EXTENDED_HOURS": False, "MIN_QUANT_SCORE": 5}, trigger="cycle")
    assert kept == {"MIN_QUANT_SCORE": 5}
    assert any("ENABLE_NXT_EXTENDED_HOURS" in n for n in notes)


def test_weekly_routes_nxt_toggle_to_review():
    kept, review, notes = partition_protected(
        {"ENABLE_NXT_AFTER_MARKET": False, "STOP_LOSS_PCT": 4.0}, trigger="weekly")
    assert kept == {"STOP_LOSS_PCT": 4.0}
    assert review == {"ENABLE_NXT_AFTER_MARKET": False}


def test_manual_keeps_all():
    kept, review, notes = partition_protected(
        {"ALLOW_US_STOCKS": True, "STOP_LOSS_PCT": 4.0}, trigger="manual")
    assert kept == {"ALLOW_US_STOCKS": True, "STOP_LOSS_PCT": 4.0}
    assert review == {} and notes == []


def test_weekly_routes_policy_to_review():
    kept, review, notes = partition_protected(
        {"ALLOW_US_STOCKS": True, "MIN_QUANT_SCORE": 5}, trigger="weekly")
    assert kept == {"MIN_QUANT_SCORE": 5}
    assert review == {"ALLOW_US_STOCKS": True}
    assert any("ALLOW_US_STOCKS" in n for n in notes)


def test_weekly_routes_all_policy_flags():
    raw = {"ALLOW_US_STOCKS": False, "ALLOW_DERIVATIVES": True,
           "ENABLE_CHEAP_FALLBACK": True, "DETERMINISTIC_SCORING": False,
           "STOP_LOSS_PCT": 4.0}
    kept, review, notes = partition_protected(raw, trigger="weekly")
    assert set(kept) == {"STOP_LOSS_PCT"}
    assert set(review) == {"ALLOW_US_STOCKS", "ALLOW_DERIVATIVES",
                           "ENABLE_CHEAP_FALLBACK", "DETERMINISTIC_SCORING"}
    assert len(notes) == 4


def test_cycle_drops_policy():
    kept, review, notes = partition_protected(
        {"ALLOW_US_STOCKS": False, "MIN_QUANT_SCORE": 5}, trigger="cycle")
    assert kept == {"MIN_QUANT_SCORE": 5}
    assert review == {}
    assert any("ALLOW_US_STOCKS" in n for n in notes)


def test_tactical_only_passthrough_all_triggers():
    for trig in ("cycle", "weekly", "manual"):
        kept, review, notes = partition_protected({"MIN_QUANT_SCORE": 6}, trigger=trig)
        assert kept == {"MIN_QUANT_SCORE": 6} and review == {} and notes == []


def test_empty_and_none_safe():
    assert partition_protected({}, trigger="cycle") == ({}, {}, [])
    assert partition_protected(None, trigger="weekly") == ({}, {}, [])


# ── wiring: _handle_param_tuning 가 자율/manual 트리거에 맞게 정책 키를 처리하는지 ──
def _patch_sideeffects(monkeypatch):
    import main_swarm
    from infra import profile_overrides, ops_history
    captured = {}

    def fake_set(uid, params):
        captured["params"] = dict(params)
        return dict(params)

    monkeypatch.setattr(profile_overrides, "set_overrides", fake_set)
    monkeypatch.setattr(profile_overrides, "record_proposal", lambda *a, **k: None)
    monkeypatch.setattr(ops_history, "append_run", lambda *a, **k: None)
    monkeypatch.setattr(main_swarm, "log_response_event", lambda *a, **k: None)
    return captured


def test_cycle_trigger_strips_protected_in_pipeline(monkeypatch):
    from infra.ops_support_worker import _handle_param_tuning
    captured = _patch_sideeffects(monkeypatch)
    plan = {"summary": "조정", "rationale": "근거",
            "param_overrides": {"ALLOW_US_STOCKS": False, "MIN_QUANT_SCORE": 5}}
    _handle_param_tuning(plan, actor_uid=99999, role="ops_support", started="t",
                         trigger="cycle", cycle_id=1, has_cycle_data=True)
    assert "ALLOW_US_STOCKS" not in captured["params"]
    assert captured["params"]["MIN_QUANT_SCORE"] == 5


def test_manual_trigger_keeps_protected_in_pipeline(monkeypatch):
    from infra.ops_support_worker import _handle_param_tuning
    captured = _patch_sideeffects(monkeypatch)
    plan = {"summary": "사장 지시", "rationale": "지시",
            "param_overrides": {"ALLOW_US_STOCKS": True}}
    _handle_param_tuning(plan, actor_uid=99999, role="ops_support", started="t",
                         trigger="manual", cycle_id=None, has_cycle_data=False)
    assert captured["params"].get("ALLOW_US_STOCKS") is True


def test_weekly_trigger_enqueues_policy_not_apply(monkeypatch):
    from infra.ops_support_worker import _handle_param_tuning
    import infra.policy_approval_inbox as box
    captured = _patch_sideeffects(monkeypatch)          # set_overrides 캡처(전술키용)
    enq = []
    monkeypatch.setattr(box, "enqueue",
                        lambda uid, key, pv, cv, r="": enq.append((uid, key, pv)))
    plan = {"summary": "주간", "rationale": "근거",
            "param_overrides": {"ALLOW_US_STOCKS": True, "MIN_QUANT_SCORE": 5}}
    _handle_param_tuning(plan, actor_uid=99999, role="ops_support", started="t",
                         trigger="weekly", cycle_id=None, has_cycle_data=True)
    # 전술키만 적용
    assert captured["params"] == {"MIN_QUANT_SCORE": 5}
    # 정책키는 승인 대기로 회부
    assert any(k == "ALLOW_US_STOCKS" for (_u, k, _v) in enq)
