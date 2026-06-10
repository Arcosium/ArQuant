"""자산슬리브 보유기간 self-thesis 저장소 — per-uid·per-sleeve.

채권·원자재 등 슬리브 매니저가 ETF 매수 시 기록한 진입가·계획 보유기간·진입사유를 보관해,
매도 판단 직전 강력 권고로 상기한다. 주식 position_thesis 와 분리.
(bond_thesis 를 sleeve_key 인자로 일반화 — 사장 지시 2026-06-09. sleeve_key='bond' 경로는
bond_thesis.json 으로 기존 라이브 파일과 동일 → 데이터 무손실.)

저장 포맷: data/<uid>/<sleeve_key>_thesis.json =
  {code: {entry_ts, entry_price, planned_hold_hours, entry_reason, source_agent}}.

동시성: 같은 uid 의 thesis 는 한 swarm 태스크에서만 갱신되므로 in-process 락 불필요.
파일 쓰기는 임시파일 → rename 의 atomic write 로 부분 기록을 막는다.
"""
from __future__ import annotations
import json
import logging
import os
from typing import Dict, Optional, Any, Tuple

from infra import user_paths

logger = logging.getLogger("SLEEVE_THESIS")

# in-memory 캐시: {(uid, sleeve_key): {code: thesis}}. 디스크 읽기 비용 절감.
_CACHE: Dict[Tuple[int, str], Dict[str, Dict[str, Any]]] = {}


def _reset_cache_for_tests() -> None:
    _CACHE.clear()


def _key(uid: int, sleeve_key: str) -> Tuple[int, str]:
    return (int(uid), str(sleeve_key))


def _load(uid: int, sleeve_key: str) -> Dict[str, Dict[str, Any]]:
    k = _key(uid, sleeve_key)
    if k in _CACHE:
        return _CACHE[k]
    p = user_paths.sleeve_thesis_path(int(uid), str(sleeve_key))
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except Exception as e:
            logger.warning(f"[sleeve_thesis] uid={uid} key={sleeve_key} 로드 실패({e}) — 빈 dict 로 시작")
            data = {}
    else:
        data = {}
    _CACHE[k] = data
    return data


def _save(uid: int, sleeve_key: str) -> None:
    data = _CACHE.get(_key(uid, sleeve_key)) or {}
    p = user_paths.sleeve_thesis_path(int(uid), str(sleeve_key))
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except Exception as e:
        logger.warning(f"[sleeve_thesis] uid={uid} key={sleeve_key} 저장 실패: {e}")


def _norm_code(code: Any) -> str:
    return str(code).strip()


def record(uid: int, sleeve_key: str, code: Any, thesis: Dict[str, Any]) -> None:
    """매수 체결 직후 슬리브 매니저가 작성한 thesis 를 기록(같은 종목 재매수 시 덮어씀)."""
    data = _load(uid, sleeve_key)
    data[_norm_code(code)] = dict(thesis)
    _save(uid, sleeve_key)


def get(uid: int, sleeve_key: str, code: Any) -> Optional[Dict[str, Any]]:
    return _load(uid, sleeve_key).get(_norm_code(code))


def get_all(uid: int, sleeve_key: str) -> Dict[str, Dict[str, Any]]:
    return dict(_load(uid, sleeve_key))


def remove(uid: int, sleeve_key: str, code: Any) -> None:
    """전량 매도 체결 시 호출. 없어도 에러 X(멱등)."""
    data = _load(uid, sleeve_key)
    if _norm_code(code) in data:
        del data[_norm_code(code)]
        _save(uid, sleeve_key)


def sync_with_holdings(uid: int, sleeve_key: str, current_codes) -> list:
    """현재 보유 슬리브와 동기화 — thesis 가 있는데 더 이상 보유 안 하면 제거.
    여러 매도가 한 사이클에 겹쳐도 idempotent. 반환: 제거된 코드 목록."""
    data = _load(uid, sleeve_key)
    current = {_norm_code(c) for c in (current_codes or [])}
    removed = [c for c in list(data.keys()) if c not in current]
    if removed:
        for c in removed:
            del data[c]
        _save(uid, sleeve_key)
    return removed
