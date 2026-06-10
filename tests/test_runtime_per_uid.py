"""런타임 전략 상태 프로필(uid)별 격리 — 프리셋 폐지 후(2026-06-09) custom params 기반.

이전에는 set_strategy(preset_name) 로 빌트인 프리셋을 적용했으나, 프리셋이 폐지되어
이제는 set_strategy(custom={...}) 로 STRATEGY_DEFAULTS 베이스 위에 파라미터를 얹는다.
uid 별 격리(한 계정 변경이 다른 계정/_default 에 영향 없음)는 그대로 검증한다.
"""
import json

import pytest

import config
import runtime

KEY = "PER_ORDER_BUDGET_RATIO"


@pytest.fixture
def rt(tmp_path, monkeypatch):
    """전략 상태 파일·인메모리 맵 + 프로필 오버라이드 디렉터리를 임시로 격리."""
    monkeypatch.setattr(runtime, "_STATE", tmp_path / "strategy_state.json")
    monkeypatch.setattr(runtime, "_HIST", tmp_path / "strategy_history.json")
    default = {"name": "default", "params": dict(config.STRATEGY_DEFAULTS)}
    monkeypatch.setattr(runtime, "_states", {"_default": default}, raising=False)
    from infra import profile_overrides as po
    monkeypatch.setattr(po, "_PROFILES_DIR", tmp_path / "profiles")
    return runtime


def test_set_strategy_per_uid_isolated(rt):
    rt.set_strategy(custom={KEY: 0.05}, uid=1)
    rt.set_strategy(custom={KEY: 0.20}, uid=2)
    assert abs(rt.get(KEY, uid=1) - 0.05) < 1e-9
    assert abs(rt.get(KEY, uid=2) - 0.20) < 1e-9


def test_uid_none_unaffected_by_per_uid_changes(rt):
    default_val = rt.get(KEY, uid=None)
    rt.set_strategy(custom={KEY: 0.05}, uid=1)
    rt.set_strategy(custom={KEY: 0.20}, uid=2)
    assert rt.get(KEY, uid=None) == default_val
    assert default_val == config.STRATEGY_DEFAULTS[KEY]


def test_active_reflects_custom_params(rt):
    rt.set_strategy(custom={KEY: 0.05}, uid=1)
    a = rt.active(uid=1)
    assert abs(a["params"][KEY] - 0.05) < 1e-9
    assert a["label"] == "사용자 설정"


def test_state_persists_keyed(rt):
    rt.set_strategy(custom={KEY: 0.05}, uid=5)
    disk = json.loads((runtime._STATE).read_text(encoding="utf-8"))
    assert "5" in disk
    assert "_default" in disk


def test_legacy_flat_state_migrates_to_default(tmp_path, monkeypatch):
    """구버전 flat {name,params,since} 파일은 로드 시 _default 로 이관된다(params 그대로 사용)."""
    f = tmp_path / "strategy_state.json"
    flat = {"name": "balanced", "params": {KEY: 0.33}, "since": "2026-01-01T00:00:00"}
    f.write_text(json.dumps(flat), encoding="utf-8")
    monkeypatch.setattr(runtime, "_STATE", f)
    monkeypatch.setattr(runtime, "_states", {"_default": {
        "name": "default", "params": dict(config.STRATEGY_DEFAULTS)}}, raising=False)
    runtime._load()
    assert abs(runtime.get(KEY, uid=None) - 0.33) < 1e-9


def test_profile_overrides_only_apply_to_that_uid(rt, tmp_path, monkeypatch):
    """프로필 오버라이드는 해당 uid 의 get() 에만 최상위로 적용되고 uid=None 엔 미적용."""
    from infra import profile_overrides as po
    monkeypatch.setattr(po, "_PROFILES_DIR", tmp_path / "profiles")
    po.set_overrides(3, {KEY: 0.07})
    assert abs(rt.get(KEY, uid=3) - 0.07) < 1e-9
    assert rt.get(KEY, uid=None) == config.STRATEGY_DEFAULTS[KEY]
    assert rt.get(KEY, uid=4) == config.STRATEGY_DEFAULTS[KEY]
