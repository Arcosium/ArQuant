"""Macro research(deep_research) — 서버제어 브라우저 파이프라인(ArachneControl) 동작.

2026-06-29 사장 지시: Hermes 에이전트(웹툴 서브프로세스) 제거 → Arachne companion
(/research/gather) 수집 + 로컬 LLM 합성으로 대체. 아래는 새 경로 검증.
"""
import pytest

from tools import global_search


@pytest.mark.asyncio
async def test_deep_research_synthesizes_from_gathered_evidence(monkeypatch):
    """gather 근거가 합성 프롬프트에 들어가고 LLM 합성 결과를 반환한다."""
    async def fake_gather(query, timeout_sec):
        return {
            "results": [{"title": "연준", "url": "https://ex/1", "content": "6월 금리 동결"}],
            "extra": [{"title": "참고", "url": "https://ex/2", "snippet": "점도표 상향"}],
        }
    monkeypatch.setattr(global_search, "_gather", fake_gather)

    captured = {}

    async def fake_chat(**kwargs):
        captured["user"] = kwargs["messages"][-1]["content"]
        captured["thinking"] = kwargs.get("thinking")
        return {"choices": [{"message": {"content": "합성된 리서치"}}]}
    monkeypatch.setattr("infra.local_llm_client.chat_completion", fake_chat)

    out = await global_search.deep_research("연준 전망", model="qwen-plain")
    assert out == "합성된 리서치"
    # 수집 근거가 프롬프트에 주입됐는지 + 합성은 reasoning OFF
    assert "6월 금리 동결" in captured["user"]
    assert captured["thinking"] is False


@pytest.mark.asyncio
async def test_deep_research_fail_open_when_no_evidence(monkeypatch):
    """수집(gather) 실패 시 LLM 호출 없이 빈 문자열(fail-open)."""
    async def empty_gather(query, timeout_sec):
        return None
    monkeypatch.setattr(global_search, "_gather", empty_gather)

    called = {"v": False}

    async def fake_chat(**kwargs):
        called["v"] = True
        return {"choices": [{"message": {"content": "x"}}]}
    monkeypatch.setattr("infra.local_llm_client.chat_completion", fake_chat)

    assert await global_search.deep_research("시장") == ""
    assert called["v"] is False
