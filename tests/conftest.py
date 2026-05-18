"""공통 픽스처.

실거래 검증 로직(`guardrails._check_single_order`, `main_swarm._affordable_one_share`)은
``import runtime; runtime.get(KEY)`` 로 활성 전략 프리셋 값을 읽는다. 테스트가 디스크에
저장된 전략 상태(`data/strategy_state.json`)에 의존하면 비결정적이 되므로, 여기서
``runtime.get`` 을 **고정된 보수형 한도 맵**으로 패치한다. 두 모듈 모두 모듈 객체를
import 하므로 `runtime.get` 한 곳만 패치하면 양쪽에 적용된다.
"""
import sys
from pathlib import Path

import pytest

# 저장소 루트를 import path 에 추가 (pytest를 어디서 호출하든 동작).
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 테스트가 가정하는 결정론적 한도 (config.py 의 'balanced' 프리셋과 동일값).
FIXED_LIMITS = {
    "CONSERVATIVE_MDD": 0.05,
    "CONSERVATIVE_STOCK_RATIO": 0.15,
    "MIN_CASH_BUFFER": 1.10,
    "MAX_CYCLE_BUDGET_RATIO": 0.25,
}


@pytest.fixture(autouse=True)
def fixed_runtime_limits(monkeypatch):
    """모든 테스트에서 runtime.get 을 고정 한도로 대체 (없는 키는 config 폴백)."""
    import runtime as _rt

    real_get = _rt.get

    def _patched(key, default=None):
        if key in FIXED_LIMITS:
            return FIXED_LIMITS[key]
        return real_get(key, default)

    monkeypatch.setattr(_rt, "get", _patched)
    return FIXED_LIMITS
