"""사장 지시 2026-06-04 ④: /api/scorecard 헬퍼 — 신호·체결·자산·후속가격을 조인해 성과 카드 반환.
(라우트는 _scorecard_for_uid 호출 + 인증 게이트. 인증 미들웨어 우회 위해 헬퍼를 직접 검증 — 레포 관례)"""


def test_scorecard_for_uid_assembles(monkeypatch, tmp_path):
    import server.app as app_mod
    import infra.scorecard_store as ss
    import tools.market_data as md
    import main_swarm

    # 신호: 퀀트점수·뉴스감성이 후속수익과 양의 상관
    monkeypatch.setattr(ss, "list_signals", lambda uid, limit=5000: [
        {"code": "A", "ts": "2026-06-01 10:00:00", "quant_score": 8, "news_sentiment": 0.7},
        {"code": "B", "ts": "2026-06-01 10:00:00", "quant_score": 3, "news_sentiment": -0.4},
        {"code": "C", "ts": "2026-06-01 10:00:00", "quant_score": 6, "news_sentiment": 0.1}])
    fwd = {"A": 0.05, "B": -0.03, "C": 0.01}
    monkeypatch.setattr(md, "forward_return_after", lambda code, ts, window_days=30: fwd.get(code))
    monkeypatch.setattr(main_swarm, "get_equity_series", lambda *a, **k: [])

    class _FakeSwarm:
        equity_path = str(tmp_path / "eq.json")

    class _FakeCtx:
        swarm = _FakeSwarm()
    monkeypatch.setattr(app_mod.REGISTRY, "get_or_create", lambda uid: _FakeCtx())

    card = app_mod._scorecard_for_uid(1)
    assert card["quant"]["ic"] is not None and card["quant"]["n"] == 3
    assert card["news"]["n"] == 3
    assert card["signal_count"] == 3
    # 후속수익 미산정(price_lookup None) 종목은 표본에서 제외됨을 보장
    assert isinstance(card["slippage"], dict) and "n" in card["slippage"]


def test_scorecard_for_uid_empty_safe(monkeypatch, tmp_path):
    """신호 0건이어도 안전하게 빈 카드(ic=None, n=0) 반환."""
    import server.app as app_mod
    import infra.scorecard_store as ss
    import main_swarm
    monkeypatch.setattr(ss, "list_signals", lambda uid, limit=5000: [])
    monkeypatch.setattr(main_swarm, "get_equity_series", lambda *a, **k: [])

    class _FakeCtx:
        class swarm:
            equity_path = str(tmp_path / "eq.json")
    monkeypatch.setattr(app_mod.REGISTRY, "get_or_create", lambda uid: _FakeCtx())
    card = app_mod._scorecard_for_uid(1)
    assert card["quant"]["ic"] is None and card["quant"]["n"] == 0
    assert card["signal_count"] == 0
