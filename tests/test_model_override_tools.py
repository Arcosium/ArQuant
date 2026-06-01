"""매크로 리서치·뉴스 분류기 모델 오버라이드 (사장 지시 2026-05-30).

버그: deep_research(매크로)·llm_classify_articles(뉴스분류)는 BaseAgent 가 아니라 tool 함수라
config.MODEL_ASSIGNMENTS 만 읽고 ADMIN 모델 오버라이드(admin_config)를 '무시'했다. → 사장이
대시보드에서 모델을 바꿔도 이 둘은 늘 config 기본값(tongyi)으로 동작. resolve_model 로 통일해 수정.
"""
import asyncio

import pytest

import config
from infra import admin_config


@pytest.fixture
def tmp_admin(monkeypatch, tmp_path):
    # 실제 data/admin_config.json 을 건드리지 않도록 임시 경로로
    monkeypatch.setattr(admin_config, "_PATH", tmp_path / "admin_config.json")
    return admin_config


def test_resolve_model_prefers_override(tmp_admin):
    tmp_admin.set_config(model_overrides={"macro_researcher": "anthropic/claude-x"})
    assert tmp_admin.resolve_model("macro_researcher", "fallback") == "anthropic/claude-x"


def test_resolve_model_falls_back_to_config(tmp_admin):
    tmp_admin.set_config(model_overrides={})  # 오버라이드 없음
    # config 기본값(tongyi)으로 폴백
    assert tmp_admin.resolve_model("news_classifier", "fallback") == \
        config.MODEL_ASSIGNMENTS["news_classifier"]


def test_resolve_model_default_when_unknown_key(tmp_admin):
    tmp_admin.set_config(model_overrides={})
    assert tmp_admin.resolve_model("no_such_key", "deepseek/deepseek-v4-flash") == \
        "deepseek/deepseek-v4-flash"


# ── 통합: 실제 호출부가 오버라이드 모델을 전송하는지 ──────────────────────────
class _FakeResp:
    status = 200

    def __init__(self, captured):
        self._c = captured

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self):
        return {"choices": [{"message": {"content": '["KR"]'}}]}


class _FakeSession:
    def __init__(self, captured):
        self._c = captured

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def post(self, url, json=None, headers=None):
        self._c["model"] = (json or {}).get("model")
        return _FakeResp(self._c)


def test_news_classifier_uses_admin_override(monkeypatch, tmp_path):
    import tools.news_monitor as nm

    monkeypatch.setattr(admin_config, "_PATH", tmp_path / "admin_config.json")
    admin_config.set_config(model_overrides={"news_classifier": "anthropic/claude-sentinel"})
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "test-key")

    captured: dict = {}

    class _ST:  # aiohttp.ClientTimeout 대체 (인자만 받는 더미)
        def __init__(self, *a, **k):
            pass

    monkeypatch.setattr(nm.aiohttp, "ClientSession", lambda *a, **k: _FakeSession(captured))
    monkeypatch.setattr(nm.aiohttp, "ClientTimeout", _ST)

    res = asyncio.run(nm.llm_classify_articles([{"title": "삼성전자 신고가", "link": "L1"}]))
    assert captured.get("model") == "anthropic/claude-sentinel", \
        "뉴스 분류기가 ADMIN 모델 오버라이드를 전송해야 한다 (config tongyi 무시 버그 수정)"
    assert res.get("L1") == "KR"
