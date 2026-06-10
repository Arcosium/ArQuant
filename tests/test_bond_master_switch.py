import config
import runtime


def test_default_master_switch_on():
    # 사장 지시 2026-06-09: 채권 ETF 자동매매 기본 ON.
    assert config.ENABLE_BOND_ETF is True


def test_runtime_default_on():
    assert bool(runtime.get("ENABLE_BOND_ETF", uid=999)) is True
