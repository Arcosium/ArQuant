"""NXT 시간외 플래그가 config 와 런타임 오버라이드 카탈로그에 존재."""
import config

def test_flags_defined():
    assert config.ENABLE_NXT_EXTENDED_HOURS is True
    assert config.ENABLE_NXT_PRE_MARKET is True
    assert config.ENABLE_NXT_AFTER_MARKET is True
    assert abs(config.EXT_HOURS_LIMIT_SLIPPAGE_PCT - 0.5) < 1e-9

def test_flags_runtime_overridable():
    for k in ("ENABLE_NXT_EXTENDED_HOURS", "ENABLE_NXT_PRE_MARKET",
              "ENABLE_NXT_AFTER_MARKET", "EXT_HOURS_LIMIT_SLIPPAGE_PCT"):
        assert k in config.STRATEGY_TUNABLE_KEYS, f"{k} 런타임 오버라이드 미등록"
