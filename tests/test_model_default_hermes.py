"""Macro research uses DeepSeek through the restricted Hermes web tool path."""
from infra import admin_config
import config


def test_macro_research_model_is_official_deepseek():
    assert config.MODEL_ASSIGNMENTS["macro_researcher"] == "deepseek-v4-pro"


def test_resolve_model_falls_back_to_deepseek(monkeypatch):
    monkeypatch.setattr(admin_config, "_read", lambda: {"model_overrides": {}})
    assert admin_config.resolve_model("macro_researcher") == "deepseek-v4-pro"
