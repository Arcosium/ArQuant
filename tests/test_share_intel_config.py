"""ADMIN 인텔리전스 공유 설정 키 — 기본값 + 튜너블 카탈로그 등재."""
import config

def test_share_flags_defaults():
    assert config.SHARE_MARKET_INTELLIGENCE is True
    assert config.SHARE_PRODUCER_WAIT_SEC == 120

def test_share_keys_in_tunable_catalog():
    for k in ("SHARE_MARKET_INTELLIGENCE", "SHARE_PRODUCER_WAIT_SEC"):
        assert k in config.STRATEGY_TUNABLE_KEYS, f"{k} missing from STRATEGY_TUNABLE_KEYS"
        assert k in config.STRATEGY_KEY_META, f"{k} missing from STRATEGY_KEY_META"
        assert k in config.STRATEGY_KEY_EFFECT, f"{k} missing from STRATEGY_KEY_EFFECT"
