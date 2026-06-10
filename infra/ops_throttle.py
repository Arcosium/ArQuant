"""운용지원실장 시간당 쓰로틀 — per-uid 마커로 사이클 spawn 빈도를 제어.

기존엔 매 사이클 fire-and-forget spawn(낭비·churn + 크래시 무한반복)이었다. throttle_sec(기본 1시간)
이상 경과했을 때만 spawn 해 '시간당 적극 튜닝' 케이던스로 전환한다(사장 지시 2026-06-05)."""
from __future__ import annotations
from pathlib import Path
from typing import Optional


def ops_due(last_ts: Optional[float], now: float, throttle_sec: float) -> bool:
    """직전 ops 실행 epoch(last_ts)로부터 throttle_sec 이상 지났으면 True. 미실행(None/0)도 True."""
    if not last_ts:
        return True
    return (float(now) - float(last_ts)) >= float(throttle_sec)


def read_last_run(marker: Path) -> float:
    try:
        return float(Path(marker).read_text().strip())
    except Exception:
        return 0.0


def write_last_run(marker: Path, now: float) -> None:
    try:
        p = Path(marker)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(float(now)))
    except Exception:
        pass
