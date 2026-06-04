"""사장 지시 2026-06-04: 결정론 점수 엔진 입력용 구조화 지표 dict + CMF(신규).
spec: docs/superpowers/specs/2026-06-04-deterministic-score-engine-design.md
"""
import pandas as pd
from tools.market_data import _cmf, compute_quant_indicators


def _daily(n=70, close_pos="high"):
    """합성 일봉 — close_pos='high'면 종가가 고가 근처(매집), 'low'면 저가 근처(분산)."""
    rows = []
    base = pd.Timestamp("2026-01-01")
    for i in range(n):
        lo, hi = 100.0 + i, 110.0 + i
        c = hi - 0.5 if close_pos == "high" else lo + 0.5
        rows.append({"date": base + pd.Timedelta(days=i), "open": (lo + hi) / 2,
                     "high": hi, "low": lo, "close": c, "volume": 1000 + i})
    return pd.DataFrame(rows)


def test_cmf_accumulation_positive():
    assert _cmf(_daily(close_pos="high"), 20) > 0.5     # 종가 고가근처 → 매집(+)


def test_cmf_distribution_negative():
    assert _cmf(_daily(close_pos="low"), 20) < -0.5     # 종가 저가근처 → 분산(−)


def test_cmf_none_when_insufficient():
    assert _cmf(_daily(n=5), 20) is None


def test_compute_quant_indicators_keys():
    inv = pd.DataFrame([{"date": pd.Timestamp("2026-01-01") + pd.Timedelta(days=i),
                         "inst_net": 100, "foreign_net": 200} for i in range(25)])
    ind = compute_quant_indicators("TEST", daily=_daily(70), investor=inv)
    for k in ("rsi14", "macd_hist_pct", "adx", "adx_dir", "vwap_dev", "sigma20",
              "mom_1m", "mom_3m", "cmf", "flow"):
        assert k in ind, f"{k} 누락"
    assert -1.0 <= ind["cmf"] <= 1.0
    assert -1.0 <= ind["flow"] <= 1.0           # 순매수(+) 종목 → 양수 방향
    assert ind["flow"] > 0


def test_compute_quant_indicators_empty_on_no_data():
    assert compute_quant_indicators("NODATA", daily=pd.DataFrame(), investor=None) == {}
