"""주간 검증 큐: cycle 회부 → 토요일 재평가 (사장 지시 2026-06-09 #7)."""
import infra.weekly_defer_queue as q


def _patch(monkeypatch, tmp_path):
    monkeypatch.setattr(q, "_path", lambda uid: tmp_path / f"weekly_deferred_{uid}.json")


def test_enqueue_list_clear_roundtrip(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path)
    q.enqueue(7, "QIW_RSI", 3, "추세 강화 근거")
    q.enqueue(7, "MAX_BUY_NAMES", 5, "집중 강화")
    items = q.list_pending(7)
    assert {it["key"] for it in items} == {"QIW_RSI", "MAX_BUY_NAMES"}
    q.clear(7)
    assert q.list_pending(7) == []


def test_enqueue_same_key_overwrites(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path)
    q.enqueue(7, "QIW_RSI", 3, "v1")
    q.enqueue(7, "QIW_RSI", 9, "v2")
    items = q.list_pending(7)
    assert len(items) == 1 and items[0]["value"] == 9


def test_summary_includes_deferred(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path)
    q.enqueue(7, "DW_QUANT", 70, "퀀트 비중↑")
    import infra.weekly_review as wr
    # 백테스트/사이클 의존부는 _list_deferred 만 검증(요점: summary 에 회부 제안이 실린다)
    assert wr._list_deferred(7) == q.list_pending(7)
    assert wr._list_deferred(7)[0]["key"] == "DW_QUANT"
