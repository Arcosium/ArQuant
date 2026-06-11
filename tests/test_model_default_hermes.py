"""Macro research uses DeepSeek through the restricted Hermes web tool path.

2026-06-10 사장 승인: macro_researcher 는 deepseek-v4-flash 고정 — 공식 DeepSeek API 의
reasoning(pro) 모델은 function-calling(Hermes web 도구) 미지원이라 pro 로 두면
도구 미장착 → 빈 리서치 + 180s 타임아웃이 났다 (라이브 검증 완료).
"""
from infra import admin_config
import config


def test_macro_research_model_is_official_deepseek():
    assert config.MODEL_ASSIGNMENTS["macro_researcher"] == "deepseek-v4-flash"


def test_resolve_model_falls_back_to_deepseek(monkeypatch):
    monkeypatch.setattr(admin_config, "_read", lambda: {"model_overrides": {}})
    assert admin_config.resolve_model("macro_researcher") == "deepseek-v4-flash"
