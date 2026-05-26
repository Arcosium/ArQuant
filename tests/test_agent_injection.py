from agents.base_agent import BaseAgent


def test_agent_uses_injected_openrouter_key():
    inj = {"openrouter_key": "OR-INJECTED",
           "openrouter_base_url": "https://openrouter.ai/api/v1",
           "model_overrides": {"quant_analyst": "anthropic/claude-x"}}
    a = BaseAgent(name="t", role="quant_analyst", system_prompt="p",
                  model_key="quant_analyst", injection=inj)
    assert a.api_key == "OR-INJECTED"
    assert a.model == "anthropic/claude-x"   # per-injection override wins


def test_agent_falls_back_to_config_when_no_injection():
    # No injection bundle → legacy behaviour (reads config). Must not crash.
    a = BaseAgent(name="t", role="quant_analyst", system_prompt="p",
                  model_key="quant_analyst")
    assert isinstance(a.model, str) and a.model
