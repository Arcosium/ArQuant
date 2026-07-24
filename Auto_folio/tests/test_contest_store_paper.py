"""자격증명 없는 페이퍼 등록 + auto_cycle 플래그 (사장 지시 2026-07-03)."""
from Auto_folio.autofolio import contest_store


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(contest_store, "_STORE_PATH", tmp_path / "contest_state.json")
    monkeypatch.setattr(contest_store, "_DATA_DIR", tmp_path)


def test_register_paper_without_credentials(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    acc = contest_store.register(1, initial_cash=1_000_000)
    assert acc["contest_id"] == "paper"
    assert acc["has_site_credentials"] is False
    assert acc["portfolio"]["cash"] == 1_000_000


def test_register_with_password_requires_contest_id(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    try:
        contest_store.register(1, "", "pass123456!")
        assert False, "contest_id 없이 password 만 주면 거절해야 한다"
    except ValueError:
        pass


def test_relax_sector_allows_buy_without_sector(tmp_path, monkeypatch):
    """ArcTrade 러너 완화 모드: 섹터 데이터 부재는 경고로만 — 나머지 룰은 그대로 검증."""
    from Auto_folio.autofolio import contest_rules
    _isolate(tmp_path, monkeypatch)
    contest_store.register(1, initial_cash=100_000_000)
    meta = {"name": "삼성전자", "market": "KOSPI", "is_common_stock": True,
            "listed_business_days": 3000, "avg_5d_trading_value_krw": 1e12,
            "market_cap_krw": 4e14, "last_price": 70000}  # sector 없음
    monkeypatch.setattr(contest_rules, "_RELAX_SECTOR", False)
    strict = contest_store.check_order(1, "buy", "005930", 10, 70000, meta=meta)
    assert not strict["ok"] and any(v["code"] == "sector_missing" for v in strict["violations"])
    monkeypatch.setattr(contest_rules, "_RELAX_SECTOR", True)
    relaxed = contest_store.check_order(1, "buy", "005930", 10, 70000, meta=meta)
    assert relaxed["ok"] and relaxed["warnings"]


def test_auto_cycle_flag_roundtrip(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    contest_store.register(1)
    contest_store.register(2)
    assert contest_store.list_auto_cycle_uids() == []
    acc = contest_store.set_auto_cycle(1, True)
    assert acc["auto_cycle"] is True
    assert contest_store.list_auto_cycle_uids() == [1]
    contest_store.set_auto_cycle(1, False)
    assert contest_store.list_auto_cycle_uids() == []
