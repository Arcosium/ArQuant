# ArQuant Login Overhaul — Backend Implementation Plan (Plan 1 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace plaintext-compare auth with argon2id, add blind-index account recovery, rate-limiting + audit, and stop collecting per-user DART/label — all in the Python backend, independently pytest-verifiable.

**Architecture:** All auth-data logic lives in `infra/auth_store.py` (hashing, blind index, migration, recovery lookups). A new focused `infra/rate_limit.py` holds the in-process limiter. `server/app.py` gets two thin public endpoints + limiter/audit wiring. A one-shot idempotent startup migration upgrades existing rows. Frontends (Plans 2/3) consume these endpoints later; this plan ships working software on its own (old web/Android keep working — `verify_password` stays signature-compatible and migrates legacy rows transparently).

**Tech Stack:** Python 3.11, FastAPI, SQLite, `cryptography` (Fernet + HKDF, already pinned), new `argon2-cffi`, pytest.

**Spec:** `docs/superpowers/specs/2026-05-19-arquant-login-overhaul-design.md` (§2 blind-index, §5 DART, §6 argon2 one-shot, §7 rate-limit/audit).

---

## File Structure

- **Modify** `requirements.txt` — add `argon2-cffi` pin.
- **Modify** `infra/auth_store.py` — add: `_FERNET_RAW` capture, `bidx()`/HKDF key, argon2 `_PH` + `hash`/`verify` path, schema ALTERs in `init()`, rewritten `upsert_user`, rewritten `verify_password`, `_set_password_hash`, `find_username_by_factors`, `reset_password_by_factors`, `migrate_passwords_and_bidx`, `audit`. Single responsibility: auth data layer.
- **Create** `infra/rate_limit.py` — `SlidingWindowLimiter` (one responsibility: throttling).
- **Modify** `server/app.py` — `_PUBLIC_PATHS` += recover paths; `RecoverIdReq`/`RecoverPwReq`; `_client_ip`; limiter instances; `/api/recover_id`, `/api/recover_password`; apply limiter+audit to `/api/login`, `/api/register`; drop `dart_key`/`label` from the `upsert_user` call in `register()`.
- **Create tests** `tests/test_auth_bidx.py`, `tests/test_auth_hashing.py`, `tests/test_auth_recovery.py`, `tests/test_auth_migration.py`, `tests/test_rate_limit.py`, `tests/test_register_no_dart.py`.

**Shared test fixture pattern** (used by every auth test — points `auth_store` at a tmp DB and resets module globals so tests are isolated and deterministic):

```python
import importlib, pytest
from infra import auth_store

@pytest.fixture
def fresh_auth(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_store, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(auth_store, "_DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(auth_store, "_FERNET_KEY_PATH", tmp_path / ".fernet.key")
    monkeypatch.setattr(auth_store, "_AUDIT_PATH", tmp_path / "auth_audit.log")
    monkeypatch.setattr(auth_store, "_INITED", False)
    monkeypatch.setattr(auth_store, "_FERNET", None)
    monkeypatch.setattr(auth_store, "_FERNET_RAW", None)
    monkeypatch.setattr(auth_store, "_BIDX_KEY", None)
    auth_store.init()
    return auth_store
```

---

## Task 1: Add argon2-cffi dependency

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Install the library**

Run: `python3.11 -m pip install argon2-cffi`
Expected: ends with `Successfully installed argon2-cffi-<ver> argon2-cffi-bindings-<ver>`

- [ ] **Step 2: Capture the resolved version**

Run: `python3.11 -c "import importlib.metadata as m; print('argon2-cffi==' + m.version('argon2-cffi'))"`
Expected: prints e.g. `argon2-cffi==23.1.0`

- [ ] **Step 3: Pin it in requirements.txt**

Insert the printed line between `aiohttp==3.13.3` and `beautifulsoup4==4.14.3` (alphabetical: aiohttp < argon2-cffi < beautifulsoup4):

```
aiohttp==3.13.3
argon2-cffi==25.1.0
beautifulsoup4==4.14.3
```

(Use the exact version Step 2 printed, not necessarily 25.1.0.)

- [ ] **Step 4: Verify import works**

Run: `python3.11 -c "from argon2 import PasswordHasher; from argon2.exceptions import VerifyMismatchError; print('ok')"`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add requirements.txt
git commit -m "build: add argon2-cffi for password hashing"
```

---

## Task 2: Capture raw Fernet key + blind-index helper

**Files:**
- Modify: `infra/auth_store.py` (imports area ~17-28; `_ensure_fernet` 86-111; add module globals + helpers)
- Test: `tests/test_auth_bidx.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_auth_bidx.py
import pytest
from infra import auth_store

@pytest.fixture
def fresh_auth(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_store, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(auth_store, "_DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(auth_store, "_FERNET_KEY_PATH", tmp_path / ".fernet.key")
    monkeypatch.setattr(auth_store, "_AUDIT_PATH", tmp_path / "auth_audit.log")
    monkeypatch.setattr(auth_store, "_INITED", False)
    monkeypatch.setattr(auth_store, "_FERNET", None)
    monkeypatch.setattr(auth_store, "_FERNET_RAW", None)
    monkeypatch.setattr(auth_store, "_BIDX_KEY", None)
    auth_store.init()
    return auth_store

def test_bidx_deterministic_and_normalized(fresh_auth):
    a = fresh_auth.bidx("APPKEY-123")
    assert a == fresh_auth.bidx("APPKEY-123")          # deterministic
    assert a == fresh_auth.bidx("  APPKEY-123  ")      # strips like registration
    assert a != fresh_auth.bidx("APPKEY-124")          # collision-free for distinct
    assert len(a) == 64 and all(c in "0123456789abcdef" for c in a)  # sha256 hex
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_auth_bidx.py -v`
Expected: FAIL — `AttributeError: module 'infra.auth_store' has no attribute 'bidx'`

- [ ] **Step 3: Implement**

In `infra/auth_store.py`, add to the stdlib imports block (after `import time`):

```python
import hashlib
import hmac as _hmac
```

Add to the `cryptography` import line area (after `from cryptography.fernet import Fernet, InvalidToken`):

```python
from cryptography.hazmat.primitives import hashes as _hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
```

Add module globals near `_INITED = False`:

```python
_FERNET_RAW: Optional[bytes] = None
_BIDX_KEY: Optional[bytes] = None
```

In `_ensure_fernet`, change the signature line `global _FERNET` to:

```python
    global _FERNET, _FERNET_RAW
```

and immediately before `_FERNET = Fernet(key)` add:

```python
    _FERNET_RAW = key
```

Add these helpers after `decrypt()` (after line ~127):

```python
def _norm(v: str) -> str:
    """복구 인자 정규화 — 등록 저장 시(.strip())와 반드시 동일해야 매칭됨."""
    return (v or "").strip()


def _bidx_key() -> bytes:
    """Fernet 키에서 HKDF-SHA256 으로 파생한 블라인드 인덱스 전용 키.
    별도 시크릿을 두지 않아 운영 부담이 없다(단, Fernet 키에 결합됨)."""
    global _BIDX_KEY
    if _BIDX_KEY is not None:
        return _BIDX_KEY
    _ensure_fernet()
    if not _FERNET_RAW:
        raise RuntimeError("blind-index 키 파생 불가 — Fernet 키 없음")
    _BIDX_KEY = HKDF(algorithm=_hashes.SHA256(), length=32, salt=None,
                     info=b"arquant-bidx-v1").derive(_FERNET_RAW)
    return _BIDX_KEY


def bidx(value: str) -> str:
    """결정론적 블라인드 인덱스(HMAC-SHA256 hex) — 평문 노출 없이 동치 조회용."""
    return _hmac.new(_bidx_key(), _norm(value).encode("utf-8"),
                     hashlib.sha256).hexdigest()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_auth_bidx.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add infra/auth_store.py tests/test_auth_bidx.py
git commit -m "feat(auth): HKDF-derived blind-index helper"
```

---

## Task 3: argon2 password hashing helpers

**Files:**
- Modify: `infra/auth_store.py` (add `_PH`, `hash_password`, `verify_pw_hash`)
- Test: `tests/test_auth_hashing.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_auth_hashing.py
from infra import auth_store

def test_hash_and_verify_roundtrip():
    h = auth_store.hash_password("Sup3r$ecret!")
    assert h.startswith("$argon2id$")
    assert auth_store.verify_pw_hash(h, "Sup3r$ecret!") is True
    assert auth_store.verify_pw_hash(h, "wrong") is False
    assert auth_store.verify_pw_hash("", "anything") is False
    assert auth_store.verify_pw_hash("not-a-hash", "x") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_auth_hashing.py -v`
Expected: FAIL — `AttributeError: ... has no attribute 'hash_password'`

- [ ] **Step 3: Implement**

In `infra/auth_store.py`, add near the cryptography imports:

```python
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerificationError
```

Add after the `bidx` helper:

```python
_PH = PasswordHasher()  # argon2id, library defaults (tune later if needed)


def hash_password(pw: str) -> str:
    """평문 비밀번호 → argon2id 해시 문자열."""
    return _PH.hash(pw or "")


def verify_pw_hash(stored_hash: str, pw: str) -> bool:
    """저장된 argon2 해시와 평문 일치 검증. 불일치/빈 해시/손상 해시는 False."""
    if not stored_hash:
        return False
    try:
        return _PH.verify(stored_hash, pw or "")
    except (VerificationError, InvalidHash):  # VerifyMismatchError ⊂ VerificationError
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_auth_hashing.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add infra/auth_store.py tests/test_auth_hashing.py
git commit -m "feat(auth): argon2id hash/verify helpers"
```

---

## Task 4: Schema migration — add password_hash + 3 bidx columns

**Files:**
- Modify: `infra/auth_store.py` `init()` (after the is_admin migration block, ~line 181)
- Test: `tests/test_auth_migration.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_auth_migration.py
import sqlite3, pytest
from infra import auth_store

@pytest.fixture
def fresh_auth(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_store, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(auth_store, "_DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(auth_store, "_FERNET_KEY_PATH", tmp_path / ".fernet.key")
    monkeypatch.setattr(auth_store, "_AUDIT_PATH", tmp_path / "auth_audit.log")
    monkeypatch.setattr(auth_store, "_INITED", False)
    monkeypatch.setattr(auth_store, "_FERNET", None)
    monkeypatch.setattr(auth_store, "_FERNET_RAW", None)
    monkeypatch.setattr(auth_store, "_BIDX_KEY", None)
    auth_store.init()
    return auth_store

def test_new_columns_exist(fresh_auth):
    con = sqlite3.connect(fresh_auth._DB_PATH)
    cols = {r[1] for r in con.execute("PRAGMA table_info(users)").fetchall()}
    con.close()
    assert {"password_hash", "kis_app_key_bidx",
            "kis_app_secret_bidx", "openrouter_key_bidx"} <= cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_auth_migration.py::test_new_columns_exist -v`
Expected: FAIL — assertion: columns missing

- [ ] **Step 3: Implement**

In `infra/auth_store.py` `init()`, directly after the existing `if ADMIN_USERNAMES:` UPDATE block (after line 181, still inside the `with _DB_LOCK, _connect() as conn:`), add:

```python
        for _c in ("password_hash", "kis_app_key_bidx",
                   "kis_app_secret_bidx", "openrouter_key_bidx"):
            if _c not in cols:
                conn.execute(
                    f"ALTER TABLE users ADD COLUMN {_c} TEXT NOT NULL DEFAULT ''")
                logger.info("auth_store 마이그레이션: users.%s 컬럼 추가", _c)
```

(`cols` is already computed at line 174 — reuse it.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_auth_migration.py::test_new_columns_exist -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add infra/auth_store.py tests/test_auth_migration.py
git commit -m "feat(auth): add password_hash + blind-index columns"
```

---

## Task 5: Rewrite `upsert_user` to store hash + bidx (no usable plaintext)

**Files:**
- Modify: `infra/auth_store.py` `upsert_user` (lines 194-239 — full replacement)
- Test: `tests/test_auth_migration.py` (add)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_auth_migration.py`:

```python
def test_upsert_stores_hash_and_bidx_not_plaintext(fresh_auth):
    uid = fresh_auth.upsert_user(
        username="alice", password="Sup3r$ecret!",
        kis_app_key="AK", kis_app_secret="AS", openrouter_key="OR",
        kis_account_no="123-01", kis_base_url="", dart_key="", label="")
    import sqlite3
    con = sqlite3.connect(fresh_auth._DB_PATH); con.row_factory = sqlite3.Row
    r = con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone(); con.close()
    assert r["password_enc"] == ""                       # no encrypted password
    assert r["password_hash"].startswith("$argon2id$")
    assert r["kis_app_key_bidx"] == fresh_auth.bidx("AK")
    assert r["kis_app_secret_bidx"] == fresh_auth.bidx("AS")
    assert r["openrouter_key_bidx"] == fresh_auth.bidx("OR")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_auth_migration.py::test_upsert_stores_hash_and_bidx_not_plaintext -v`
Expected: FAIL (password_enc not empty / bidx empty)

- [ ] **Step 3: Implement — replace the whole `upsert_user` function (lines 194-239) with:**

```python
def upsert_user(username: str, password: str, kis_app_key: str, kis_app_secret: str,
                openrouter_key: str, kis_account_no: str, kis_base_url: str,
                dart_key: str = "", label: str = "", is_admin: bool = False) -> int:
    """username 기준 upsert. 비밀번호는 argon2id 해시로만 저장(password_enc 미사용),
    복구용 블라인드 인덱스도 함께 기록. user_id 반환.

    is_admin: 신규 생성 시에만 적용. 기존 ADMIN 강등 안 함."""
    init()
    now = time.time()
    username = (username or "").strip()
    base_url = (kis_base_url or "https://openapi.koreainvestment.com:9443").strip()
    label = (label or username).strip()
    vals = dict(
        password_hash=hash_password(password),
        kis_app_key_enc=encrypt(kis_app_key),
        kis_app_secret_enc=encrypt(kis_app_secret),
        openrouter_key_enc=encrypt(openrouter_key),
        kis_account_no_enc=encrypt(kis_account_no),
        kis_base_url=base_url,
        dart_key_enc=encrypt(dart_key) if (dart_key or "").strip() else "",
        label=label,
        kis_app_key_bidx=bidx(kis_app_key),
        kis_app_secret_bidx=bidx(kis_app_secret),
        openrouter_key_bidx=bidx(openrouter_key),
    )
    with _DB_LOCK, _connect() as conn:
        row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if row:
            uid = int(row["id"])
            conn.execute(
                """UPDATE users SET password_hash=?, password_enc='',
                   kis_app_key_enc=?, kis_app_secret_enc=?, openrouter_key_enc=?,
                   kis_account_no_enc=?, kis_base_url=?, dart_key_enc=?, label=?,
                   kis_app_key_bidx=?, kis_app_secret_bidx=?, openrouter_key_bidx=?,
                   last_login_at=?, last_validated_at=? WHERE id=?""",
                (vals["password_hash"], vals["kis_app_key_enc"], vals["kis_app_secret_enc"],
                 vals["openrouter_key_enc"], vals["kis_account_no_enc"], vals["kis_base_url"],
                 vals["dart_key_enc"], vals["label"], vals["kis_app_key_bidx"],
                 vals["kis_app_secret_bidx"], vals["openrouter_key_bidx"], now, now, uid),
            )
            return uid
        adm = 1 if (is_admin or username in ADMIN_USERNAMES) else 0
        cur = conn.execute(
            """INSERT INTO users (username, password_enc, password_hash,
               kis_app_key_enc, kis_app_secret_enc, openrouter_key_enc,
               kis_account_no_enc, kis_base_url, dart_key_enc, label,
               kis_app_key_bidx, kis_app_secret_bidx, openrouter_key_bidx,
               is_admin, created_at, last_login_at, last_validated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (username, "", vals["password_hash"], vals["kis_app_key_enc"],
             vals["kis_app_secret_enc"], vals["openrouter_key_enc"],
             vals["kis_account_no_enc"], vals["kis_base_url"], vals["dart_key_enc"],
             vals["label"], vals["kis_app_key_bidx"], vals["kis_app_secret_bidx"],
             vals["openrouter_key_bidx"], adm, now, now, now),
        )
        return int(cur.lastrowid)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_auth_migration.py -v`
Expected: PASS (both tests). Run full suite — all green (verify_password integration is covered by Task 6's own tests).

- [ ] **Step 5: Commit**

```bash
git add infra/auth_store.py tests/test_auth_migration.py
git commit -m "feat(auth): upsert_user stores argon2 hash + blind index, no plaintext"
```

---

## Task 6: Rewrite `verify_password` (argon2 + legacy migrate-on-login)

**Files:**
- Modify: `infra/auth_store.py` — add `password_hash` to `_row_to_creds` (252-area), add `_set_password_hash`, replace `verify_password` (291-298)
- Test: `tests/test_auth_hashing.py` (add)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_auth_hashing.py`:

```python
import pytest
from infra import auth_store as A

@pytest.fixture
def fresh_auth(tmp_path, monkeypatch):
    for name, val in [("_DATA_DIR", tmp_path), ("_DB_PATH", tmp_path/"a.db"),
                      ("_FERNET_KEY_PATH", tmp_path/".k"),
                      ("_AUDIT_PATH", tmp_path/"au.log"), ("_INITED", False),
                      ("_FERNET", None), ("_FERNET_RAW", None), ("_BIDX_KEY", None)]:
        monkeypatch.setattr(A, name, val, raising=False)
    A.init(); return A

def test_verify_password_argon2_path(fresh_auth):
    fresh_auth.upsert_user("bob", "P@ssword12!", "k", "s", "o", "1-1", "", "", "")
    assert fresh_auth.verify_password("bob", "P@ssword12!")["username"] == "bob"
    assert fresh_auth.verify_password("bob", "nope") is None
    assert fresh_auth.verify_password("ghost", "x") is None

def test_verify_password_legacy_then_migrates(fresh_auth):
    # simulate a pre-migration row: password_enc set, password_hash empty
    uid = fresh_auth.upsert_user("carol", "Legacy$pw99", "k", "s", "o", "1-1", "", "", "")
    import sqlite3
    con = sqlite3.connect(fresh_auth._DB_PATH)
    con.execute("UPDATE users SET password_hash='', password_enc=? WHERE id=?",
                (fresh_auth.encrypt("Legacy$pw99"), uid)); con.commit(); con.close()
    assert fresh_auth.verify_password("carol", "Legacy$pw99")["username"] == "carol"
    con = sqlite3.connect(fresh_auth._DB_PATH); con.row_factory = sqlite3.Row
    r = con.execute("SELECT password_hash, password_enc FROM users WHERE id=?",
                     (uid,)).fetchone(); con.close()
    assert r["password_hash"].startswith("$argon2id$") and r["password_enc"] == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_auth_hashing.py -v`
Expected: FAIL (legacy still plaintext-compares / no migrate-on-login)

- [ ] **Step 3: Implement**

In `_row_to_creds` (the returned dict, ~line 252), add a key (mirror the `is_admin` defensive pattern):

```python
        "password_hash": row["password_hash"] if "password_hash" in row.keys() else "",
```

Add after `touch_login` (~line 304):

```python
def _set_password_hash(user_id: int, new_hash: str, clear_enc: bool = True) -> None:
    init()
    with _DB_LOCK, _connect() as conn:
        if clear_enc:
            conn.execute("UPDATE users SET password_hash=?, password_enc='' WHERE id=?",
                         (new_hash, int(user_id)))
        else:
            conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                         (new_hash, int(user_id)))
```

Replace `verify_password` (lines 291-298) with:

```python
def verify_password(username: str, password: str) -> Optional[Dict[str, Any]]:
    """argon2id 검증. 미마이그레이션(legacy) 행이면 복호-비교 후 즉시 해시로 승격.
    성공 시 자격증명 dict, 실패 시 None."""
    u = find_user_by_username(username)
    if not u:
        return None
    stored = u.get("password_hash") or ""
    if stored:
        if not verify_pw_hash(stored, password or ""):
            return None
        try:
            if _PH.check_needs_rehash(stored):
                _set_password_hash(u["id"], hash_password(password or ""), clear_enc=True)
        except Exception:
            logger.warning("argon2 rehash 점검 실패(user_id=%s)", u.get("id"))
        return u
    # legacy: password_hash 없음 → 복호-비교 후 해시 승격(1회성)
    if (u.get("password") or "") and u["password"] == (password or ""):
        _set_password_hash(u["id"], hash_password(password or ""), clear_enc=True)
        return u
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_auth_hashing.py tests/test_auth_migration.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add infra/auth_store.py tests/test_auth_hashing.py
git commit -m "feat(auth): argon2 verify_password with legacy migrate-on-login"
```

---

## Task 7: One-shot idempotent startup migration

**Files:**
- Modify: `infra/auth_store.py` — add `migrate_passwords_and_bidx()`
- Modify: `server/app.py` — call it in the existing startup event (near line 557)
- Test: `tests/test_auth_migration.py` (add)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_auth_migration.py`:

```python
def test_one_shot_migration_is_idempotent(fresh_auth):
    uid = fresh_auth.upsert_user("dave", "Migrate$99x", "AK", "AS", "OR",
                                 "1-1", "", "", "")
    import sqlite3
    con = sqlite3.connect(fresh_auth._DB_PATH)
    con.execute("UPDATE users SET password_hash='', password_enc=?, "
                "kis_app_key_bidx='', kis_app_secret_bidx='', openrouter_key_bidx='' "
                "WHERE id=?", (fresh_auth.encrypt("Migrate$99x"), uid))
    con.commit(); con.close()
    s1 = fresh_auth.migrate_passwords_and_bidx()
    assert s1 == {"pw": 1, "bidx": 1}
    s2 = fresh_auth.migrate_passwords_and_bidx()          # idempotent
    assert s2 == {"pw": 0, "bidx": 0}
    con = sqlite3.connect(fresh_auth._DB_PATH); con.row_factory = sqlite3.Row
    r = con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone(); con.close()
    assert r["password_hash"].startswith("$argon2id$") and r["password_enc"] == ""
    assert r["kis_app_key_bidx"] == fresh_auth.bidx("AK")

def test_migration_skips_row_with_corrupt_enc_no_data_loss(fresh_auth):
    uid = fresh_auth.upsert_user("eve", "Corrupt$pw9", "AK", "AS", "OR",
                                 "1-1", "", "", "")
    import sqlite3
    con = sqlite3.connect(fresh_auth._DB_PATH)
    # legacy + CORRUPT password_enc (not valid Fernet), bidx cleared
    con.execute("UPDATE users SET password_hash='', password_enc='not-a-valid-fernet-token', "
                "kis_app_key_bidx='', kis_app_secret_bidx='', openrouter_key_bidx='' "
                "WHERE id=?", (uid,))
    con.commit(); con.close()
    stats = fresh_auth.migrate_passwords_and_bidx()
    # corrupt row must be skipped, NOT counted, NOT mutated
    assert stats == {"pw": 0, "bidx": 0}
    con = sqlite3.connect(fresh_auth._DB_PATH); con.row_factory = sqlite3.Row
    r = con.execute("SELECT password_hash, password_enc, kis_app_key_bidx "
                     "FROM users WHERE id=?", (uid,)).fetchone(); con.close()
    # password_enc preserved (NOT blanked), no bogus hash, no bogus bidx written
    assert r["password_enc"] == "not-a-valid-fernet-token"
    assert r["password_hash"] == ""
    assert r["kis_app_key_bidx"] == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3.11 -m pytest tests/test_auth_migration.py::test_one_shot_migration_is_idempotent tests/test_auth_migration.py::test_migration_skips_row_with_corrupt_enc_no_data_loss -v`
Expected: FAIL — no attribute `migrate_passwords_and_bidx` (first) / corrupt row gets counted (second)

- [ ] **Step 3: Implement**

Add to `infra/auth_store.py` after `_set_password_hash`:

```python
def migrate_passwords_and_bidx() -> Dict[str, int]:
    """부팅 1회 실행(멱등). password_enc→argon2 해시 승격 + 누락된 블라인드
    인덱스 백필. 이미 마이그레이션된 행은 건너뜀. {'pw':n,'bidx':m} 반환.

    복호 실패(InvalidToken 또는 기타 예외) 시 해당 행을 건너뜀 — 데이터 훼손 방지.
    FernetKeyLost 는 _ensure_fernet 에서 루프 진입 전에 발생하므로 여기서 잡지 않음."""
    init()
    stats = {"pw": 0, "bidx": 0}
    with _DB_LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT id, password_enc, password_hash, "
            "kis_app_key_enc, kis_app_secret_enc, openrouter_key_enc, "
            "kis_app_key_bidx, kis_app_secret_bidx, openrouter_key_bidx FROM users"
        ).fetchall()
        for r in rows:
            try:
                updates: Dict[str, Any] = {}
                did_pw = False
                did_bidx = False

                # ── 비밀번호 승격 경로 ──────────────────────────────────────
                if not (r["password_hash"] or "") and (r["password_enc"] or ""):
                    dec_pw = decrypt(r["password_enc"])
                    if not dec_pw:
                        # 복호 실패 — 행 보존, 스킵 (데이터 훼손 방지)
                        logger.error(
                            "auth 마이그레이션: user_id=%s password_enc 복호 실패 — 행 보존(스킵)",
                            r["id"])
                        continue
                    updates["password_hash"] = hash_password(dec_pw)
                    updates["password_enc"] = ""
                    did_pw = True

                # ── bidx 백필 경로 ─────────────────────────────────────────
                if not (r["kis_app_key_bidx"] or ""):
                    enc_key = r["kis_app_key_enc"] or ""
                    enc_secret = r["kis_app_secret_enc"] or ""
                    enc_or = r["openrouter_key_enc"] or ""
                    if enc_key and enc_secret and enc_or:
                        dec_key = decrypt(enc_key)
                        dec_secret = decrypt(enc_secret)
                        dec_or = decrypt(enc_or)
                        if not (dec_key and dec_secret and dec_or):
                            # 하나 이상 복호 실패 — 행 전체 업데이트 없음 (부분 쓰기 방지)
                            logger.error(
                                "auth 마이그레이션: user_id=%s *_enc 복호 실패 — bidx 백필 스킵",
                                r["id"])
                            continue
                        updates["kis_app_key_bidx"] = bidx(dec_key)
                        updates["kis_app_secret_bidx"] = bidx(dec_secret)
                        updates["openrouter_key_bidx"] = bidx(dec_or)
                        did_bidx = True
                    # enc 자체가 비어있는 경우(빈 enc) — bidx 백필 대상 아님, 통과

                # 여기까지 오면 updates 는 안전하게 쓸 수 있는 값들만 포함
                if updates:
                    sets = ",".join(f"{k}=?" for k in updates)
                    conn.execute(f"UPDATE users SET {sets} WHERE id=?",
                                 (*updates.values(), int(r["id"])))
                if did_pw:
                    stats["pw"] += 1
                if did_bidx:
                    stats["bidx"] += 1
            except Exception as e:
                logger.error(
                    "auth 마이그레이션 행 실패 user_id=%s: %s — 스킵", r["id"], e)
                continue
    if stats["pw"] or stats["bidx"]:
        logger.info("auth 마이그레이션 완료: 해시승격 %d, bidx백필 %d",
                    stats["pw"], stats["bidx"])
    return stats
```

In `server/app.py`, find the startup event (`@app.on_event("startup")` whose body calls `auth_store.bootstrap_from_env()`). Add `auth_store.migrate_passwords_and_bidx()` immediately **before** the `seeded = auth_store.bootstrap_from_env()` call, with `FernetKeyLost` handled loudly (it signals total unrecoverability) and other exceptions swallowed:

```python
        try:
            auth_store.migrate_passwords_and_bidx()
        except auth_store.FernetKeyLost:
            logging.getLogger("auth_store").critical(
                "부팅 마이그레이션 중단 — Fernet 키 분실(전 계정 복호 불능). 키 복구 필요.")
            raise
        except Exception as e:
            logging.getLogger("auth_store").error("부팅 마이그레이션 실패(계속): %s", e)
        seeded = auth_store.bootstrap_from_env()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3.11 -m pytest tests/test_auth_migration.py -v`
Expected: PASS (all 4)

- [ ] **Step 5: Commit**

```bash
git add infra/auth_store.py server/app.py tests/test_auth_migration.py
git commit -m "fix(auth): migration must not corrupt rows on decrypt failure; surface FernetKeyLost"
```

---

## Task 8: Recovery lookups (blind-index)

**Files:**
- Modify: `infra/auth_store.py` — add `find_username_by_factors`, `reset_password_by_factors`
- Test: `tests/test_auth_recovery.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_auth_recovery.py
import pytest
from infra import auth_store as A

@pytest.fixture
def fresh_auth(tmp_path, monkeypatch):
    for n, v in [("_DATA_DIR", tmp_path), ("_DB_PATH", tmp_path/"a.db"),
                 ("_FERNET_KEY_PATH", tmp_path/".k"), ("_AUDIT_PATH", tmp_path/"au.log"),
                 ("_INITED", False), ("_FERNET", None), ("_FERNET_RAW", None),
                 ("_BIDX_KEY", None)]:
        monkeypatch.setattr(A, n, v)
    A.init(); return A

def test_find_username_by_factors(fresh_auth):
    fresh_auth.upsert_user("erin", "P@ss12345!", "AK1", "AS1", "OR1", "1-1", "", "", "")
    assert fresh_auth.find_username_by_factors("AK1", "AS1", "OR1") == "erin"
    assert fresh_auth.find_username_by_factors(" AK1 ", "AS1", "OR1") == "erin"  # norm
    assert fresh_auth.find_username_by_factors("AK1", "AS1", "WRONG") is None
    assert fresh_auth.find_username_by_factors("AK1", "AS1", "") is None

def test_reset_password_by_factors(fresh_auth):
    fresh_auth.upsert_user("fred", "OldP@ss123", "AK2", "AS2", "OR2", "1-1", "", "", "")
    assert fresh_auth.reset_password_by_factors(
        "fred", "AK2", "AS2", "OR2", "N3wP@ssword!") is True
    assert fresh_auth.verify_password("fred", "N3wP@ssword!")["username"] == "fred"
    assert fresh_auth.verify_password("fred", "OldP@ss123") is None
    # wrong factor → no reset
    assert fresh_auth.reset_password_by_factors(
        "fred", "AK2", "AS2", "BAD", "Another1!") is False
    # weak new pw → policy error
    with pytest.raises(ValueError):
        fresh_auth.reset_password_by_factors("fred", "AK2", "AS2", "OR2", "weak")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_auth_recovery.py -v`
Expected: FAIL — no attribute `find_username_by_factors`

- [ ] **Step 3: Implement** — add to `infra/auth_store.py` after `reset`/recovery area (e.g., after `get_user_credentials`):

```python
def find_username_by_factors(kis_app_key: str, kis_app_secret: str,
                             openrouter_key: str) -> Optional[str]:
    """세 자격증명이 모두 정확히 일치하는 단일 유저의 아이디 반환(없으면 None).
    블라인드 인덱스 단일 인덱스 조회 — 전체 복호 없음."""
    if not (_norm(kis_app_key) and _norm(kis_app_secret) and _norm(openrouter_key)):
        return None
    init()
    a, b, c = bidx(kis_app_key), bidx(kis_app_secret), bidx(openrouter_key)
    with _DB_LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT username FROM users WHERE kis_app_key_bidx=? AND "
            "kis_app_secret_bidx=? AND openrouter_key_bidx=?", (a, b, c)).fetchone()
    return row["username"] if row else None


def reset_password_by_factors(username: str, kis_app_key: str, kis_app_secret: str,
                              openrouter_key: str, new_password: str) -> bool:
    """아이디 + 세 자격증명이 모두 일치하면 새 비밀번호로 재설정. 정책 위반은
    ValueError. 일치 실패 시 False(아무 것도 바꾸지 않음)."""
    perr = password_policy_error(new_password or "")
    if perr:
        raise ValueError(perr)
    if not (_norm(kis_app_key) and _norm(kis_app_secret) and _norm(openrouter_key)):
        return False
    init()
    a, b, c = bidx(kis_app_key), bidx(kis_app_secret), bidx(openrouter_key)
    with _DB_LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE username=? AND kis_app_key_bidx=? AND "
            "kis_app_secret_bidx=? AND openrouter_key_bidx=?",
            (_norm(username), a, b, c)).fetchone()
        if not row:
            return False
        conn.execute("UPDATE users SET password_hash=?, password_enc='' WHERE id=?",
                     (hash_password(new_password), int(row["id"])))
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_auth_recovery.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add infra/auth_store.py tests/test_auth_recovery.py
git commit -m "feat(auth): blind-index recovery (find-id / reset-pw)"
```

---

## Task 9: In-process rate limiter

**Files:**
- Create: `infra/rate_limit.py`
- Test: `tests/test_rate_limit.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_rate_limit.py
import time
from infra.rate_limit import SlidingWindowLimiter

def test_sliding_window_trips_and_recovers():
    lim = SlidingWindowLimiter(max_hits=3, window_sec=0.3)
    assert lim.hit("ip1") is None
    assert lim.hit("ip1") is None
    assert lim.hit("ip1") is None
    retry = lim.hit("ip1")
    assert retry is not None and 0 < retry <= 0.3   # 4th blocked
    assert lim.hit("ip2") is None                   # other key independent
    time.sleep(0.32)
    assert lim.hit("ip1") is None                   # window slid → allowed
    lim.reset("ip1")
    assert lim.hit("ip1") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_rate_limit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'infra.rate_limit'`

- [ ] **Step 3: Implement**

```python
# infra/rate_limit.py
"""인-프로세스 슬라이딩 윈도우 레이트리미터.

단일 프로세스 uvicorn 기준(현재 배포). 멀티워커면 워커별로 독립 — Phase 2에서
필요 시 공유 저장소로 교체. 외부 의존성 없음."""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional


class SlidingWindowLimiter:
    def __init__(self, max_hits: int, window_sec: float):
        self.max = int(max_hits)
        self.win = float(window_sec)
        self._buckets: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str) -> Optional[float]:
        """1회 시도 기록. 허용이면 None, 한도 초과면 재시도까지 남은 초(>0)."""
        now = time.time()
        with self._lock:
            dq = self._buckets[key]
            cutoff = now - self.win
            while dq and dq[0] <= cutoff:
                dq.popleft()
            if len(dq) >= self.max:
                return max(0.001, self.win - (now - dq[0]))
            dq.append(now)
            return None

    def reset(self, key: str) -> None:
        with self._lock:
            self._buckets.pop(key, None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_rate_limit.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add infra/rate_limit.py tests/test_rate_limit.py
git commit -m "feat: in-process sliding-window rate limiter"
```

---

## Task 10: Auth audit log helper

**Files:**
- Modify: `infra/auth_store.py` — add `_AUDIT_PATH` global + `audit()`
- Test: `tests/test_auth_recovery.py` (add)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_auth_recovery.py`:

```python
import json

def test_audit_appends_jsonl_and_never_logs_secrets(fresh_auth):
    fresh_auth.audit("recover_id", username="erin", ip="1.2.3.4",
                     outcome="fail", detail="no-match")
    lines = (fresh_auth._AUDIT_PATH).read_text(encoding="utf-8").strip().splitlines()
    rec = json.loads(lines[-1])
    assert rec["event"] == "recover_id" and rec["outcome"] == "fail"
    assert rec["username"] == "erin" and rec["ip"] == "1.2.3.4"
    assert "detail" in rec and "ts" in rec
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_auth_recovery.py::test_audit_appends_jsonl_and_never_logs_secrets -v`
Expected: FAIL — no attribute `audit` / `_AUDIT_PATH`

- [ ] **Step 3: Implement**

Add `import json as _json` to the stdlib imports in `infra/auth_store.py`. Add near the other path constants (after `_FERNET_KEY_PATH`, ~line 35):

```python
_AUDIT_PATH = _DATA_DIR / "auth_audit.log"   # *.log → .gitignore 로 추적 제외
```

Add after `bidx()` helpers:

```python
def audit(event: str, *, username: Optional[str], ip: str,
          outcome: str, detail: str = "") -> None:
    """인증 감사 로그(JSONL). 절대 키/자격증명 값을 detail 에 넣지 말 것."""
    try:
        rec = {"ts": time.time(), "event": event, "username": username or "",
               "ip": ip or "", "outcome": outcome, "detail": detail}
        with open(_AUDIT_PATH, "a", encoding="utf-8") as f:
            f.write(_json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        logger.warning("auth audit 기록 실패(event=%s)", event)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_auth_recovery.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add infra/auth_store.py tests/test_auth_recovery.py
git commit -m "feat(auth): JSONL audit log helper"
```

---

## Task 11: Recovery endpoints + rate-limit/audit wiring

**Files:**
- Modify: `server/app.py` — `_PUBLIC_PATHS` (29-31); models (after `LoginReq` ~150); `_client_ip`; limiter instances; `/api/recover_id`, `/api/recover_password`; throttle+audit `/api/login` & `/api/register`
- Test: `tests/test_recovery_endpoints.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_recovery_endpoints.py
import pytest
from fastapi.testclient import TestClient
from infra import auth_store as A

@pytest.fixture
def client(tmp_path, monkeypatch):
    for n, v in [("_DATA_DIR", tmp_path), ("_DB_PATH", tmp_path/"a.db"),
                 ("_FERNET_KEY_PATH", tmp_path/".k"), ("_AUDIT_PATH", tmp_path/"au.log"),
                 ("_INITED", False), ("_FERNET", None), ("_FERNET_RAW", None),
                 ("_BIDX_KEY", None)]:
        monkeypatch.setattr(A, n, v)
    A.init()
    A.upsert_user("zoe", "Orig$pass99", "AKz", "ASz", "ORz", "1-1", "", "", "")
    from server.app import app
    return TestClient(app)

def test_recover_id_endpoint(client):
    r = client.post("/api/recover_id", json={
        "kis_app_key": "AKz", "kis_app_secret": "ASz", "openrouter_key": "ORz"})
    assert r.status_code == 200 and r.json()["username"] == "zoe"
    bad = client.post("/api/recover_id", json={
        "kis_app_key": "AKz", "kis_app_secret": "ASz", "openrouter_key": "NOPE"})
    assert bad.status_code == 404

def test_recover_password_endpoint(client):
    ok = client.post("/api/recover_password", json={
        "username": "zoe", "kis_app_key": "AKz", "kis_app_secret": "ASz",
        "openrouter_key": "ORz", "new_password": "BrandN3w$pw"})
    assert ok.status_code == 200 and ok.json()["ok"] is True
    assert client.post("/api/login", json={
        "username": "zoe", "password": "BrandN3w$pw"}).status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_recovery_endpoints.py -v`
Expected: FAIL — 404 route not found / model missing

- [ ] **Step 3: Implement**

(a) `_PUBLIC_PATHS` (lines 29-31) — replace with:

```python
_PUBLIC_PATHS = {"/health", "/api/health", "/", "/favicon.ico",
                 "/api/login", "/api/register", "/api/auth_status",
                 "/api/check_username", "/api/recover_id", "/api/recover_password"}
```

(b) After `class LoginReq(BaseModel):` block (~line 150) add:

```python
class RecoverIdReq(BaseModel):
    kis_app_key: str
    kis_app_secret: str
    openrouter_key: str
class RecoverPwReq(BaseModel):
    username: str
    kis_app_key: str
    kis_app_secret: str
    openrouter_key: str
    new_password: str
```

(c) After `_task: Optional[asyncio.Task] = None` (line 154) add:

```python
from infra.rate_limit import SlidingWindowLimiter
_rl_login = SlidingWindowLimiter(max_hits=int(os.getenv("ARQUANT_RL_LOGIN_MAX", "8")),
                                 window_sec=float(os.getenv("ARQUANT_RL_WIN", "900")))
_rl_recover = SlidingWindowLimiter(max_hits=int(os.getenv("ARQUANT_RL_RECOVER_MAX", "5")),
                                   window_sec=float(os.getenv("ARQUANT_RL_WIN", "900")))

def _client_ip(request: Request) -> str:
    xff = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return xff or (request.client.host if request.client else "unknown")

def _throttle(lim: SlidingWindowLimiter, key: str) -> None:
    retry = lim.hit(key)
    if retry is not None:
        raise HTTPException(429, f"요청이 너무 많습니다. {int(retry)+1}초 후 다시 시도하세요.")
```

(d) Replace the `login` handler (lines 202-210) with a throttled+audited version:

```python
@app.post("/api/login")
async def login(req: LoginReq, request: Request):
    """재로그인 — 아이디 + 비밀번호 (argon2 검증)."""
    ip = _client_ip(request)
    _throttle(_rl_login, f"login:{ip}")
    _throttle(_rl_login, f"login:user:{(req.username or '').strip()}")
    u = auth_store.verify_password((req.username or "").strip(), req.password or "")
    if not u:
        auth_store.audit("login", username=(req.username or "").strip(), ip=ip,
                         outcome="fail", detail="")
        raise HTTPException(401, "아이디 또는 비밀번호가 일치하지 않습니다.")
    auth_store.audit("login", username=u["username"], ip=ip, outcome="ok", detail="")
    await _activate_with_policy(u["id"])
    auth_store.touch_login(u["id"])
    return _issue_session(u["id"], req.remember)
```

(e) In the `register` handler (lines 174-200): change the signature to `async def register(req: RegisterReq, request: Request):`, add right after the docstring:

```python
    ip = _client_ip(request)
    _throttle(_rl_recover, f"register:{ip}")
```

and change the `auth_store.upsert_user(` call (lines 192-197) to drop `dart_key`/`label` (DART/계정이름 no longer collected — §5):

```python
    uid = auth_store.upsert_user(
        username=username, password=req.password,
        kis_app_key=req.kis_app_key.strip(), kis_app_secret=req.kis_app_secret.strip(),
        openrouter_key=req.openrouter_key.strip(), kis_account_no=req.kis_account_no.strip(),
        kis_base_url=req.kis_base_url.strip())
    auth_store.audit("register", username=username, ip=ip, outcome="ok", detail="")
```

(f) Add the two endpoints immediately after the `login` handler:

```python
@app.post("/api/recover_id")
async def recover_id(req: RecoverIdReq, request: Request):
    ip = _client_ip(request)
    _throttle(_rl_recover, f"recid:{ip}")
    uname = auth_store.find_username_by_factors(
        req.kis_app_key, req.kis_app_secret, req.openrouter_key)
    auth_store.audit("recover_id", username=uname, ip=ip,
                     outcome=("ok" if uname else "fail"), detail="")
    if not uname:
        raise HTTPException(404, "일치하는 계정을 찾을 수 없습니다.")
    return {"username": uname}

@app.post("/api/recover_password")
async def recover_password(req: RecoverPwReq, request: Request):
    ip = _client_ip(request)
    _throttle(_rl_recover, f"recpw:{ip}")
    try:
        ok = auth_store.reset_password_by_factors(
            (req.username or "").strip(), req.kis_app_key, req.kis_app_secret,
            req.openrouter_key, req.new_password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    auth_store.audit("recover_password", username=(req.username or "").strip(),
                     ip=ip, outcome=("ok" if ok else "fail"), detail="")
    if not ok:
        raise HTTPException(404, "일치하는 계정을 찾을 수 없습니다.")
    return {"ok": True}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_recovery_endpoints.py -v`
Expected: PASS. (If importing `server.app` is too heavy/side-effecting in CI, the authoritative coverage is the `auth_store` unit tests in Tasks 8/10; keep this as a smoke test.)

- [ ] **Step 5: Commit**

```bash
git add server/app.py tests/test_recovery_endpoints.py
git commit -m "feat(api): recovery endpoints + login/register throttle & audit; drop dart/label"
```

---

## Task 12: Guard — DART crawl stays env-only

**Files:**
- Test: `tests/test_register_no_dart.py`

(No code change: `tools/dart_disclosure.py` already reads only `config.OPENDART_API_KEY`. This test locks that invariant so a future change can't reintroduce a per-user/hardcoded key.)

- [ ] **Step 1: Write the test**

```python
# tests/test_register_no_dart.py
import re
from pathlib import Path

def test_dart_module_uses_only_env_config_key():
    src = Path("tools/dart_disclosure.py").read_text(encoding="utf-8")
    # only source of the key is config.OPENDART_API_KEY
    assert "from config import OPENDART_API_KEY" in src
    # no hardcoded crtfc_key literal (would be a leaked key)
    assert not re.search(r'crtfc_key["\']\s*:\s*["\'][A-Za-z0-9]{20,}', src)

def test_registerreq_dart_label_ignored_by_upsert_call():
    src = Path("server/app.py").read_text(encoding="utf-8")
    m = re.search(r"uid = auth_store\.upsert_user\((.*?)\)", src, re.S)
    assert m and "dart_key=" not in m.group(1) and "label=" not in m.group(1)
```

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `python3.11 -m pytest tests/test_register_no_dart.py -v`
Expected: PASS (Task 11 already removed `dart_key=`/`label=` from the call; dart module is already env-only). If FAIL on the second test, ensure Task 11 step (e) was applied.

- [ ] **Step 3: Commit**

```bash
git add tests/test_register_no_dart.py
git commit -m "test: lock DART env-only invariant + no dart/label in register upsert"
```

---

## Task 13: Full suite + branch checkpoint

- [ ] **Step 1: Run the entire test suite**

Run: `python3.11 -m pytest -q`
Expected: all tests pass (new auth tests + pre-existing `test_backtest`, `test_guardrails`, etc. unaffected).

- [ ] **Step 2: Manual smoke (existing single user still works)**

Run: `python3.11 -c "from infra import auth_store as A; A.init(); A.migrate_passwords_and_bidx(); print('migration ok')"`
Expected: `migration ok` (no exception against the real `data/arquant_auth.db`; hh09080 row gets hash+bidx, password_enc blanked).

- [ ] **Step 3: Commit checkpoint**

```bash
git add -A
git commit -m "chore: backend login-overhaul plan complete (Plan 1/3)"
```

---

## Self-Review

**1. Spec coverage:**
- §2 blind-index recovery → Tasks 2, 8, 11 ✓
- §6 argon2 + one-shot startup migration → Tasks 3, 4, 5, 6, 7 ✓
- §7 rate-limit + audit → Tasks 9, 10, 11 ✓
- §5 drop DART/label collection; DART env-only → Tasks 11(e), 12 ✓
- §1/§3/§4 (logo/badge/button) → **not here** — frontend Plans 2 (web) & 3 (Android), by design.

**2. Placeholder scan:** No "TBD/TODO/handle edge cases" — every code step has complete code; the argon2 version is discovered by an exact command (Task 1 Step 2), not guessed.

**3. Type/name consistency:** `bidx`, `hash_password`, `verify_pw_hash`, `_set_password_hash`, `find_username_by_factors`, `reset_password_by_factors`, `migrate_passwords_and_bidx`, `audit`, `_AUDIT_PATH`, `_FERNET_RAW`, `_BIDX_KEY`, `SlidingWindowLimiter.hit/reset`, `_throttle`, `_client_ip` — used consistently across tasks. `_row_to_creds` now exposes `password_hash`, consumed by `verify_password`. `upsert_user` signature unchanged (back-compat) but ignores stored plaintext.

**Note for executor:** Task 5 Step 4 depends on Task 6 (`verify_password`). Execute Tasks in order; if Task 5's test is red, proceed to Task 6 then re-run — this dependency is called out inline.
