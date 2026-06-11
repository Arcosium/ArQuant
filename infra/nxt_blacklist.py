"""NXT(대체거래소) 거래불가 종목 공유 블랙리스트 (사장 지시 2026-06-11).

KIS가 시간외(NXT) 주문을 "해당 종목정보가 없습니다. NXT 상장종목인지 확인하세요"류로
거부한 종목을 영속 기록해, 이후 모든 계정(uid)이 시간외 세션에서 그 종목의
매수 후보 선정·주문 시도 자체를 건너뛴다. (정규장 KRX 거래는 영향 없음.)

- 저장소: data/nxt_untradable.json — 전역(per-uid 아님). 단일 프로세스 asyncio 라
  in-memory 캐시 + mtime 무효화로 충분하다.
- 153130 매도가 4사이클 연속 같은 사유로 거부된 사례가 계기 (2026-06-10~11).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional
from zoneinfo import ZoneInfo

_KST = ZoneInfo("Asia/Seoul")

logger = logging.getLogger("arquant.nxt_blacklist")

_PATH = Path(__file__).parent.parent / "data" / "nxt_untradable.json"
_DEFAULT_PATH = _PATH

# in-memory 캐시: (mtime, data)
_cache_mtime: Optional[float] = None
_cache_data: Dict[str, dict] = {}

# KIS 거부 메시지에서 'NXT 미상장/종목정보 없음'을 식별하는 시그니처.
_REJECT_SIGNATURES = ("종목정보가 없습니다", "NXT 상장종목", "NXT 미상장")


def _load() -> Dict[str, dict]:
    """파일 → dict (mtime 캐시). 파일 없으면 빈 dict."""
    global _cache_mtime, _cache_data
    # pytest 가 라이브 블랙리스트를 읽으면 테스트가 운영 데이터에 좌우된다(예: 153130 등록
    # 후 finalize 테스트가 가로막힘) — 테스트는 _PATH 를 monkeypatch 한 경우만 실데이터 사용.
    import os as _os
    if _os.environ.get("PYTEST_CURRENT_TEST") and _PATH == _DEFAULT_PATH:
        return {}
    try:
        mtime = _PATH.stat().st_mtime
    except OSError:
        _cache_mtime, _cache_data = None, {}
        return _cache_data
    if _cache_mtime == mtime:
        return _cache_data
    try:
        d = json.loads(_PATH.read_text(encoding="utf-8"))
        _cache_data = d if isinstance(d, dict) else {}
    except Exception as e:
        logger.warning(f"[NXT블랙리스트] 읽기 실패({e}) — 빈 목록으로 동작")
        _cache_data = {}
    _cache_mtime = mtime
    return _cache_data


def _save(data: Dict[str, dict]) -> None:
    global _cache_mtime, _cache_data
    # 운영호스트 pytest 가 라이브 블랙리스트를 오염시키지 않게 (테스트는 _PATH monkeypatch).
    import os as _os
    if _os.environ.get("PYTEST_CURRENT_TEST") and _PATH == _DEFAULT_PATH:
        return
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        _PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        _cache_data = data
        try:
            _cache_mtime = _PATH.stat().st_mtime
        except OSError:
            _cache_mtime = None
    except Exception as e:
        logger.warning(f"[NXT블랙리스트] 저장 실패: {e}")


def looks_nxt_unsupported(result_msg: str) -> bool:
    """KIS 주문 결과 문자열이 'NXT 미상장/종목정보 없음' 거부인지."""
    s = str(result_msg or "")
    return any(sig in s for sig in _REJECT_SIGNATURES)


def is_blocked(ticker: str) -> bool:
    tk = str(ticker or "").strip()
    return bool(tk) and tk in _load()


def record(ticker: str, note: str = "") -> bool:
    """거래불가 종목 기록. 신규 등록이면 True, 이미 있으면 카운트만 올리고 False."""
    tk = str(ticker or "").strip()
    if not tk:
        return False
    data = dict(_load())
    now = datetime.now(_KST).strftime("%Y-%m-%d %H:%M:%S")
    if tk in data:
        ent = dict(data[tk])
        ent["count"] = int(ent.get("count") or 0) + 1
        ent["last_seen"] = now
        if note:
            ent["note"] = str(note)[:200]
        data[tk] = ent
        _save(data)
        return False
    data[tk] = {"first_seen": now, "last_seen": now, "count": 1, "note": str(note)[:200]}
    _save(data)
    logger.info(f"[NXT블랙리스트] {tk} 등록 — 이후 전 계정 시간외(NXT) 매수/주문 시도 제외")
    return True


def all_blocked() -> Dict[str, dict]:
    return dict(_load())
