"""슬리브 보유기간 self-thesis 저장소 — per-uid·per-sleeve (사장 지시 2026-06-09).

bond_thesis 를 sleeve_key 인자로 일반화. 경로 data/<uid>/<sleeve_key>_thesis.json
(sleeve_key="bond" → bond_thesis.json 으로 기존 라이브 파일과 동일 — 무손실)."""
import infra.sleeve_thesis as st
from infra import user_paths


def _patch(monkeypatch, tmp_path):
    monkeypatch.setattr(user_paths, "sleeve_thesis_path",
                        lambda uid, key: tmp_path / f"{key}_thesis_{uid}.json")
    st._reset_cache_for_tests()


def test_record_and_get_per_sleeve(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    st.record(99, "bond", "148070", {"entry_price": 10000, "planned_hold_hours": 168})
    st.record(99, "commodity", "GLD", {"entry_price": 200, "planned_hold_hours": 72})
    assert "148070" in st.get_all(99, "bond")
    assert "GLD" in st.get_all(99, "commodity")
    # 슬리브 격리 — 채권 thesis 에 원자재 코드 없음, 그 반대도
    assert "GLD" not in st.get_all(99, "bond")
    assert "148070" not in st.get_all(99, "commodity")


def test_get_and_remove(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    st.record(99, "bond", "TLT", {"entry_price": 90.0})
    assert st.get(99, "bond", "TLT")["entry_price"] == 90.0
    st.remove(99, "bond", "TLT")
    assert st.get(99, "bond", "TLT") is None


def test_sync_removes_unheld(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    st.record(99, "bond", "148070", {"entry_price": 10000})
    st.record(99, "bond", "114260", {"entry_price": 10000})
    removed = st.sync_with_holdings(99, "bond", ["148070"])
    assert removed == ["114260"]
    assert set(st.get_all(99, "bond")) == {"148070"}


def test_sync_isolated_per_sleeve(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    st.record(99, "bond", "148070", {"entry_price": 10000})
    st.record(99, "commodity", "GLD", {"entry_price": 200})
    # 채권 동기화는 원자재 thesis 를 건드리지 않는다
    st.sync_with_holdings(99, "bond", [])
    assert st.get_all(99, "bond") == {}
    assert "GLD" in st.get_all(99, "commodity")
