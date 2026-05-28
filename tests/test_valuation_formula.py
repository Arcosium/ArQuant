"""현재 평가액 공식 — 외화평가총액 + 국내 유가증권 + D+2 예수금 (D0 폴백 금지).

배경(2026-05-28 진단): hh09080 자산곡선이 거래 없이 2.87M→7.82M로 +4.9M 유령점프.
라이브 KIS raw 확인 결과 D+2(prvs_rcdl_excc_amt)=2,825,543 이 정상값인데, KIS가 결제 과도기에
D+2/D+1 을 0 으로 깜빡이면 cash 가 dnca_tot_amt(D0)=7,753,266(미결제 매수분이 아직 안 빠져
부풀려진 값)로 폴백돼 곡선이 튀었다. 사장 지시 2026-05-28: 평가액은 무조건 D+2 기반, D0 폴백 금지,
국내 유가증권평가액 포함(전체 보유주식 + D+2). 해외(외화평가총액)는 호출부에서 더한다.
"""
from infra.kis_broker import kr_net_valuation


def test_normal_uses_d2_plus_securities():
    total, settled = kr_net_valuation(scts_eval=237051, cash_d2=2825543, cash_d1=2800000)
    assert settled == 2825543
    assert total == 237051 + 2825543


def test_d2_glitch_carries_forward_prev_not_d0():
    """D+2/D+1 모두 0(글리치) → 직전 정상 D+2 유지. D0(7.75M)는 절대 쓰지 않는다."""
    total, settled = kr_net_valuation(scts_eval=237051, cash_d2=0.0, cash_d1=0.0,
                                      prev_settled=2825543)
    assert settled == 2825543, "글리치 시 직전 D+2 유지"
    assert total == 237051 + 2825543


def test_d2_zero_no_history_falls_to_d1_not_d0():
    total, settled = kr_net_valuation(scts_eval=0.0, cash_d2=0.0, cash_d1=2800000,
                                      prev_settled=None)
    assert settled == 2800000


def test_never_uses_d0():
    """시그니처에 D0 가 없어 구조적으로 폴백 불가. 전부 0이면 settled=0(스파이크 금지)."""
    total, settled = kr_net_valuation(scts_eval=50000, cash_d2=0.0, cash_d1=0.0,
                                      prev_settled=None)
    assert settled == 0.0
    assert total == 50000


def test_securities_included():
    """국내 유가증권평가액 포함 (사장 결정 2026-05-28: 전체 보유주식 + D+2)."""
    total, _ = kr_net_valuation(scts_eval=500000, cash_d2=1000000, cash_d1=0.0)
    assert total == 1500000
