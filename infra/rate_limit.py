"""인-프로세스 슬라이딩 윈도우 레이트리미터.

단일 프로세스 uvicorn 기준(현재 배포). 멀티워커면 워커별로 독립 — Phase 2에서
필요 시 공유 저장소로 교체. 외부 의존성 없음."""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional


class SlidingWindowLimiter:
    def __init__(self, max_hits: int, window_sec: float):
        self.max = int(max_hits)
        self.win = float(window_sec)
        self._buckets: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str) -> Optional[float]:
        """1회 시도 기록. 허용이면 None, 한도 초과면 재시도까지 남은 초(>0)."""
        now = time.time()
        with self._lock:
            dq = self._buckets[key]
            cutoff = now - self.win
            # 만료 타임스탬프는 다음 hit() 때 지연 제거(의도된 설계 — 유휴 키 TTL 스윕 불필요).
            while dq and dq[0] <= cutoff:
                dq.popleft()
            if len(dq) >= self.max:
                retry = (self.win - (now - dq[0])) if dq else self.win
                return max(0.001, retry)
            dq.append(now)
            return None

    def reset(self, key: str) -> None:
        with self._lock:
            self._buckets.pop(key, None)
