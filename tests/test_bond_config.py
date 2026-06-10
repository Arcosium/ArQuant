import config

def test_bond_flags_exist_with_defaults():
    assert config.ENABLE_BOND_ETF is True           # 마스터 스위치 기본 ON (사장 지시 2026-06-09)
    assert 0.0 < config.BOND_TARGET_MAX_PCT <= 1.0
    assert 0.0 <= config.BOND_REBALANCE_BAND_PCT < 0.2

def test_bond_etf_pools_shape():
    # 자산군 확장(2026-06-09): 풀 태그 5필드 (code, name, duration, kind, fx).
    for pool in (config.BOND_ETF_POOL_KR, config.BOND_ETF_POOL_US):
        assert len(pool) >= 3
        for code, name, dur, kind, fx in pool:
            assert isinstance(code, str) and isinstance(name, str)
            assert dur in ("short", "mid", "long", "na")
            assert kind in ("govt", "rate", "credit", "tips")
            assert fx in ("krw", "hedged", "exposed")
    kr_codes = [c for c, *_ in config.BOND_ETF_POOL_KR]
    assert kr_codes[:3] == ["153130", "114260", "148070"]  # 기존 국고채 3종 보존
    us_codes = [c for c, *_ in config.BOND_ETF_POOL_US]
    assert us_codes[:3] == ["SHY", "IEF", "TLT"]

def test_bond_keys_are_tunable_and_have_meta():
    for k in ("ENABLE_BOND_ETF", "BOND_TARGET_MAX_PCT", "BOND_REBALANCE_BAND_PCT",
              "BOND_PER_CYCLE_RATIO"):
        assert k in config.STRATEGY_TUNABLE_KEYS
        assert k in config.STRATEGY_KEY_META


def test_bond_per_cycle_ratio_default_and_meta():
    # 채권 전용 사이클 예산비율 — 주식 MAX_CYCLE_BUDGET_RATIO 와 분리된 별도 게이트.
    assert 0.0 <= config.BOND_PER_CYCLE_RATIO <= 1.0
    assert config.BOND_PER_CYCLE_RATIO == 0.15
    m = config.STRATEGY_KEY_META["BOND_PER_CYCLE_RATIO"]
    assert m["type"] == "pct_raw"
    assert m["group"] == "매도 규칙"
    assert m["min"] == 0.0 and m["max"] == 1.0
    # EFFECT(단수형) 카탈로그 등록 확인
    assert "BOND_PER_CYCLE_RATIO" in config.STRATEGY_KEY_EFFECT
