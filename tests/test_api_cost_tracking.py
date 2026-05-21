"""API 비용 추적 — 영속 롤업(일/월/누적) + 프로필별 표시 모드.

사장 지시 2026-05-21: 우상단 API 비용을 프로필에서 시간(/h)·일(/d)·월(/m)·총누적 중
하나로 선택해 표시. /h 는 회전식(인메모리), /d·/m·total 은 재시작 생존(롤업 파일).
"""
import json
import time
from datetime import datetime, timezone, timedelta

import pytest

KST = timezone(timedelta(hours=9))


@pytest.fixture
def cost(tmp_path, monkeypatch):
    import agents.base_agent as ba
    monkeypatch.setattr(ba, "_COST_ROLLUP_PATH", tmp_path / "api_cost_rollup.json")
    ba.reset_api_cost_log()
    # 롤업 인메모리 캐시도 초기화
    if hasattr(ba, "_reset_cost_rollup"):
        ba._reset_cost_rollup()
    return ba


def test_record_updates_total_and_buckets(cost):
    ba = cost
    ba._record_api_call("deepseek/deepseek-v4-flash", "tester", 1_000_000, 1_000_000)
    # flash 단가 (0.10 in, 0.30 out) → 0.10 + 0.30 = 0.40 USD
    s = ba.cost_summary()
    assert s["total"]["usd"] == pytest.approx(0.40, abs=1e-6)
    assert s["total"]["calls"] == 1
    assert s["d"]["usd"] == pytest.approx(0.40, abs=1e-6)
    assert s["m"]["usd"] == pytest.approx(0.40, abs=1e-6)
    assert s["h"]["calls"] == 1


def test_rollup_persists_across_reload(cost, tmp_path, monkeypatch):
    ba = cost
    ba._record_api_call("deepseek/deepseek-v4-pro", "tester", 1_000_000, 0)  # 0.50 in
    # 인메모리 캐시를 비우고 파일에서 다시 읽어도 누적/일/월은 유지돼야 한다
    if hasattr(ba, "_reset_cost_rollup"):
        ba._reset_cost_rollup()
    ba.reset_api_cost_log()  # 회전식(/h) 만 비움
    s = ba.cost_summary()
    assert s["total"]["usd"] == pytest.approx(0.50, abs=1e-6)
    assert s["total"]["calls"] == 1
    # /h 는 회전식이라 리셋 후 0
    assert s["h"]["calls"] == 0


def test_summary_has_all_modes(cost):
    ba = cost
    s = ba.cost_summary()
    for k in ("h", "d", "m", "total"):
        assert k in s
        assert "usd" in s[k] and "calls" in s[k]


# ── 프로필별 표시 모드 ─────────────────────────────────────────────────────────

@pytest.fixture
def rt(tmp_path, monkeypatch):
    import runtime as r
    monkeypatch.setattr(r, "_COST_MODE_FILE", tmp_path / "api_cost_mode.json")
    monkeypatch.setattr(r, "_cost_mode", {"_default": {"mode": "h"}}, raising=False)
    return r


def test_cost_mode_default(rt):
    assert rt.cost_display_mode() == "h"
    assert rt.cost_display_mode(uid=123) == "h"   # 미설정 → 기본


def test_cost_mode_set_per_profile(rt):
    rt.set_cost_display_mode("m", uid=7)
    assert rt.cost_display_mode(uid=7) == "m"
    assert rt.cost_display_mode(uid=8) == "h"      # 다른 프로필은 영향 없음


def test_cost_mode_rejects_invalid(rt):
    with pytest.raises(ValueError):
        rt.set_cost_display_mode("year", uid=7)


def test_cost_mode_persists(rt, tmp_path):
    rt.set_cost_display_mode("total", uid=9)
    # 파일에 저장됐는지
    data = json.loads((tmp_path / "api_cost_mode.json").read_text(encoding="utf-8"))
    assert data["9"]["mode"] == "total"


# ── 엔드포인트 스모크 (/api/cost_mode · /api/status api_cost) ───────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    import infra.auth_store as a
    for n, v in [("_DATA_DIR", tmp_path), ("_DB_PATH", tmp_path / "auth.db"),
                 ("_FERNET_KEY_PATH", tmp_path / ".fernet.key"),
                 ("_AUDIT_PATH", tmp_path / "audit.log"),
                 ("_INITED", False), ("_FERNET", None),
                 ("_FERNET_RAW", None), ("_BIDX_KEY", None)]:
        monkeypatch.setattr(a, n, v, raising=False)
    a.init()
    uid = a.upsert_user("u1", "OldPassw0rd!!", "AK", "AS", "OR", "5012345601",
                        "https://openapi.koreainvestment.com:9443")
    tok = a.create_session(uid)
    import runtime as r
    monkeypatch.setattr(r, "_COST_MODE_FILE", tmp_path / "api_cost_mode.json")
    monkeypatch.setattr(r, "_cost_mode", {"_default": {"mode": "h"}}, raising=False)
    from fastapi.testclient import TestClient
    import server.app as app_mod
    c = TestClient(app_mod.app)
    c.headers.update({"X-Session": tok})
    return c, r, uid


def test_cost_mode_endpoint_sets_and_status_reflects(client):
    c, r, uid = client
    assert c.post("/api/cost_mode", json={"mode": "m"}).status_code == 200
    assert r.cost_display_mode(uid) == "m"
    body = c.get("/api/status").json()
    assert body["api_cost"]["mode"] == "m"
    for k in ("h", "d", "m", "total"):
        assert k in body["api_cost"]


def test_cost_mode_endpoint_rejects_invalid(client):
    c, r, uid = client
    assert c.post("/api/cost_mode", json={"mode": "year"}).status_code == 400
