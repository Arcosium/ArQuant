from agents.base_agent import BaseAgent


def test_agent_uses_injected_local_model_override():
    import config
    inj = {"model_overrides": {"quant_analyst": config.LOCAL_LLM_MODEL_THINKING}}
    a = BaseAgent(name="t", role="quant_analyst", system_prompt="p",
                  model_key="quant_analyst", injection=inj)
    assert a.api_key == ""
    assert a.model == config.LOCAL_LLM_MODEL_THINKING


def test_agent_falls_back_to_config_when_no_injection():
    # No injection bundle → legacy behaviour (reads config). Must not crash.
    a = BaseAgent(name="t", role="quant_analyst", system_prompt="p",
                  model_key="quant_analyst")
    assert isinstance(a.model, str) and a.model
