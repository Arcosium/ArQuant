"""뉴스 분류 폐루프 결정론 진단기 — 권고가 데이터 불일치를 정확히 잡는지 고정."""
from infra import news_weight_tuner as t


def test_empty_stats_is_data_shortage():
    a = t.analyze({})
    assert a["ok"] is False
    assert a["verdict"] == "데이터 부족"


def test_small_sample_is_data_shortage():
    a = t.analyze({"cycles": 1, "headlines_kr": 2})
    assert a["ok"] is False
    assert "표본 부족" in a["findings"][0]


def test_us_overclassified_flagged():
    a = t.analyze({"cycles": 10, "headlines_kr": 70, "headlines_us": 30,
                   "headlines_both": 0, "candidates_us": 0, "bought_us": 0,
                   "candidates_kr": 8, "bought_kr": 4})
    assert a["ok"] is True
    assert a["verdict"] == "조정 권고"
    assert any("US 헤드라인 비중" in f for f in a["findings"])


def test_balanced_distribution_needs_no_change():
    a = t.analyze({"cycles": 10, "headlines_kr": 60, "headlines_us": 30,
                   "headlines_both": 10, "candidates_kr": 6, "candidates_us": 4,
                   "bought_kr": 3, "bought_us": 2})
    assert a["verdict"] == "균형"
    assert any("불필요" in f for f in a["findings"])


def test_candidates_but_no_buys_points_at_sizing():
    a = t.analyze({"cycles": 8, "headlines_kr": 50, "headlines_us": 5,
                   "headlines_both": 5, "candidates_kr": 12, "candidates_us": 0,
                   "bought_kr": 0, "bought_us": 0})
    assert any("사이징/리스크 게이트" in f for f in a["findings"])


def test_summary_line_is_single_string():
    line = t.summary_line({"cycles": 10, "headlines_kr": 70, "headlines_us": 30,
                           "headlines_both": 0})
    assert isinstance(line, str) and line.startswith("[뉴스분류 진단")
