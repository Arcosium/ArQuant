"""비관리자: 뉴스 크롤/분석 활동 메시지 미노출. ADMIN: 노출."""
import asyncio
import main_swarm

def _orch(is_admin):
    o = main_swarm.ArquantOrchestrator.__new__(main_swarm.ArquantOrchestrator)
    o.is_admin = is_admin
    o._emitted = []
    async def _emit(msg): o._emitted.append(msg)
    o._emit = _emit
    return o

def test_admin_emits_news_activity():
    o = _orch(True)
    asyncio.run(o._emit_news_activity({"type": "news", "count": 3}))
    assert o._emitted == [{"type": "news", "count": 3}]

def test_non_admin_suppresses_news_activity():
    o = _orch(False)
    asyncio.run(o._emit_news_activity({"type": "news", "count": 3}))
    assert o._emitted == []
