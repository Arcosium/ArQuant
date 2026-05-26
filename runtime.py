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

# ── 전략 상태 — 프로필(계정)별 (Task 10: 멀티테넌트 마무리) ──────────────────────
# "전략" = 튜닝 트레이딩 파라미터 묶음(sizing·TP/SL·리스크게이트·뉴스임계...).
# 이전엔 _state 가 프로세스 전역 단일 dict 라 한 계정의 전략/파라미터 변경이 모든
# 계정에 적용됐다(멀티테넌트 격리 깨짐). 이제 _notif_settings/_cost_mode 와 동일한
# {"_default":..., "<uid>":...} 키드 패턴으로 uid 별로 분리한다.
# 저장 형식: {"_default": {name,params,since}, "<uid>": {name,params,since}}.
# 구버전 flat 포맷({name,params,since})은 로드 시 _default 로 자동 이관(하위호환).
def _default_state() -> dict:
    return {"name": config.DEFAULT_STRATEGY,
            "params": dict(config.STRATEGY_PRESETS[config.DEFAULT_STRATEGY]),
            "since": datetime.now().isoformat()}


_states: dict = {"_default": _default_state()}


def _strat_key(uid=None) -> str:
    return str(int(uid)) if uid is not None else "_default"


def _get_state(uid=None) -> dict:
    """그 uid 의 전략 상태. 미설정 시 _default 로 폴백(없으면 새 기본 생성)."""
    return _states.get(_strat_key(uid)) or _states.get("_default") or _default_state()


# 사장 피드백 2026-05-18: 프로필 한정 오버라이드 레이어 (최상위 우선순위).
# Task 10: 전역 인메모리 레이어를 폐지하고 get(key, uid) 가 디스크
# (infra.profile_overrides.load(uid) = data/profiles/<uid>/overrides.json)를 직접 참조한다.
# uid=None 이면 프로필 오버라이드 미적용(시스템 기본).


def set_profile_overrides(d: dict | None):
    """(LEGACY no-op) 과거 전역 오버라이드 레이어 교체용. Task 10 에서 프로필 오버라이드는
    get(key, uid) 가 디스크에서 uid 별로 직접 읽으므로 전역 레이어는 폐지됐다.
    하위호환을 위한 thin shim — 아무 동작도 하지 않는다."""
    return None


def profile_overrides() -> dict:
    """(LEGACY) 전역 오버라이드 레이어는 폐지됨 — 항상 빈 dict."""
    return {}


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


# ── API 비용 표시 모드 — 프로필(계정)별 (사장 지시 2026-05-21) ───────────────────
# 우상단 API 비용 표시를 시간(/h)·일(/d)·월(/m)·총누적(total) 중 하나로 프로필별 선택.
# 저장 형식: {"_default": {"mode": "h"}, "<uid>": {"mode": ...}}  — ops_feedback 와 동일 패턴.
_COST_MODE_FILE = _DIR / "api_cost_mode.json"
_COST_MODES = ("h", "d", "m", "total")
_cost_mode: dict = {"_default": {"mode": "h"}}


def _cost_key(uid=None) -> str:
    return str(int(uid)) if uid is not None else "_default"


def _load_cost_mode():
    global _cost_mode
    try:
        if _COST_MODE_FILE.exists():
            d = json.loads(_COST_MODE_FILE.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                _cost_mode = {k: v for k, v in d.items()
                              if isinstance(v, dict) and v.get("mode") in _COST_MODES}
                _cost_mode.setdefault("_default", {"mode": "h"})
    except Exception:
        pass


def cost_display_mode(uid=None) -> str:
    """프로필(uid)별 비용 표시 모드. 미설정 시 _default(기본 'h')."""
    st = _cost_mode.get(_cost_key(uid)) or _cost_mode.get("_default") or {}
    mode = st.get("mode", "h")
    return mode if mode in _COST_MODES else "h"


def set_cost_display_mode(mode: str, uid=None) -> str:
    """비용 표시 모드 변경 — 프로필(uid)별. uid=None 이면 기본값. 재시작 불필요."""
    global _cost_mode
    if mode not in _COST_MODES:
        raise ValueError(f"잘못된 비용 표시 모드: {mode!r} (가능: {_COST_MODES})")
    _cost_mode[_cost_key(uid)] = {"mode": mode, "since": datetime.now().isoformat()}
    try:
        _COST_MODE_FILE.write_text(json.dumps(_cost_mode, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    except Exception:
        pass
    return mode


# ── 모바일 알림 설정 — 프로필(계정)별 (사장 지시 2026-05-21) ──────────────────────
# 모바일 앱(네이티브 WsManager)이 띄울 4종 푸시 알림을 프로필별로 on/off:
#   order_submitted(체결 신청) · trade(체결 완료) · cycle(사이클 완료) · market_close(장 마감)
# 저장 형식: {"_default": {order_submitted,trade,cycle,market_close}, "<uid>": {...}} (전부 기본 ON).
# 웹 대시보드 표시는 이 설정과 무관(항상 전체) — 이 토글은 '모바일 푸시'만 게이트한다.
_NOTIF_FILE = _DIR / "notif_settings.json"
_NOTIF_KEYS = ("order_submitted", "trade", "cycle", "market_close")
_NOTIF_DEFAULT = {k: True for k in _NOTIF_KEYS}
_notif_settings: dict = {"_default": dict(_NOTIF_DEFAULT)}


def _notif_key(uid=None) -> str:
    return str(int(uid)) if uid is not None else "_default"


def _clean_notif(d: dict) -> dict:
    return {k: bool((d or {}).get(k, True)) for k in _NOTIF_KEYS}


def _load_notif_settings():
    global _notif_settings
    try:
        if _NOTIF_FILE.exists():
            d = json.loads(_NOTIF_FILE.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                _notif_settings = {k: _clean_notif(v) for k, v in d.items() if isinstance(v, dict)}
                _notif_settings.setdefault("_default", dict(_NOTIF_DEFAULT))
    except Exception:
        pass


def notif_settings(uid=None) -> dict:
    """프로필(uid)별 모바일 알림 설정. 미설정 시 _default(전부 ON)로 폴백."""
    st = _notif_settings.get(_notif_key(uid)) or _notif_settings.get("_default") or _NOTIF_DEFAULT
    return _clean_notif(st)


def set_notif_settings(settings: dict, uid=None) -> dict:
    """모바일 알림 설정 변경 — 프로필(uid)별. 부분 갱신 허용. 재시작 불필요."""
    global _notif_settings
    cur = notif_settings(uid)
    cur.update({k: bool(v) for k, v in (settings or {}).items() if k in _NOTIF_KEYS})
    _notif_settings[_notif_key(uid)] = cur
    try:
        _NOTIF_FILE.write_text(json.dumps(_notif_settings, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return cur


def _persist():
    try:
        _STATE.write_text(json.dumps(_states, ensure_ascii=False, indent=2), encoding="utf-8")
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


def _normalize_state(d: dict) -> dict:
    return {"name": d.get("name", "custom"), "params": dict(d.get("params") or {}),
            "since": d.get("since", datetime.now().isoformat())}


def _load():
    global _states
    try:
        if _STATE.exists():
            d = json.loads(_STATE.read_text(encoding="utf-8"))
            if not isinstance(d, dict):
                return
            if d.get("params"):
                # 구버전 flat 포맷({name,params,since}) → _default 로 이관(하위호환)
                _states = {"_default": _normalize_state(d)}
            else:
                # 키드 포맷({"_default":..., "<uid>":...})
                loaded = {k: _normalize_state(v) for k, v in d.items()
                          if isinstance(v, dict) and v.get("params")}
                if loaded:
                    _states = loaded
                _states.setdefault("_default", _default_state())
    except Exception:
        pass


_load()
_load_ops_feedback()
_load_cost_mode()
_load_notif_settings()
if not _HIST.exists():
    _dflt = _get_state(None)
    _append_history({"name": _dflt["name"], "params": _dflt["params"], "by": "init"})


def get(key, default=None, uid=None):
    """Live tunable value — 프로필(uid)별. 우선순위 (Task 10):
      1) 이 uid 의 프로필 오버라이드 (비관리자 운용지원실장 튜닝 — 디스크에서 직접 로드,
         data/profiles/<uid>/overrides.json). uid=None 이면 미적용.
      2) 이 uid 의 전략 프리셋/커스텀 params (대시보드 '전략' 탭). uid=None → _default.
      3) config 모듈 상수 → 호출자 default
    """
    if uid is not None:
        try:
            from infra import profile_overrides as _po
            ov = _po.load(uid)
            if key in ov:
                return ov[key]
        except Exception:
            pass
    p = _get_state(uid).get("params") or {}
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


def active(uid=None) -> dict:
    """그 uid 의 활성 전략(이름·라벨·since + 효과적 params). uid=None → _default."""
    st = _get_state(uid)
    keys = config.STRATEGY_TUNABLE_KEYS
    return {"name": st["name"], "label": _preset_label(st["name"]),
            "since": st.get("since"), "params": {k: get(k, uid=uid) for k in keys}}


def list_presets(uid=None) -> list:
    """Built-in presets first, then user-saved presets (사장 지시 2026-05-14).
    Each row carries `source` ∈ {'builtin','user'} so the UI can show a delete button for user ones.
    `active` 플래그는 그 uid 의 활성 전략 기준(uid=None → _default)."""
    cur_name = _get_state(uid)["name"]
    keys = config.STRATEGY_TUNABLE_KEYS
    out = []
    for name, pr in config.STRATEGY_PRESETS.items():
        out.append({"name": name, "label": pr.get("label", name), "source": "builtin",
                    "params": {k: pr.get(k, getattr(config, k, None)) for k in keys},
                    "active": name == cur_name})
    for name, pr in _load_user_presets().items():
        # user names cannot collide with builtins — skip if they somehow do (builtin wins)
        if name in config.STRATEGY_PRESETS:
            continue
        params = pr.get("params") or {}
        out.append({"name": name, "label": pr.get("label", name), "source": "user",
                    "params": {k: params.get(k, getattr(config, k, None)) for k in keys},
                    "active": name == cur_name})
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
    # 사용자 프리셋은 전역 공유 정의다. 삭제 시, 이 프리셋을 활성으로 쓰던 모든 프로필을
    # DEFAULT_STRATEGY 로 폴백시킨다(전역 단일 _state 시절엔 한 개였으나 이제 uid 별).
    for k, st in list(_states.items()):
        if isinstance(st, dict) and st.get("name") == name:
            _uid = None if k == "_default" else int(k)
            set_strategy(config.DEFAULT_STRATEGY, by="auto_fallback_after_delete", uid=_uid)
    return {"ok": True, "message": f"사용자 프리셋 '{name}' 삭제됨", "presets": list_presets()}


def history() -> list:
    try:
        if _HIST.exists():
            h = json.loads(_HIST.read_text(encoding="utf-8"))
            return h if isinstance(h, list) else []
    except Exception:
        pass
    return []


def set_strategy(name: str, custom: dict = None, by: str = "user", uid=None) -> dict:
    """Activate a preset by name OR apply custom params — 프로필(uid)별. uid=None → _default.
    (사장 지시 2026-05-14: 사용자 프리셋도 인식)
    Resolution order: builtin → user → 'custom' (use supplied params).
    """
    keys = set(config.STRATEGY_TUNABLE_KEYS)
    if name in config.STRATEGY_PRESETS:
        params = {k: v for k, v in config.STRATEGY_PRESETS[name].items() if k in keys}
        new_state = {"name": name, "params": params, "since": datetime.now().isoformat()}
    elif name in _load_user_presets():
        up = _load_user_presets()[name].get("params") or {}
        new_state = {"name": name, "params": {k: up.get(k, getattr(config, k, None)) for k in keys},
                     "since": datetime.now().isoformat()}
    else:
        # 'custom' or unknown → use supplied params (filtered to known keys), defaulting from balanced
        base = dict(config.STRATEGY_PRESETS[config.DEFAULT_STRATEGY])
        if custom:
            for k, v in custom.items():
                if k in keys:
                    base[k] = v
        new_state = {"name": "custom", "params": {k: base.get(k) for k in config.STRATEGY_TUNABLE_KEYS},
                     "since": datetime.now().isoformat()}
    _states[_strat_key(uid)] = new_state
    _persist()
    _append_history({"name": new_state["name"], "label": _preset_label(new_state["name"]),
                     "params": new_state["params"], "by": by,
                     "uid": (None if uid is None else int(uid))})
    return active(uid)
