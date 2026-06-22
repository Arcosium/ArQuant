"""Macro research model default (Hermes web 도구 경로).

2026-06-18 사장 지시: deepseek → qwen/qwen3.6-35b-a3b(OpenRouter) 전환.
macro_researcher 는 web_search/web_extract(tool-calling) 가 필수라 flash 티어 →
reasoning OFF 인 평문 qwen 슬러그(QWEN_MODEL)를 기본값으로 둔다. (reasoning ON 변종은
tool-calling 안정성이 라이브 미검증이라 매크로 리서치엔 평문을 쓴다.)
"""
from infra import admin_config
import config


def test_macro_research_model_is_qwen_plain():
    assert config.MODEL_ASSIGNMENTS["macro_researcher"] == config.QWEN_MODEL


def test_resolve_model_falls_back_to_qwen(monkeypatch):
    monkeypatch.setattr(admin_config, "_read", lambda: {"model_overrides": {}})
    assert admin_config.resolve_model("macro_researcher") == config.QWEN_MODEL
