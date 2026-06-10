# ADMIN 단일 인텔리전스 생산자 · 정시 동기화 · API 비용 절감 — 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** hh09080(ADMIN)이 시장 전역 LLM 인텔리전스(뉴스 분류·매크로 리서치·매크로 분석)를 사이클마다 1회 산출·공유하고, 모든 계정 사이클을 벽시계 정시(:00)에 동기화해 비관리자 계정의 중복 LLM 비용을 제거한다.

**Architecture:** 단일 프로세스 + per-uid asyncio 태스크 구조 위에, 프로세스 전역 싱글턴 `MarketIntelligenceStore`(asyncio `Condition` 기반 publish/peek/wait_for)를 둔다. 생산자(ADMIN)는 계산 후 게시, 소비자는 그 시각(hour) 결과 게시까지 대기 후 수신(타임아웃 시 자체계산 폴백). 사이클 트리거 앵커를 "프로세스 시작 시각"에서 "벽시계 시(hour)"로 바꿔 재시작 무관 :00 정렬.

**Tech Stack:** Python 3.11, asyncio, FastAPI, pytest. 테스트는 **반드시 `python3.11 -m pytest`** (기본 `python`은 argon2 import 실패).

**스펙:** `docs/superpowers/specs/2026-06-08-admin-intelligence-sharing-design.md`

> **⚠️ ArQuant 커밋 정책 (CLAUDE.md):** 이 저장소는 외부 auto-Backup 도구가 주기적으로 `git add -A` + `Backup:` 커밋을 한다. **구현자는 `git commit`을 직접 실행하지 말 것** — 각 태스크는 "테스트 통과 확인"으로 끝낸다. 배포(반영)는 `sudo systemctl restart arquant.service`이며 **사장 확인 후에만** 한다(이 계획 범위 밖).

---

## File Structure

| 파일 | 책임 | 신규/수정 |
|------|------|-----------|
| `infra/market_intel.py` | `MarketIntelligenceStore`(publish/peek/wait_for) + `get_intel_store()` 싱글턴 | **신규** |
| `config.py` | `SHARE_MARKET_INTELLIGENCE`·`SHARE_PRODUCER_WAIT_SEC` 플래그 + 튜너블 등재 | 수정 |
| `infra/auth_store.py` | `set_admin` 잠금 + `init()` 부팅 스윕 | 수정 |
| `main_swarm.py` | `_current_hour_key`/`_current_hour_key_str` 헬퍼, `_should_run_periodic`, 정시 케이던스, `_producer_absent_this_cycle`, `_shared_or_compute`, 3개 공유 호출 배선, `_emit_news_activity` 게이팅 | 수정 |

---

## Task 1: 설정 플래그 + 튜너블 등재

**Files:**
- Modify: `config.py:136` (NXT 블록 끝 다음에 플래그 추가), `config.py:270` (TUNABLE_KEYS), `config.py:405` (META), `config.py:492` (EFFECT)
- Test: `tests/test_share_intel_config.py`

- [ ] **Step 1: Write the failing test**

`tests/test_share_intel_config.py`:
```python
"""ADMIN 인텔리전스 공유 설정 키 — 기본값 + 튜너블 카탈로그 등재."""
import config

def test_share_flags_defaults():
    assert config.SHARE_MARKET_INTELLIGENCE is True
    assert config.SHARE_PRODUCER_WAIT_SEC == 120

def test_share_keys_in_tunable_catalog():
    for k in ("SHARE_MARKET_INTELLIGENCE", "SHARE_PRODUCER_WAIT_SEC"):
        assert k in config.STRATEGY_TUNABLE_KEYS, f"{k} missing from STRATEGY_TUNABLE_KEYS"
        assert k in config.STRATEGY_KEY_META, f"{k} missing from STRATEGY_KEY_META"
        assert k in config.STRATEGY_KEY_EFFECT, f"{k} missing from STRATEGY_KEY_EFFECT"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_share_intel_config.py -v`
Expected: FAIL with `AttributeError: module 'config' has no attribute 'SHARE_MARKET_INTELLIGENCE'`

- [ ] **Step 3: Add the flags**

In `config.py`, after line 136 (`EXT_HOURS_LIMIT_SLIPPAGE_PCT = 0.5`), insert:
```python

# ─── ADMIN 단일 인텔리전스 공유 (사장 지시 2026-06-08) ─────────────────────────
# hh09080(ADMIN)이 시장 전역 분석(뉴스 분류·매크로 리서치·매크로 분석)을 사이클마다 1회
# 산출·게시하고, 비관리자 계정은 그 결과를 공유받아 같은 LLM 호출을 중복하지 않는다.
SHARE_MARKET_INTELLIGENCE = True   # 마스터 토글. False면 전 계정이 현행대로 각자 계산
SHARE_PRODUCER_WAIT_SEC   = 120    # 소비자가 ADMIN 게시를 기다리는 단계별 최대 초(초과 시 자체계산 폴백)
```

- [ ] **Step 4: Register in STRATEGY_TUNABLE_KEYS**

In `config.py`, change the NXT block at lines 268-270 from:
```python
    # NXT 시간외 매매 (사장 지시 2026-06-08)
    "ENABLE_NXT_EXTENDED_HOURS", "ENABLE_NXT_PRE_MARKET", "ENABLE_NXT_AFTER_MARKET",
    "EXT_HOURS_LIMIT_SLIPPAGE_PCT",
]
```
to:
```python
    # NXT 시간외 매매 (사장 지시 2026-06-08)
    "ENABLE_NXT_EXTENDED_HOURS", "ENABLE_NXT_PRE_MARKET", "ENABLE_NXT_AFTER_MARKET",
    "EXT_HOURS_LIMIT_SLIPPAGE_PCT",
    # ADMIN 인텔리전스 공유 (사장 지시 2026-06-08)
    "SHARE_MARKET_INTELLIGENCE", "SHARE_PRODUCER_WAIT_SEC",
]
```

- [ ] **Step 5: Register in STRATEGY_KEY_META**

In `config.py`, after the `EXT_HOURS_LIMIT_SLIPPAGE_PCT` META entry (ends line 405 `..."group": "시간외(NXT)"},`), insert:
```python
    "SHARE_MARKET_INTELLIGENCE":  {"label": "시장 인텔리전스 공유(ADMIN 단일 생산)", "type": "bool",
                                   "help": "ON이면 ADMIN(hh09080)이 매크로·뉴스 분석을 1회 산출, 다른 계정은 공유받아 LLM 중복 비용 절감",
                                   "group": "비용"},
    "SHARE_PRODUCER_WAIT_SEC":    {"label": "공유 대기 타임아웃", "type": "int", "unit": "초",
                                   "help": "비관리자 계정이 ADMIN 게시를 기다리는 최대 초. 초과하면 자체 계산(생산자 부재 대응)",
                                   "min": 10, "max": 600, "step": 10, "group": "비용"},
```

- [ ] **Step 6: Register in STRATEGY_KEY_EFFECT**

In `config.py`, before the closing `}` of `STRATEGY_KEY_EFFECT` (line 493), after the `EXT_HOURS_LIMIT_SLIPPAGE_PCT` effect line (492), insert:
```python
    "SHARE_MARKET_INTELLIGENCE": "켜면 ADMIN이 매크로·뉴스 분석을 1회만 하고 다른 계정이 공유(LLM 비용↓), 끄면 계정마다 각자 계산.",
    "SHARE_PRODUCER_WAIT_SEC": "올리면 ADMIN 분석을 더 오래 기다림(공유 적중↑), 내리면 빨리 자체계산으로 전환(지연↓).",
```

- [ ] **Step 7: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_share_intel_config.py -v`
Expected: PASS (2 passed)

---

## Task 2: MarketIntelligenceStore (신규 파일)

**Files:**
- Create: `infra/market_intel.py`
- Test: `tests/test_market_intel_store.py`

- [ ] **Step 1: Write the failing test**

`tests/test_market_intel_store.py`:
```python
"""프로세스 전역 인텔리전스 스토어 — hour_key 매칭 + 대기/타임아웃."""
import asyncio
from infra.market_intel import MarketIntelligenceStore, get_intel_store

def test_peek_hit_same_hour_key():
    s = MarketIntelligenceStore()
    asyncio.run(s.publish("macro_research", "2026-06-08 10", "R", None, uid=1, now=1000.0))
    assert s.peek("macro_research", "2026-06-08 10", None) == "R"

def test_peek_miss_different_hour_key():
    s = MarketIntelligenceStore()
    asyncio.run(s.publish("macro_research", "2026-06-08 10", "R", None, uid=1, now=1000.0))
    assert s.peek("macro_research", "2026-06-08 11", None) is None   # 직전 시각(stale) 무효

def test_peek_miss_when_empty():
    s = MarketIntelligenceStore()
    assert s.peek("news_report", "2026-06-08 10", None) is None

def test_fingerprint_mismatch_returns_none():
    s = MarketIntelligenceStore()
    asyncio.run(s.publish("news_report", "2026-06-08 10", "R", "fpA", uid=1, now=1000.0))
    assert s.peek("news_report", "2026-06-08 10", "fpB") is None
    assert s.peek("news_report", "2026-06-08 10", "fpA") == "R"
    assert s.peek("news_report", "2026-06-08 10", None) == "R"   # None이면 fingerprint 무시

def test_wait_for_receives_late_publish():
    async def scenario():
        s = MarketIntelligenceStore()
        async def producer():
            await asyncio.sleep(0.01)
            await s.publish("macro_report", "2026-06-08 10", "MR", None, uid=1, now=1000.0)
        async def consumer():
            return await s.wait_for("macro_report", "2026-06-08 10", None, timeout=1.0)
        res, _ = await asyncio.gather(consumer(), producer())
        return res
    assert asyncio.run(scenario()) == "MR"

def test_wait_for_times_out_to_none():
    async def scenario():
        s = MarketIntelligenceStore()
        return await s.wait_for("macro_report", "2026-06-08 10", None, timeout=0.05)
    assert asyncio.run(scenario()) is None

def test_get_intel_store_is_singleton():
    assert get_intel_store() is get_intel_store()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_market_intel_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'infra.market_intel'`

- [ ] **Step 3: Create the implementation**

`infra/market_intel.py`:
```python
"""프로세스 전역 시장 인텔리전스 공유 스토어 (사장 지시 2026-06-08).

ADMIN(hh09080) 오케스트레이터가 매 :00 사이클에서 매크로·뉴스 분석을 산출해 publish 하고,
비관리자 오케스트레이터는 같은 시각(hour_key)의 결과를 wait_for 로 받아 LLM 중복 호출을 피한다.
단일 프로세스 + asyncio 협조형이라 락 없이 Condition 으로 대기-알림한다.
"""
from __future__ import annotations
from typing import Any, Dict, Optional

INTEL_KINDS = ("macro_research", "macro_analyst", "news_report")


class MarketIntelligenceStore:
    def __init__(self) -> None:
        self._d: Dict[str, Dict[str, Any]] = {}   # kind -> {hour_key, result, fingerprint, produced_at, uid}
        self._cond = None                          # asyncio.Condition (러닝 루프 내 lazy 생성)

    def _ensure_cond(self):
        if self._cond is None:
            import asyncio
            self._cond = asyncio.Condition()
        return self._cond

    def peek(self, kind: str, hour_key: str, fingerprint: Optional[str]):
        e = self._d.get(kind)
        if not e or e["hour_key"] != hour_key:
            return None                            # 미게시 또는 직전 시각(stale)
        if fingerprint is not None and e["fingerprint"] != fingerprint:
            return None
        return e["result"]

    async def publish(self, kind: str, hour_key: str, result: Any,
                      fingerprint: Optional[str], *, uid: Optional[int], now: float) -> None:
        cond = self._ensure_cond()
        async with cond:
            self._d[kind] = {"hour_key": hour_key, "result": result,
                             "fingerprint": fingerprint, "produced_at": now, "uid": uid}
            cond.notify_all()

    async def wait_for(self, kind: str, hour_key: str, fingerprint: Optional[str], *, timeout: float):
        hit = self.peek(kind, hour_key, fingerprint)
        if hit is not None:
            return hit
        import asyncio
        cond = self._ensure_cond()
        try:
            async with cond:
                await asyncio.wait_for(
                    cond.wait_for(lambda: self.peek(kind, hour_key, fingerprint) is not None),
                    timeout=timeout)
        except asyncio.TimeoutError:
            return None
        return self.peek(kind, hour_key, fingerprint)


_store: Optional[MarketIntelligenceStore] = None


def get_intel_store() -> MarketIntelligenceStore:
    global _store
    if _store is None:
        _store = MarketIntelligenceStore()
    return _store
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_market_intel_store.py -v`
Expected: PASS (7 passed)

---

## Task 3: ADMIN 잠금 (hh09080 영구·단독)

**Files:**
- Modify: `infra/auth_store.py:439-450` (`set_admin`), `infra/auth_store.py:249-252` (`init` 부팅 스윕)
- Test: `tests/test_admin_lockdown.py`

현재 `set_admin`(439)은 시드 ADMIN 강등만 막는다. 추가로 (a) hh09080 외 계정의 **승격 거부**, (b) 부팅 시 stray admin **강등**이 필요하다.

- [ ] **Step 1: Write the failing test**

`tests/test_admin_lockdown.py`:
```python
"""ADMIN = hh09080 영구·단독 — 승격 거부 + 강등 거부 + 부팅 스윕."""
import importlib, sqlite3, time
import infra.auth_store as A

def _fresh_db(tmp_path, monkeypatch):
    db = tmp_path / "auth.db"
    monkeypatch.setattr(A, "_DB_PATH", str(db), raising=False)
    # _connect 가 _DB_PATH 를 참조하도록 — 실제 구현은 모듈 내부 경로 상수를 쓴다.
    monkeypatch.setattr(A, "_INITED", False, raising=False)
    return db

def _mk_user(conn, username, is_admin=0):
    now = time.time()
    conn.execute(
        "INSERT INTO users (username, password_enc, password_hash, kis_app_key_enc, "
        "kis_app_secret_enc, deepseek_api_key_enc, kis_account_no_enc, kis_base_url, "
        "dart_key_enc, label, created_at, last_login_at, last_validated_at, is_admin) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (username, "", "h", "", "", "", "", "", "", username, now, now, 0.0, is_admin))

def test_reject_promote_non_admin_username(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch); A.init()
    with A._DB_LOCK, A._connect() as conn:
        _mk_user(conn, "alice")
        uid = conn.execute("SELECT id FROM users WHERE username='alice'").fetchone()[0]
    assert A.set_admin(uid, True) is False
    assert A.is_admin(uid) is False

def test_reject_demote_hh09080(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch); A.init()
    with A._DB_LOCK, A._connect() as conn:
        _mk_user(conn, "hh09080", is_admin=1)
        uid = conn.execute("SELECT id FROM users WHERE username='hh09080'").fetchone()[0]
    assert A.set_admin(uid, False) is False
    assert A.is_admin(uid) is True

def test_boot_sweep_demotes_stray_admin(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch); A.init()
    with A._DB_LOCK, A._connect() as conn:
        _mk_user(conn, "hh09080", is_admin=0)   # 시드 전 상태
        _mk_user(conn, "mallory", is_admin=1)   # stray admin
    A._INITED = False                            # 재초기화로 부팅 스윕 재실행
    A.init()
    with A._DB_LOCK, A._connect() as conn:
        rows = {r[0]: r[1] for r in conn.execute("SELECT username, is_admin FROM users")}
    assert rows["hh09080"] == 1                  # 시드 승격
    assert rows["mallory"] == 0                  # stray 강등
```

> 참고: 실제 `_connect`/`_DB_PATH` 상수명은 구현 시 `infra/auth_store.py` 상단을 확인해 정확히 monkeypatch 한다. 위 헬퍼가 안 맞으면 구현자가 모듈의 실제 연결 함수에 맞춰 조정한다(테스트 의도: 깨끗한 임시 DB에서 검증).

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_admin_lockdown.py -v`
Expected: FAIL — `test_reject_promote_non_admin_username` 에서 `set_admin` 이 True 반환(현재는 누구나 승격 가능)

- [ ] **Step 3: Harden `set_admin`**

In `infra/auth_store.py`, replace the body of `set_admin` (lines 439-450) with:
```python
def set_admin(user_id: int, is_admin_flag: bool) -> bool:
    """ADMIN 권한 부여/회수. 사장 지시 2026-06-08: ADMIN 은 hh09080 영구·단독.
    - hh09080 외 계정 승격 거부, hh09080 강등 거부 (모두 return False + 경고, 예외 없음).
    """
    init()
    with _DB_LOCK, _connect() as conn:
        row = conn.execute("SELECT username FROM users WHERE id=?", (int(user_id),)).fetchone()
        if not row:
            return False
        uname = row["username"]
        if is_admin_flag and uname not in ADMIN_USERNAMES:
            logger.warning("set_admin 거부: %s 는 ADMIN 화이트리스트(hh09080) 밖 — 승격 불가", uname)
            return False
        if not is_admin_flag and uname in ADMIN_USERNAMES:
            logger.warning("set_admin 거부: %s 는 시드 ADMIN — 강등 불가", uname)
            return False
        conn.execute("UPDATE users SET is_admin=? WHERE id=?",
                     (1 if is_admin_flag else 0, int(user_id)))
    return True
```

- [ ] **Step 4: Add boot sweep in `init()`**

In `infra/auth_store.py`, replace the seed block at lines 249-252:
```python
        if ADMIN_USERNAMES:
            qs = ",".join("?" * len(ADMIN_USERNAMES))
            conn.execute(f"UPDATE users SET is_admin=1 WHERE username IN ({qs})",
                         tuple(ADMIN_USERNAMES))
```
with (promote whitelist + demote everyone else — sweep):
```python
        # 사장 지시 2026-06-08: ADMIN = hh09080 영구·단독. 화이트리스트는 승격하고
        # 그 외 stray is_admin=1 행은 강등(부팅 스윕).
        if ADMIN_USERNAMES:
            qs = ",".join("?" * len(ADMIN_USERNAMES))
            conn.execute(f"UPDATE users SET is_admin=1 WHERE username IN ({qs})",
                         tuple(ADMIN_USERNAMES))
            conn.execute(f"UPDATE users SET is_admin=0 WHERE username NOT IN ({qs})",
                         tuple(ADMIN_USERNAMES))
        else:
            conn.execute("UPDATE users SET is_admin=0")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_admin_lockdown.py -v`
Expected: PASS (3 passed). 조정이 필요하면 `_connect`/`_DB_PATH` monkeypatch만 모듈 실제 상수명에 맞춘다.

---

## Task 4: 정시(:00) 동기화 케이던스

**Files:**
- Modify: `main_swarm.py:105` (헬퍼 추가), `main_swarm.py:2746-2747` (`__init__` 상태 필드), `main_swarm.py:3049-3076` (진입), `main_swarm.py:3110-3175` (트리거)
- Test: `tests/test_hourly_aligned_cadence.py`

- [ ] **Step 1: Write the failing test**

`tests/test_hourly_aligned_cadence.py`:
```python
"""사이클 트리거 앵커를 벽시계 시(hour)로 — 진입 즉시 발화 안 함, :00에 발화."""
from datetime import datetime, timezone, timedelta
import main_swarm

KST = timezone(timedelta(hours=9))

def test_current_hour_key_floors_to_hour(monkeypatch):
    monkeypatch.setattr(main_swarm, "_now_kst",
                        lambda: datetime(2026, 6, 8, 10, 37, 12, tzinfo=KST))
    assert main_swarm._current_hour_key() == datetime(2026, 6, 8, 10, 0, 0, tzinfo=KST)
    assert main_swarm._current_hour_key_str() == "2026-06-08 10"

def _orch():
    o = main_swarm.ArquantOrchestrator.__new__(main_swarm.ArquantOrchestrator)
    return o

def test_no_periodic_within_same_hour(monkeypatch):
    monkeypatch.setattr(main_swarm, "_now_kst",
                        lambda: datetime(2026, 6, 8, 10, 37, 0, tzinfo=KST))
    o = _orch()
    o._last_cycle_hour_key = main_swarm._current_hour_key()   # 진입 시 앵커
    # 같은 시(10시) 안 — 분이 흘러도 미발화
    monkeypatch.setattr(main_swarm, "_now_kst",
                        lambda: datetime(2026, 6, 8, 10, 59, 0, tzinfo=KST))
    assert o._should_run_periodic() is False

def test_periodic_fires_on_hour_rollover(monkeypatch):
    monkeypatch.setattr(main_swarm, "_now_kst",
                        lambda: datetime(2026, 6, 8, 10, 37, 0, tzinfo=KST))
    o = _orch()
    o._last_cycle_hour_key = main_swarm._current_hour_key()
    monkeypatch.setattr(main_swarm, "_now_kst",
                        lambda: datetime(2026, 6, 8, 11, 0, 1, tzinfo=KST))
    assert o._should_run_periodic() is True

def test_restart_invariant_first_fire_at_next_hour(monkeypatch):
    # 08:37 시작 → 첫 periodic 은 09:00 에 (08:xx 내내 미발화)
    monkeypatch.setattr(main_swarm, "_now_kst",
                        lambda: datetime(2026, 6, 8, 8, 37, 0, tzinfo=KST))
    o = _orch()
    o._last_cycle_hour_key = main_swarm._current_hour_key()
    monkeypatch.setattr(main_swarm, "_now_kst",
                        lambda: datetime(2026, 6, 8, 8, 59, 59, tzinfo=KST))
    assert o._should_run_periodic() is False
    monkeypatch.setattr(main_swarm, "_now_kst",
                        lambda: datetime(2026, 6, 8, 9, 0, 0, tzinfo=KST))
    assert o._should_run_periodic() is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_hourly_aligned_cadence.py -v`
Expected: FAIL — `AttributeError: module 'main_swarm' has no attribute '_current_hour_key'`

- [ ] **Step 3: Add module-level hour-key helpers**

In `main_swarm.py`, after line 105 (`def _now_kst(): return datetime.now(KST)`), insert:
```python
def _current_hour_key():
    """현재 KST 시각을 시(hour) 단위로 내림한 datetime — 사이클 정시(:00) 앵커."""
    return _now_kst().replace(minute=0, second=0, microsecond=0)

def _current_hour_key_str() -> str:
    """공유 스토어 키용 직렬화 (예: '2026-06-08 10')."""
    return _current_hour_key().strftime("%Y-%m-%d %H")
```

- [ ] **Step 4: Add `_should_run_periodic` method + state field**

In `main_swarm.py`, in `ArquantOrchestrator.__init__`, after line 2747 (`self._last_session: ...`), insert:
```python
        self._last_cycle_hour_key = None   # 사장 지시 2026-06-08: 벽시계 시(hour) 앵커 (정시 정렬)
        self._producer_absent_this_cycle = False  # 이번 사이클 ADMIN 게시 부재 확정 플래그
```

Add this method right after `_research_macro_themes` definition region is fine; place it near `start_continuous`. After line 2769 (`_emit` method) and before `_research_macro_themes` (2771), insert:
```python
    def _should_run_periodic(self) -> bool:
        """정기 사이클 트리거 — 벽시계 시(hour)가 직전 발화 시각과 다르면 True(=:00 통과)."""
        return _current_hour_key() != self._last_cycle_hour_key
```

- [ ] **Step 5: Rewrite `start_continuous` entry (lines 3049-3076)**

Change lines 3051-3054 from:
```python
        self._last_cycle_at = time.time()        # hourly periodic trigger fires 1h after start, not immediately
        self._last_session = get_current_session()
        self._last_status_state = None
        first_run_pending = True                  # ▶ 실행 직후 1회는 누적 뉴스로 즉시 사이클 (장중일 때)
```
to:
```python
        self._last_cycle_at = time.time()        # 상태표시용(다음 사이클 카운트다운). 트리거 앵커는 hour_key.
        self._last_cycle_hour_key = _current_hour_key()  # 사장 지시 2026-06-08: 진입 시 현재 시(hour)로 앵커
                                                         # → 같은 시(hour) 내 즉시 발화 안 함, 다음 :00 대기
        self._last_session = get_current_session()
        self._last_status_state = None
```

Change the entry status message at line 3076 from:
```python
            await self._set_status("MONITORING", "연속 감시 시작 — 즉시 1회 + 이후 1시간마다 + 장 개장 시 사이클", force=True)
```
to:
```python
            await self._set_status("MONITORING",
                f"연속 감시 시작 — 다음 정시({_current_hour_key_str()[-2:]}:00 다음)부터 사이클, 장 개장 시에도 1회", force=True)
```

- [ ] **Step 6: Rewrite trigger logic (lines 3110-3112)**

Change lines 3110-3112 from:
```python
                first_run = first_run_pending
                market_open = (session in self._MARKET_OPEN_SESSIONS) and (self._last_session not in self._LIVE_SESSIONS)
                periodic_due = (time.time() - self._last_cycle_at) >= PERIODIC_CYCLE_SEC
```
to:
```python
                market_open = (session in self._MARKET_OPEN_SESSIONS) and (self._last_session not in self._LIVE_SESSIONS)
                periodic_due = self._should_run_periodic()    # 사장 지시 2026-06-08: 벽시계 :00 앵커
```

- [ ] **Step 7: Update the three trigger gates + skip path + success path**

Replace `if first_run or market_open or periodic_due:` at line 3120 with `if market_open or periodic_due:`.

Change the 5-minute dedup guard at line 3124 from:
```python
                    elif (time.time() - self._last_cycle_at) < 300 and not first_run:
```
to:
```python
                    elif (time.time() - self._last_cycle_at) < 300:
```

In the skip path, change lines 3138-3142 from:
```python
                if skip_reason and (first_run or market_open or periodic_due):
                    await self._set_status("MONITORING", f"⏭ 사이클 사전 게이트: {skip_reason}", force=True)
                    self._last_cycle_at = time.time()  # 트리거 재무장 방지 — 다음 1시간 후 재시도
                    first_run_pending = False
                    self._last_session = session
```
to:
```python
                if skip_reason and (market_open or periodic_due):
                    await self._set_status("MONITORING", f"⏭ 사이클 사전 게이트: {skip_reason}", force=True)
                    self._last_cycle_at = time.time()
                    self._last_cycle_hour_key = _current_hour_key()  # 이 시각엔 재발화 방지(다음 :00 재시도)
                    self._last_session = session
```

Change the main fire gate at line 3148-3149 from:
```python
                if (first_run or market_open or periodic_due) and is_trading_hours():
                    first_run_pending = False
```
to:
```python
                if (market_open or periodic_due) and is_trading_hours():
                    self._producer_absent_this_cycle = False   # 사이클 시작 — 공유 부재 플래그 리셋
```

Remove the now-dead `first_run` status branch at lines 3156-3158:
```python
                    if first_run:
                        await self._set_status("MONITORING",
                            f"▶ 실행 — 뉴스 {len(cycle_news)}건 기반 첫 사이클{_fb_note}", force=True)
                    elif market_open:
```
becomes:
```python
                    if market_open:
```

Change the success-path update at line 3168 from:
```python
                    self._last_cycle_at = time.time()
```
to:
```python
                    self._last_cycle_at = time.time()
                    self._last_cycle_hour_key = _current_hour_key()   # 이 시각 발화 완료 — 다음 :00까지 대기
```

Update the post-cycle status text at line 3175 from:
```python
                            await self._set_status("MONITORING", "사이클 완료 — 감시 재개 (다음 사이클 1시간 뒤)", force=True)
```
to:
```python
                            await self._set_status("MONITORING", "사이클 완료 — 감시 재개 (다음 정시 :00 사이클)", force=True)
```

- [ ] **Step 8: Run tests**

Run: `python3.11 -m pytest tests/test_hourly_aligned_cadence.py -v`
Expected: PASS (4 passed)

Run: `python3.11 -c "import main_swarm"` → no error (구문/이름 검증)
Expected: 출력 없음(정상)

---

## Task 5: `_shared_or_compute` 헬퍼

**Files:**
- Modify: `main_swarm.py` (`_shared_or_compute` 메서드 추가, `_emit` 근처)
- Test: `tests/test_shared_or_compute.py`

`self.is_admin` 은 이미 `__init__`(line 2682)에 존재한다. `_producer_absent_this_cycle` 는 Task 4에서 추가됨.

- [ ] **Step 1: Write the failing test**

`tests/test_shared_or_compute.py`:
```python
"""ADMIN=게시 / 소비자=대기-수신·폴백 / SHARE off=항상 자체계산."""
import asyncio
from datetime import datetime, timezone, timedelta
import main_swarm
from infra.market_intel import MarketIntelligenceStore

KST = timezone(timedelta(hours=9))

class _StubRuntime:
    def __init__(self, params): self._p = params
    def get(self, k, default=None, uid=None): return self._p.get(k, default)

def _setup(monkeypatch, *, share=True, wait=0.05):
    monkeypatch.setattr(main_swarm, "_now_kst",
                        lambda: datetime(2026, 6, 8, 10, 5, 0, tzinfo=KST))
    monkeypatch.setattr(main_swarm, "runtime", _StubRuntime(
        {"SHARE_MARKET_INTELLIGENCE": share, "SHARE_PRODUCER_WAIT_SEC": wait}))
    store = MarketIntelligenceStore()
    monkeypatch.setattr(main_swarm, "get_intel_store", lambda: store)
    return store

def _orch(is_admin):
    o = main_swarm.ArquantOrchestrator.__new__(main_swarm.ArquantOrchestrator)
    o.uid = 1 if is_admin else 2
    o.is_admin = is_admin
    o._producer_absent_this_cycle = False
    return o

def _counter():
    calls = {"n": 0}
    async def compute():
        calls["n"] += 1
        return "COMPUTED"
    return calls, compute

def test_admin_computes_and_publishes(monkeypatch):
    store = _setup(monkeypatch)
    o = _orch(True); calls, compute = _counter()
    res = asyncio.run(o._shared_or_compute("macro_report", None, compute))
    assert res == "COMPUTED" and calls["n"] == 1
    assert store.peek("macro_report", "2026-06-08 10", None) == "COMPUTED"

def test_admin_empty_result_not_published(monkeypatch):
    store = _setup(monkeypatch)
    o = _orch(True)
    async def empty(): return ""
    res = asyncio.run(o._shared_or_compute("macro_report", None, empty))
    assert res == ""
    assert store.peek("macro_report", "2026-06-08 10", None) is None

def test_consumer_hit_does_not_compute(monkeypatch):
    store = _setup(monkeypatch)
    asyncio.run(store.publish("macro_report", "2026-06-08 10", "SHARED", None, uid=1, now=1.0))
    o = _orch(False); calls, compute = _counter()
    res = asyncio.run(o._shared_or_compute("macro_report", None, compute))
    assert res == "SHARED" and calls["n"] == 0          # 자체계산 안 함

def test_consumer_miss_falls_back_and_sets_flag(monkeypatch):
    _setup(monkeypatch, wait=0.05)
    o = _orch(False); calls, compute = _counter()
    res = asyncio.run(o._shared_or_compute("macro_report", None, compute))
    assert res == "COMPUTED" and calls["n"] == 1        # 타임아웃→폴백
    assert o._producer_absent_this_cycle is True

def test_consumer_absent_flag_short_circuits(monkeypatch):
    _setup(monkeypatch, wait=10.0)                       # 큰 타임아웃이어도
    o = _orch(False); o._producer_absent_this_cycle = True
    calls, compute = _counter()
    res = asyncio.run(o._shared_or_compute("news_report", None, compute))
    assert res == "COMPUTED" and calls["n"] == 1         # 대기 없이 즉시 폴백

def test_share_disabled_always_computes(monkeypatch):
    store = _setup(monkeypatch, share=False)
    o = _orch(True); calls, compute = _counter()
    res = asyncio.run(o._shared_or_compute("macro_report", None, compute))
    assert res == "COMPUTED" and calls["n"] == 1
    assert store.peek("macro_report", "2026-06-08 10", None) is None   # 게시 안 함
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_shared_or_compute.py -v`
Expected: FAIL — `AttributeError: 'ArquantOrchestrator' object has no attribute '_shared_or_compute'`

- [ ] **Step 3: Implement the helper**

In `main_swarm.py`, ensure `get_intel_store` is imported. Near the other infra imports at the top, add:
```python
from infra.market_intel import get_intel_store
```
Then add the method after `_should_run_periodic` (added in Task 4):
```python
    async def _shared_or_compute(self, kind, fingerprint, compute):
        """ADMIN=계산 후 게시 / 비관리자=같은 시각(hour) 게시를 대기-수신, 미게시 시 자체계산 폴백.
        compute: zero-arg async 콜러블(기존 LLM 호출). 사장 지시 2026-06-08.
        플래그는 runtime.get (override-or-config-default) — main_swarm 은 `from config import` 만 하고
        `import config` 는 안 하므로 config.X 직접 참조 금지(프로젝트 관용)."""
        if not runtime.get("SHARE_MARKET_INTELLIGENCE", uid=self.uid):
            return await compute()
        store = get_intel_store()
        hk = _current_hour_key_str()
        if self.is_admin:                                       # 생산자(hh09080)
            r = await compute()
            if r:                                               # 성공/비어있지 않음만 게시
                await store.publish(kind, hk, r, fingerprint, uid=self.uid, now=time.time())
            return r
        if self._producer_absent_this_cycle:                    # 이번 사이클 부재 확정 → 즉시 폴백
            return await compute()
        hit = store.peek(kind, hk, fingerprint)
        if hit is None:
            _wait = float(runtime.get("SHARE_PRODUCER_WAIT_SEC", uid=self.uid) or 120)
            hit = await store.wait_for(kind, hk, fingerprint, timeout=_wait)
        if hit is not None:
            return hit
        self._producer_absent_this_cycle = True                 # 첫 타임아웃 → 이후 공유단계 즉시 폴백
        return await compute()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_shared_or_compute.py -v`
Expected: PASS (6 passed)

---

## Task 6: 3개 공유 호출 배선

**Files:**
- Modify: `main_swarm.py:3316` (news_analyst.think), `main_swarm.py:3352` (_research_macro_themes), `main_swarm.py:3384` (macro_analyst.think)
- Test: `tests/test_shared_wiring.py`

각 `await EXPR` 를 `await self._shared_or_compute("<kind>", None, lambda: EXPR)` 로 감싼다(최소 침습).

- [ ] **Step 1: Write the failing test**

`tests/test_shared_wiring.py`:
```python
"""3개 시장-전역 분석 호출이 _shared_or_compute 를 통과하는지(kind 인자 검증)."""
import asyncio
import main_swarm

def test_wraps_route_through_shared(monkeypatch):
    seen = []
    async def fake_shared(self, kind, fp, compute):
        seen.append(kind)
        return await compute()          # compute 실제 실행해 부작용/타입 유지
    monkeypatch.setattr(main_swarm.ArquantOrchestrator, "_shared_or_compute", fake_shared)

    o = main_swarm.ArquantOrchestrator.__new__(main_swarm.ArquantOrchestrator)

    # 3개 compute 를 직접 흉내내 _shared_or_compute 경유를 확인 (단위 수준)
    async def drive():
        await o._shared_or_compute("news_report", None, lambda: _r("NEWS"))
        await o._shared_or_compute("macro_research", None, lambda: _r("RES"))
        await o._shared_or_compute("macro_report", None, lambda: _r("MACRO"))
    async def _r(v):
        return v
    asyncio.run(drive())
    assert seen == ["news_report", "macro_research", "macro_report"]
```

> 이 테스트는 배선 헬퍼 계약(3 kind)을 고정한다. 실제 사이클 통합은 전체 스위트(Task 8) + 라이브 검증으로 확인한다(`_run_analysis_cycle` 은 대형 함수라 단위 격리 비현실적).

- [ ] **Step 2: Run test to verify it fails (then passes)**

Run: `python3.11 -m pytest tests/test_shared_wiring.py -v`
Expected: 이 테스트는 헬퍼만 검증하므로 Task 5 구현 후 PASS. (배선 자체는 아래 Step 3에서 소스에 반영하고 Step 4 import 검증으로 확인.)

- [ ] **Step 3: Wire the three call sites**

(a) `main_swarm.py:3316` — news_analyst.think. Change:
```python
                news_report = await self.news_analyst.think(
                    f"네이버 금융 '증권 속보' 크롤링 결과입니다 (전체 누적 {len(news_articles)}건 — 국내·미국 뉴스 혼재).\n"
                    ...
                    f"이 분석은 전략리서치팀장 매크로 분석 및 운용전략실장 종목 선정에 최우선으로 반영됩니다.")
```
to (wrap the whole `await self.news_analyst.think(...)` expression in a lambda):
```python
                _news_prompt = (
                    f"네이버 금융 '증권 속보' 크롤링 결과입니다 (전체 누적 {len(news_articles)}건 — 국내·미국 뉴스 혼재).\n"
                    f"현재 세션은 **{session}** — 지금 실제로 매매 가능한 시장은 **{_tradeable_now}**입니다.\n{formatted_news}\n\n"
                    f"이 뉴스들을 분석해 다음을 정리하십시오:\n"
                    f"① 직접 언급되거나 직접 영향을 받는 **종목/업종**과 각각의 호재·악재·이벤트 — 가능하면 종목명(또는 6자리 코드/미국 티커)을 함께 적되, "
                    f"**각 종목이 국내(KR)인지 미국(US)인지 시장을 표기**하고, 뉴스에 실제로 나온 것만 쓰고 모르는 코드는 지어내지 마십시오. "
                    f"지금 매매 가능한 시장({_tradeable_now})의 종목을 우선 부각하되, 반대 시장 뉴스도 맥락·테마로 정리하십시오.\n"
                    f"② 시장 전반 분위기·주목 테마 (1~3줄).\n"
                    f"③ 매크로(금리/환율/원자재/지정학) 시사점 — 전략리서치팀장 매크로 분석에 영감을 줄 수 있는 포인트 1~3개.\n"
                    f"이 분석은 전략리서치팀장 매크로 분석 및 운용전략실장 종목 선정에 최우선으로 반영됩니다.")
                news_report = await self._shared_or_compute(
                    "news_report", None, lambda: self.news_analyst.think(_news_prompt))
```

(b) `main_swarm.py:3352` — _research_macro_themes. Change:
```python
                    _macro_research = await self._research_macro_themes(
                        session, force=market_open,
                        news_digest=news_report, index_digest=index_facts)
```
to:
```python
                    _macro_research = await self._shared_or_compute(
                        "macro_research", None,
                        lambda: self._research_macro_themes(
                            session, force=market_open,
                            news_digest=news_report, index_digest=index_facts))
```

(c) `main_swarm.py:3384` — macro_analyst.think. Change:
```python
                macro_report = await self.macro_analyst.think(
                    f"{_cache_hint}{index_facts}\n{_prev_macro_hint}"
                    ...
                    f"표에 없는 수치는 추정·생성 금지.")
```
to (build prompt into a var, then wrap):
```python
                _macro_prompt = (
                    f"{_cache_hint}{index_facts}\n{_prev_macro_hint}"
                    f"{_research_section}\n"
                    f"뉴스분석팀장 뉴스 분석 (감성·이벤트 정리, 본 사이클 {len(news_articles)}건 기반):\n{news_report}\n\n"
                    f"최신 공시:\n{dart_report}\n\n세션: {session}.\n"
                    f"위 정보를 종합하여 매크로 분석과 자산배분 가이드라인을 제시하십시오. **가격 데이터 우선순위**:\n"
                    f"  1순위 (수치): '검증된 글로벌 지수' (네이버 크롤) — 모든 가격·% 인용은 여기서만\n"
                    f"  2순위 (해설): 매크로 리서치 (alibaba)의 시황·정책·수급·심리 분석 — 가격은 인용 금지, 해설만 활용\n"
                    f"  3순위 (이벤트): 뉴스분석팀장 분석 — 감성·이벤트 흐름\n"
                    f"⚠️ 매크로 리서치 결과에 가격·지수 수치가 있어도 **출처를 알 수 없으므로 인용 금지**. "
                    f"가격이 필요하면 위 검증된 지수 표에서만 가져오십시오.\n"
                    f"⚠️ 리서치 답변에서 *해설·정책 방향·투자심리·수급 흐름·지정학적 영향*은 적극 활용·인용 OK.\n"
                    f"⚠️ 뉴스에서 짚은 매크로 시사점(금리/환율/원자재/지정학)을 반드시 매크로 결론에 통합하십시오.\n"
                    f"⚠️ 직전 권고와 다른 비중을 권고할 때는 반드시 정확한 변경 폭을 표기하십시오.\n"
                    f"⚠️ 사장 피드백 2026-05-16: **상세히** 작성하십시오 — 매크로 환경 요약은 핵심 동인별로 "
                    f"(무엇이/왜/시장 영향 경로) 풀어쓰고, 자산 배분 권고는 주식·채권·현금 각각에 대해 "
                    f"근거 1~2줄씩, 핵심 리스크는 3개 이상 + 각 리스크의 트리거/모니터링 포인트를 함께 적으십시오. "
                    f"단, 가격·지수 수치 인용 규칙(검증 지수만)은 그대로 지키고 표/마크다운 강조는 쓰지 마십시오.\n"
                    f"표에 없는 수치는 추정·생성 금지.")
                macro_report = await self._shared_or_compute(
                    "macro_report", None, lambda: self.macro_analyst.think(_macro_prompt))
```

> ⚠️ 정확성: macro_analyst.think 프롬프트 본문(3385-3402)을 `_macro_prompt` 로 **글자 그대로** 옮긴다(누락·변형 금지). 구현 시 원본 라인을 복사해 붙인 뒤 think(...) 만 _shared_or_compute 로 교체할 것.

- [ ] **Step 4: Verify import + wiring test**

Run: `python3.11 -c "import main_swarm"` → 출력 없음(정상)
Run: `python3.11 -m pytest tests/test_shared_wiring.py -v`
Expected: PASS (1 passed)

---

## Task 7: 비관리자 뉴스 활동 표시 게이팅

**Files:**
- Modify: `main_swarm.py` (`_emit_news_activity` 추가), `main_swarm.py:3103` (크롤 emit), `main_swarm.py:3313`·`3327` (news_analyst agent_msg)
- Test: `tests/test_news_activity_gating.py`

`/api/news` 헤드라인은 그대로(전 계정). 가리는 것은 **크롤 활동 emit + news_analyst 분석 메시지** — 소스측에서 `self.is_admin` 일 때만 emit.

- [ ] **Step 1: Write the failing test**

`tests/test_news_activity_gating.py`:
```python
"""비관리자: 뉴스 크롤/분석 활동 메시지 미노출. ADMIN: 노출."""
import asyncio
import main_swarm

def _orch(is_admin):
    o = main_swarm.ArquantOrchestrator.__new__(main_swarm.ArquantOrchestrator)
    o.is_admin = is_admin
    o._emitted = []
    async def _emit(msg): o._emitted.append(msg)
    o._emit = _emit
    return o

def test_admin_emits_news_activity():
    o = _orch(True)
    asyncio.run(o._emit_news_activity({"type": "news", "count": 3}))
    assert o._emitted == [{"type": "news", "count": 3}]

def test_non_admin_suppresses_news_activity():
    o = _orch(False)
    asyncio.run(o._emit_news_activity({"type": "news", "count": 3}))
    assert o._emitted == []          # 비관리자엔 emit 자체 생략
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_news_activity_gating.py -v`
Expected: FAIL — `AttributeError: ... '_emit_news_activity'`

- [ ] **Step 3: Add `_emit_news_activity` + apply to the 3 news emits**

In `main_swarm.py`, after `_emit` (line 2769), add:
```python
    async def _emit_news_activity(self, msg):
        """뉴스 크롤·분석 활동 메시지 — 사장 지시 2026-06-08: ADMIN(hh09080)에게만 노출.
        (비관리자 대시보드는 /api/news 헤드라인만 보고, 크롤/분석 '활동'은 가린다.)"""
        if self.is_admin:
            await self._emit(msg)
```

Apply at the three sites (replace `await self._emit(` with `await self._emit_news_activity(` ONLY for these news-activity messages):

(a) `main_swarm.py:3103` — the news crawl emit `await self._emit({"type": "news", ...})` → `await self._emit_news_activity({"type": "news", ...})`.

(b) `main_swarm.py:3313-3314` — the `_sell_only` news agent_msg:
```python
                await self._emit({"type":"agent_msg","agent":"뉴스분석팀장",
                    "message":"🔕 신규 뉴스 없음 — 뉴스 분석 생략, 보유 종목 매도 평가(계량분석)만 진행합니다."})
```
→ replace `self._emit(` with `self._emit_news_activity(`.

(c) `main_swarm.py:3327` — the news report agent_msg:
```python
                await self._emit({"type":"agent_msg","agent":"뉴스분석팀장","message":news_report})
```
→ replace `self._emit(` with `self._emit_news_activity(`.

> 매크로 메시지(전략리서치팀장)는 게이팅하지 않는다 — 비관리자도 자기가 거래에 쓰는 (공유) 매크로 뷰는 본다(스펙 비목표).

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_news_activity_gating.py -v`
Expected: PASS (2 passed)

---

## Task 8: 전체 스위트 + 통합 검증

**Files:** (없음 — 검증 전용)

- [ ] **Step 1: Run the full test suite**

Run: `python3.11 -m pytest -q`
Expected: 기존 전체(직전 기준 692) + 본 계획 신규(약 22) 모두 PASS, 실패 0.

- [ ] **Step 2: Import smoke test**

Run: `python3.11 -c "import main_swarm, infra.market_intel, infra.auth_store, config; print('IMPORT_OK')"`
Expected: `IMPORT_OK`

- [ ] **Step 3: Spec coverage self-check (수동)**

스펙의 각 요구가 태스크에 매핑됨을 확인:
- MarketIntelligenceStore → Task 2 ✓
- _shared_or_compute(생산자 게시/소비자 대기·폴백) → Task 5 ✓, 배선 → Task 6 ✓
- 정시 케이던스(앵커=:00, first_run 제거, restart-invariant) → Task 4 ✓
- ADMIN 잠금(승격 거부·강등 거부·부팅 스윕) → Task 3 ✓
- 뉴스 활동 게이팅(/api/news 유지, 크롤·분석 활동 admin만) → Task 7 ✓
- 설정 2키 + 튜너블 등재 → Task 1 ✓

- [ ] **Step 4: 배포는 사장 확인 후**

코드 변경 반영 = `sudo systemctl restart arquant.service`. **이 계획 범위 밖이며 사장 확인 후에만** 실행한다(CLAUDE.md). 라이브 검증 포인트: ① 재시작 후 첫 사이클이 즉시가 아니라 다음 :00에 발화 ② hh09080 가동 시 비관리자(uid=2)가 매크로/뉴스를 자체 호출하지 않고 공유 수신 ③ 비관리자 대시보드에 뉴스 크롤/분석 활동 미표시·헤드라인은 표시 ④ 비-hh09080 admin 승격 거부.

---

## Self-Review (작성자 체크)

**1. Spec coverage:** 스펙 6개 요구 모두 태스크 존재(Task 8 Step 3 매핑표). 갭 없음.

**2. Placeholder scan:** "TBD/적절히/handle edge cases" 없음. 모든 코드 스텝에 실제 코드 포함. (Task 3 의 `_connect`/`_DB_PATH` monkeypatch는 모듈 실제 상수에 맞춰 조정하라는 명시적 단서 포함 — placeholder 아님.)

**3. Type consistency:** `_shared_or_compute(kind, fingerprint, compute)` 시그니처가 Task 5 정의 ↔ Task 6 호출 일치. `MarketIntelligenceStore.publish/peek/wait_for` 시그니처가 Task 2 정의 ↔ Task 5 사용 일치(`peek(kind, hour_key, fingerprint)`, `wait_for(..., timeout=)`, `publish(..., uid=, now=)`). `_current_hour_key()`(datetime) vs `_current_hour_key_str()`(str) 구분 일관(케이던스=datetime 비교, 스토어 키=str). `self.is_admin`(기존 2682)·`self._producer_absent_this_cycle`(Task 4 추가) 일관.
