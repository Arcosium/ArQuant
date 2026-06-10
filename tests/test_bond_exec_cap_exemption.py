"""I2 회귀: 채권 매수가 주식과 _max_trades cap 을 공유해 밀려서 누락되던 버그.

_cyc_stage_execute 의 _exec_list = _ap_sells + _ap_buys[:_max_trades] 에서 채권 매수가
주식 매수와 같은 _ap_buys 에 섞여 매매빈도 cap 을 공유 → 주식이 cap 을 채우면 채권
리밸런싱이 조용히 누락('주문 절대 스킵 금지' 위반 소지).

수정: 채권 매수는 cap 에서 면제. _exec_list = 매도 전부 + 채권 매수 전부 + 주식 매수[:cap].
"""
from infra.asset_sleeves import build_exec_list, all_sleeve_pool_codes  # 채권 함수 일반화(2026-06-09)

POOL = all_sleeve_pool_codes()


def _o(ticker, side="buy"):
    return {"ticker": ticker, "side": side, "qty": 1, "reason": "x"}


def test_bond_buys_exempt_from_cap():
    orders = [
        _o("005930"), _o("000660"), _o("035720"),  # 주식 매수 3개
        _o("153130"), _o("TLT"),                    # 채권 매수 2개
    ]
    out = build_exec_list(orders, max_trades=1, sleeve_pool_codes=POOL)
    tickers = [o["ticker"] for o in out]
    # 채권 둘 다 실행 + 주식은 cap(1) 만큼만
    assert "153130" in tickers and "TLT" in tickers, "채권 매수는 cap 면제 — 누락되면 안 됨"
    stock_in = [t for t in tickers if t in ("005930", "000660", "035720")]
    assert len(stock_in) == 1, "주식 매수는 cap(1)만큼만 실행"


def test_sells_always_included_and_bonds_exempt():
    orders = [
        _o("005930", "sell"), _o("AAPL", "sell"),   # 매도 2개
        _o("000660"), _o("035720"),                 # 주식 매수 2개
        _o("148070", "sell"),                       # 채권 매도(있으면 매도 트랙으로 전량 실행)
        _o("SHY"),                                  # 채권 매수
    ]
    out = build_exec_list(orders, max_trades=1, sleeve_pool_codes=POOL)
    tickers = [o["ticker"] for o in out]
    # 매도 전부(채권 매도 포함)
    assert tickers.count("005930") == 1 and "AAPL" in tickers and "148070" in tickers
    # 채권 매수 면제
    assert "SHY" in tickers
    # 주식 매수는 cap(1)
    assert len([t for t in tickers if t in ("000660", "035720")]) == 1


def test_order_sells_then_bonds_then_stocks():
    orders = [_o("000660"), _o("153130"), _o("005930", "sell")]
    out = build_exec_list(orders, max_trades=5, sleeve_pool_codes=POOL)
    # 순서: 매도 → 채권 매수 → 주식 매수
    assert out[0]["ticker"] == "005930" and out[0]["side"] == "sell"
    assert out[1]["ticker"] == "153130"
    assert out[2]["ticker"] == "000660"


def test_empty_pool_matches_legacy_behavior():
    # ENABLE_BOND_ETF OFF 시뮬레이션: 빈 풀 → 기존 sells + buys[:cap] 와 동일
    orders = [_o("005930", "sell"), _o("000660"), _o("035720"), _o("000100")]
    out = build_exec_list(orders, max_trades=2, sleeve_pool_codes=set())
    tickers = [o["ticker"] for o in out]
    # 매도 1 + 주식 매수 2 = 총 3
    assert tickers == ["005930", "000660", "035720"]


def test_cap_zero_still_runs_bonds_and_sells():
    orders = [_o("005930", "sell"), _o("000660"), _o("153130")]
    out = build_exec_list(orders, max_trades=0, sleeve_pool_codes=POOL)
    tickers = [o["ticker"] for o in out]
    assert "005930" in tickers  # 매도
    assert "153130" in tickers  # 채권 매수(cap 면제)
    assert "000660" not in tickers  # 주식 매수는 cap=0 → 0건


def test_none_max_trades_defaults_to_two():
    orders = [_o("a1"), _o("a2"), _o("a3")]
    out = build_exec_list(orders, max_trades=None, sleeve_pool_codes=POOL)
    assert len(out) == 2
