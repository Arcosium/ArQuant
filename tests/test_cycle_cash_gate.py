"""사이클 사전 게이트 — '예수금 부족' 스킵은 *신뢰 가능한* 잔고 스냅샷에만 적용한다.

버그(2026-05-29 사장 제보 — 모의계좌): 잔고 조회가 일시 실패(ok=False)해 cash=0 으로
반환되면, 게이트가 '가용 예수금 부족'으로 사이클을 통째로 스킵했다. 조회 실패는 '돈이 없음'이
아니라 '데이터를 모름'이므로, 이 때는 스킵하지 말고 진행해야 한다(브로커가 사이클 안에서 재시도).
'예수금 부족' 스킵은 스냅샷이 ok=True(신뢰 가능)이고 실제로 cash 가 최소 매매 단위 미만일 때만.
"""
from main_swarm import _low_cash_skip_reason, MIN_TRADABLE_CASH_KRW


def _snap(cash, ok=True):
    return {"buying_power": {"cash": cash, "ok": ok}, "ok": ok}


def test_reliable_low_cash_skips():
    """ok=True + cash 부족 → 스킵 사유 반환."""
    assert _low_cash_skip_reason(_snap(1000, ok=True)) is not None


def test_reliable_sufficient_cash_no_skip():
    """ok=True + cash 충분 → 스킵 안 함."""
    assert _low_cash_skip_reason(_snap(1_000_000, ok=True)) is None


def test_failed_read_never_skips():
    """ok=False(조회 실패) + cash=0 → 스킵하면 안 됨(데이터 미상이지 돈 없음이 아님)."""
    assert _low_cash_skip_reason(_snap(0, ok=False)) is None


def test_failed_read_with_zero_cash_proceeds_even_at_boundary():
    """조회 실패면 cash 값과 무관하게 진행."""
    assert _low_cash_skip_reason(_snap(MIN_TRADABLE_CASH_KRW - 1, ok=False)) is None
