"""사장 지시 2026-06-04 ④: 성과귀인 순수함수 — IC(스피어만)·슬리피지·알파베타."""
from tools.agent_scorecard import information_coefficient, slippage_stats, alpha_beta, compute_scorecard


def test_ic_perfect_positive():
    pairs = [(1, 0.1), (2, 0.2), (3, 0.3), (4, 0.4)]
    assert abs(information_coefficient(pairs) - 1.0) < 1e-9


def test_ic_perfect_negative():
    pairs = [(1, 0.4), (2, 0.3), (3, 0.2), (4, 0.1)]
    assert abs(information_coefficient(pairs) + 1.0) < 1e-9


def test_ic_too_few_points_none():
    assert information_coefficient([(1, 0.1), (2, 0.2)]) is None


def test_slippage_bps_sign():
    # 매수가 결정가보다 비싸게 체결 → 양(+) 불리한 슬리피지
    fills = [{"side": "buy", "decision_price": 100.0, "fill_price": 101.0}]
    st = slippage_stats(fills)
    assert st["n"] == 1 and st["mean_bps"] > 0


def test_alpha_beta_recovers_known():
    bench = [0.01, -0.02, 0.03, 0.00, 0.015]
    port = [2 * b + 0.001 for b in bench]   # beta≈2, alpha≈0.001
    ab = alpha_beta(port, bench)
    assert abs(ab["beta"] - 2.0) < 1e-6
    assert abs(ab["alpha"] - 0.001) < 1e-6


def test_alpha_beta_too_few_none():
    assert alpha_beta([0.01], [0.02]) is None


def test_compute_scorecard_shape():
    signals = [
        {"code": "A", "ts": "2026-06-01 10:00:00", "quant_score": 8, "news_sentiment": 0.7},
        {"code": "B", "ts": "2026-06-01 10:00:00", "quant_score": 3, "news_sentiment": -0.4},
        {"code": "C", "ts": "2026-06-01 10:00:00", "quant_score": 6, "news_sentiment": 0.1},
    ]
    fwd = {"A": 0.05, "B": -0.03, "C": 0.01}     # 점수·감성과 양의 상관
    card = compute_scorecard(signals, trades=[], equity=[], bench=[],
                             price_lookup=lambda code, ts: fwd.get(code), window_days=30)
    assert "quant" in card and "news" in card
    assert card["quant"]["ic"] is not None and card["quant"]["n"] == 3
