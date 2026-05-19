"""ITEM6 회귀 테스트: 계정별 상시 지시사항 영속성·격리·주입.

- 지시사항이 uid별로 저장되고 uid가 다른 계정에는 보이지 않는다
- 운용전략실장 컨텍스트 블록에 지시사항이 포함된다
- 중복 추가는 멱등 처리된다
"""
import pytest
from pathlib import Path


@pytest.fixture
def isolated_profiles(tmp_path, monkeypatch):
    """data/profiles 를 tmp_path로 격리."""
    import infra.standing_directives as sd
    monkeypatch.setattr(sd, "_PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr(sd, "_DATA_DIR", tmp_path)
    return sd


def test_append_and_load(isolated_profiles):
    sd = isolated_profiles
    uid = 42
    sd.append_directive(uid, "달러 자산 우선 편입")
    directives = sd.load(uid)
    assert len(directives) == 1
    assert "달러 자산 우선 편입" in directives[0]["text"]


def test_isolation_between_uids(isolated_profiles):
    """uid A의 지시가 uid B에는 보이지 않아야 한다."""
    sd = isolated_profiles
    uid_a, uid_b = 1, 2
    sd.append_directive(uid_a, "A 계정 전용 지시")
    sd.append_directive(uid_b, "B 계정 전용 지시")

    directives_a = sd.load(uid_a)
    directives_b = sd.load(uid_b)

    assert len(directives_a) == 1
    assert "A 계정" in directives_a[0]["text"]
    assert len(directives_b) == 1
    assert "B 계정" in directives_b[0]["text"]

    # 교차 오염 확인
    all_a_texts = [d["text"] for d in directives_a]
    all_b_texts = [d["text"] for d in directives_b]
    assert not any("B 계정" in t for t in all_a_texts)
    assert not any("A 계정" in t for t in all_b_texts)


def test_idempotent_append(isolated_profiles):
    """동일 내용 중복 추가는 1건만 저장."""
    sd = isolated_profiles
    uid = 99
    text = "매크로 붕괴 대비 달러 전환"
    added_first = sd.append_directive(uid, text)
    added_second = sd.append_directive(uid, text)

    assert added_first is True
    assert added_second is False  # 중복 → False
    assert len(sd.load(uid)) == 1


def test_clear_directives(isolated_profiles):
    sd = isolated_profiles
    uid = 7
    sd.append_directive(uid, "지시1")
    sd.append_directive(uid, "지시2")
    count = sd.clear_directives(uid)
    assert count == 2
    assert sd.load(uid) == []


def test_build_orchestrator_directive_block_present(isolated_profiles):
    """지시가 있으면 블록 텍스트에 지시 내용 포함."""
    sd = isolated_profiles
    uid = 10
    sd.append_directive(uid, "원화 자산 최소화")
    block = sd.build_orchestrator_directive_block(uid)
    # TASK B: 헤더/푸터 문구가 완화된 형태로 업데이트됨
    assert "사장님 상시 지침" in block
    assert "원화 자산 최소화" in block
    assert "균형 있게" in block
    assert "파이썬 리스크·guardrail 게이트" in block


def test_build_orchestrator_directive_block_empty_when_no_directives(isolated_profiles):
    """지시가 없으면 빈 문자열 반환 — 프롬프트에 불필요한 노이즈 없음."""
    sd = isolated_profiles
    block = sd.build_orchestrator_directive_block(uid=999)
    assert block == ""


def test_build_orchestrator_directive_block_none_uid(isolated_profiles):
    """uid=None 이면 빈 문자열."""
    sd = isolated_profiles
    assert sd.build_orchestrator_directive_block(None) == ""


def test_multiple_directives_all_present_in_block(isolated_profiles):
    """여러 지시가 모두 블록에 포함되어야 한다."""
    sd = isolated_profiles
    uid = 20
    sd.append_directive(uid, "달러 MMF 핵심 축")
    sd.append_directive(uid, "금·비트코인 배제")
    sd.append_directive(uid, "리밸런싱 트리거 수립")
    block = sd.build_orchestrator_directive_block(uid)
    assert "달러 MMF 핵심 축" in block
    assert "금·비트코인 배제" in block
    assert "리밸런싱 트리거 수립" in block


def test_persistence_across_calls(isolated_profiles, tmp_path):
    """append 후 모듈을 재-import 해도 데이터가 유지되는지 확인
    (파일 기반 영속성 검증 — 메모리 캐시가 아님)."""
    sd = isolated_profiles
    uid = 55
    sd.append_directive(uid, "영속 테스트 지시사항")

    # 파일이 실제로 존재하는지 확인
    p = sd._directives_path(uid)
    assert p.exists()

    # 파일 내용 직접 확인
    import json
    data = json.loads(p.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert len(data) == 1
    assert "영속 테스트" in data[0]["text"]


def test_seed_admin_directive_safe_no_op_without_admin(isolated_profiles, monkeypatch):
    """admin 계정이 DB에 없어도 크래시 없이 종료 (safe no-op)."""
    # auth_store.find_user_by_username 을 None 반환으로 패치
    import infra.auth_store as _as
    monkeypatch.setattr(_as, "find_user_by_username", lambda *a, **kw: None)

    # 크래시 없이 실행되어야 함
    try:
        from infra.standing_directives import seed_admin_directive as _real_seed
        _real_seed()
    except Exception as e:
        pytest.fail(f"seed_admin_directive raised unexpectedly: {e}")


# ─── sentinel / non-resurrection 테스트 ────────────────────────────────────────

def _make_fake_user(uid: int):
    return {"id": uid, "username": "hh09080"}


def test_seed_writes_sentinel_on_first_seed(isolated_profiles, monkeypatch):
    """최초 시드 후 sentinel 파일이 생성되어야 한다."""
    sd = isolated_profiles
    import infra.auth_store as _as
    fake_uid = 77
    monkeypatch.setattr(_as, "find_user_by_username", lambda *a, **kw: _make_fake_user(fake_uid))

    sd.seed_admin_directive()

    sentinel = sd._seed_sentinel_path(fake_uid)
    assert sentinel.exists(), "sentinel 파일이 존재해야 한다"
    import json as _json
    data = _json.loads(sentinel.read_text(encoding="utf-8"))
    assert "seeded_ids" in data
    assert "ts" in data


def test_non_resurrection_after_clear_directives(isolated_profiles, monkeypatch):
    """시드 → clear_directives → seed_admin_directive() 재호출 시 지시가 부활하지 않는다.

    sentinel 존재 → 재시드 차단 → 지시 목록은 여전히 비어있어야 함.
    """
    sd = isolated_profiles
    import infra.auth_store as _as
    fake_uid = 88
    monkeypatch.setattr(_as, "find_user_by_username", lambda *a, **kw: _make_fake_user(fake_uid))

    # 1) 최초 시드
    sd.seed_admin_directive()
    assert len(sd.load(fake_uid)) == 1, "시드 후 지시 1건 있어야 함"

    # 2) 사용자가 지시 삭제
    deleted = sd.clear_directives(fake_uid)
    assert deleted == 1
    assert sd.load(fake_uid) == [], "삭제 후 목록 비어있어야 함"

    # sentinel은 그대로 남아있음
    assert sd._seed_sentinel_exists(fake_uid), "sentinel은 삭제 후에도 유지되어야 함"

    # 3) 재시작 시뮬레이션: seed_admin_directive() 재호출
    sd.seed_admin_directive()

    # 4) 지시가 부활하지 않아야 함
    directives_after = sd.load(fake_uid)
    assert directives_after == [], (
        f"sentinel이 있으면 지시가 부활하면 안 된다. 실제: {directives_after}"
    )


def test_non_resurrection_after_remove_directive(isolated_profiles, monkeypatch):
    """시드 → remove_directive → seed_admin_directive() 재호출 시 지시가 부활하지 않는다."""
    sd = isolated_profiles
    import infra.auth_store as _as
    fake_uid = 89
    monkeypatch.setattr(_as, "find_user_by_username", lambda *a, **kw: _make_fake_user(fake_uid))

    # 1) 최초 시드
    sd.seed_admin_directive()
    directives = sd.load(fake_uid)
    assert len(directives) == 1
    directive_id = directives[0]["id"]

    # 2) 단건 삭제
    removed = sd.remove_directive(fake_uid, directive_id)
    assert removed is True
    assert sd.load(fake_uid) == []

    # 3) 재시작 시뮬레이션
    sd.seed_admin_directive()

    # 4) 지시 부활 없음
    assert sd.load(fake_uid) == [], "remove_directive 후 sentinel이 재시드를 차단해야 한다"


def test_sentinel_governs_regardless_of_directive_presence(isolated_profiles, monkeypatch):
    """sentinel 존재 시 지시 파일이 없어도 / 있어도 재시드 없음.

    sentinel만 수동으로 기록해 두고 seed 호출 → 지시가 새로 추가되지 않아야 한다.
    """
    sd = isolated_profiles
    import infra.auth_store as _as
    fake_uid = 91
    monkeypatch.setattr(_as, "find_user_by_username", lambda *a, **kw: _make_fake_user(fake_uid))

    # sentinel만 미리 기록 (지시 파일 없음)
    sd._write_seed_sentinel(fake_uid, ["manual"])

    sd.seed_admin_directive()

    # 지시가 추가되지 않아야 함
    assert sd.load(fake_uid) == [], "sentinel이 있으면 지시가 없어도 재시드하면 안 된다"


def test_multiple_restarts_only_seed_once(isolated_profiles, monkeypatch):
    """여러 번 재시작(seed_admin_directive 다회 호출)해도 지시는 1건만."""
    sd = isolated_profiles
    import infra.auth_store as _as
    fake_uid = 92
    monkeypatch.setattr(_as, "find_user_by_username", lambda *a, **kw: _make_fake_user(fake_uid))

    for _ in range(5):
        sd.seed_admin_directive()

    directives = sd.load(fake_uid)
    assert len(directives) == 1, f"5번 재시드해도 지시는 1건이어야 함. 실제: {len(directives)}"
