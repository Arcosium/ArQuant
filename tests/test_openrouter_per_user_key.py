"""회원가입 OpenRouter 키 + per-user 키 사용(sk-or- 가드).

사장 지시 2026-06-19: 가입 시 DeepSeek 키 대신 OpenRouter 키를 받는다. 스웜은 per-user
OpenRouter 키(sk-or-...)가 있으면 그걸 쓰고, 아니면(잔액소진된 구 deepseek 키/빈값) 공유
OPENROUTER_API_KEY 로 폴백한다 → 기존 계정(uid1 실거래) 무중단.
"""
import config
import pytest

from infra import deepseek_client as dc


# ── per-user 키 해석(sk-or- 가드) ────────────────────────────────────────────
def test_resolve_openrouter_key_prefers_per_user_or_key(monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "SHARED")
    assert dc.resolve_openrouter_key("sk-or-v1-USERKEY") == "sk-or-v1-USERKEY"


def test_resolve_openrouter_key_falls_back_for_non_or_key(monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "SHARED")
    # 구 deepseek 키(sk-...)·빈값/None → 공유 키 (기존 uid1 무중단)
    assert dc.resolve_openrouter_key("sk-staledeepseekkey") == "SHARED"
    assert dc.resolve_openrouter_key("") == "SHARED"
    assert dc.resolve_openrouter_key(None) == "SHARED"


# ── build_request: per-user OR 키 사용 ───────────────────────────────────────
def test_build_request_uses_per_user_or_key(monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "SHARED")
    _, headers, _ = dc.build_request(
        api_key="«REDACTED»", model=config.QWEN_MODEL, messages=[],
        max_tokens=10, temperature=0.3, thinking=None, response_format=None)
    assert headers["Authorization"] == "Bearer sk-or-v1-USERKEY"


def test_build_request_falls_back_to_shared_for_stale_deepseek_key(monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "SHARED")
    _, headers, _ = dc.build_request(
        api_key="«REDACTED»", model=config.QWEN_MODEL, messages=[],
        max_tokens=10, temperature=0.3, thinking=None, response_format=None)
    assert headers["Authorization"] == "Bearer SHARED"


# ── Hermes deep_research: per-user OR 키 사용 ────────────────────────────────
@pytest.mark.asyncio
async def test_deep_research_uses_per_user_or_key(monkeypatch):
    from tools import global_search
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "SHARED")
    captured = {}

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return b"result", b""

    async def fake_exec(*argv, **kw):
        captured["env"] = kw["env"]
        return FakeProc()

    monkeypatch.setattr(global_search.asyncio, "create_subprocess_exec", fake_exec)
    await global_search.deep_research("x", model=config.QWEN_MODEL, api_key="«REDACTED»")
    assert captured["env"]["OPENROUTER_API_KEY"] == "sk-or-v1-USER"


# ── 등록 검증: OpenRouter 키 형식 거부(HTTP 없이 형식만) ──────────────────────
@pytest.mark.asyncio
async def test_validate_openrouter_rejects_non_or_key():
    import server.app as app_mod
    ok, msg = await app_mod._validate_openrouter("«REDACTED»")
    assert ok is False
    assert "sk-or" in msg
