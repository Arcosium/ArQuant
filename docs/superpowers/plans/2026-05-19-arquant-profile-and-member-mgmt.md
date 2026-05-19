# ArQuant 프로필 시스템 · 회원 관리 · 복구 2인자 — 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 상단바 배지를 통신로그 옆으로 이동하고, 🚪 로그아웃 버튼을 사람 아이콘→프로필 모달(로그아웃·비번변경·정보변경·탈퇴·상시지시 CRUD)로 대체하며, ADMIN(hh09080) 전용 회원현황·완전삭제, 비밀번호 복구 인자를 한투 계좌번호+App Secret 2개로 변경한다.

**Architecture:** 기존 SPA(`server/static/index.html`) 모달 + FastAPI 세션 미들웨어(`request.state.user_id`) + `auth_store.is_admin` 게이트 재사용. 신규 인증 엔드포인트는 `/api/profile/*`(세션 필요)·`/api/admin/*`(ADMIN 게이트). 복구 인자 변경은 `users.kis_account_no_bidx` 컬럼 추가 + 부팅 1회 멱등 백필. 안드로이드는 LoginScreen 복구 폼만 네이티브 변경.

**Tech Stack:** Python 3.11, FastAPI, SQLite(WAL) + Fernet/argon2id/HKDF-HMAC blind index, pytest, vanilla JS SPA, Kotlin/Compose(Android).

**스펙:** `docs/superpowers/specs/2026-05-19-arquant-profile-and-member-mgmt-design.md`

**브랜치:** `feature/profile-system` (이미 생성됨, 스펙 커밋 `e3e81ff`).

**보안 불변식:** 서버 데이터(아이디·지시문)를 DOM 에 넣을 때 **`innerHTML` 금지**.
`createElement`+`textContent`+closure 만 사용(이 프로젝트의 통신로그 표
렌더 XSS-safe 패턴과 동일). 악성 username/지시문으로 XSS 불가해야 한다.

---

## 파일 구조

| 파일 | 책임 | 변경 |
|---|---|---|
| `infra/auth_store.py` | 스키마·bidx·복구·계정 CRUD | 수정: `kis_account_no_bidx` 컬럼/upsert/마이그레이션, 복구 2인자, `delete_user`, `list_members`, `change_password`, `update_credentials` |
| `server/app.py` | 인증·프로필·ADMIN 엔드포인트 | 수정: 복구 모델/엔드포인트 2인자, `/api/profile/*`, `/api/admin/*`, 제네릭 admin 게이트 |
| `server/static/index.html` | SPA UI | 수정: 배지 이동, 프로필 모달, ADMIN 섹션, 복구 패널 2필드 |
| `arquant_mobile/.../ui/screens/LoginScreen.kt` | 네이티브 로그인/복구 | 수정: 복구 폼 2필드 |
| `arquant_mobile/.../network/ArQuantApi.kt` | Retrofit 모델 | 수정: RecoverId/PwRequest 2인자 |
| `arquant_mobile/.../viewmodel/AuthViewModel.kt` | 복구 호출 | 수정: 2인자 시그니처 |
| `tests/test_recovery_two_factor.py` | 복구 2인자 회귀 | 신규 |
| `tests/test_account_bidx_migration.py` | 계좌 bidx 마이그레이션 멱등 | 신규 |
| `tests/test_account_admin_helpers.py` | 계정 관리 헬퍼 | 신규 |
| `tests/test_profile_endpoints.py` | 프로필 API | 신규 |
| `tests/test_admin_members.py` | ADMIN 회원 API | 신규 |
| `tests/test_auth_recovery.py`, `tests/test_recovery_endpoints.py` | 기존 3인자 테스트 | 수정(2인자로) |

기존 `find_username_by_factors`/`reset_password_by_factors` 시그니처를 2인자로 **교체**(추가 아님 — YAGNI). 호출부는 Task 4에서 동시 갱신, 기존 테스트는 Task 2에서 갱신해 각 태스크 종료 시 그린 유지.

---

## Task 1: `kis_account_no_bidx` 컬럼 + upsert + 마이그레이션 백필

**Files:**
- Modify: `infra/auth_store.py` (init() 컬럼 루프 ~253-258, upsert_user vals ~283-295, migrate_passwords_and_bidx ~493-521)
- Test: `tests/test_account_bidx_migration.py` (Create)

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_account_bidx_migration.py`:
```python
"""kis_account_no_bidx 컬럼·upsert·부팅 백필 멱등 회귀."""
import pytest


@pytest.fixture
def store(tmp_path, monkeypatch):
    import infra.auth_store as a
    for n, v in [("_DATA_DIR", tmp_path), ("_DB_PATH", tmp_path / "auth.db"),
                 ("_FERNET_KEY_PATH", tmp_path / ".fernet.key"),
                 ("_AUDIT_PATH", tmp_path / "audit.log"),
                 ("_INITED", False), ("_FERNET", None),
                 ("_FERNET_RAW", None), ("_BIDX_KEY", None)]:
        monkeypatch.setattr(a, n, v, raising=False)
    a.init()
    return a


def test_schema_has_kis_account_no_bidx(store):
    with store._connect() as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(users)").fetchall()}
    assert "kis_account_no_bidx" in cols


def test_upsert_sets_account_bidx(store):
    uid = store.upsert_user("u1", "Passw0rd!!xx", "AK", "AS", "OR",
                            "5012345601", "https://openapi.koreainvestment.com:9443")
    with store._connect() as c:
        row = c.execute("SELECT kis_account_no_bidx FROM users WHERE id=?",
                         (uid,)).fetchone()
    assert row["kis_account_no_bidx"] == store.bidx("5012345601")


def test_migration_backfills_blank_account_bidx_idempotent(store):
    uid = store.upsert_user("u2", "Passw0rd!!xx", "AK", "AS", "OR",
                            "777888999", "https://openapi.koreainvestment.com:9443")
    with store._connect() as c:
        c.execute("UPDATE users SET kis_account_no_bidx='' WHERE id=?", (uid,))
    s1 = store.migrate_passwords_and_bidx()
    with store._connect() as c:
        v = c.execute("SELECT kis_account_no_bidx FROM users WHERE id=?",
                       (uid,)).fetchone()["kis_account_no_bidx"]
    assert v == store.bidx("777888999")
    assert s1.get("acct_bidx", 0) >= 1
    s2 = store.migrate_passwords_and_bidx()
    assert s2.get("acct_bidx", 0) == 0
```

- [ ] **Step 2: 실패 확인**

Run: `python3.11 -m pytest tests/test_account_bidx_migration.py -q`
Expected: FAIL — `kis_account_no_bidx` 컬럼/통계 키 없음.

- [ ] **Step 3: 구현 — 스키마 컬럼 추가**

`infra/auth_store.py` `init()` 의 컬럼 루프를 수정:
```python
        for _c in ("password_hash", "kis_app_key_bidx",
                   "kis_app_secret_bidx", "openrouter_key_bidx",
                   "kis_account_no_bidx"):
            if _c not in cols:
                conn.execute(
                    f"ALTER TABLE users ADD COLUMN {_c} TEXT NOT NULL DEFAULT ''")
                logger.info("auth_store 마이그레이션: users.%s 컬럼 추가", _c)
```

- [ ] **Step 4: 구현 — upsert_user 가 account bidx 기록**

`vals = dict(...)` 의 `openrouter_key_bidx=bidx(openrouter_key),` 다음 줄 추가:
```python
        kis_account_no_bidx=bidx(kis_account_no),
```
UPDATE SET 절에 `kis_account_no_bidx=?` 추가, 값 튜플의 `vals["openrouter_key_bidx"]`
뒤에 `vals["kis_account_no_bidx"]` 추가. INSERT 컬럼리스트에
`kis_account_no_bidx` 추가, VALUES `?` 1개 추가, 값 튜플의
`vals["openrouter_key_bidx"]` 뒤(`adm` 앞)에 `vals["kis_account_no_bidx"]` 추가.

- [ ] **Step 5: 구현 — 마이그레이션 백필**

`migrate_passwords_and_bidx()`: `stats = {"pw": 0, "bidx": 0}` →
`stats = {"pw": 0, "bidx": 0, "acct_bidx": 0}`. SELECT 컬럼에
`kis_account_no_enc, kis_account_no_bidx` 추가. 루프 상단 `did_bidx = False`
옆에 `did_acct = False`. bidx 백필 블록 뒤(`if updates:` 앞)에 추가:
```python
                if not (r["kis_account_no_bidx"] or ""):
                    enc_acct = r["kis_account_no_enc"] or ""
                    if enc_acct:
                        dec_acct = decrypt(enc_acct)
                        if dec_acct:
                            updates["kis_account_no_bidx"] = bidx(dec_acct)
                            did_acct = True
```
통계 반영부에 `if did_acct: stats["acct_bidx"] += 1` 추가. 최종 로그:
```python
    if stats["pw"] or stats["bidx"] or stats["acct_bidx"]:
        logger.info("auth 마이그레이션 완료: 해시승격 %d, bidx백필 %d, 계좌bidx백필 %d",
                    stats["pw"], stats["bidx"], stats["acct_bidx"])
```

- [ ] **Step 6: 통과 확인 + 전체 회귀**

Run: `python3.11 -m pytest tests/test_account_bidx_migration.py -q && python3.11 -m pytest -q`
Expected: 신규 3건 PASS, 전체 그린.

- [ ] **Step 7: 커밋**
```bash
git add infra/auth_store.py tests/test_account_bidx_migration.py
git commit -m "feat(auth): kis_account_no_bidx 컬럼·upsert·부팅 백필(멱등)"
```

---

## Task 2: 복구 함수 2인자 전환 (계좌번호 + App Secret)

**Files:**
- Modify: `infra/auth_store.py` (`find_username_by_factors` ~361, `reset_password_by_factors` ~376)
- Modify: `tests/test_auth_recovery.py` (기존 3인자 → 2인자)
- Test: `tests/test_recovery_two_factor.py` (Create)

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_recovery_two_factor.py`:
```python
"""복구 인자 = 한투 계좌번호 + 한투 App Secret (2개)."""
import pytest


@pytest.fixture
def store(tmp_path, monkeypatch):
    import infra.auth_store as a
    for n, v in [("_DATA_DIR", tmp_path), ("_DB_PATH", tmp_path / "auth.db"),
                 ("_FERNET_KEY_PATH", tmp_path / ".fernet.key"),
                 ("_AUDIT_PATH", tmp_path / "audit.log"),
                 ("_INITED", False), ("_FERNET", None),
                 ("_FERNET_RAW", None), ("_BIDX_KEY", None)]:
        monkeypatch.setattr(a, n, v, raising=False)
    a.init()
    a.upsert_user("trader", "Passw0rd!!xx", "APPKEY1", "SECRET1", "ORK1",
                  "5012345601", "https://openapi.koreainvestment.com:9443")
    return a


def test_find_username_by_account_and_secret(store):
    assert store.find_username_by_factors("5012345601", "SECRET1") == "trader"


def test_find_fails_on_wrong_secret(store):
    assert store.find_username_by_factors("5012345601", "WRONG") is None


def test_find_fails_on_empty(store):
    assert store.find_username_by_factors("", "SECRET1") is None


def test_reset_password_two_factor(store):
    ok = store.reset_password_by_factors("trader", "5012345601", "SECRET1",
                                         "NewPassw0rd!!")
    assert ok is True
    assert store.verify_password("trader", "NewPassw0rd!!")


def test_reset_policy_checked_before_factor_match_enum_oracle(store):
    with pytest.raises(ValueError):
        store.reset_password_by_factors("trader", "WRONGACCT", "WRONGSEC", "short")


def test_reset_fails_wrong_factors_valid_policy(store):
    assert store.reset_password_by_factors("trader", "BAD", "BAD",
                                           "ValidPassw0rd!!") is False
```

- [ ] **Step 2: 실패 확인**

Run: `python3.11 -m pytest tests/test_recovery_two_factor.py -q`
Expected: FAIL — 함수가 3인자 시그니처.

- [ ] **Step 3: 구현 — 2인자 전환**

`infra/auth_store.py` 두 함수를 통째 교체:
```python
def find_username_by_factors(kis_account_no: str, kis_app_secret: str) -> Optional[str]:
    """한투 계좌번호 + 한투 App Secret 이 모두 일치하는 단일 유저 아이디 반환.
    블라인드 인덱스 조회 — 전체 복호 없음."""
    if not (_norm(kis_account_no) and _norm(kis_app_secret)):
        return None
    init()
    a, b = bidx(kis_account_no), bidx(kis_app_secret)
    with _DB_LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT username FROM users WHERE kis_account_no_bidx=? AND "
            "kis_app_secret_bidx=?", (a, b)).fetchone()
    return row["username"] if row else None


def reset_password_by_factors(username: str, kis_account_no: str,
                              kis_app_secret: str, new_password: str) -> bool:
    """정책 먼저 검사(위반→ValueError, 팩터 정오 무관 — enum 오라클 차단).
    그다음 아이디+계좌번호+App Secret 완전 일치 시에만 재설정."""
    perr = password_policy_error(new_password or "")
    if perr:
        raise ValueError(perr)
    if not (_norm(kis_account_no) and _norm(kis_app_secret)):
        return False
    init()
    a, b = bidx(kis_account_no), bidx(kis_app_secret)
    with _DB_LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE username=? AND kis_account_no_bidx=? AND "
            "kis_app_secret_bidx=?", (_norm(username), a, b)).fetchone()
        if not row:
            return False
        conn.execute("UPDATE users SET password_hash=?, password_enc='' WHERE id=?",
                     (hash_password(new_password), int(row["id"])))
    return True
```

- [ ] **Step 4: 기존 3인자 테스트 갱신**

`tests/test_auth_recovery.py` 내 `find_username_by_factors`/
`reset_password_by_factors` 호출을 2인자(`kis_account_no, kis_app_secret`)로
수정(app_key/openrouter 인자 제거, 계좌번호 사용). 파일 내 모든 해당 호출.

- [ ] **Step 5: 통과 확인**

Run: `python3.11 -m pytest tests/test_recovery_two_factor.py tests/test_auth_recovery.py -q`
Expected: PASS. (`test_recovery_endpoints.py` 는 Task 4에서 그린 복구 — 명시.)

- [ ] **Step 6: 커밋**
```bash
git add infra/auth_store.py tests/test_recovery_two_factor.py tests/test_auth_recovery.py
git commit -m "feat(auth): 복구 인자 2개(계좌번호+App Secret)로 전환"
```

---

## Task 3: 계정 관리 헬퍼 — delete_user · list_members · change_password · update_credentials

**Files:**
- Modify: `infra/auth_store.py` (`list_accounts()` 정의 뒤)
- Test: `tests/test_account_admin_helpers.py` (Create)

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_account_admin_helpers.py`:
```python
import pytest


@pytest.fixture
def store(tmp_path, monkeypatch):
    import infra.auth_store as a
    for n, v in [("_DATA_DIR", tmp_path), ("_DB_PATH", tmp_path / "auth.db"),
                 ("_FERNET_KEY_PATH", tmp_path / ".fernet.key"),
                 ("_AUDIT_PATH", tmp_path / "audit.log"),
                 ("_INITED", False), ("_FERNET", None),
                 ("_FERNET_RAW", None), ("_BIDX_KEY", None)]:
        monkeypatch.setattr(a, n, v, raising=False)
    a.init()
    return a


def test_delete_user_removes_row_and_sessions(store):
    uid = store.upsert_user("u1", "Passw0rd!!xx", "AK", "AS", "OR",
                            "5012345601", "https://openapi.koreainvestment.com:9443")
    tok = store.create_session(uid)
    assert store.lookup_session(tok) == uid
    assert store.delete_user(uid) is True
    assert store.find_user_by_username("u1") is None
    assert store.lookup_session(tok) is None
    assert store.delete_user(uid) is False


def test_list_members_fields(store):
    store.upsert_user("admin", "Passw0rd!!xx", "AK", "AS", "OR", "1",
                       "https://openapivts.koreainvestment.com:29443", is_admin=True)
    store.upsert_user("live1", "Passw0rd!!xx", "AK2", "AS2", "OR2", "2",
                       "https://openapi.koreainvestment.com:9443")
    ms = {m["username"]: m for m in store.list_members()}
    assert ms["admin"]["is_admin"] is True and ms["admin"]["is_mock"] is True
    assert ms["live1"]["is_admin"] is False and ms["live1"]["is_mock"] is False
    assert "created_at" in ms["live1"] and "last_login_at" in ms["live1"]


def test_change_password_requires_current(store):
    uid = store.upsert_user("u3", "OldPassw0rd!!", "AK", "AS", "OR", "9",
                            "https://openapi.koreainvestment.com:9443")
    with pytest.raises(ValueError):
        store.change_password(uid, "WRONG", "NewPassw0rd!!")
    with pytest.raises(ValueError):
        store.change_password(uid, "OldPassw0rd!!", "short")
    assert store.change_password(uid, "OldPassw0rd!!", "NewPassw0rd!!") is True
    assert store.verify_password("u3", "NewPassw0rd!!")


def test_update_credentials_recomputes_bidx(store):
    uid = store.upsert_user("u4", "Passw0rd!!xx", "AK", "AS", "OR", "111",
                            "https://openapi.koreainvestment.com:9443")
    store.update_credentials(uid, kis_account_no="222", kis_app_secret="AS2")
    with store._connect() as c:
        r = c.execute("SELECT kis_account_no_bidx, kis_app_secret_bidx "
                       "FROM users WHERE id=?", (uid,)).fetchone()
    assert r["kis_account_no_bidx"] == store.bidx("222")
    assert r["kis_app_secret_bidx"] == store.bidx("AS2")
    assert store.get_user_credentials(uid)["kis_account_no"] == "222"
```

- [ ] **Step 2: 실패 확인**

Run: `python3.11 -m pytest tests/test_account_admin_helpers.py -q`
Expected: FAIL — 함수 미정의.

- [ ] **Step 3: 구현 — 신규 함수 (`list_accounts()` 정의 뒤 추가)**
```python
def delete_user(user_id: int) -> bool:
    """유저 행 + 해당 세션 완전 삭제. 성공 True, 없는 uid False.
    (SQLite FK CASCADE 는 PRAGMA 미설정이라 세션을 명시 삭제한다.)"""
    init()
    with _DB_LOCK, _connect() as conn:
        row = conn.execute("SELECT id FROM users WHERE id=?",
                           (int(user_id),)).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM sessions WHERE user_id=?", (int(user_id),))
        conn.execute("DELETE FROM users WHERE id=?", (int(user_id),))
    return True


def _is_mock_url(url: str) -> bool:
    u = url or ""
    return ("openapivts" in u) or (":29443" in u)


def list_members() -> List[Dict[str, Any]]:
    """ADMIN 회원 현황(읽기 전용). 민감값 비노출. is_mock 은 Base URL 파생."""
    init()
    with _DB_LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT id, username, kis_base_url, created_at, last_login_at, "
            "is_admin FROM users ORDER BY created_at ASC").fetchall()
    return [{
        "id": int(r["id"]), "username": r["username"],
        "created_at": r["created_at"], "last_login_at": r["last_login_at"],
        "is_admin": bool(r["is_admin"]),
        "is_mock": _is_mock_url(r["kis_base_url"]),
    } for r in rows]


def change_password(user_id: int, current: str, new_password: str) -> bool:
    """현재 비번 검증 → 신규 정책 검사 → argon2 해시 갱신.
    현재 비번 불일치/정책 위반 → ValueError."""
    init()
    creds = get_user_credentials(int(user_id))
    if not creds:
        raise ValueError("계정을 찾을 수 없습니다.")
    if not verify_password(creds["username"], current or ""):
        raise ValueError("현재 비밀번호가 일치하지 않습니다.")
    perr = password_policy_error(new_password or "")
    if perr:
        raise ValueError(perr)
    _set_password_hash(int(user_id), hash_password(new_password), clear_enc=True)
    return True


def update_credentials(user_id: int, *, openrouter_key: Optional[str] = None,
                        kis_app_key: Optional[str] = None,
                        kis_app_secret: Optional[str] = None,
                        kis_account_no: Optional[str] = None,
                        kis_base_url: Optional[str] = None) -> bool:
    """제공된 자격증명만 갱신(None=미변경). 변경분 enc + bidx 동시 재계산."""
    init()
    sets, params = [], []
    if openrouter_key is not None:
        sets += ["openrouter_key_enc=?", "openrouter_key_bidx=?"]
        params += [encrypt(openrouter_key), bidx(openrouter_key)]
    if kis_app_key is not None:
        sets += ["kis_app_key_enc=?", "kis_app_key_bidx=?"]
        params += [encrypt(kis_app_key), bidx(kis_app_key)]
    if kis_app_secret is not None:
        sets += ["kis_app_secret_enc=?", "kis_app_secret_bidx=?"]
        params += [encrypt(kis_app_secret), bidx(kis_app_secret)]
    if kis_account_no is not None:
        sets += ["kis_account_no_enc=?", "kis_account_no_bidx=?"]
        params += [encrypt(kis_account_no), bidx(kis_account_no)]
    if kis_base_url is not None:
        sets += ["kis_base_url=?"]
        params += [(kis_base_url or "").strip()]
    if not sets:
        return False
    params.append(int(user_id))
    with _DB_LOCK, _connect() as conn:
        conn.execute(f"UPDATE users SET {','.join(sets)} WHERE id=?", tuple(params))
    return True
```

- [ ] **Step 4: 통과 확인 + 전체 회귀**

Run: `python3.11 -m pytest tests/test_account_admin_helpers.py -q && python3.11 -m pytest -q`
Expected: 신규 PASS, 전체 그린(`test_recovery_endpoints.py` 는 Task 4 후 그린).

- [ ] **Step 5: 커밋**
```bash
git add infra/auth_store.py tests/test_account_admin_helpers.py
git commit -m "feat(auth): delete_user·list_members·change_password·update_credentials"
```

---

## Task 4: 복구 엔드포인트 2인자 + 기존 엔드포인트 테스트 갱신

**Files:**
- Modify: `server/app.py` (`RecoverIdReq` ~154, `RecoverPwReq` ~159, `/api/recover_id` ~256, `/api/recover_password` ~268)
- Modify: `tests/test_recovery_endpoints.py`

- [ ] **Step 1: 실패 테스트 갱신**

`tests/test_recovery_endpoints.py` 의 recover_id 바디 →
`{"kis_account_no": "...", "kis_app_secret": "..."}`, recover_password 바디 →
`{"username","kis_account_no","kis_app_secret","new_password"}`. 상태코드 기대
(200/404/400)는 동일. 파일 내 모든 해당 호출 갱신.

- [ ] **Step 2: 실패 확인**

Run: `python3.11 -m pytest tests/test_recovery_endpoints.py -q`
Expected: FAIL — 서버 모델이 아직 3인자.

- [ ] **Step 3: 구현 — 모델/엔드포인트 2인자**

`server/app.py`:
```python
class RecoverIdReq(BaseModel):
    kis_account_no: str
    kis_app_secret: str

class RecoverPwReq(BaseModel):
    username: str
    kis_account_no: str
    kis_app_secret: str
    new_password: str
```
`/api/recover_id` 본문:
```python
    uname = auth_store.find_username_by_factors(
        req.kis_account_no, req.kis_app_secret)
```
`/api/recover_password` 본문:
```python
        ok = auth_store.reset_password_by_factors(
            (req.username or "").strip(), req.kis_account_no,
            req.kis_app_secret, req.new_password)
```

- [ ] **Step 4: 통과 확인 + 전체 회귀**

Run: `python3.11 -m pytest -q`
Expected: 전체 그린.

- [ ] **Step 5: 커밋**
```bash
git add server/app.py tests/test_recovery_endpoints.py
git commit -m "feat(api): 복구 엔드포인트 2인자(계좌번호+App Secret)"
```

---

## Task 5: 프로필 엔드포인트 + 제네릭 admin 게이트

**Files:**
- Modify: `server/app.py` (`_admin_uid_or_403` 옆, `/api/me` 라우트 뒤)
- Test: `tests/test_profile_endpoints.py` (Create)

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_profile_endpoints.py`:
```python
"""프로필 엔드포인트 — 세션 인증·비번변경·지시 CRUD·탈퇴."""
import pytest
from fastapi.testclient import TestClient


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
    import server.app as app_mod
    c = TestClient(app_mod.app)
    c.headers.update({"X-Session": tok})
    return c, a, uid


def test_password_change_unauth_401():
    import server.app as app_mod
    c = TestClient(app_mod.app)
    assert c.post("/api/profile/password",
                  json={"current": "x", "new": "y"}).status_code == 401


def test_password_change_flow(client):
    c, a, uid = client
    assert c.post("/api/profile/password",
                  json={"current": "WRONG", "new": "NewPassw0rd!!"}).status_code == 400
    assert c.post("/api/profile/password",
                  json={"current": "OldPassw0rd!!", "new": "short"}).status_code == 400
    assert c.post("/api/profile/password",
                  json={"current": "OldPassw0rd!!", "new": "NewPassw0rd!!"}).status_code == 200
    assert a.verify_password("u1", "NewPassw0rd!!")


def test_directives_crud(client):
    c, a, uid = client
    assert c.get("/api/profile/directives").json()["directives"] == []
    assert c.post("/api/profile/directives",
                  json={"text": "달러 비중 확대"}).status_code == 200
    lst = c.get("/api/profile/directives").json()["directives"]
    assert len(lst) == 1 and lst[0]["text"] == "달러 비중 확대"
    did = lst[0]["id"]
    assert c.delete(f"/api/profile/directives/{did}").status_code == 200
    assert c.get("/api/profile/directives").json()["directives"] == []


def test_delete_account_requires_password(client):
    c, a, uid = client
    assert c.post("/api/profile/delete_account",
                  json={"password": "WRONG"}).status_code == 400
    assert c.post("/api/profile/delete_account",
                  json={"password": "OldPassw0rd!!"}).status_code == 200
    assert a.find_user_by_username("u1") is None
```

- [ ] **Step 2: 실패 확인**

Run: `python3.11 -m pytest tests/test_profile_endpoints.py -q`
Expected: FAIL — 404(라우트 미정의).

- [ ] **Step 3: 구현 — 제네릭 admin 게이트 + 프로필 라우트**

`server/app.py` `_admin_uid_or_403` 옆에:
```python
def _require_admin(request: Request) -> int:
    uid = _uid_or_403(request)
    if not auth_store.is_admin(uid):
        raise HTTPException(403, "ADMIN(hh09080) 전용 기능입니다.")
    return uid
```
`/api/me` 라우트 뒤에:
```python
class PwChangeReq(BaseModel):
    current: str
    new: str

class CredsReq(BaseModel):
    openrouter_key: Optional[str] = None
    kis_app_key: Optional[str] = None
    kis_app_secret: Optional[str] = None
    kis_account_no: Optional[str] = None
    kis_base_url: Optional[str] = None

class DirectiveReq(BaseModel):
    text: str

class DeleteAccountReq(BaseModel):
    password: str

@app.post("/api/profile/password")
async def profile_password(req: PwChangeReq, request: Request):
    uid = _uid_or_403(request)
    try:
        auth_store.change_password(uid, req.current, req.new)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}

@app.post("/api/profile/credentials")
async def profile_credentials(req: CredsReq, request: Request):
    uid = _uid_or_403(request)
    cur = auth_store.get_user_credentials(uid) or {}
    ak = req.kis_app_key if req.kis_app_key is not None else cur.get("kis_app_key")
    as_ = req.kis_app_secret if req.kis_app_secret is not None else cur.get("kis_app_secret")
    bu = req.kis_base_url if req.kis_base_url is not None else cur.get("kis_base_url")
    if req.kis_app_key is not None or req.kis_app_secret is not None or req.kis_base_url is not None:
        ok, msg = await _validate_kis(ak, as_, bu)
        if not ok:
            raise HTTPException(400, msg)
    if req.openrouter_key is not None:
        ok, msg = await _validate_openrouter(req.openrouter_key)
        if not ok:
            raise HTTPException(400, msg)
    auth_store.update_credentials(
        uid, openrouter_key=req.openrouter_key, kis_app_key=req.kis_app_key,
        kis_app_secret=req.kis_app_secret, kis_account_no=req.kis_account_no,
        kis_base_url=req.kis_base_url)
    if creds_layer.current().get("user_id") == uid:
        await _activate_with_policy(uid)
    return {"ok": True}

@app.get("/api/profile/directives")
async def profile_directives_list(request: Request):
    uid = _uid_or_403(request)
    from infra import standing_directives as sd
    return {"directives": sd.load(uid)}

@app.post("/api/profile/directives")
async def profile_directives_add(req: DirectiveReq, request: Request):
    uid = _uid_or_403(request)
    from infra import standing_directives as sd
    added = sd.append_directive(uid, req.text)
    return {"ok": True, "added": added, "directives": sd.load(uid)}

@app.delete("/api/profile/directives/{did}")
async def profile_directives_del(did: str, request: Request):
    uid = _uid_or_403(request)
    from infra import standing_directives as sd
    sd.remove_directive(uid, did)
    return {"ok": True, "directives": sd.load(uid)}

@app.post("/api/profile/delete_account")
async def profile_delete_account(req: DeleteAccountReq, request: Request):
    uid = _uid_or_403(request)
    creds = auth_store.get_user_credentials(uid)
    if not creds or not auth_store.verify_password(creds["username"], req.password or ""):
        raise HTTPException(400, "비밀번호가 일치하지 않습니다.")
    import shutil
    from pathlib import Path
    auth_store.delete_user(uid)
    shutil.rmtree(Path(__file__).resolve().parent.parent / "data" /
                  "profiles" / str(uid), ignore_errors=True)
    resp = JSONResponse(content={"ok": True})
    resp.delete_cookie(auth_store.SESSION_COOKIE, path="/",
                       secure=_COOKIE_SECURE, httponly=True, samesite="lax")
    return resp
```

- [ ] **Step 4: 통과 확인 + 전체 회귀**

Run: `python3.11 -m pytest tests/test_profile_endpoints.py -q && python3.11 -m pytest -q`
Expected: PASS, 전체 그린.

- [ ] **Step 5: 커밋**
```bash
git add server/app.py tests/test_profile_endpoints.py
git commit -m "feat(api): 프로필 엔드포인트(비번/자격증명/지시/탈퇴) + 제네릭 admin 게이트"
```

---

## Task 6: ADMIN 회원 엔드포인트 (현황 읽기전용 / 완전삭제)

**Files:**
- Modify: `server/app.py` (프로필 라우트 뒤)
- Test: `tests/test_admin_members.py` (Create)

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_admin_members.py`:
```python
import pytest
from fastapi.testclient import TestClient


def _mk(tmp_path, monkeypatch):
    import infra.auth_store as a
    for n, v in [("_DATA_DIR", tmp_path), ("_DB_PATH", tmp_path / "auth.db"),
                 ("_FERNET_KEY_PATH", tmp_path / ".fernet.key"),
                 ("_AUDIT_PATH", tmp_path / "audit.log"),
                 ("_INITED", False), ("_FERNET", None),
                 ("_FERNET_RAW", None), ("_BIDX_KEY", None)]:
        monkeypatch.setattr(a, n, v, raising=False)
    a.init()
    return a


def test_members_requires_admin(tmp_path, monkeypatch):
    a = _mk(tmp_path, monkeypatch)
    uid = a.upsert_user("normal", "Passw0rd!!xx", "AK", "AS", "OR", "1",
                         "https://openapi.koreainvestment.com:9443")
    tok = a.create_session(uid)
    import server.app as app_mod
    c = TestClient(app_mod.app); c.headers.update({"X-Session": tok})
    assert c.get("/api/admin/members").status_code == 403


def test_admin_list_and_delete(tmp_path, monkeypatch):
    a = _mk(tmp_path, monkeypatch)
    admin = a.upsert_user("hh09080", "Passw0rd!!xx", "AK", "AS", "OR", "1",
                          "https://openapi.koreainvestment.com:9443", is_admin=True)
    a.upsert_user("victim", "Passw0rd!!xx", "AK2", "AS2", "OR2", "2",
                  "https://openapivts.koreainvestment.com:29443")
    tok = a.create_session(admin)
    import server.app as app_mod
    c = TestClient(app_mod.app); c.headers.update({"X-Session": tok})
    ms = c.get("/api/admin/members").json()["members"]
    assert {m["username"] for m in ms} == {"hh09080", "victim"}
    assert next(m for m in ms if m["username"] == "victim")["is_mock"] is True
    assert c.post("/api/admin/members/delete",
                  json={"username": "hh09080"}).status_code == 400
    assert c.post("/api/admin/members/delete",
                  json={"username": "victim"}).status_code == 200
    assert a.find_user_by_username("victim") is None
```

- [ ] **Step 2: 실패 확인**

Run: `python3.11 -m pytest tests/test_admin_members.py -q`
Expected: FAIL — 라우트 미정의.

- [ ] **Step 3: 구현 — ADMIN 라우트 (프로필 라우트 뒤)**
```python
class AdminDeleteReq(BaseModel):
    username: str

@app.get("/api/admin/members")
async def admin_members(request: Request):
    _require_admin(request)
    return {"members": auth_store.list_members()}

@app.post("/api/admin/members/delete")
async def admin_member_delete(req: AdminDeleteReq, request: Request):
    me = _require_admin(request)
    target = auth_store.find_user_by_username((req.username or "").strip())
    if not target:
        raise HTTPException(404, "해당 회원을 찾을 수 없습니다.")
    if target["id"] == me:
        raise HTTPException(400, "본인 계정은 삭제할 수 없습니다.")
    if target.get("is_admin"):
        raise HTTPException(400, "ADMIN 계정은 삭제할 수 없습니다(단독 ADMIN 보호).")
    auth_store.delete_user(target["id"])
    import shutil
    from pathlib import Path
    shutil.rmtree(Path(__file__).resolve().parent.parent / "data" /
                  "profiles" / str(target["id"]), ignore_errors=True)
    auth_store.audit("admin_delete_member", username=req.username,
                     ip=_client_ip(request), outcome="ok", detail="")
    return {"ok": True}
```

- [ ] **Step 4: 통과 확인 + 전체 회귀**

Run: `python3.11 -m pytest tests/test_admin_members.py -q && python3.11 -m pytest -q`
Expected: PASS, 전체 그린.

- [ ] **Step 5: 커밋**
```bash
git add server/app.py tests/test_admin_members.py
git commit -m "feat(api): ADMIN 회원 현황(읽기전용)·완전삭제(본인/ADMIN 보호)"
```

---

## Task 7: index.html — 배지 이동 + 프로필 모달 + ADMIN 섹션 + 복구 2필드

**Files:** Modify `server/static/index.html` (CSS ~27/224, 상단바 ~318-319, 복구패널 ~262-265, 로그인 폼 ~248-251, JS ~507-520)

UI는 수동 검증(스펙 §7). **서버 데이터는 절대 innerHTML 금지 — createElement+textContent+closure.**

- [ ] **Step 1: 배지를 통신로그 옆으로(데스크톱 표시)**

line ~27 `#badgeMirror{display:none;...}` →
`#badgeMirror{display:inline-flex;margin-left:6px;vertical-align:middle}`.
`@media`(line ~224)의 중복 `#badgeMirror{display:inline-flex}` 제거.
상단바 `<div id="badge" class="badge badge-idle">IDLE</div>`(line ~319) 삭제.
`updateBadge()` 의 `#badge` 처리는 이미 `if(b)` 가드 — 동작 확인만(변경 불필요).

- [ ] **Step 2: 🚪 → 사람 아이콘 + 프로필 모달 마크업(정적)**

`<div id="acctChip" title="로그아웃">🚪 로그아웃</div>`(line ~318) →
`<div id="acctChip" title="프로필">👤</div>`.
`#acctChip` CSS(빨강, line ~306-307) 를 중립색으로:
`color:#cbd5e1;background:#1a2231;border:1px solid #2a3344` (hover 동일 톤).
CSS 추가: `.pf-sec{border-top:1px solid #232c3b;padding:12px 0}.pf-sec input{width:100%;box-sizing:border-box;margin:4px 0;padding:8px;border-radius:7px;border:1px solid #2a3344;background:#0e131d;color:#e8edf6}`
`#loginOv` div 뒤(동일 레벨)에 **데이터 없는 정적 마크업**만 추가:
```html
<div id="profileOv" style="position:fixed;inset:0;z-index:9998;display:none;align-items:center;justify-content:center;background:#0b0f1af5">
  <div style="background:#11161f;border:1px solid #2a3344;border-radius:14px;padding:22px;width:min(440px,92vw);max-height:88vh;overflow:auto">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
      <div><b id="pf_user"></b> <span id="pf_admin" style="display:none;font-size:10px;color:#fbbf24;border:1px solid #fbbf24;border-radius:5px;padding:1px 6px;margin-left:6px">ADMIN</span></div>
      <button onclick="closeProfile()" class="btn btn-ghost">✕</button>
    </div>
    <div class="pf-sec"><button class="btn" onclick="pfLogout()">로그아웃</button></div>
    <div class="pf-sec"><b>비밀번호 변경</b><input id="pf_cur" type="password" placeholder="현재 비밀번호"><input id="pf_new" type="password" placeholder="새 비밀번호(10자+특수문자)"><button class="btn" onclick="pfChangePw()">변경</button> <span id="pf_pwmsg"></span></div>
    <div class="pf-sec"><b>정보 변경</b><input id="pf_or" placeholder="OpenRouter Key(미입력=유지)"><input id="pf_ak" placeholder="KIS App Key"><input id="pf_as" placeholder="KIS App Secret"><input id="pf_acct2" placeholder="한국투자증권 계좌번호"><input id="pf_url" placeholder="KIS Base URL"><button class="btn" onclick="pfSaveCreds()">저장</button> <span id="pf_cmsg"></span></div>
    <div class="pf-sec"><b>사장님 상시 지시사항</b><div id="pf_dirs"></div><input id="pf_dir" placeholder="새 지시사항"><button class="btn" onclick="pfAddDir()">추가</button></div>
    <div class="pf-sec" id="pf_admin_sec" style="display:none"><b>회원 관리 (ADMIN)</b><div id="pf_members"></div></div>
    <div class="pf-sec"><button class="btn" style="color:#fca5a5" onclick="pfDeleteAccount()">회원 탈퇴</button></div>
  </div>
</div>
```

- [ ] **Step 3: 프로필 JS (XSS-safe — innerHTML 금지)**

`acctChip.onclick`(기존 로그아웃 confirm, line ~519) 교체 + 함수 추가.
서버 데이터는 `textContent`/`createElement`/closure 로만 렌더:
```javascript
document.getElementById('acctChip').onclick=()=>openProfile();
async function openProfile(){
  const m=await fetch(API+'/api/me').then(r=>r.json()).catch(()=>({}));
  document.getElementById('pf_user').textContent=m.username||'계정';
  document.getElementById('pf_admin').style.display=m.is_admin?'inline-block':'none';
  document.getElementById('pf_admin_sec').style.display=m.is_admin?'block':'none';
  if(m.is_admin)loadMembers();
  loadDirs();
  document.getElementById('profileOv').style.display='flex';
}
function closeProfile(){document.getElementById('profileOv').style.display='none';}
async function pfLogout(){await fetch(API+'/api/logout',{method:'POST'});location.reload();}
async function pfChangePw(){
  const r=await fetch(API+'/api/profile/password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({current:document.getElementById('pf_cur').value,new:document.getElementById('pf_new').value})});
  document.getElementById('pf_pwmsg').textContent=r.ok?'변경됨':((await r.json()).detail||'실패');}
async function pfSaveCreds(){
  const g=id=>document.getElementById(id).value,b={};
  if(g('pf_or'))b.openrouter_key=g('pf_or');if(g('pf_ak'))b.kis_app_key=g('pf_ak');
  if(g('pf_as'))b.kis_app_secret=g('pf_as');if(g('pf_acct2'))b.kis_account_no=g('pf_acct2');
  if(g('pf_url'))b.kis_base_url=g('pf_url');
  const r=await fetch(API+'/api/profile/credentials',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});
  document.getElementById('pf_cmsg').textContent=r.ok?'저장됨':((await r.json()).detail||'실패');}
async function loadDirs(){
  const d=((await fetch(API+'/api/profile/directives').then(r=>r.json()))||{}).directives||[];
  const box=document.getElementById('pf_dirs');box.textContent='';
  if(!d.length){const i=document.createElement('i');i.style.color='#7a869c';i.textContent='없음';box.appendChild(i);return;}
  d.forEach(x=>{const row=document.createElement('div');row.style.cssText='display:flex;justify-content:space-between;gap:6px;padding:3px 0';
    const s=document.createElement('span');s.textContent=x.text;
    const btn=document.createElement('button');btn.textContent='✕';btn.onclick=()=>pfDelDir(x.id);
    row.appendChild(s);row.appendChild(btn);box.appendChild(row);});}
async function pfAddDir(){const t=document.getElementById('pf_dir');if(!t.value.trim())return;
  await fetch(API+'/api/profile/directives',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:t.value})});t.value='';loadDirs();}
async function pfDelDir(id){await fetch(API+'/api/profile/directives/'+encodeURIComponent(id),{method:'DELETE'});loadDirs();}
async function loadMembers(){
  const r=await fetch(API+'/api/admin/members');const box=document.getElementById('pf_members');box.textContent='';
  if(!r.ok){box.textContent='권한 없음';return;}
  const ms=((await r.json())||{}).members||[];
  ms.forEach(m=>{const row=document.createElement('div');row.style.cssText='display:flex;justify-content:space-between;gap:6px;padding:4px 0;border-bottom:1px solid #1c2533';
    const s=document.createElement('span');s.textContent=m.username+' ';
    if(m.is_admin){const a=document.createElement('b');a.style.color='#fbbf24';a.textContent='ADMIN ';s.appendChild(a);}
    const mode=document.createElement('span');mode.style.color=m.is_mock?'#fbbf24':'#34d399';mode.textContent=m.is_mock?'모의':'실거래';s.appendChild(mode);
    row.appendChild(s);
    if(!m.is_admin){const btn=document.createElement('button');btn.textContent='삭제';btn.onclick=()=>pfDelMember(m.username);row.appendChild(btn);}
    box.appendChild(row);});}
async function pfDelMember(u){if(!confirm(u+' 회원을 완전 삭제합니다. 되돌릴 수 없습니다.'))return;
  await fetch(API+'/api/admin/members/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:u})});loadMembers();}
async function pfDeleteAccount(){
  if(!confirm('정말 회원 탈퇴하시겠습니까? 계정·자격증명·지시사항이 영구 삭제됩니다.'))return;
  const pw=prompt('확인을 위해 비밀번호를 입력하세요');if(!pw)return;
  const r=await fetch(API+'/api/profile/delete_account',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:pw})});
  if(r.ok){alert('탈퇴 완료');location.reload();}else{alert(((await r.json())||{}).detail||'실패');}}
```
`#acctChip` 텍스트 고정 코드(line ~507-508 `c.textContent='🚪 로그아웃'`) →
`c.textContent='👤'`.

- [ ] **Step 4: 복구 패널 2필드로**

`#recoverPanel`(line ~265~) 입력을 **한국투자증권 계좌번호 + 한국투자증권
App Secret** 2개로 교체(기존 App Key/OpenRouter 입력 제거). 복구 호출 JS body:
아이디 찾기 `{kis_account_no, kis_app_secret}`, 비번 재설정
`{username, kis_account_no, kis_app_secret, new_password}`. 응답 메시지는
`textContent` 로만 표시(innerHTML 금지).

- [ ] **Step 5: 수동 검증 (서버 재시작은 Task 9)**

코드 정합·브라우저 콘솔 에러 없음 확인. 가능하면 별도 포트로 임시 기동해
👤→모달, 비번변경, 지시 CRUD, 복구 2필드, (ADMIN)회원목록, 배지 위치 확인.
악성 username/지시문 입력해도 스크립트 실행 안 됨(textContent) 확인.

- [ ] **Step 6: 커밋**
```bash
git add server/static/index.html
git commit -m "feat(spa): 배지 이동 + 프로필 모달(XSS-safe) + ADMIN 회원관리 + 복구 2필드"
```

---

## Task 8: 안드로이드 LoginScreen 복구 폼 2필드 + APK 재빌드

**Files:** Modify `arquant_mobile/.../network/ArQuantApi.kt`, `.../viewmodel/AuthViewModel.kt`, `.../ui/screens/LoginScreen.kt`

- [ ] **Step 1: API 모델 2인자**

`ArQuantApi.kt`: `RecoverIdRequest` → `kisAccountNo`(`@SerialName("kis_account_no")`),
`kisAppSecret`(`@SerialName("kis_app_secret")`). `RecoverPwRequest` →
`username`, `kisAccountNo`, `kisAppSecret`, `newPassword`. 기존
kisAppKey/openrouterKey 필드 제거.

- [ ] **Step 2: ViewModel/Repository 시그니처 2인자**

`AuthViewModel.kt`(및 Repository 존재 시) recoverId/recoverPassword 인자를
`kisAccountNo, kisAppSecret`(+username/newPassword) 로 변경.

- [ ] **Step 3: LoginScreen 복구 폼 2필드**

`LoginScreen.kt` `RecoverIdForm`/`RecoverPwForm` 입력을 "한국투자증권
계좌번호" + "한국투자증권 App Secret" 2개로 교체(App Key/OpenRouter 제거).

- [ ] **Step 4: APK 재빌드**
```bash
cd /home/opc/projects/ArQuant && rm -f /home/opc/android-build/out/*.apk
docker run --rm --platform linux/amd64 \
  -v /home/opc/projects/ArQuant/arquant_mobile:/workspace \
  -v arcaive-android-gradle-cache:/root/.gradle \
  -v /home/opc/android-build/out:/out \
  -e GRADLE_USER_HOME=/root/.gradle \
  arcaive-android-build:jdk17-sdk35 '
    set -e
    echo "sdk.dir=${ANDROID_SDK_ROOT}" > /workspace/local.properties
    chmod +x /workspace/gradlew; cd /workspace
    ./gradlew --no-daemon :app:assembleDebug --stacktrace
    find /workspace -path "*/outputs/apk/*" -name "*.apk" -exec cp -v {} /out/ \;'
cp /home/opc/android-build/out/app-debug.apk /home/opc/projects/ArQuant/ArQuant.apk
unzip -l /home/opc/projects/ArQuant/ArQuant.apk | grep classes.dex
```
Expected: `BUILD SUCCESSFUL`, classes.dex 존재.

- [ ] **Step 5: 커밋**
```bash
git add arquant_mobile/app/src/main/java/com/arquant/mobile/network/ArQuantApi.kt \
        arquant_mobile/app/src/main/java/com/arquant/mobile/viewmodel/AuthViewModel.kt \
        arquant_mobile/app/src/main/java/com/arquant/mobile/ui/screens/LoginScreen.kt
git commit -m "feat(android): 복구 폼 2필드(계좌번호+App Secret) + APK 재빌드"
```

---

## Task 9: 통합 검증 · 일괄 배포 · 모니터링

- [ ] **Step 1: 전체 회귀**

Run: `python3.11 -m pytest -q` → 전체 그린(기존 140 + 신규). 실패 시 해당 Task 복귀.

- [ ] **Step 2: 브랜치 머지**
```bash
git checkout main && git merge --no-ff feature/profile-system
python3.11 -m pytest -q
```
(push 는 사용자 명시 지시 시에만.)

- [ ] **Step 3: 일괄 배포 (UUP fix 포함) — 사용자 확인 후**

`sudo systemctl restart arquant` 는 **사용자 승인 후** 실행(실거래 인프라).
부팅 로그에서 `auth 마이그레이션 완료: ... 계좌bidx백필 N` 확인.

- [ ] **Step 4: 헬스·스모크**

`curl -s localhost:8500/health` → ok. 로그인/프로필/복구 2필드 1회 수동.

- [ ] **Step 5: 사이클 모니터링**

익일 한국 장 마감까지 사이클별 모니터링(`/api/status`·`claude_response.json`·
주문 결과 — 특히 US 주문 정상 단가 전송[UUP fix] 확인).

---

## 자체 검토 (작성자 체크)

- **스펙 커버리지:** 배지(T7-S1)·프로필 5기능(T5,T7)·ADMIN 현황/삭제(T6,T7)·복구 2인자+마이그레이션(T1,T2,T4)·안드로이드(T8)·배포/모니터링(T9). 스펙 전 항목 매핑. ADMIN 승격/실전모의 전환 — 미구현(스펙 비목표) 일치.
- **플레이스홀더:** 코드 스텝 전부 실제 코드. UI(T7/T8)는 스펙 §7대로 수동검증 + 구체 anchor·코드.
- **타입 일관성:** `find_username_by_factors(kis_account_no, kis_app_secret)`·`reset_password_by_factors(username, kis_account_no, kis_app_secret, new_password)`·`delete_user(uid)->bool`·`list_members()->[{id,username,created_at,last_login_at,is_admin,is_mock}]`·`change_password(uid,current,new)`·`update_credentials(uid,*,...)`·`_require_admin(request)->uid` — Task 1~6 일관.
- **순서 안전:** Task2 시그니처 교체 시 `test_auth_recovery.py` 동시 갱신, `test_recovery_endpoints.py` 는 Task4 에서 그린(명시). 각 Task 종료 시 신규 그린, 전체 그린은 Task4 이후.
- **보안:** 모든 신규 API 세션/ADMIN 게이트, 미인증 401·비ADMIN 403. UI 는 innerHTML 금지(textContent/createElement) — XSS 차단. 탈퇴/회원삭제 비번·확인 게이트 + 본인/단독ADMIN 보호.
