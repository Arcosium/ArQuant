"""사장 지시 2026-06-04: 재시작 시 인메모리 대기(pending) 뉴스풀이 비고, crawl_once 는 영속
seen_links 와 대조해 '이미 본' 최근 기사를 다시 안 담는다 → 재시작 직후 첫 사이클이 '뉴스 0'
sell-only 로 눈먼다(news_history 엔 최근 뉴스가 멀쩡히 있는데도). 이를 막기 위해 재시작 후
대기풀이 비면 최근 history(기본 90분 이내 크롤)를 시드한다.

2026-06-04 뉴스 풀 단일화: KR/US 구분 폐지 → seed_pending_news 는 단일 리스트 반환(시장 분기 없음).
spec: docs/superpowers/specs/2026-06-04-unified-news-pool-design.md
"""
from main_swarm import seed_pending_news

NOW = "2026-06-04 14:35:00"


def _a(title, mins_ago, link=None):
    from datetime import datetime, timedelta
    t = datetime.strptime(NOW, "%Y-%m-%d %H:%M:%S") - timedelta(minutes=mins_ago)
    return {"title": title, "link": link or title, "crawled_at": t.strftime("%Y-%m-%d %H:%M:%S")}


def test_recent_articles_seeded_single_list():
    out = seed_pending_news([_a("삼성", 30), _a("AAPL", 10)], NOW)
    assert [a["title"] for a in out] == ["삼성", "AAPL"]


def test_market_field_ignored():
    # market 필드가 있든 없든 상관없이 전부 단일 리스트로 시드(시장 분기 폐지)
    arts = [{"title": "X", "market": "KR", "link": "x", "crawled_at": "2026-06-04 14:20:00"},
            {"title": "Y", "market": "US", "link": "y", "crawled_at": "2026-06-04 14:20:00"}]
    out = seed_pending_news(arts, NOW)
    assert {a["title"] for a in out} == {"X", "Y"}


def test_stale_article_excluded_by_window():
    out = seed_pending_news([_a("오래된", 200)], NOW)
    assert out == []


def test_window_param_respected():
    out = seed_pending_news([_a("최근", 30), _a("좀된", 80)], NOW, window_min=45)
    assert [a["title"] for a in out] == ["최근"]


def test_missing_crawled_at_excluded():
    out = seed_pending_news([{"title": "x", "link": "x"}], NOW)
    assert out == []


def test_empty_input():
    assert seed_pending_news([], NOW) == []
