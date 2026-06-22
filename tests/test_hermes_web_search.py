import pytest

from tools import global_search


@pytest.mark.asyncio
async def test_deep_research_uses_deepseek_with_web_tools_only(monkeypatch):
    captured = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return "검색 결과".encode(), b""

    async def fake_exec(*argv, **kwargs):
        captured["argv"] = list(argv)
        captured["env"] = kwargs["env"]
        return FakeProcess()

    monkeypatch.setattr(global_search.asyncio, "create_subprocess_exec", fake_exec)
    result = await global_search.deep_research(
        "오늘 시장", model="deepseek-v4-pro", api_key="DS-USER-KEY")

    argv = captured["argv"]
    assert result == "검색 결과"
    assert argv[argv.index("--provider") + 1] == "deepseek"
    assert argv[argv.index("--model") + 1] == "deepseek-v4-pro"
    assert argv[argv.index("--toolsets") + 1] == "web"
    assert "terminal" not in argv and "files" not in argv
    assert captured["env"]["DEEPSEEK_API_KEY"] == "DS-USER-KEY"


@pytest.mark.asyncio
async def test_deep_research_without_key_fails_open(monkeypatch):
    # 기본 macro_researcher 모델은 qwen(OpenRouter) — 활성 백엔드 키가 없으면 fail-open.
    monkeypatch.setattr("config.OPENROUTER_API_KEY", "")
    monkeypatch.setattr("config.DEEPSEEK_API_KEY", "")
    called = False

    async def fake_exec(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(global_search.asyncio, "create_subprocess_exec", fake_exec)
    assert await global_search.deep_research("시장") == ""
    assert called is False
