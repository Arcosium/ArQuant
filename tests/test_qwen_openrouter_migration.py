"""Qwen(OpenRouter) 전환 — DeepSeek pro/flash → qwen/qwen3.6-35b-a3b, 티어별 reasoning.

사장 지시 2026-06-18: deepseek 모델을 qwen/qwen3.6-35b-a3b(OpenRouter)로 교체한다.
  • pro 티어(결정 에이전트) → thinking(reasoning) ON
  • flash 티어            → thinking(reasoning) OFF
라우팅은 '모델 슬러그' 자체가 결정한다(슬러그에 '/' 있으면 OpenRouter, 없으면 DeepSeek) →
admin 모델 오버라이드로 에이전트별 qwen↔deepseek 자유 전환(되돌리기 포함)이 가능하다.
OpenRouter는 reasoning을 요청 파라미터(`reasoning:{enabled}`)로 토글하므로, '+thinking'
접미사를 단 가상 슬러그로 ON 변종을 표현하고 클라이언트가 접미사를 떼어 번역한다.
"""
import config
import pytest

from infra import deepseek_client as dc

PRO_ROLES = ["chief_orchestrator", "macro_analyst", "post_manager",
             "ops_support", "bond_manager", "commodity_manager"]
FLASH_ROLES = ["quant_analyst", "news_analyst", "news_curator",
               "macro_researcher", "trader", "risk_guard", "fund_planner"]


# ── 1. 기본 모델 배정: pro→qwen+thinking, flash→qwen ──────────────────────────
def test_qwen_slug_shapes():
    assert config.QWEN_MODEL == "qwen/qwen3.6-35b-a3b"
    assert config.QWEN_MODEL_THINKING.startswith(config.QWEN_MODEL)
    assert config.QWEN_MODEL_THINKING.endswith("+thinking")


def test_pro_roles_default_to_qwen_thinking():
    for r in PRO_ROLES:
        assert config.MODEL_ASSIGNMENTS[r] == config.QWEN_MODEL_THINKING, r


def test_flash_roles_default_to_qwen_plain():
    for r in FLASH_ROLES:
        assert config.MODEL_ASSIGNMENTS[r] == config.QWEN_MODEL, r


# ── 2. 슬러그 파싱 헬퍼 ──────────────────────────────────────────────────────
def test_split_thinking_strips_suffix():
    assert dc.split_thinking("qwen/qwen3.6-35b-a3b+thinking") == ("qwen/qwen3.6-35b-a3b", True)
    assert dc.split_thinking("qwen/qwen3.6-35b-a3b") == ("qwen/qwen3.6-35b-a3b", False)


def test_is_openrouter_model_by_slash():
    assert dc.is_openrouter_model("qwen/qwen3.6-35b-a3b") is True
    assert dc.is_openrouter_model("deepseek-v4-pro") is False
    assert dc.is_openrouter_model("deepseek-v4-flash") is False


# ── 3. 요청 조립(순수 함수) — 백엔드 라우팅·키·reasoning 번역 ─────────────────
def test_openrouter_request_url_key_and_reasoning_on(monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "OR-KEY")
    url, headers, payload = dc.build_request(
        api_key="ds-user-key", model=config.QWEN_MODEL_THINKING,
        messages=[{"role": "user", "content": "hi"}], max_tokens=100,
        temperature=0.3, thinking=None, response_format=None)
    assert url.startswith("https://openrouter.ai")
    # 공유 OpenRouter 키 사용 — 넘어온 per-user deepseek 키는 무시한다.
    assert headers["Authorization"] == "Bearer OR-KEY"
    assert payload["model"] == config.QWEN_MODEL          # +thinking 접미사 제거
    assert payload["reasoning"] == {"enabled": True}
    assert payload["stream"] is False


def test_openrouter_plain_model_reasoning_off(monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "OR-KEY")
    _, _, payload = dc.build_request(
        api_key="x", model=config.QWEN_MODEL, messages=[], max_tokens=10,
        temperature=0.3, thinking=None, response_format=None)
    assert payload["reasoning"] == {"enabled": False}
    assert payload["model"] == config.QWEN_MODEL


def test_explicit_thinking_arg_overrides_suffix(monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "OR-KEY")
    # 평문 모델인데 thinking=True → reasoning ON (ops_support 경로)
    _, _, p1 = dc.build_request(api_key="x", model=config.QWEN_MODEL, messages=[],
                                max_tokens=10, temperature=0.3, thinking=True,
                                response_format=None)
    assert p1["reasoning"] == {"enabled": True}
    # +thinking 모델인데 thinking=False → reasoning OFF (분류기 경로)
    _, _, p2 = dc.build_request(api_key="x", model=config.QWEN_MODEL_THINKING, messages=[],
                                max_tokens=10, temperature=0.3, thinking=False,
                                response_format=None)
    assert p2["reasoning"] == {"enabled": False}


def test_deepseek_request_unchanged():
    url, headers, payload = dc.build_request(
        api_key="ds-key", model="deepseek-v4-pro",
        messages=[{"role": "user", "content": "hi"}], max_tokens=100,
        temperature=0.3, thinking=True, response_format=None)
    assert url.startswith("https://api.deepseek.com")
    assert headers["Authorization"] == "Bearer ds-key"   # deepseek 경로는 넘어온 키 사용
    assert payload["model"] == "deepseek-v4-pro"
    assert payload["thinking"] == {"type": "enabled"}     # deepseek 형식
    assert "reasoning" not in payload


def test_openrouter_response_format_passthrough(monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "OR-KEY")
    _, _, payload = dc.build_request(
        api_key="x", model=config.QWEN_MODEL_THINKING, messages=[], max_tokens=10,
        temperature=0.2, thinking=True, response_format={"type": "json_object"})
    assert payload["response_format"] == {"type": "json_object"}


def test_openrouter_missing_key_raises(monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    with pytest.raises(dc.DeepSeekAPIError):
        dc.build_request(api_key="x", model=config.QWEN_MODEL, messages=[],
                         max_tokens=10, temperature=0.3, thinking=None,
                         response_format=None)


# ── 4. 모델별 키 선택 ────────────────────────────────────────────────────────
def test_api_key_for_routes_by_slug(monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "OR")
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "DS")
    assert dc.api_key_for(config.QWEN_MODEL) == "OR"
    assert dc.api_key_for("deepseek-v4-flash") == "DS"


# ── 5. admin allowlist — qwen 허용 + deepseek 되돌리기 허용 ───────────────────
def test_admin_allows_qwen_models(tmp_path, monkeypatch):
    from infra import admin_config as ac
    monkeypatch.setattr(ac, "_PATH", tmp_path / "admin.json")
    ac.set_config(model_overrides={"quant_analyst": config.QWEN_MODEL_THINKING})
    assert ac.get_model_override("quant_analyst") == config.QWEN_MODEL_THINKING
    ac.set_config(model_overrides={"trader": config.QWEN_MODEL})
    assert ac.get_model_override("trader") == config.QWEN_MODEL


def test_admin_still_allows_deepseek_for_revert(tmp_path, monkeypatch):
    from infra import admin_config as ac
    monkeypatch.setattr(ac, "_PATH", tmp_path / "admin.json")
    ac.set_config(model_overrides={"quant_analyst": "deepseek-v4-pro"})
    assert ac.get_model_override("quant_analyst") == "deepseek-v4-pro"


# ── 6. base_agent 기본 모델 — 티어 따라 ──────────────────────────────────────
def test_base_agent_pro_role_uses_qwen_thinking():
    from agents.base_agent import BaseAgent
    a = BaseAgent(name="t", role="post_manager", model_key="post_manager", system_prompt="x")
    assert a.model == config.QWEN_MODEL_THINKING


def test_base_agent_flash_role_uses_qwen_plain():
    from agents.base_agent import BaseAgent
    a = BaseAgent(name="t", role="trader", model_key="trader", system_prompt="x")
    assert a.model == config.QWEN_MODEL


# ── 7. 비용 단가표 — qwen 자체 단가(deepseek 기본단가 오청구 방지) ─────────────
def test_qwen_in_pricing_table():
    import agents.base_agent as ba
    assert config.QWEN_MODEL in ba._MODEL_PRICING
    assert config.QWEN_MODEL_THINKING in ba._MODEL_PRICING


# ── 8. Hermes 웹리서치 — qwen 모델 → OpenRouter provider ──────────────────────
@pytest.mark.asyncio
async def test_deep_research_qwen_uses_openrouter_provider(monkeypatch):
    from tools import global_search
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "OR-KEY")
    captured = {}

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return "리서치 결과".encode(), b""

    async def fake_exec(*argv, **kw):
        captured["argv"] = list(argv)
        captured["env"] = kw["env"]
        return FakeProc()

    monkeypatch.setattr(global_search.asyncio, "create_subprocess_exec", fake_exec)
    out = await global_search.deep_research("시장", model=config.QWEN_MODEL_THINKING)
    argv = captured["argv"]
    assert out == "리서치 결과"
    assert argv[argv.index("--provider") + 1] == "openrouter"
    assert argv[argv.index("--model") + 1] == config.QWEN_MODEL   # 접미사 제거
    assert argv[argv.index("--toolsets") + 1] == "web"
    assert captured["env"]["OPENROUTER_API_KEY"] == "OR-KEY"
