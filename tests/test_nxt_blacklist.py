"""NXT 거래불가 종목 공유 블랙리스트 (사장 지시 2026-06-11).

KIS 가 'NXT 상장종목인지 확인하세요'로 거부한 종목을 영속 기록 → 이후 전 계정이
시간외(NXT) 세션에서 매수 후보·주문 시도 자체를 건너뛴다. 153130 매도가 4사이클
연속 같은 사유로 거부된 사례가 계기.
"""
import asyncio
import types

import pytest

from infra import nxt_blacklist as nb


@pytest.fixture(autouse=True)
def _tmp_blacklist(tmp_path, monkeypatch):
    monkeypatch.setattr(nb, "_PATH", tmp_path / "nxt_untradable.json")
    monkeypatch.setattr(nb, "_cache_mtime", None)
    monkeypatch.setattr(nb, "_cache_data", {})


def test_looks_nxt_unsupported_matches_live_reject_message():
    assert nb.looks_nxt_unsupported("[실패] 해당 종목정보가 없습니다. NXT 상장종목인지 확인하세요")
    assert not nb.looks_nxt_unsupported("주문 전송 완료 되었습니다")
    assert not nb.looks_nxt_unsupported("")


def test_record_and_is_blocked_persist():
    assert not nb.is_blocked("153130")
    assert nb.record("153130", note="x") is True       # 신규 등록
    assert nb.is_blocked("153130")
    assert nb.record("153130", note="y") is False      # 중복 → 카운트만
    assert nb.all_blocked()["153130"]["count"] == 2


def test_finalize_skips_blacklisted_ticker_in_extended_hours(monkeypatch):
    """시간외(NXT) 세션 + 블랙리스트 종목 → 주문 시도 전에 보류된다 (조용한 누락 아님 — 사유 반환)."""
    import main_swarm
    from main_swarm import ArquantOrchestrator
    nb.record("153130", note="NXT 미상장")
    o = object.__new__(ArquantOrchestrator)
    o.uid = 1
    o.broker = types.SimpleNamespace()  # 시세 조회 전에 가로막혀야 하므로 메서드 불필요
    od = types.SimpleNamespace(market="KR", ticker="153130", side="sell")
    od2, skip = asyncio.run(o._finalize_kr_order_for_session(od, "KR_AFTER_MARKET"))
    assert skip and "NXT 거래불가" in skip
    # 정규장(KRX)은 영향 없음
    od3 = types.SimpleNamespace(market="KR", ticker="153130", side="sell")
    _, skip_regular = asyncio.run(o._finalize_kr_order_for_session(od3, "KR_TRADING"))
    assert skip_regular is None
