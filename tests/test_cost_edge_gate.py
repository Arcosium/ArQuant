"""비용인지 진입 엣지 게이트 (고회전 수익화, 2026-06-18).

고회전 전략이 손실인 수학적 이유: US 왕복비용 0.6%(0.3%×2)를 못 넘는 매매가 누적 출혈.
진입 시 '기대이동% − 왕복비용% ≥ MIN_NET_EDGE_PCT' 를 요구해, 비용 못 넘는 매매를 차단한다.
기대이동% = 일간변동성 = sigma20(연율화%)/√252. KR 비용≈0, US=0.6% → KR/US 비대칭 자동 반영.
운용지원실장이 MIN_NET_EDGE_PCT 를 조절해 회전율-수익성 균형을 맞춘다.
"""
from main_swarm import cost_edge_ok, filter_targets_by_cost_edge


def test_us_low_vol_blocked():
    # sigma20=10 → 일간 0.63% ; US 비용 0.6% → net 0.03% < 0.8% → 차단
    assert cost_edge_ok(10.0, is_us=True, min_net_edge_pct=0.8) is False


def test_us_high_vol_passes():
    # sigma20=40 → 일간 2.52% ; US 비용 0.6% → net 1.92% ≥ 0.8% → 통과
    assert cost_edge_ok(40.0, is_us=True, min_net_edge_pct=0.8) is True


def test_kr_moderate_vol_passes():
    # sigma20=20 → 일간 1.26% ; KR 비용 0% → net 1.26% ≥ 0.8% → 통과
    assert cost_edge_ok(20.0, is_us=False, min_net_edge_pct=0.8) is True


def test_kr_very_low_vol_blocked():
    # sigma20=10 → 일간 0.63% ; KR 비용 0% → net 0.63% < 0.8% → 차단
    assert cost_edge_ok(10.0, is_us=False, min_net_edge_pct=0.8) is False


def test_missing_sigma_preserves_candidate():
    # 변동성·익절 폴백 둘 다 결손 → 무음 차단 금지(보존)
    assert cost_edge_ok(None, is_us=True, min_net_edge_pct=0.8) is True
    assert cost_edge_ok(0.0, is_us=False, min_net_edge_pct=0.8) is True


def test_missing_sigma_uses_take_profit_fallback():
    # sigma 결손 시 TAKE_PROFIT_PCT 를 기대이동 폴백으로 — US 12% 익절 − 0.6% 비용 ≥ 0.8 → 통과
    assert cost_edge_ok(None, is_us=True, min_net_edge_pct=0.8, take_profit_pct=12.0) is True
    # 익절 0.5% 라면 US 비용 0.6% 도 못 넘음 → 차단
    assert cost_edge_ok(None, is_us=True, min_net_edge_pct=0.8, take_profit_pct=0.5) is False


def test_threshold_is_tunable():
    # 같은 종목도 MIN_NET_EDGE_PCT 를 올리면 차단(ops 튜닝 레버)
    assert cost_edge_ok(20.0, is_us=False, min_net_edge_pct=0.8) is True
    assert cost_edge_ok(20.0, is_us=False, min_net_edge_pct=2.0) is False


def test_filter_splits_kept_dropped():
    sigmas = {"AAA": 40.0, "BBB": 10.0, "005930": 20.0, "010140": 5.0}
    is_us = lambda c: not c.isdigit()
    kept, dropped = filter_targets_by_cost_edge(
        ["AAA", "BBB", "005930", "010140"], sigmas, 0.8, is_us_fn=is_us)
    assert kept == ["AAA", "005930"]      # 고변동 US + 중변동 KR
    assert set(dropped) == {"BBB", "010140"}  # 저변동 US·초저변동 KR
