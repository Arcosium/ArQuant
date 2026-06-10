"""프로세스 전역 시장 인텔리전스 공유 스토어 (사장 지시 2026-06-08).

ADMIN(hh09080) 오케스트레이터가 매 :00 사이클에서 매크로·뉴스 분석을 산출해 publish 하고,
비관리자 오케스트레이터는 같은 시각(hour_key)의 결과를 wait_for 로 받아 LLM 중복 호출을 피한다.
단일 프로세스 + asyncio 협조형이라 락 없이 Condition 으로 대기-알림한다.
"""
from __future__ import annotations
from typing import Any, Dict, Optional

INTEL_KINDS = ("news_report", "macro_research", "macro_report")  # _shared_or_compute 가 쓰는 실제 kind


class MarketIntelligenceStore:
    def __init__(self) -> None:
        self._d: Dict[str, Dict[str, Any]] = {}   # kind -> {hour_key, result, fingerprint, produced_at, uid}
        self._cond = None                          # asyncio.Condition (러닝 루프 내 lazy 생성)

    def _ensure_cond(self):
        if self._cond is None:
            import asyncio
            self._cond = asyncio.Condition()
        return self._cond

    def peek(self, kind: str, hour_key: str, fingerprint: Optional[str]):
        e = self._d.get(kind)
        if not e or e["hour_key"] != hour_key:
            return None                            # 미게시 또는 직전 시각(stale)
        if fingerprint is not None and e["fingerprint"] != fingerprint:
            return None
        return e["result"]

    async def publish(self, kind: str, hour_key: str, result: Any,
                      fingerprint: Optional[str], *, uid: Optional[int], now: float) -> None:
        cond = self._ensure_cond()
        async with cond:
            self._d[kind] = {"hour_key": hour_key, "result": result,
                             "fingerprint": fingerprint, "produced_at": now, "uid": uid}
            cond.notify_all()

    async def wait_for(self, kind: str, hour_key: str, fingerprint: Optional[str], *, timeout: float):
        hit = self.peek(kind, hour_key, fingerprint)
        if hit is not None:
            return hit
        import asyncio
        cond = self._ensure_cond()
        try:
            async with cond:
                await asyncio.wait_for(
                    cond.wait_for(lambda: self.peek(kind, hour_key, fingerprint) is not None),
                    timeout=timeout)
        except asyncio.TimeoutError:
            return None
        return self.peek(kind, hour_key, fingerprint)


_store: Optional[MarketIntelligenceStore] = None


def get_intel_store() -> MarketIntelligenceStore:
    global _store
    if _store is None:
        _store = MarketIntelligenceStore()
    return _store
