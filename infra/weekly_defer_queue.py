"""주간(토요일) 검증 대기 큐 (per-uid) — 사장 지시 2026-06-09 #7.

운용지원실장이 평일(cycle) 사이클에서 제안한 'weekly-tier' 파라미터(점수엔진 가중치·사이징 모델·
유니버스·슬리브 구조값 등 — 모델/구조 변경)는 즉시 적용하지 않고 여기에 적재한다. 토요일 주간
리뷰가 백테스트+실데이터로 재평가해 적용 여부를 결정한다(weekly 트리거는 전부 apply).

policy_approval_inbox 와 별개: 그쪽은 정책 플래그(자산군 정책)로 *사장 승인* 필요. 이쪽은 ops
자율 영역이되 *백테스트 검증 타이밍*만 토요일로 미루는 것 — 사장 승인 없이 토요일 워커가 처리.
저장: data/profiles/<uid>/weekly_deferred.json (제안 리스트, 같은 키는 최신으로 덮어씀).
"""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger("WEEKLY_DEFER_QUEUE")
KST = timezone(timedelta(hours=9))
_PROFILES_DIR = Path(__file__).parent.parent / "data" / "profiles"
_FILENAME = "weekly_deferred.json"


def _now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


def _path(uid: int) -> Path:
    d = _PROFILES_DIR / str(int(uid))
    d.mkdir(parents=True, exist_ok=True)
    return d / _FILENAME


def _load(uid: int) -> List[Dict[str, Any]]:
    p = _path(uid)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning("weekly_defer_queue 로드 실패(uid=%s): %s", uid, e)
        return []


def _save(uid: int, items: List[Dict[str, Any]]) -> None:
    p = _path(uid)
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
    except Exception as e:
        logger.warning("weekly_defer_queue 저장 실패(uid=%s): %s", uid, e)


def enqueue(uid: int, key: str, value: Any, rationale: str = "") -> None:
    """weekly-tier 제안 1건을 적재(같은 key 는 최신 제안으로 덮어씀)."""
    items = [it for it in _load(uid) if it.get("key") != key]
    items.append({"key": key, "value": value, "rationale": rationale, "ts": _now_kst()})
    _save(uid, items)


def list_pending(uid: int) -> List[Dict[str, Any]]:
    """대기 중인 weekly-tier 제안 목록."""
    return _load(uid)


def clear(uid: int) -> None:
    """토요일 워커가 처리 완료 후 비운다(멱등)."""
    _save(uid, [])
