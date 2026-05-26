"""런타임 전략 상태 프로필(uid)별 격리 — Task 10 (멀티테넌트 리팩터 마무리).

이전에는 runtime._state 가 프로세스 전역 단일 dict 라, 한 계정이 전략/파라미터를 바꾸면
모든 계정에 적용됐다(멀티테넌트 격리 깨짐). 이제 _notif_settings/_cost_mode 와 동일한
{"_default":..., "<uid>":...} 키드 패턴으로 uid 별로 분리한다.

  - set_strategy(name, uid=...) 는 그 uid 의 상태만 바꾼다.
  - get(KEY, uid=...) 는 그 uid 의 params(+프로필 오버라이드)를 본다.
  - uid=None 은 _default 상태이며 다른 uid 변경에 영향받지 않는다(프로필 오버라이드도 미적용).
"""
import json

import pytest

import config
import runtime


# config 빌트인 프리셋 중 PER_ORDER_BUDGET_RATIO 가 서로 다른 두 프리셋을 고른다.
def _two_distinct_presets():
    key = "PER_ORDER_BUDGET_RATIO"
    seen = {}
    for name, pr in config.STRATEGY_PRESETS.items():
        if key in pr:
            seen[name] = pr[key]
    # 값이 다른 두 프리셋 쌍을 찾는다
    names = list(seen)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            if seen[names[i]] != seen[names[j]]:
                return names[i], names[j], key
    pytest.skip("PER_ORDER_BUDGET_RATIO 가 서로 다른 빌트인 프리셋 2개가 없음")


@pytest.fixture
def rt(tmp_path, monkeypatch):
    """전략 상태 파일·인메모리 맵 + 프로필 오버라이드 디렉터리를 임시로 격리."""
    monkeypatch.setattr(runtime, "_STATE", tmp_path / "strategy_state.json")
    monkeypatch.setattr(runtime, "_HIST", tmp_path / "strategy_history.json")
    default = {"name": config.DEFAULT_STRATEGY,
               "params": dict(config.STRATEGY_PRESETS[config.DEFAULT_STRATEGY])}
    monkeypatch.setattr(runtime, "_states", {"_default": default}, raising=False)
    # 실제 data/profiles/<uid>/overrides.json 이 get(uid=...) 로 새지 않도록 격리
    from infra import profile_overrides as po
    monkeypatch.setattr(po, "_PROFILES_DIR", tmp_path / "profiles")
    return runtime


def test_set_strategy_per_uid_isolated(rt):
    a, b, key = _two_distinct_presets()
    rt.set_strategy(a, uid=1)
    rt.set_strategy(b, uid=2)

    va = rt.get(key, uid=1)
    vb = rt.get(key, uid=2)
    assert va != vb
    assert va == config.STRATEGY_PRESETS[a][key]
    assert vb == config.STRATEGY_PRESETS[b][key]


def test_uid_none_unaffected_by_per_uid_changes(rt):
    a, b, key = _two_distinct_presets()
    default_val = rt.get(key, uid=None)  # _default 상태 = DEFAULT_STRATEGY
    rt.set_strategy(a, uid=1)
    rt.set_strategy(b, uid=2)
    # 두 uid 가 바뀌어도 _default(uid=None) 는 그대로
    assert rt.get(key, uid=None) == default_val
    assert default_val == config.STRATEGY_PRESETS[config.DEFAULT_STRATEGY][key]


def test_active_per_uid(rt):
    a, b, _ = _two_distinct_presets()
    rt.set_strategy(a, uid=1)
    rt.set_strategy(b, uid=2)
    assert rt.active(uid=1)["name"] == a
    assert rt.active(uid=2)["name"] == b
    assert rt.active(uid=None)["name"] == config.DEFAULT_STRATEGY


def test_list_presets_active_flag_per_uid(rt):
    a, b, _ = _two_distinct_presets()
    rt.set_strategy(a, uid=1)
    rt.set_strategy(b, uid=2)
    actives_1 = [p["name"] for p in rt.list_presets(uid=1) if p["active"]]
    actives_2 = [p["name"] for p in rt.list_presets(uid=2) if p["active"]]
    assert actives_1 == [a]
    assert actives_2 == [b]


def test_state_persists_keyed(rt):
    a, _, _ = _two_distinct_presets()
    rt.set_strategy(a, uid=5)
    disk = json.loads((runtime._STATE).read_text(encoding="utf-8"))
    assert "5" in disk
    assert disk["5"]["name"] == a
    # _default 도 함께 직렬화됨
    assert "_default" in disk


def test_legacy_flat_state_migrates_to_default(tmp_path, monkeypatch):
    """구버전 flat {name,params,since} 파일은 로드 시 _default 로 이관된다."""
    f = tmp_path / "strategy_state.json"
    a, b, key = _two_distinct_presets()
    flat = {"name": a, "params": dict(config.STRATEGY_PRESETS[a]),
            "since": "2026-01-01T00:00:00"}
    f.write_text(json.dumps(flat), encoding="utf-8")
    monkeypatch.setattr(runtime, "_STATE", f)
    monkeypatch.setattr(runtime, "_states", {"_default": {
        "name": config.DEFAULT_STRATEGY,
        "params": dict(config.STRATEGY_PRESETS[config.DEFAULT_STRATEGY])}}, raising=False)
    runtime._load()
    # flat 파일이 _default 로 들어갔는지
    assert runtime.get(key, uid=None) == config.STRATEGY_PRESETS[a][key]


def test_profile_overrides_only_apply_to_that_uid(rt, tmp_path, monkeypatch):
    """프로필 오버라이드는 해당 uid 의 get() 에만 최상위로 적용되고 uid=None 엔 미적용."""
    from infra import profile_overrides as po
    key = "PER_ORDER_BUDGET_RATIO"
    # 프로필 디렉터리를 임시로 격리
    monkeypatch.setattr(po, "_PROFILES_DIR", tmp_path / "profiles")
    po.set_overrides(3, {key: 0.07})
    assert abs(rt.get(key, uid=3) - 0.07) < 1e-9
    # uid=None 은 오버라이드 미적용 → config/default 프리셋 값
    assert rt.get(key, uid=None) == config.STRATEGY_PRESETS[config.DEFAULT_STRATEGY][key]
    # 다른 uid(4) 도 영향 없음
    assert rt.get(key, uid=4) == config.STRATEGY_PRESETS[config.DEFAULT_STRATEGY][key]
