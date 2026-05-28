"""투자 thesis 영속 저장소 — per-uid data/<uid>/position_thesis.json.

매수 시점에 펀드기획팀장이 작성한 진입 사유·목표가·손절가·계획 보유기간을 보관해,
사후관리실장이 매도 판단 직전 상기한다(우선순위 3, 사장 확정 2026-05-28).

저장 포맷: {code: {entry_ts, entry_price, target_price, stop_price,
                    planned_hold_hours, entry_reason, source_agent}}.

동시성: 같은 uid 의 thesis 는 한 swarm 태스크에서만 갱신되므로 in-process 락은 불필요.
파일 쓰기는 임시파일 → rename 의 atomic write 로 부분 기록을 막는다.
"""
from __future__ import annotations
import json
import logging
import os
from typing import Dict, Optional, Any

from infra import user_paths

logger = logging.getLogger("THESIS")

# in-memory 캐시: {uid: {code: thesis}}. 디스크 읽기 비용 절감.
_CACHE: Dict[int, Dict[str, Dict[str, Any]]] = {}


def _reset_cache_for_tests() -> None:
    _CACHE.clear()


def _load(uid: int) -> Dict[str, Dict[str, Any]]:
    if uid in _CACHE:
        return _CACHE[uid]
    p = user_paths.position_thesis_path(uid)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except Exception as e:
            logger.warning(f"[thesis] uid={uid} 파일 로드 실패({e}) — 빈 dict 로 시작")
            data = {}
    else:
        data = {}
    _CACHE[uid] = data
    return data


def _save(uid: int) -> None:
    data = _CACHE.get(uid) or {}
    p = user_paths.position_thesis_path(uid)
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except Exception as e:
        logger.warning(f"[thesis] uid={uid} 저장 실패: {e}")


def _norm_code(code: Any) -> str:
    return str(code).strip()


def record(uid: int, code: Any, thesis: Dict[str, Any]) -> None:
    """매수 체결 직후 펀드기획팀장이 작성한 thesis 를 기록(같은 종목 재매수 시 덮어씀)."""
    data = _load(int(uid))
    data[_norm_code(code)] = dict(thesis)
    _save(int(uid))


def get(uid: int, code: Any) -> Optional[Dict[str, Any]]:
    return _load(int(uid)).get(_norm_code(code))


def get_all(uid: int) -> Dict[str, Dict[str, Any]]:
    return dict(_load(int(uid)))


def remove(uid: int, code: Any) -> None:
    """전량 매도 체결 시 호출. 없어도 에러 X(멱등)."""
    data = _load(int(uid))
    if _norm_code(code) in data:
        del data[_norm_code(code)]
        _save(int(uid))


def sync_with_holdings(uid: int, current_codes) -> list:
    """현재 보유 종목과 동기화 — thesis 가 있는데 더 이상 보유 안 하면 제거.
    여러 매도가 한 사이클에 겹쳐도 idempotent. 반환: 제거된 코드 목록."""
    data = _load(int(uid))
    current = {_norm_code(c) for c in (current_codes or [])}
    removed = [c for c in list(data.keys()) if c not in current]
    if removed:
        for c in removed:
            del data[c]
        _save(int(uid))
    return removed
