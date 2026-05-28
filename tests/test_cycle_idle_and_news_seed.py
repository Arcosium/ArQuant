"""C: 뉴스 신호 있는데 후보 0 → 뉴스 괄호표기 종목으로 보강 (낭비 방지).
   D: 뉴스0·미개장·보유0 = 매수·매도 둘 다 불가 → 사이클 경량 종료(비용 절감).

배경(2026-05-28 로그 리뷰):
 - uid2 cycle 24: 뉴스 51건(MU +0.65, NVDA/AMD +0.60)인데 candidate_codes=[] → 퀀트 0 →
   거래 0. '신호 있는데 거래 안 함'. 후보 해석이 0건이면 뉴스 괄호표기 종목으로 보강한다
   (보강 후보도 downstream 퀀트≥6·DART·리스크 게이트를 통과해야 매수 — 과매수 위험 낮음).
 - uid1 심야: 뉴스0인데 풀 LLM 스웜 비용. 보유까지 없으면 매수·매도 둘 다 불가라 분석이 무의미.
"""
from main_swarm import seed_candidates_from_news, cycle_is_idle


# ── C: seed_candidates_from_news ──────────────────────────────────────────
def test_seed_extracts_us_tickers_from_parenthetical_mentions():
    news = ("① 직접 영향 종목\n- 마이크론(MU): 감성 +0.65\n"
            "- 엔비디아(NVDA), AMD 등 동반 수혜\n- 비트코인/가상자산")
    out = seed_candidates_from_news(news, "US_TRADING")
    assert "MU" in out and "NVDA" in out, "괄호표기 US 티커를 후보로 보강해야 한다"


def test_seed_session_filter_kr_keeps_codes_drops_us():
    news = "삼성전자(005930) 호재, SK하이닉스(000660) 강세, 마이크론(MU) 수혜"
    out = seed_candidates_from_news(news, "KR_PRE_MARKET")
    assert out == ["005930", "000660"], "KR 세션은 6자리 코드만, US 티커는 제외"


def test_seed_filters_common_nonticker_stopwords():
    news = "AI(AI) 테마 강세, 팔란티어(PLTR) 부각, ETF(ETF) 자금유입"
    out = seed_candidates_from_news(news, "US_TRADING")
    assert "PLTR" in out
    assert "AI" not in out and "ETF" not in out, "AI·ETF 등 비종목 약어는 제외"


def test_seed_empty_when_no_tickers():
    assert seed_candidates_from_news("신규 뉴스 없음 — 분석 생략", "US_TRADING") == []


def test_seed_dedups_and_caps_at_five():
    news = "(MU) (MU) (NVDA) (AMD) (TSM) (AAPL) (META) (GOOG)"
    out = seed_candidates_from_news(news, "US_TRADING")
    assert len(out) <= 5
    assert out.count("MU") == 1


# ── D: cycle_is_idle ──────────────────────────────────────────────────────
def test_idle_when_sell_only_and_no_holdings():
    assert cycle_is_idle(sell_only=True, holdings=[]) is True


def test_not_idle_when_holdings_present():
    assert cycle_is_idle(sell_only=True, holdings=[{"code": "NU", "qty": 1}]) is False


def test_not_idle_when_news_present():
    # sell_only=False = 뉴스 있음(매수 후보 가능) → 분석 진행해야 한다
    assert cycle_is_idle(sell_only=False, holdings=[]) is False
