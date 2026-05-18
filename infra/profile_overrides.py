"""
ArQuant — 프로필 한정 런타임 오버라이드 + 비관리자 제안 이력 (사장 피드백 2026-05-18)

배경:
  ArQuant 는 단일 프로세스·단일 소스다. ops_support_worker 가 .py 소스를 직접 고치고
  서버를 재시작하면 그 변경은 **모든 유저**에게 적용된다. 그래서:
    • ADMIN 계정(hh09080 / .env 시드)  → 기존대로 소스 수정+재시작 = 전체 반영
    • 비관리자 계정                     → 소스·서버 불가침. 대신 '튜닝 파라미터'만
                                          이 모듈을 통해 **해당 프로필 전용**으로 저장

격리가 성립하는 이유:
  credentials.set_active() 가 '활성 계정은 언제나 단 하나'를 강제한다. 따라서 활성
  프로필의 오버라이드를 runtime 의 최상위 레이어로 올렸다가 계정 전환 시 교체하면,
  전역 runtime 상태가 곧 '현재 활성 프로필의 상태'가 된다. 계정별 코드 로딩이 필요 없다.

보안 경계:
  허용 키 = config.STRATEGY_TUNABLE_KEYS (UI '전략' 탭이 쓰는 그 화이트리스트).
  KIS 자격증명/계좌/소스 식별자는 구조적으로 이 목록에 없으므로 비관리자가
  param_overrides 로 자격증명을 밀어넣는 것은 불가능하다.

데이터 위치 (둘 다 .gitignore 대상 — data/ 하위):
  data/profiles/<uid>/overrides.json     {KEY: value, ...}  (튜닝 파라미터)
  data/profiles/<uid>/ops_history.json   [{...}]            (운용지원실장 제안 이력)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("PROFILE_OV")
KST = timezone(timedelta(hours=9))

_DATA_DIR = Path(__file__).parent.parent / "data"
_PROFILES_DIR = _DATA_DIR / "profiles"
_PROPOSAL_CAP = 500  # 프로필별 보관할 제안 이력 최대 건수


def _now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


def _profile_dir(uid: int) -> Path:
    d = _PROFILES_DIR / str(int(uid))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _overrides_path(uid: int) -> Path:
    return _profile_dir(uid) / "overrides.json"


def _history_path(uid: int) -> Path:
    return _profile_dir(uid) / "ops_history.json"


# ─── 화이트리스트 & 타입 강제 ──────────────────────────────────────────────────
def whitelist() -> set:
    """허용 가능한 오버라이드 키. config 의 큐레이션된 튜닝 키 목록을 그대로 재사용."""
    try:
        import config
        return set(config.STRATEGY_TUNABLE_KEYS)
    except Exception as e:
        logger.warning("whitelist 로드 실패: %s — 빈 집합(전부 거부)", e)
        return set()


def _coerce(key: str, value: Any) -> Any:
    """STRATEGY_KEY_META 기준으로 타입을 강제하고 min/max 로 클램프.
    LLM 이 문자열·범위 밖 값을 줘도 시스템이 깨지지 않도록 방어."""
    try:
        import config
        meta = (config.STRATEGY_KEY_META or {}).get(key, {})
    except Exception:
        meta = {}
    t = meta.get("type")
    try:
        if t == "bool":
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on", "y", "t")
            return bool(value)
        if t == "int":
            v = int(float(value))
        elif t in ("pct_ratio", "pct_raw", "multiplier"):
            v = float(value)
        else:
            # 알 수 없는 타입 — 숫자면 float, 아니면 원본
            try:
                v = float(value)
            except (TypeError, ValueError):
                return value
        lo, hi = meta.get("min"), meta.get("max")
        # pct_ratio 는 '저장값=비율(0.10=10%)'이지만 LLM/사람은 % 숫자(10)를 준다.
        # 모호성 해소: v>1 이면 퍼센트로 보고 /100, v<=1 이면 이미 비율로 간주.
        # min/max(meta)는 UI 단위(예: 5~50)이므로 비율로 환산해 클램프.
        if t == "pct_ratio":
            if v > 1.0:
                v = v / 100.0
            if lo is not None:
                v = max(v, float(lo) / 100.0)
            if hi is not None:
                v = min(v, float(hi) / 100.0)
        else:
            if lo is not None:
                v = max(v, float(lo))
            if hi is not None:
                v = min(v, float(hi))
        return int(v) if t == "int" else v
    except (TypeError, ValueError) as e:
        logger.warning("_coerce 실패 key=%s value=%r: %s — 무시", key, value, e)
        return None


# ─── 오버라이드 저장/로드 ──────────────────────────────────────────────────────
def load(uid: Optional[int]) -> Dict[str, Any]:
    if uid is None:
        return {}
    p = _overrides_path(uid)
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            return {}
        wl = whitelist()
        # 디스크에 화이트리스트 밖 키가 있어도 (구버전·수기수정) 적용 단계에서 제외
        return {k: v for k, v in d.items() if k in wl}
    except Exception as e:
        logger.warning("overrides 로드 실패(uid=%s): %s", uid, e)
        return {}


def set_overrides(uid: int, params: Dict[str, Any]) -> Dict[str, Any]:
    """params 중 화이트리스트 키만 타입강제·클램프 후 **기존값에 병합**하여 저장.
    반환: 실제로 반영된 {key: coerced_value}. (잘못된 키/값은 조용히 탈락)"""
    wl = whitelist()
    cur = load(uid)
    accepted: Dict[str, Any] = {}
    for k, raw in (params or {}).items():
        if k not in wl:
            continue
        cv = _coerce(k, raw)
        if cv is None:
            continue
        cur[k] = cv
        accepted[k] = cv
    if accepted:
        try:
            _overrides_path(uid).write_text(
                json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("overrides 저장 실패(uid=%s): %s", uid, e)
    return accepted


def clear_overrides(uid: int) -> None:
    try:
        p = _overrides_path(uid)
        if p.exists():
            p.unlink()
    except Exception:
        pass


# ─── 활성 프로필 → runtime 레이어 ──────────────────────────────────────────────
def activate(uid: Optional[int], is_admin: bool = False) -> Dict[str, Any]:
    """credentials.set_active() 에서 호출. 활성 계정의 프로필 오버라이드를 runtime
    최상위 레이어로 올린다. ADMIN 이거나 오버라이드가 없으면 레이어를 비워서
    직전 비관리자 프로필의 튜닝이 다음 세션으로 새지 않게 한다.

    반환: 실제 적용된 오버라이드 dict (로깅/응답용)."""
    ov = {} if (is_admin or uid is None) else load(uid)
    try:
        import runtime
        runtime.set_profile_overrides(ov)
    except Exception as e:
        logger.warning("runtime.set_profile_overrides 실패(uid=%s): %s", uid, e)
    if ov:
        logger.info("프로필 오버라이드 활성화 uid=%s keys=%s", uid, list(ov.keys()))
    return ov


# ─── 비관리자 제안 이력 (프로필 전용) ──────────────────────────────────────────
def record_proposal(uid: int, meta: Dict[str, Any]) -> None:
    """비관리자 ops_support 워커가 제안한 내용을 프로필 전용 이력에 누적.
    소스는 건드리지 않았으므로 'applied' 가 아니라 'proposed'/'overrides_applied' 로 기록."""
    p = _history_path(uid)
    try:
        h = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        if not isinstance(h, list):
            h = []
    except Exception:
        h = []
    h.append({"ts": _now_kst(), **meta})
    if len(h) > _PROPOSAL_CAP:
        h = h[-_PROPOSAL_CAP:]
    try:
        p.write_text(json.dumps(h, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("record_proposal 저장 실패(uid=%s): %s", uid, e)


def load_proposals(uid: Optional[int]) -> List[Dict[str, Any]]:
    if uid is None:
        return []
    p = _history_path(uid)
    if not p.exists():
        return []
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def proposal_summary_text(uid: Optional[int], limit: int = 20) -> str:
    """비관리자 계정의 '@운용지원실장 이력 보여줘' 응답용 사람-친화 텍스트."""
    h = load_proposals(uid)[-limit:]
    if not h:
        return ("📋 (비관리자 프로필) 운용지원실장 제안 이력이 아직 없습니다.\n"
                "ℹ️ 이 계정은 공유 소스 코드를 변경할 수 없습니다. 운용지원실장에게 내린 "
                "튜닝 지시는 이 프로필 전용 파라미터로만 반영됩니다 "
                "(전략·예산·익절/손절 등). 소스 구조 변경은 ADMIN(hh09080) 전용입니다.")
    lines = [f"📋 (비관리자 프로필) 운용지원실장 제안·프로필 반영 이력 — 최근 {len(h)}건:"]
    for i, r in enumerate(h, 1):
        ts = r.get("ts", "?")
        disp = r.get("role_display") or "운용지원실장"
        lines.append(f"\n━━━ [{i}] {ts} ({disp}) ━━━")
        lines.append(f"요약: {r.get('summary', '(요약 없음)')}")
        rat = (r.get("rationale") or "").strip()
        if rat:
            lines.append(f"근거: {rat[:300]}{'...' if len(rat) > 300 else ''}")
        ova = r.get("overrides_applied") or {}
        if ova:
            lines.append(f"✅ 이 프로필에 반영된 파라미터 ({len(ova)}건):")
            for k, v in ova.items():
                lines.append(f"  • {k} = {v}")
        prop = r.get("proposed_source_changes") or []
        if prop:
            lines.append(f"📝 소스 변경 제안(미적용 — ADMIN만 반영 가능) {len(prop)}건:")
            for s in prop[:5]:
                lines.append(f"  • {s}")
    lines.append("\n(소스 .py 변경·서버 재시작은 ADMIN 계정에서만 전체 반영됩니다.)")
    return "\n".join(lines)
