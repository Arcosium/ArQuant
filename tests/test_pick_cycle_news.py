"""사장 지시 2026-06-04: 뉴스 풀 단일화 — 사이클마다 풀 전체 소비 후 비움.
풀이 비면 최신 20개(history)로 폴백해 뉴스 없이 헛도는 사이클 방지.
spec: docs/superpowers/specs/2026-06-04-unified-news-pool-design.md
"""
from main_swarm import pick_cycle_news


def test_pool_nonempty_used_as_is():
    pending = [{"title": "a"}, {"title": "b"}]
    news, used_fb = pick_cycle_news(pending, [{"title": "z"}])
    assert [n["title"] for n in news] == ["a", "b"]
    assert used_fb is False


def test_empty_pool_falls_back_to_recent_20():
    recent = [{"title": f"r{i}"} for i in range(30)]
    news, used_fb = pick_cycle_news([], recent, fallback_n=20)
    assert len(news) == 20 and news[0]["title"] == "r0"
    assert used_fb is True


def test_both_empty_returns_empty():
    news, used_fb = pick_cycle_news([], [])
    assert news == [] and used_fb is True


def test_fallback_respects_n():
    recent = [{"title": f"r{i}"} for i in range(10)]
    news, used_fb = pick_cycle_news([], recent, fallback_n=5)
    assert len(news) == 5 and used_fb is True
