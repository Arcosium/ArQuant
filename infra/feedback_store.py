"""유저 피드백/버그 제보 저장소 (사장 지시 2026-05-24).

흐름: ① 유저가 프로필 관리 창에서 피드백 제출 → ② ADMIN(사장)이 ADMIN 탭에서 전체 피드백 확인
→ ③ 사장이 답글 작성 → ④ 유저가 자기 피드백에서 답글 확인.

전역 단일 파일(data/feedback.json)에 모든 유저의 항목을 보관한다 — ADMIN 은 전체를, 일반 유저는
자기 uid 항목만 조회한다. profile_overrides 와 동일한 lock+json 패턴.
"""
import json
import secrets
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

KST = timezone(timedelta(hours=9))
_PATH = Path(__file__).resolve().parent.parent / "data" / "feedback.json"
_LOCK = threading.Lock()
_CAP = 2000
_VALID_TYPES = {"bug", "feature", "etc"}


def _now() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


def _read() -> List[Dict[str, Any]]:
    try:
        if _PATH.exists():
            d = json.loads(_PATH.read_text(encoding="utf-8"))
            if isinstance(d, list):
                return d
    except Exception:
        pass
    return []


def _write(items: List[Dict[str, Any]]) -> None:
    _PATH.parent.mkdir(exist_ok=True)
    _PATH.write_text(json.dumps(items[-_CAP:], ensure_ascii=False, indent=2), encoding="utf-8")


def submit(uid: int, username: str, ftype: str, title: str, body: str) -> Dict[str, Any]:
    """유저 피드백 1건 저장. 빈 제목·본문이면 ValueError."""
    ftype = ftype if ftype in _VALID_TYPES else "etc"
    title = (title or "").strip()[:200]
    body = (body or "").strip()[:5000]
    if not title and not body:
        raise ValueError("내용이 비어 있습니다.")
    entry = {
        "id": f"fb_{int(time.time() * 1000)}_{secrets.token_hex(3)}",
        "uid": int(uid), "username": (username or "").strip(),
        "type": ftype, "title": title, "body": body,
        "ts": _now(), "status": "open",
        "reply": "", "reply_ts": "", "reply_seen": False,
    }
    with _LOCK:
        items = _read()
        items.append(entry)
        _write(items)
    return entry


def list_for_user(uid: int) -> List[Dict[str, Any]]:
    """해당 유저가 올린 피드백 (최신순)."""
    mine = [e for e in _read() if e.get("uid") == int(uid)]
    return sorted(mine, key=lambda e: e.get("ts", ""), reverse=True)


def list_all() -> List[Dict[str, Any]]:
    """전체 피드백 (ADMIN 전용, 최신순)."""
    return sorted(_read(), key=lambda e: e.get("ts", ""), reverse=True)


def reply(feedback_id: str, text: str) -> Optional[Dict[str, Any]]:
    """ADMIN 답글 작성. 항목을 찾으면 갱신된 entry, 없으면 None."""
    text = (text or "").strip()[:5000]
    with _LOCK:
        items = _read()
        target = None
        for e in items:
            if e.get("id") == feedback_id:
                e["reply"] = text
                e["reply_ts"] = _now()
                e["status"] = "answered" if text else "open"
                e["reply_seen"] = False  # 새 답글 → 유저 미확인 상태로
                target = e
                break
        if target:
            _write(items)
    return target


def mark_replies_seen(uid: int) -> None:
    """유저가 피드백 화면을 열면 자기 답글들을 '확인함'으로 표시(배지 클리어)."""
    with _LOCK:
        items = _read()
        changed = False
        for e in items:
            if e.get("uid") == int(uid) and e.get("reply") and not e.get("reply_seen"):
                e["reply_seen"] = True
                changed = True
        if changed:
            _write(items)


def count_open() -> int:
    """미답변 피드백 수 (ADMIN 배지용)."""
    return sum(1 for e in _read() if e.get("status") == "open")


def count_unseen_replies(uid: int) -> int:
    """해당 유저가 아직 확인 안 한 답글 수 (유저 배지용)."""
    return sum(1 for e in _read()
               if e.get("uid") == int(uid) and e.get("reply") and not e.get("reply_seen"))
