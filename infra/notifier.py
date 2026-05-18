"""ArQuant — 운영자 실패 알림 (사장 피드백 2026-05-18: 조용한 실패 표면화).

실거래 시스템에서 가장 위험한 것은 *조용히 삼켜진 실패*다 — 주문 실패,
체결 재확인 실패, 잔고/equity 기록 실패가 로그 한 줄로만 남고 아무도
모르면 시스템이 "거래 중"이라 착각한 채 멈춰 있을 수 있다.

이 모듈은 그런 실패를 **운영자에게 보이는 알림**으로 끌어올린다. 설계 원칙:

  1. **절대 예외를 던지지 않는다.** 알림 실패가 트레이딩을 막으면 안 된다
     (호출부는 보통 except 블록 안이다).
  2. **중복 억제.** 사이클이 매번 같은 이유로 실패하면 알림이 폭주해
     무뎌진다. `dedup_key` 동일 알림은 억제 윈도우 동안 1회만 보낸다.
  3. **항상 작동하는 파일 싱크 + 선택적 외부 채널.** 외부 의존 없이도
     `data/alerts.json` 에 영속되고, 환경변수로 웹훅을 켜면 외부로도 간다.

호출 예:
    from infra import notifier
    notifier.alert("CRITICAL", "주문 실행 실패", f"{ticker} {qty}주 — {e}",
                    dedup_key=f"order_fail:{ticker}")
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger("NOTIFY")
KST = timezone(timedelta(hours=9))

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_ALERT_LOG = _DATA_DIR / "alerts.json"
_MAX_ALERTS = 2000                 # soft cap — 가장 오래된 것부터 잘림
_DEFAULT_DEDUP_WINDOW_SEC = 30 * 60  # 동일 dedup_key 억제 윈도우 (기본 30분)

_LEVELS = ("INFO", "WARN", "CRITICAL")
_lock = threading.RLock()
_last_sent: Dict[str, float] = {}   # dedup_key -> 마지막 전송 epoch
_broadcast_cb = None                # 대시보드 실시간 푸시 (main_swarm 가 주입)


def set_broadcast_callback(cb) -> None:
    """main_swarm 가 WebSocket broadcast 함수를 주입해 알림을 UI 로도 흘린다."""
    global _broadcast_cb
    _broadcast_cb = cb


def _now_iso() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


def _append_alert_log(entry: dict) -> None:
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        data = []
        if _ALERT_LOG.exists():
            try:
                data = json.loads(_ALERT_LOG.read_text(encoding="utf-8")) or []
            except (json.JSONDecodeError, OSError):
                data = []
        data.append(entry)
        if len(data) > _MAX_ALERTS:
            data = data[-_MAX_ALERTS:]
        _ALERT_LOG.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        logger.warning(f"alert 로그 기록 실패: {e}")


def _post_webhook(entry: dict) -> None:
    """선택적 외부 채널. 환경변수 ARQUANT_ALERT_WEBHOOK 가 있을 때만.

    ── 사장님 직접 정의 가능 (선택) ──
    기본은 범용 JSON POST 다. 텔레그램/디스코드/슬랙 등 특정 채널 포맷이
    필요하면 이 함수만 그 채널 페이로드에 맞게 바꾸면 된다 (다른 코드 영향 없음).
    인증 토큰은 코드가 아니라 환경변수로 — config.py 의 시크릿 분리 원칙 준수.
    """
    url = os.getenv("ARQUANT_ALERT_WEBHOOK", "").strip()
    if not url:
        return
    try:
        import requests  # 런타임 의존성에 이미 포함
        requests.post(url, json=entry, timeout=4)
    except Exception as e:  # 외부 호출 — 어떤 실패든 트레이딩에 영향 주면 안 됨
        logger.warning(f"alert 웹훅 전송 실패(무시): {e}")


def alert(level: str, title: str, detail: str = "", *,
          dedup_key: Optional[str] = None,
          dedup_window_sec: int = _DEFAULT_DEDUP_WINDOW_SEC) -> bool:
    """운영자 알림 발송. 반환값: 실제 발송했으면 True, 억제됐으면 False.

    절대 예외를 던지지 않는다 (호출부가 except 블록인 경우가 많음).
    """
    try:
        level = (level or "WARN").upper()
        if level not in _LEVELS:
            level = "WARN"
        now = time.time()
        with _lock:
            if dedup_key:
                last = _last_sent.get(dedup_key, 0.0)
                if now - last < dedup_window_sec:
                    return False  # 억제 — 최근에 같은 알림을 보냄
                _last_sent[dedup_key] = now

        entry = {"ts": _now_iso(), "level": level, "title": str(title),
                 "detail": str(detail)[:2000], "dedup_key": dedup_key}

        log_fn = {"INFO": logger.info, "WARN": logger.warning,
                  "CRITICAL": logger.error}.get(level, logger.warning)
        log_fn(f"[ALERT/{level}] {title} — {detail}")

        _append_alert_log(entry)
        _post_webhook(entry)
        if _broadcast_cb is not None:
            try:
                _broadcast_cb({"type": "alert", **entry})
            except Exception:
                pass  # UI 푸시 실패는 무시 — 파일 싱크가 진실의 원천
        return True
    except Exception as e:  # 알림 자체가 트레이딩을 막으면 안 됨
        try:
            logger.error(f"notifier.alert 내부 오류(무시): {e}")
        except Exception:
            pass
        return False


def recent(limit: int = 100, level: Optional[str] = None) -> list:
    """대시보드/디버깅용 — 최근 알림 조회."""
    try:
        if not _ALERT_LOG.exists():
            return []
        data = json.loads(_ALERT_LOG.read_text(encoding="utf-8")) or []
        if level:
            data = [a for a in data if a.get("level") == level.upper()]
        return data[-limit:]
    except (json.JSONDecodeError, OSError):
        return []
