"""사장 지시 2026-06-04 ②: 리스크기반 포지션 사이징(순수). weight=1.0 → 균등 1몫.
점수↑→비중↑, σ↑→비중↓. equal/strength0→전원 1.0. Σw=종목수. 결측=중립."""
from tools.position_sizing import compute_sizing_weights


def test_equal_mode_all_one():
    w = compute_sizing_weights(["A", "B"], {"A": 9, "B": 3}, {"A": 10, "B": 80}, mode="equal")
    assert w == {"A": 1.0, "B": 1.0}


def test_strength_zero_all_one():
    w = compute_sizing_weights(["A", "B"], {"A": 9, "B": 3}, {"A": 10, "B": 80},
                               mode="risk_weighted", strength=0.0)
    assert all(abs(v - 1.0) < 1e-9 for v in w.values())


def test_weights_sum_to_n():
    codes = ["A", "B", "C"]
    w = compute_sizing_weights(codes, {"A": 8, "B": 5, "C": 6}, {"A": 20, "B": 30, "C": 25},
                               mode="risk_weighted", strength=0.5, max_tilt=2.0)
    assert abs(sum(w.values()) - len(codes)) < 1e-6
    assert all(v > 0 for v in w.values())


def test_higher_score_gets_more():
    w = compute_sizing_weights(["HI", "LO"], {"HI": 9, "LO": 4}, {"HI": 25, "LO": 25},
                               mode="risk_weighted", strength=1.0, max_tilt=3.0)
    assert w["HI"] > w["LO"]


def test_higher_vol_gets_less():
    w = compute_sizing_weights(["CALM", "WILD"], {"CALM": 6, "WILD": 6}, {"CALM": 15, "WILD": 60},
                               mode="risk_weighted", strength=1.0, max_tilt=3.0)
    assert w["CALM"] > w["WILD"]


def test_max_tilt_bounds_each_weight():
    w = compute_sizing_weights(["A", "B", "C"], {"A": 10, "B": 0, "C": 5}, {"A": 5, "B": 90, "C": 30},
                               mode="risk_weighted", strength=1.0, max_tilt=2.0)
    # 합=종목수 보장 하에서 극단치가 1/max_tilt~max_tilt 근방으로 억제(과집중 방지) — 최대가 종목수 미만
    assert max(w.values()) < len(w)


def test_missing_score_and_sigma_neutral():
    # 점수/σ 둘 다 결측 → 균등(중립)
    w = compute_sizing_weights(["A", "B"], {}, {}, mode="risk_weighted", strength=1.0)
    assert all(abs(v - 1.0) < 1e-9 for v in w.values())


def test_empty_codes():
    assert compute_sizing_weights([], {}, {}) == {}
