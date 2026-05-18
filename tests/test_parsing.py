"""LLM 자유 서술에서 매매 지시를 끌어내는 결정론 파서들.

이 파서가 틀리면 LLM 의도와 다른 매매가 실행되거나 무시되므로,
경계 케이스를 고정한다.
"""
import main_swarm as ms


# ── _parse_sell_decisions ────────────────────────────────────────────────
def test_parse_sell_decisions_basic():
    txt = "사후관리 분석...\n매도결정: 005930=전량, 000660=절반, AAPL=보유"
    assert ms._parse_sell_decisions(txt) == {
        "005930": "전량", "000660": "절반", "AAPL": "보유"}


def test_parse_sell_decisions_absent_line_is_empty():
    assert ms._parse_sell_decisions("매도 지시가 전혀 없는 자유 서술") == {}


def test_parse_sell_decisions_lowercase_ticker_upcased():
    assert ms._parse_sell_decisions("매도결정: aapl=전량") == {"AAPL": "전량"}


# ── _parse_entry_directive ───────────────────────────────────────────────
def test_entry_directive_market_default_when_absent():
    d = ms._parse_entry_directive("진입가 언급 없음", "005930")
    assert d["mode"] == "market"


def test_entry_directive_explicit_market():
    d = ms._parse_entry_directive("진입가: 005930=시장가", "005930")
    assert d["mode"] == "market"


def test_entry_directive_limit_price():
    d = ms._parse_entry_directive("진입가: 005930=71500", "005930")
    assert d["mode"] == "limit"
    assert d["limit_price"] == 71500.0


def test_entry_directive_watch_pct_negative_word():
    d = ms._parse_entry_directive("진입가: 005930=관망 3% 하락 시", "005930")
    assert d["mode"] == "watch"
    assert d["watch_pct"] == -3.0


def test_entry_directive_watch_pct_clamped_to_10():
    d = ms._parse_entry_directive("진입가: 005930=관망 +25%", "005930")
    assert d["mode"] == "watch"
    assert d["watch_pct"] == 10.0  # 극단값 ±10% 로 클램프


# ── _clean_codes / 코드 추출 ─────────────────────────────────────────────
def test_clean_codes_dedupes_and_filters():
    out = ms._clean_codes(["005930", "005930", "12345", "000660"], ["AAPL", "aapl", "TOOLONGX"])
    assert out == ["005930", "000660", "AAPL"]  # 중복·5자초과·5자리 제거


def test_extract_codes_after_label_present_vs_absent():
    assert ms._extract_codes_after("후보종목: 005930, AAPL", "후보종목") == ["005930", "AAPL"]
    assert ms._extract_codes_after("관련 라벨 없음", "후보종목") == []


def test_extract_stock_codes_prefers_explicit_line():
    txt = "분석 중 005930 언급...\n대상종목: 000660, 035720\n그 외 잡담"
    assert ms._extract_stock_codes(txt) == ["000660", "035720"]


def test_has_label():
    assert ms._has_label("최종승인: 005930", "최종승인") is True
    assert ms._has_label("그런 라벨 없음", "최종승인") is False
