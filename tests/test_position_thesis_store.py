"""투자 thesis 영속 저장소 — per-uid data/<uid>/position_thesis.json.

배경 (2026-05-28 사장 지시):
  현재 사이클이 매 cycle 보유 종목을 처음 보듯 매도/보유를 재평가 → 단타 편중.
  매수 시점에 '왜 샀고 어디까지 들고 갈 계획인지' 를 펀드기획팀장이 박아두고,
  사후관리실장이 매도 판단 직전 상기시킨다(우선순위 3, 사장 확정 2026-05-28).

요구 동작:
  - record(uid, code, thesis_dict): 기존이 있으면 덮어씀(같은 종목 재매수 시 새 thesis).
  - get(uid, code): None 또는 thesis dict.
  - get_all(uid): {code: thesis} 전체.
  - remove(uid, code): 멱등 — 없어도 에러 X.
  - 파일 영속 (data/<uid>/position_thesis.json) — atomic write (임시파일 + rename).
  - thesis dict 필수 키: entry_ts, entry_price, target_price, stop_price,
    planned_hold_hours, entry_reason, source_agent.
"""
import json
import tempfile
from pathlib import Path
import pytest

import infra.position_thesis as ps


@pytest.fixture
def tmp_data(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        from infra import user_paths
        monkeypatch.setattr(user_paths, "_DATA_DIR", Path(d))
        ps._reset_cache_for_tests()       # 테스트간 in-process 캐시 격리
        yield Path(d)
        ps._reset_cache_for_tests()


def _sample_thesis(**overrides):
    base = {
        "entry_ts": "2026-05-28 14:09:14",
        "entry_price": 655500.0,
        "target_price": 700000.0,
        "stop_price": 620000.0,
        "planned_hold_hours": 48,
        "entry_reason": "운용전략실장 2차전지 비중 확대 권고",
        "source_agent": "펀드기획팀장",
    }
    base.update(overrides)
    return base


def test_record_then_get_roundtrip(tmp_data):
    ps.record(uid=1, code="006400", thesis=_sample_thesis())
    got = ps.get(uid=1, code="006400")
    assert got is not None
    assert got["entry_price"] == 655500.0
    assert got["target_price"] == 700000.0
    assert got["planned_hold_hours"] == 48
    assert got["source_agent"] == "펀드기획팀장"


def test_get_missing_returns_none(tmp_data):
    assert ps.get(uid=1, code="999999") is None


def test_get_all_returns_dict_keyed_by_code(tmp_data):
    ps.record(uid=1, code="006400", thesis=_sample_thesis(entry_price=655500.0))
    ps.record(uid=1, code="003490", thesis=_sample_thesis(entry_price=27050.0))
    all_t = ps.get_all(uid=1)
    assert set(all_t.keys()) == {"006400", "003490"}
    assert all_t["006400"]["entry_price"] == 655500.0
    assert all_t["003490"]["entry_price"] == 27050.0


def test_record_overwrites_existing_on_repurchase(tmp_data):
    """같은 종목 재매수 → 새 thesis 가 덮어씀 (이전 thesis 흔적은 안 남김)."""
    ps.record(uid=1, code="006400", thesis=_sample_thesis(entry_price=655500.0,
                                                            entry_ts="2026-05-28 14:09"))
    ps.record(uid=1, code="006400", thesis=_sample_thesis(entry_price=678000.0,
                                                            entry_ts="2026-05-29 10:00"))
    got = ps.get(uid=1, code="006400")
    assert got["entry_price"] == 678000.0
    assert got["entry_ts"] == "2026-05-29 10:00"


def test_remove_is_idempotent(tmp_data):
    ps.record(uid=1, code="006400", thesis=_sample_thesis())
    ps.remove(uid=1, code="006400")
    assert ps.get(uid=1, code="006400") is None
    # 한 번 더 — 에러 없어야
    ps.remove(uid=1, code="006400")
    ps.remove(uid=1, code="000000")
    assert ps.get(uid=1, code="006400") is None


def test_uid_isolation(tmp_data):
    """uid 별로 완전 분리."""
    ps.record(uid=1, code="006400", thesis=_sample_thesis(entry_price=655500.0))
    ps.record(uid=2, code="006400", thesis=_sample_thesis(entry_price=666000.0))
    assert ps.get(uid=1, code="006400")["entry_price"] == 655500.0
    assert ps.get(uid=2, code="006400")["entry_price"] == 666000.0
    ps.remove(uid=1, code="006400")
    assert ps.get(uid=1, code="006400") is None
    assert ps.get(uid=2, code="006400") is not None      # uid=2는 영향 없어야


def test_persists_to_disk_atomically(tmp_data):
    """기록은 디스크에 영속되어야 (프로세스 재시작 가정)."""
    ps.record(uid=1, code="006400", thesis=_sample_thesis())
    # 캐시 무효화 후 디스크에서 다시 읽기
    ps._reset_cache_for_tests()
    assert ps.get(uid=1, code="006400")["entry_price"] == 655500.0
    # 파일이 실제로 존재
    p = tmp_data / "1" / "position_thesis.json"
    assert p.exists()
    d = json.loads(p.read_text(encoding="utf-8"))
    assert "006400" in d


def test_record_normalizes_code_to_string(tmp_data):
    """code 가 숫자로 들어와도 문자열 키로 저장(JSON 호환)."""
    ps.record(uid=1, code=6400, thesis=_sample_thesis())
    # 둘 다 같은 thesis 가져와야
    assert ps.get(uid=1, code=6400) is not None
    assert ps.get(uid=1, code="6400") is not None


def test_get_all_empty_returns_empty_dict(tmp_data):
    assert ps.get_all(uid=99) == {}


def test_sync_with_holdings_removes_sold_codes(tmp_data):
    """전량 매도된 종목의 thesis 는 자동 제거 (현재 보유 목록과 동기화)."""
    ps.record(uid=1, code="006400", thesis=_sample_thesis())
    ps.record(uid=1, code="003490", thesis=_sample_thesis())
    ps.record(uid=1, code="005380", thesis=_sample_thesis())
    # 사이클 종료 후 보유 종목 = 006400 만 — 나머지 매도된 셈
    removed = ps.sync_with_holdings(uid=1, current_codes=["006400"])
    assert set(removed) == {"003490", "005380"}, f"매도된 thesis 가 제거돼야: {removed}"
    rem = ps.get_all(uid=1)
    assert set(rem.keys()) == {"006400"}


def test_sync_with_holdings_keeps_all_when_all_held(tmp_data):
    ps.record(uid=1, code="006400", thesis=_sample_thesis())
    ps.record(uid=1, code="003490", thesis=_sample_thesis())
    removed = ps.sync_with_holdings(uid=1, current_codes=["006400", "003490"])
    assert removed == []
    assert set(ps.get_all(uid=1).keys()) == {"006400", "003490"}


def test_sync_with_holdings_with_empty_current_removes_all(tmp_data):
    """현재 보유 0건 → 모든 thesis 제거 (전량 청산 시나리오)."""
    ps.record(uid=1, code="006400", thesis=_sample_thesis())
    ps.record(uid=1, code="003490", thesis=_sample_thesis())
    removed = ps.sync_with_holdings(uid=1, current_codes=[])
    assert set(removed) == {"006400", "003490"}
    assert ps.get_all(uid=1) == {}
