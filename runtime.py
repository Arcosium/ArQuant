"""
Arquant v1.0 — runtime strategy state.
A "strategy" = the set of tunable trading parameters (sizing, TP/SL, risk gates, news threshold...).
The active preset is chosen in the dashboard '전략' tab; this module persists the choice and
serves live overrides so the engine picks them up without a restart.

  runtime.get("PER_ORDER_BUDGET_RATIO")   # override-or-config-default
  runtime.active()                         # {name, label, params, since}
  runtime.set_strategy("aggressive")       # apply a preset, persist, append to history
  runtime.set_strategy("custom", custom={...})  # ad-hoc params
  runtime.list_presets() / runtime.history()
"""
import json, time, re
from datetime import datetime
from pathlib import Path
import config

_DIR = Path(__file__).parent / "data"
_DIR.mkdir(exist_ok=True)
_STATE = _DIR / "strategy_state.json"
_HIST = _DIR / "strategy_history.json"
_USER_PRESETS = _DIR / "user_presets.json"  # 사장 지시 2026-05-14: 사용자 정의 프리셋 영구 저장
_OPS_FLAG = _DIR / "ops_feedback.json"      # 사장 피드백 2026-05-18: 운용지원실장 피드백 on/off 토글

_state = {"name": config.DEFAULT_STRATEGY, "params": dict(config.STRATEGY_PRESETS[config.DEFAULT_STRATEGY]),
          "since": datetime.now().isoformat()}

# 사장 피드백 2026-05-18: 프로필 한정 오버라이드 레이어 (최상위 우선순위).
# credentials.set_active() 가 활성 계정의 비관리자 오버라이드를 여기로 올리고,
# 계정 전환·ADMIN 로그인 시 {} 로 교체한다 → 전역 strategy 선택(아래 _state)을
# 건드리지 않으면서 활성 프로필 한정 튜닝만 얹는다. 단일 활성 계정 불변식 덕에 격리 성립.
_profile_overrides: dict = {}


def set_profile_overrides(d: dict | None):
    """활성 프로필의 튜닝 오버라이드 교체 (None/{} = 레이어 해제). 재시작 불필요 —
    get() 이 즉시 최상위로 참조한다. infra.profile_overrides.activate() 가 호출자."""
    global _profile_overrides
    _profile_overrides = dict(d or {})


def profile_overrides() -> dict:
    return dict(_profile_overrides)


# ── 운용지원실장 피드백 on/off — 프로필(계정)별 (사장 지시 2026-05-20) ───────────
# 계정마다 독립 토글. 기본 ON(기존 동작 보존). 끄면 그 프로필이 활성일 때 워커를
# 띄우지 않는다(재시작 불필요). 저장 형식:
#   {"_default": {enabled,since,by}, "<uid>": {enabled,since,by}, ...}
# 구버전 flat 포맷({"enabled":...})은 로드 시 _default 로 자동 이관(하위호환).
_ops_feedback: dict = {"_default": {"enabled": True, "since": datetime.now().isoformat(), "by": "default"}}


def _ops_key(uid=None) -> str:
    return str(int(uid)) if uid is not None else "_default"


def _load_ops_feedback():
    global _ops_feedback
    try:
        if _OPS_FLAG.exists():
            d = json.loads(_OPS_FLAG.read_text(encoding="utf-8"))
            if isinstance(d, dict) and "enabled" in d:
                # 구버전 flat 포맷 → _default 로 이관
                _ops_feedback = {"_default": {"enabled": bool(d["enabled"]),
                                              "since": d.get("since", datetime.now().isoformat()),
                                              "by": d.get("by", "load")}}
            elif isinstance(d, dict):
                _ops_feedback = {k: v for k, v in d.items() if isinstance(v, dict) and "enabled" in v}
                _ops_feedback.setdefault("_default", {"enabled": True,
                                                      "since": datetime.now().isoformat(), "by": "default"})
    except Exception:
        pass


def ops_feedback_enabled(uid=None) -> bool:
    """프로필(uid)별 토글. 해당 프로필 설정 없으면 _default(기본 ON)로 폴백."""
    st = _ops_feedback.get(_ops_key(uid)) or _ops_feedback.get("_default") or {}
    return bool(st.get("enabled", True))


def ops_feedback_state(uid=None) -> dict:
    key = _ops_key(uid)
    st = _ops_feedback.get(key) or _ops_feedback.get("_default") or {"enabled": True}
    return {**dict(st), "uid": (None if key == "_default" else int(key))}


def set_ops_feedback(enabled: bool, uid=None, by: str = "dashboard") -> dict:
    """운용지원실장 피드백 토글 — 프로필(uid)별. uid=None 이면 기본값. 재시작 불필요."""
    global _ops_feedback
    key = _ops_key(uid)
    _ops_feedback[key] = {"enabled": bool(enabled),
                          "since": datetime.now().isoformat(), "by": by}
    try:
        _OPS_FLAG.write_text(json.dumps(_ops_feedback, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    except Exception:
        pass
    _append_history({"ops_feedback": bool(enabled),
                     "uid": (None if key == "_default" else int(key)), "by": f"{by}:ops_feedback"})
    return ops_feedback_state(uid)


def _persist():
    try:
        _STATE.write_text(json.dumps(_state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _append_history(entry: dict):
    try:
        h = []
        if _HIST.exists():
            try: h = json.loads(_HIST.read_text(encoding="utf-8"))
            except Exception: h = []
        if not isinstance(h, list): h = []
        h.append({"ts": datetime.now().isoformat(), **entry})
        _HIST.write_text(json.dumps(h[-200:], ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _load():
    global _state
    try:
        if _STATE.exists():
            d = json.loads(_STATE.read_text(encoding="utf-8"))
            if isinstance(d, dict) and d.get("params"):
                _state = {"name": d.get("name", "custom"), "params": dict(d["params"]),
                          "since": d.get("since", datetime.now().isoformat())}
    except Exception:
        pass


_load()
_load_ops_feedback()
if not _HIST.exists():
    _append_history({"name": _state["name"], "params": _state["params"], "by": "init"})


def get(key, default=None):
    """Live tunable value. 우선순위 (사장 피드백 2026-05-18):
      1) 활성 프로필 오버라이드 (비관리자 운용지원실장 튜닝 — 프로필 한정)
      2) 전역 전략 프리셋/커스텀 (대시보드 '전략' 탭 — ADMIN/공통)
      3) config 모듈 상수 → 호출자 default
    """
    if key in _profile_overrides:
        return _profile_overrides[key]
    p = _state.get("params") or {}
    if key in p:
        return p[key]
    return getattr(config, key, default)


def _load_user_presets() -> dict:
    """Load {name: {label, params}} from data/user_presets.json. Returns {} if missing."""
    if not _USER_PRESETS.exists():
        return {}
    try:
        d = json.loads(_USER_PRESETS.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_user_presets(presets: dict):
    try:
        _USER_PRESETS.write_text(json.dumps(presets, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _preset_label(name):
    pr = config.STRATEGY_PRESETS.get(name)
    if pr: return pr.get("label", name)
    up = _load_user_presets().get(name)
    if up: return up.get("label", name)
    return "사용자 지정" if name == "custom" else name


def active() -> dict:
    keys = config.STRATEGY_TUNABLE_KEYS
    return {"name": _state["name"], "label": _preset_label(_state["name"]),
            "since": _state.get("since"), "params": {k: get(k) for k in keys}}


def list_presets() -> list:
    """Built-in presets first, then user-saved presets (사장 지시 2026-05-14).
    Each row carries `source` ∈ {'builtin','user'} so the UI can show a delete button for user ones."""
    keys = config.STRATEGY_TUNABLE_KEYS
    out = []
    for name, pr in config.STRATEGY_PRESETS.items():
        out.append({"name": name, "label": pr.get("label", name), "source": "builtin",
                    "params": {k: pr.get(k, getattr(config, k, None)) for k in keys},
                    "active": name == _state["name"]})
    for name, pr in _load_user_presets().items():
        # user names cannot collide with builtins — skip if they somehow do (builtin wins)
        if name in config.STRATEGY_PRESETS:
            continue
        params = pr.get("params") or {}
        out.append({"name": name, "label": pr.get("label", name), "source": "user",
                    "params": {k: params.get(k, getattr(config, k, None)) for k in keys},
                    "active": name == _state["name"]})
    return out


_NAME_RE = re.compile(r"^[A-Za-z0-9_\-가-힣 ]{1,40}$")


def save_user_preset(name: str, label: str, params: dict, by: str = "dashboard") -> dict:
    """Persist a user-defined preset. Rejects names that would shadow built-ins or contain weird chars.
    Returns {ok, message, presets}. (사장 지시 2026-05-14)"""
    name = (name or "").strip()
    label = (label or name or "").strip()
    if not name or not _NAME_RE.match(name):
        return {"ok": False, "message": f"이름 형식 오류: 한글/영문/숫자/하이픈 1~40자 이내 ({name!r})"}
    if name in config.STRATEGY_PRESETS:
        return {"ok": False, "message": f"빌트인 프리셋 이름은 사용 불가: {name}"}
    if name == "custom":
        return {"ok": False, "message": "'custom'은 예약된 이름입니다"}
    # filter to known keys only
    keys = set(config.STRATEGY_TUNABLE_KEYS)
    clean_params = {k: v for k, v in (params or {}).items() if k in keys}
    presets = _load_user_presets()
    presets[name] = {"label": label or name, "params": clean_params,
                     "created_at": datetime.now().isoformat(),
                     "updated_at": datetime.now().isoformat(),
                     "created_by": by}
    _save_user_presets(presets)
    _append_history({"name": name, "label": label, "params": clean_params, "by": f"{by}:save_preset"})
    return {"ok": True, "message": f"사용자 프리셋 '{name}' 저장 완료", "presets": list_presets()}


def delete_user_preset(name: str) -> dict:
    """Delete a user preset. Built-in presets cannot be deleted. (사장 지시 2026-05-14)"""
    name = (name or "").strip()
    if name in config.STRATEGY_PRESETS:
        return {"ok": False, "message": f"빌트인 프리셋은 삭제할 수 없습니다: {name}"}
    presets = _load_user_presets()
    if name not in presets:
        return {"ok": False, "message": f"존재하지 않는 사용자 프리셋: {name}"}
    removed = presets.pop(name)
    _save_user_presets(presets)
    _append_history({"name": name, "by": "dashboard:delete_preset", "removed": removed.get("label", name)})
    # if the deleted preset was active, fall back to balanced
    if _state.get("name") == name:
        set_strategy(config.DEFAULT_STRATEGY, by="auto_fallback_after_delete")
    return {"ok": True, "message": f"사용자 프리셋 '{name}' 삭제됨", "presets": list_presets()}


def history() -> list:
    try:
        if _HIST.exists():
            h = json.loads(_HIST.read_text(encoding="utf-8"))
            return h if isinstance(h, list) else []
    except Exception:
        pass
    return []


def set_strategy(name: str, custom: dict = None, by: str = "user") -> dict:
    """Activate a preset by name OR apply custom params. (사장 지시 2026-05-14: 사용자 프리셋도 인식)
    Resolution order: builtin → user → 'custom' (use supplied params).
    """
    global _state
    keys = set(config.STRATEGY_TUNABLE_KEYS)
    if name in config.STRATEGY_PRESETS:
        params = {k: v for k, v in config.STRATEGY_PRESETS[name].items() if k in keys}
        _state = {"name": name, "params": params, "since": datetime.now().isoformat()}
    elif name in _load_user_presets():
        up = _load_user_presets()[name].get("params") or {}
        _state = {"name": name, "params": {k: up.get(k, getattr(config, k, None)) for k in keys},
                  "since": datetime.now().isoformat()}
    else:
        # 'custom' or unknown → use supplied params (filtered to known keys), defaulting from balanced
        base = dict(config.STRATEGY_PRESETS[config.DEFAULT_STRATEGY])
        if custom:
            for k, v in custom.items():
                if k in keys:
                    base[k] = v
        _state = {"name": "custom", "params": {k: base.get(k) for k in config.STRATEGY_TUNABLE_KEYS},
                  "since": datetime.now().isoformat()}
    _persist()
    _append_history({"name": _state["name"], "label": _preset_label(_state["name"]),
                     "params": _state["params"], "by": by})
    return active()
