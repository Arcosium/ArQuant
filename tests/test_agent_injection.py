from agents.base_agent import BaseAgent


def test_agent_uses_injected_deepseek_key():
    inj = {"deepseek_api_key": "DS-INJECTED",
           "model_overrides": {"quant_analyst": "deepseek-v4-pro"}}
    a = BaseAgent(name="t", role="quant_analyst", system_prompt="p",
                  model_key="quant_analyst", injection=inj)
    assert a.api_key == "DS-INJECTED"
    assert a.model == "deepseek-v4-pro"


def test_agent_falls_back_to_config_when_no_injection():
    # No injection bundle → legacy behaviour (reads config). Must not crash.
    a = BaseAgent(name="t", role="quant_analyst", system_prompt="p",
                  model_key="quant_analyst")
    assert isinstance(a.model, str) and a.model
