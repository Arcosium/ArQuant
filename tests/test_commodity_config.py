"""원자재 슬리브 + 채권 확장 config 검증 (사장 지시 2026-06-09).

화이트리스트 풀이라 코드 오류=실주문 실패 — 검증된 코드 등재 여부를 핀.
풀 태그는 5필드 (code, name, duration, kind, fx)로 확장됨."""
import config


def test_commodity_defaults_on():
    assert config.ENABLE_COMMODITY_ETF is True
    assert config.STRATEGY_DEFAULTS["ENABLE_COMMODITY_ETF"] is True


def test_bond_default_on():
    assert config.ENABLE_BOND_ETF is True
    assert config.STRATEGY_DEFAULTS["ENABLE_BOND_ETF"] is True


def test_commodity_pools_have_verified_codes():
    kr = {c for c, *_ in config.COMMODITY_ETF_POOL_KR}
    us = {c for c, *_ in config.COMMODITY_ETF_POOL_US}
    assert {"132030", "261220", "137610"} <= kr   # 금/원유/농산물
    assert {"GLD", "USO", "DBA"} <= us


def test_bond_pool_expanded():
    kr = {c for c, *_ in config.BOND_ETF_POOL_KR}
    assert {"357870", "459580", "273130", "451540", "458250"} <= kr  # CD/회사채/환헤지
    us = {c for c, *_ in config.BOND_ETF_POOL_US}
    assert {"LQD", "HYG", "TIP"} <= us


def test_pool_tags_have_five_fields():
    for tag in (list(config.BOND_ETF_POOL_KR) + list(config.BOND_ETF_POOL_US)
                + list(config.COMMODITY_ETF_POOL_KR) + list(config.COMMODITY_ETF_POOL_US)):
        assert len(tag) == 5, tag  # (code, name, duration, kind, fx)


def test_commodity_tunable_keys_registered():
    for k in ("ENABLE_COMMODITY_ETF", "COMMODITY_TARGET_MAX_PCT",
              "COMMODITY_REBALANCE_BAND_PCT", "COMMODITY_PER_CYCLE_RATIO"):
        assert k in config.STRATEGY_TUNABLE_KEYS
        assert k in config.STRATEGY_KEY_META
        assert k in config.STRATEGY_KEY_EFFECT
