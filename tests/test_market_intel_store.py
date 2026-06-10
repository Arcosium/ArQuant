"""프로세스 전역 인텔리전스 스토어 — hour_key 매칭 + 대기/타임아웃."""
import asyncio
from infra.market_intel import MarketIntelligenceStore, get_intel_store

def test_peek_hit_same_hour_key():
    s = MarketIntelligenceStore()
    asyncio.run(s.publish("macro_research", "2026-06-08 10", "R", None, uid=1, now=1000.0))
    assert s.peek("macro_research", "2026-06-08 10", None) == "R"

def test_peek_miss_different_hour_key():
    s = MarketIntelligenceStore()
    asyncio.run(s.publish("macro_research", "2026-06-08 10", "R", None, uid=1, now=1000.0))
    assert s.peek("macro_research", "2026-06-08 11", None) is None

def test_peek_miss_when_empty():
    s = MarketIntelligenceStore()
    assert s.peek("news_report", "2026-06-08 10", None) is None

def test_fingerprint_mismatch_returns_none():
    s = MarketIntelligenceStore()
    asyncio.run(s.publish("news_report", "2026-06-08 10", "R", "fpA", uid=1, now=1000.0))
    assert s.peek("news_report", "2026-06-08 10", "fpB") is None
    assert s.peek("news_report", "2026-06-08 10", "fpA") == "R"
    assert s.peek("news_report", "2026-06-08 10", None) == "R"

def test_wait_for_receives_late_publish():
    async def scenario():
        s = MarketIntelligenceStore()
        async def producer():
            await asyncio.sleep(0.01)
            await s.publish("macro_report", "2026-06-08 10", "MR", None, uid=1, now=1000.0)
        async def consumer():
            return await s.wait_for("macro_report", "2026-06-08 10", None, timeout=1.0)
        res, _ = await asyncio.gather(consumer(), producer())
        return res
    assert asyncio.run(scenario()) == "MR"

def test_wait_for_times_out_to_none():
    async def scenario():
        s = MarketIntelligenceStore()
        return await s.wait_for("macro_report", "2026-06-08 10", None, timeout=0.05)
    assert asyncio.run(scenario()) is None

def test_get_intel_store_is_singleton():
    assert get_intel_store() is get_intel_store()
