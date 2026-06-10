"""3개 시장-전역 분석 호출이 _shared_or_compute 를 통과하는지(kind 인자 검증)."""
import asyncio
import main_swarm

def test_wraps_route_through_shared(monkeypatch):
    seen = []
    async def fake_shared(self, kind, fp, compute):
        seen.append(kind)
        return await compute()
    monkeypatch.setattr(main_swarm.ArquantOrchestrator, "_shared_or_compute", fake_shared)
    o = main_swarm.ArquantOrchestrator.__new__(main_swarm.ArquantOrchestrator)
    async def _r(v): return v
    async def drive():
        await o._shared_or_compute("news_report", None, lambda: _r("NEWS"))
        await o._shared_or_compute("macro_research", None, lambda: _r("RES"))
        await o._shared_or_compute("macro_report", None, lambda: _r("MACRO"))
    asyncio.run(drive())
    assert seen == ["news_report", "macro_research", "macro_report"]
