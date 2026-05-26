"""_resolve_candidate_codes — 운용전략실장의 후보 코드 환각을 이름→코드 검색으로 보정.

버그 2026-05-22: 운용전략실장이 종목명은 알아도 코드를 몰라 후보종목으로 123456~567890
같은 가짜 코드를 냈고, 계량분석이 '일봉 데이터 없음 → 후보 0개'로 사이클이 통째 무산됐다.
종목명(코드) 형식에서 코드가 무효면 종목명으로 정확한 코드를 검색해 채운다.
"""
from main_swarm import _resolve_candidate_codes

_DB = {"클래시스": "214150", "에코프로비엠": "247540", "HPSP": "403870"}
_VALID = {"005930": "삼성전자", "000660": "SK하이닉스"}


def _resolver(name):
    return _DB.get(name, "")


def _namecheck(code):
    return _VALID.get(code, "")


def test_resolves_name_when_code_hallucinated():
    line = "후보종목: 클래시스(123456), 에코프로비엠(234567), 삼성전자(005930)"
    assert _resolve_candidate_codes(line, session="KR_TRADING", resolver=_resolver, name_check=_namecheck) \
        == ["214150", "247540", "005930"]


def test_bare_names_including_english_kr_name():
    # KR 세션에선 'HPSP'(영문 종목명)도 US 티커가 아니라 코드로 해석
    assert _resolve_candidate_codes("후보종목: 클래시스, HPSP", session="KR_TRADING",
                                    resolver=_resolver, name_check=_namecheck) == ["214150", "403870"]


def test_us_ticker_passthrough_in_us_session():
    assert _resolve_candidate_codes("후보종목: AAPL, MSFT", session="US_TRADING",
                                    resolver=_resolver, name_check=_namecheck) == ["AAPL", "MSFT"]


def test_invalid_code_without_name_dropped():
    # 무효 코드 + 이름 없음 → 보정 불가 → 버림(환각 통과 금지)
    assert _resolve_candidate_codes("후보종목: 999999, 삼성전자(005930)", session="KR_TRADING",
                                    resolver=_resolver, name_check=_namecheck) == ["005930"]


def test_valid_code_kept():
    assert _resolve_candidate_codes("후보종목: 삼성전자(005930)", session="KR_TRADING",
                                    resolver=_resolver, name_check=_namecheck) == ["005930"]


def test_no_label_empty():
    assert _resolve_candidate_codes("아무 텍스트", resolver=_resolver, name_check=_namecheck) == []
