"""
ArQuant — Coresight 투자 로직 신호 → 사장님 지시 Surfacing 큐 (Implementation.md §3.3)

설계 원칙 (TASK A §3.3):
  - ADMIN(hh09080) 전용. is_admin(uid)==False 이면 전 기능 비활성(deny-by-default).
  - 감지: CORESIGHT_PATH/*.json 에서 투자 로직 신호 스키마를 보수적 규칙으로 탐지.
    모호하면 fail-closed (무시). LLM 호출 없이 키워드/스키마 필드 기반 규칙만 사용.
  - 큐: data/profiles/<uid>/coresight_pending.json 영속 저장 (재시작 후도 유지).
  - NOTHING auto-executes. 명시적 admin approve 후에만 standing_directives 에 추가.
  - 멱등성: 처리한 신호 id 를 data/profiles/<uid>/coresight_seen.json 에 기록.

공개 API:
  scan_and_enqueue(uid) -> int          — 신규 신호 탐지·큐 적재. admin uid 필수.
  list_pending(uid) -> list[dict]       — 미처리 Coresight 제안 목록 (최신순).
  approve(uid, item_id) -> bool         — 명시 승인 → standing_directive 에 추가.
  reject(uid, item_id) -> bool          — 거부 → 큐에서 제거(영구).
"""
from __future__ import annotations

import json
import logging
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("CORESIGHT_INBOX")
KST = timezone(timedelta(hours=9))

_DATA_DIR = Path(__file__).parent.parent / "data"
_PROFILES_DIR = _DATA_DIR / "profiles"
_PENDING_FILENAME = "coresight_pending.json"
_SEEN_FILENAME = "coresight_seen.json"
_MAX_PENDING = 100  # 계정당 최대 대기 항목


def _now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


def _profile_dir(uid: int) -> Path:
    d = _PROFILES_DIR / str(int(uid))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _pending_path(uid: int) -> Path:
    return _profile_dir(uid) / _PENDING_FILENAME


def _seen_path(uid: int) -> Path:
    return _profile_dir(uid) / _SEEN_FILENAME


# ── Admin 권한 확인 ─────────────────────────────────────────────────────────────

def _require_admin(uid: int) -> bool:
    """uid 가 ADMIN 인지 확인. 아니면 False 반환 (deny-by-default, fail-soft)."""
    try:
        from infra.auth_store import is_admin
        return bool(is_admin(uid))
    except Exception as e:
        logger.debug("coresight_inbox: admin 확인 실패(uid=%s) — 거부: %s", uid, e)
        return False


# ── 영속 I/O ────────────────────────────────────────────────────────────────────

def _load_json_list(path: Path) -> List[Any]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning("coresight_inbox: 파일 로드 실패(%s): %s", path, e)
        return []


def _save_json_list(path: Path, data: List[Any]) -> None:
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("coresight_inbox: 파일 저장 실패(%s): %s", path, e)


def _load_pending(uid: int) -> List[Dict[str, Any]]:
    return _load_json_list(_pending_path(uid))


def _save_pending(uid: int, items: List[Dict[str, Any]]) -> None:
    _save_json_list(_pending_path(uid), items)


def _load_seen(uid: int) -> List[str]:
    return _load_json_list(_seen_path(uid))


def _save_seen(uid: int, seen_ids: List[str]) -> None:
    _save_json_list(_seen_path(uid), seen_ids)


def _mark_seen(uid: int, signal_id: str) -> None:
    seen = _load_seen(uid)
    if signal_id not in seen:
        seen.append(signal_id)
        _save_seen(uid, seen)


def _is_seen(uid: int, signal_id: str) -> bool:
    return signal_id in _load_seen(uid)


# ── 신호 ID 생성 ────────────────────────────────────────────────────────────────

def _signal_id(filepath: str, content: str) -> str:
    """파일 경로 + 내용 해시로 결정론적 신호 ID (멱등 체크용)."""
    raw = f"{filepath}|{content[:500]}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ── 투자 로직 신호 탐지 (보수적 규칙, fail-closed) ─────────────────────────────
# Implementation.md §3.3: 분류는 보수적 규칙 우선(키워드/스키마 필드),
# 모호하면 fail-closed (불확실 → 무시, 사장님 지시로 올리지 않음).

_SIGNAL_SCHEMA_TYPES = frozenset({"directive", "signal", "investment_directive",
                                   "strategy_signal", "trade_instruction"})
_INSTRUCTION_FIELDS = ("instruction", "directive", "trade_signal", "strategy")

# 투자 로직 관련 키워드 (한국어/영어) — 임계: 최소 1개 필드 키워드 매칭 OR 스키마 type 매칭
_INVESTMENT_KEYWORDS_KO = (
    "매수", "매도", "투자", "포트폴리오", "비중", "자산배분", "전략", "종목", "리밸런싱",
    "수익", "손실", "헤지", "포지션", "섹터", "리스크", "운용"
)
_INVESTMENT_KEYWORDS_EN = (
    "buy", "sell", "invest", "portfolio", "allocation", "strategy", "rebalance",
    "position", "sector", "risk", "hedge", "trade"
)


def _classify_as_investment_signal(data: Any, filepath: str) -> Optional[Dict[str, Any]]:
    """JSON 데이터를 투자 로직 신호로 분류 시도.

    반환: 신호 dict (text, source_file, raw_type) 또는 None (fail-closed).
    모호한 경우 None 반환 — 잘못된 지시 위험 최소화.
    """
    if not isinstance(data, dict):
        return None  # 리스트·기타 형태는 무시 (보수적)

    raw_type = str(data.get("type", "")).lower()
    instruction_text = ""
    for field in _INSTRUCTION_FIELDS:
        v = data.get(field)
        if isinstance(v, str) and v.strip():
            instruction_text = v.strip()
            break

    # 스키마 type 매칭 (명시적 "directive"/"signal" 등)
    type_match = raw_type in _SIGNAL_SCHEMA_TYPES

    # 키워드 매칭 (instruction 텍스트 또는 전체 직렬화)
    search_text = (instruction_text or json.dumps(data, ensure_ascii=False)).lower()
    kw_match = (
        any(kw in search_text for kw in _INVESTMENT_KEYWORDS_KO)
        or any(kw in search_text for kw in _INVESTMENT_KEYWORDS_EN)
    )

    # 결정: type 매칭 AND 텍스트 있음 → 확정. 또는 instruction 텍스트 있고 키워드 있음.
    if type_match and instruction_text:
        pass  # 확정
    elif instruction_text and kw_match:
        pass  # 확정
    else:
        return None  # fail-closed: 모호 → 무시

    # timestamp 추출
    ts_raw = data.get("ts") or data.get("timestamp") or data.get("created_at") or ""

    return {
        "text": instruction_text,
        "source_file": Path(filepath).name,
        "raw_type": raw_type or "unknown",
        "source_ts": str(ts_raw),
    }


# ── 공개 API ─────────────────────────────────────────────────────────────────────

def scan_and_enqueue(uid: int) -> int:
    """CORESIGHT_PATH 를 폴링해 신규 투자 로직 신호를 탐지·큐 적재.

    Admin uid 전용 — 비관리자에선 즉시 0 반환(fail-soft).
    반환: 새로 enqueue 된 항목 수.
    """
    if not _require_admin(uid):
        logger.debug("scan_and_enqueue: 비관리자 uid=%s — no-op", uid)
        return 0

    try:
        from config import CORESIGHT_PATH
        import glob
        import os
    except Exception as e:
        logger.warning("scan_and_enqueue: config 로드 실패 — no-op: %s", e)
        return 0

    json_files = glob.glob(os.path.join(CORESIGHT_PATH, "*.json"))
    pending = _load_pending(uid)
    existing_ids = {item["id"] for item in pending}

    new_count = 0
    for filepath in json_files:
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue

        content_str = json.dumps(data, ensure_ascii=False)
        sig_id = _signal_id(filepath, content_str)

        # 멱등: 이미 seen 또는 pending 에 있으면 스킵
        if _is_seen(uid, sig_id) or sig_id in existing_ids:
            continue

        classified = _classify_as_investment_signal(data, filepath)
        if classified is None:
            # fail-closed: 분류 안 됨 → 무시하되 seen 에도 기록하지 않음
            # (다음 스캔 때 다시 시도 가능 — 규칙 업데이트 대비)
            continue

        item = {
            "id": sig_id,
            "text": classified["text"],
            "source_file": classified["source_file"],
            "raw_type": classified["raw_type"],
            "source_ts": classified["source_ts"],
            "enqueued_at": _now_kst(),
            "status": "pending",          # pending / approved / rejected
            "label": "Coresight 제안(미승인)",
        }
        pending.append(item)
        existing_ids.add(sig_id)
        _mark_seen(uid, sig_id)
        new_count += 1
        logger.info("coresight_inbox: 신규 Coresight 제안 enqueue (uid=%s id=%s file=%s)",
                    uid, sig_id, classified["source_file"])

    if new_count > 0:
        # 최대 항목 수 제한 (오래된 것 제거)
        if len(pending) > _MAX_PENDING:
            pending = pending[-_MAX_PENDING:]
        _save_pending(uid, pending)

    return new_count


def list_pending(uid: int) -> List[Dict[str, Any]]:
    """미처리(pending) Coresight 제안 목록 반환 (최신순).

    Admin uid 전용 — 비관리자에선 [] 반환(fail-soft).
    """
    if not _require_admin(uid):
        return []
    items = _load_pending(uid)
    pending_only = [i for i in items if i.get("status") == "pending"]
    return list(reversed(pending_only))


def approve(uid: int, item_id: str) -> bool:
    """Coresight 제안을 명시 승인 → standing_directive 에 추가.

    Admin uid 전용. item 이 없거나 비관리자면 False 반환(fail-soft).
    자동 체결 금지 — standing_directives.append_directive() 는 프롬프트 주입 경로이며
    파이썬 리스크·guardrail 게이트를 통과하지 않은 매매를 강제하지 않는다.
    """
    if not _require_admin(uid):
        logger.info("approve: 비관리자 uid=%s — 거부", uid)
        return False

    items = _load_pending(uid)
    target = next((i for i in items if i.get("id") == item_id), None)
    if target is None:
        logger.info("approve: item_id=%s not found (uid=%s)", item_id, uid)
        return False
    if target.get("status") != "pending":
        logger.info("approve: item_id=%s already %s (uid=%s)", item_id, target.get("status"), uid)
        return False

    # standing_directive 에 추가 (기존 안전 경로 — 프롬프트 주입만, auto-exec 없음)
    try:
        from infra.standing_directives import append_directive
        directive_text = f"[Coresight 유래] {target['text']}"
        append_directive(uid, directive_text)
    except Exception as e:
        logger.warning("approve: standing_directive 추가 실패(uid=%s id=%s): %s", uid, item_id, e)
        return False

    # 상태 업데이트
    target["status"] = "approved"
    target["label"] = "사장님 지시 (Coresight 유래)"
    target["approved_at"] = _now_kst()
    _save_pending(uid, items)
    logger.info("approve: Coresight 제안 승인 → standing_directive 추가 (uid=%s id=%s)", uid, item_id)
    return True


def reject(uid: int, item_id: str) -> bool:
    """Coresight 제안을 거부 → 큐에서 영구 제거.

    Admin uid 전용. 반환 True=제거됨, False=없거나 권한 없음.
    """
    if not _require_admin(uid):
        logger.info("reject: 비관리자 uid=%s — 거부", uid)
        return False

    items = _load_pending(uid)
    new_items = [i for i in items if i.get("id") != item_id]
    if len(new_items) == len(items):
        return False  # 항목 없음

    _save_pending(uid, new_items)
    logger.info("reject: Coresight 제안 제거 (uid=%s id=%s)", uid, item_id)
    return True
