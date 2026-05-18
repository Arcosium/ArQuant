"""ArQuant — 경량 구조화 메트릭 (사장 피드백 2026-05-18: 관측성).

`claude_response.json` 한 파일에 모든 게 섞여 있어 "사이클이 평소보다 느린가?
주문 성공률이 떨어졌나? LLM 비용이 튀었나?" 를 답하기 어려웠다. 이 모듈은
수치 메트릭만 `data/metrics.jsonl` 에 한 줄=한 이벤트로 영속한다
(외부 의존 없음 · grep/jq 로 바로 분석 가능 · 절대 예외를 던지지 않음).

    from infra import metrics
    with metrics.timer("analysis_cycle", session="KR"):
        ...                       # 블록 소요시간(ms) 자동 기록
    metrics.incr("orders_executed", market="KR")
    metrics.gauge("llm_cost_usd", 0.0123, scope="cycle")
    metrics.snapshot()            # 대시보드용 최근 집계
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Deque, Dict

logger = logging.getLogger("METRICS")
KST = timezone(timedelta(hours=9))

_PATH = Path(__file__).resolve().parent.parent / "data" / "metrics.jsonl"
_RING_MAX = 5000
_lock = threading.RLock()
_ring: Deque[dict] = deque(maxlen=_RING_MAX)  # 빠른 스냅샷용 인메모리 최근 이벤트


def _emit(kind: str, name: str, value, tags: Dict) -> None:
    rec = {"ts": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
           "kind": kind, "name": name, "value": value, "tags": tags or {}}
    try:
        with _lock:
            _ring.append(rec)
            _PATH.parent.mkdir(parents=True, exist_ok=True)
            with _PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except (OSError, TypeError) as e:
        logger.warning(f"metrics emit 실패(무시): {e}")


def incr(name: str, by: float = 1.0, **tags) -> None:
    """카운터 — 주문 성공/실패, 사이클 수 등."""
    _emit("count", name, by, tags)


def gauge(name: str, value: float, **tags) -> None:
    """게이지 — equity, LLM 비용, 예수금 등 순간값."""
    _emit("gauge", name, value, tags)


def timing(name: str, ms: float, **tags) -> None:
    """소요시간(ms) 직접 기록."""
    _emit("timing", name, round(ms, 1), tags)


@contextmanager
def timer(name: str, **tags):
    """with 블록 소요시간을 timing 메트릭으로 자동 기록.
    블록이 예외로 끝나도 시간은 기록하고 예외는 그대로 전파한다."""
    t0 = time.perf_counter()
    ok = True
    try:
        yield
    except BaseException:
        ok = False
        raise
    finally:
        timing(name, (time.perf_counter() - t0) * 1000.0, ok=ok, **tags)


def snapshot(window_sec: int = 24 * 3600) -> dict:
    """인메모리 ring 기반 최근 집계 — 대시보드 표시용. 예외를 던지지 않는다."""
    try:
        cutoff = time.time() - window_sec
        out: Dict[str, dict] = {}
        with _lock:
            items = list(_ring)
        for r in items:
            try:
                ts = datetime.strptime(r["ts"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
            except (ValueError, KeyError):
                continue
            if ts.timestamp() < cutoff:
                continue
            slot = out.setdefault(r["name"], {"kind": r["kind"], "n": 0,
                                              "sum": 0.0, "last": None})
            slot["n"] += 1
            v = r.get("value")
            if isinstance(v, (int, float)):
                slot["sum"] += v
                slot["last"] = v
        for s in out.values():
            s["avg"] = (s["sum"] / s["n"]) if s["n"] else 0.0
        return out
    except Exception as e:
        logger.warning(f"metrics snapshot 실패: {e}")
        return {}
