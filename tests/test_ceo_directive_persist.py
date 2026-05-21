"""사장 지시 자동 저장 (체크박스 제거 → 에이전트가 결과로 자동 판단 — 사장 지시 2026-05-21).

지시가 '지속 운영 원칙'으로 분류되면 활성 계정의 standing_directive 로 자동 저장하고,
'일회성/질문'이면 저장하지 않는다. _auto_persist_directive 를 스텁 self 로 단위 검증한다
(무거운 ArquantOrchestrator 전체 생성 없이 — 메서드는 self._active_actor 만 사용).
"""
import asyncio
import pytest

import main_swarm as ms


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    import infra.standing_directives as sd
    monkeypatch.setattr(sd, "_PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr(sd, "_DATA_DIR", tmp_path)

    async def _bnoop(*a, **k):
        return None
    monkeypatch.setattr(ms, "_broadcast", _bnoop)
    return sd


class _Stub:
    def __init__(self, uid):
        self._uid = uid

    def _active_actor(self):
        return (self._uid, False)


def _patch_classifier(monkeypatch, verdict: bool):
    async def _clf(message, response):
        return verdict
    monkeypatch.setattr(ms, "_llm_is_standing_directive", _clf)


def _run_persist(uid, message, response):
    return asyncio.run(ms.ArquantOrchestrator._auto_persist_directive(_Stub(uid), message, response))


def test_saves_when_classified_standing(isolated, monkeypatch):
    sd = isolated
    _patch_classifier(monkeypatch, True)
    _run_persist(42, "원화 자산 비중을 최소화하라", "네, 매 운용에 반영하겠습니다")
    lst = sd.load(42)
    assert len(lst) == 1 and lst[0]["text"] == "원화 자산 비중을 최소화하라"


def test_not_saved_when_classified_oneshot(isolated, monkeypatch):
    sd = isolated
    _patch_classifier(monkeypatch, False)
    _run_persist(42, "지금 삼성전자 얼마야?", "현재가는 ...")
    assert sd.load(42) == []


def test_strips_leading_mention_before_save(isolated, monkeypatch):
    sd = isolated
    _patch_classifier(monkeypatch, True)
    _run_persist(7, "@운용전략실장 달러 단기국채를 핵심 축으로", "반영하겠습니다")
    lst = sd.load(7)
    assert len(lst) == 1 and lst[0]["text"] == "달러 단기국채를 핵심 축으로"


def test_error_response_not_saved(isolated, monkeypatch):
    """에이전트 에러 응답('[...에러]')이면 판단 보류 — 저장하지 않는다."""
    sd = isolated
    _patch_classifier(monkeypatch, True)   # 분류기가 STANDING 이라 해도
    _run_persist(7, "원화 비중 최소화", "[운용전략실장 에러] API 호출 실패")
    assert sd.load(7) == []


def test_no_active_account_no_save(isolated, monkeypatch):
    sd = isolated
    _patch_classifier(monkeypatch, True)
    _run_persist(None, "원화 비중 최소화", "반영")
    # uid None → 저장 대상 없음 (load(None) 도 빈 목록)
    assert sd.load(1) == [] and sd.load(0) == []
