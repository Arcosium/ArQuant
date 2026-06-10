# ArQuant Phase 2 — True Multi-Tenant Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let 5–15 accounts trade concurrently and independently in one process, with no cross-contamination of credentials, brokers, swarms, equity/trade logs, cycle DB, or WebSocket events.

**Architecture:** Replace the single global active-account model (`config` globals + `_broker`/`_swarm` singletons + `.active_account`) with a `UserRegistry: dict[uid] -> UserContext`. Each `UserContext` owns its credentials, a `KISBroker(creds)`, an `ArquantOrchestrator(ctx)`, its asyncio task, and a `data/<uid>/` path set. The shared FastAPI/WS/news layer routes by `request.state.user_id`.

**Tech Stack:** Python 3.11 (tests MUST run under `python3.11` — default `python` dies on argon2 import), FastAPI, asyncio, aiohttp, SQLite, pytest.

**Spec:** `docs/superpowers/specs/2026-05-26-arquant-multitenant-phase2-design.md`

**Conventions for every task:**
- Run tests with `python3.11 -m pytest` (never bare `python`).
- Korean is fine in strings/comments; keep identifiers/log/commands in original form.
- Do NOT restart `arquant.service` mid-plan; deploy happens once at the final task (owner confirms).
- Commit after each task. The external auto-Backup tool also commits periodically — that is expected.

---

## File structure (created / modified)

- **Create** `infra/user_context.py` — `UserContext` + `UserRegistry` (the isolation core).
- **Create** `infra/data_migration.py` — one-shot boot migration (back up globals, start fresh).
- **Create** `infra/user_paths.py` — per-uid path helper (`data/<uid>/…`).
- **Modify** `infra/kis_broker.py` — `KISBroker.__init__(creds, token_path)`; retire `get_broker()` global singleton.
- **Modify** `agents/base_agent.py` — inject `deepseek_api_key`/`base_url`/`model_overrides` instead of reading `config` globals.
- **Modify** `agents/specialists.py` — `create_*` factories accept and forward an injection bundle.
- **Modify** `main_swarm.py` — `ArquantOrchestrator(ctx)`; per-uid equity/trade paths; `record_equity(ctx, …)`; broadcast via `send_to_uid`; drop `_active_actor` global lookup; per-uid cost tag.
- **Modify** `infra/cycle_store.py` — add `uid` column + filter reads by uid.
- **Modify** `server/app.py` — registry wiring; per-uid `/api/start|stop|ceo|status`; drop `set_active`/`account_switch_policy`; WS per-uid; boot resume of all running uids.
- **Modify** `infra/credentials.py` — remove `set_active`/`account_switch_policy`/`.active_account`; keep only credential-decrypt helpers used by `UserContext`.
- **Modify** `agents/base_agent.py` cost log + **create** `data/<uid>/cost_rollup.json` writer.

---

## Task 1: UserContext + UserRegistry (isolation core)

**Files:**
- Create: `infra/user_paths.py`
- Create: `infra/user_context.py`
- Test: `tests/test_user_context.py`

- [ ] **Step 1: Write the path helper** (`infra/user_paths.py`)

```python
"""Per-uid data paths. Every user's runtime state lives under data/<uid>/."""
from __future__ import annotations
from pathlib import Path

_DATA_DIR = Path(__file__).parent.parent / "data"


def user_dir(uid: int) -> Path:
    d = _DATA_DIR / str(int(uid))
    d.mkdir(parents=True, exist_ok=True)
    return d


def equity_path(uid: int) -> Path:
    return user_dir(uid) / "equity_curve.json"


def trade_log_path(uid: int) -> Path:
    return user_dir(uid) / "trade_log.json"


def token_path(uid: int) -> Path:
    return user_dir(uid) / "kis_token.json"


def cost_rollup_path(uid: int) -> Path:
    return user_dir(uid) / "cost_rollup.json"


def running_marker(uid: int) -> Path:
    return user_dir(uid) / ".running"
```

- [ ] **Step 2: Write the failing test** (`tests/test_user_context.py`)

```python
import pytest
from infra import user_context as uc


def test_registry_isolates_two_uids(monkeypatch):
    creds_by_uid = {
        1: {"id": 1, "username": "hh09080", "is_admin": True,
            "kis_app_key": "K1", "kis_app_secret": "S1", "kis_account_no": "111-01",
            "kis_base_url": "https://openapi.koreainvestment.com:9443",
            "deepseek_api_key": "OR1", "dart_key": "", "label": "admin"},
        2: {"id": 2, "username": "hh0908", "is_admin": False,
            "kis_app_key": "K2", "kis_app_secret": "S2", "kis_account_no": "222-01",
            "kis_base_url": "https://openapivts.koreainvestment.com:29443",
            "deepseek_api_key": "OR2", "dart_key": "", "label": "mock"},
    }
    monkeypatch.setattr(uc.auth_store, "get_user_credentials",
                        lambda uid: creds_by_uid.get(int(uid)))

    reg = uc.UserRegistry()
    c1 = reg.get_or_create(1)
    c2 = reg.get_or_create(2)

    assert c1 is not c2
    assert c1.creds["kis_app_key"] == "K1"
    assert c2.creds["kis_app_key"] == "K2"
    assert c1.is_admin is True and c2.is_admin is False
    # Same uid returns the same cached context (no rebuild)
    assert reg.get_or_create(1) is c1


def test_unknown_uid_raises(monkeypatch):
    monkeypatch.setattr(uc.auth_store, "get_user_credentials", lambda uid: None)
    reg = uc.UserRegistry()
    with pytest.raises(ValueError):
        reg.get_or_create(999)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_user_context.py -v`
Expected: FAIL with `ModuleNotFoundError: infra.user_context`.

- [ ] **Step 4: Implement `infra/user_context.py`**

```python
"""Per-uid runtime context + registry — the multi-tenant isolation core.

Each UserContext holds one user's decrypted credentials and lazily builds that
user's KISBroker and ArquantOrchestrator. The registry keeps one context per uid
for the life of the process. Nothing here mutates global config — isolation comes
from injection, not from rewriting shared globals.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

from infra import auth_store
from infra import user_paths

logger = logging.getLogger("USER_CTX")


class UserContext:
    def __init__(self, creds: Dict[str, Any]):
        self.uid: int = int(creds["id"])
        self.creds: Dict[str, Any] = creds
        self.is_admin: bool = bool(creds.get("is_admin"))
        self.paths = user_paths
        self._broker = None   # lazy — built on first access
        self._swarm = None    # lazy — built on first access
        self.task = None      # asyncio.Task | None (this uid's trading loop)

    @property
    def broker(self):
        if self._broker is None:
            from infra.kis_broker import KISBroker
            self._broker = KISBroker(self.creds,
                                     token_path=user_paths.token_path(self.uid))
        return self._broker

    @property
    def swarm(self):
        if self._swarm is None:
            from main_swarm import ArquantOrchestrator
            self._swarm = ArquantOrchestrator(self)
        return self._swarm

    def reset(self) -> None:
        """Drop broker/swarm so they rebuild (e.g. after a credentials update)."""
        self._broker = None
        self._swarm = None


class UserRegistry:
    def __init__(self):
        self._ctx: Dict[int, UserContext] = {}
        self._lock = threading.RLock()

    def get_or_create(self, uid: int) -> UserContext:
        uid = int(uid)
        with self._lock:
            ctx = self._ctx.get(uid)
            if ctx is not None:
                return ctx
            creds = auth_store.get_user_credentials(uid)
            if not creds:
                raise ValueError(f"user_id={uid} 자격증명 없음 — 컨텍스트 생성 불가")
            ctx = UserContext(creds)
            self._ctx[uid] = ctx
            logger.info("UserContext 생성 uid=%s label=%s admin=%s",
                        uid, creds.get("label"), ctx.is_admin)
            return ctx

    def get(self, uid: int) -> Optional[UserContext]:
        return self._ctx.get(int(uid))

    def all_contexts(self) -> Dict[int, UserContext]:
        return dict(self._ctx)

    def drop(self, uid: int) -> None:
        with self._lock:
            self._ctx.pop(int(uid), None)


REGISTRY = UserRegistry()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_user_context.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add infra/user_paths.py infra/user_context.py tests/test_user_context.py
git commit -m "feat(mt): UserContext + UserRegistry isolation core"
```

---

## Task 2: Inject credentials into KISBroker (per-uid broker + token)

**Files:**
- Modify: `infra/kis_broker.py:63-102` (`__init__`, `_load_token_file`, `_save_token_file`), `kis_broker.py:1034-1038` (`get_broker`)
- Test: `tests/test_broker_injection.py`

- [ ] **Step 1: Write the failing test** (`tests/test_broker_injection.py`)

```python
from pathlib import Path
from infra.kis_broker import KISBroker


def _creds(mock=False):
    return {
        "kis_app_key": "APPKEY", "kis_app_secret": "SECRET",
        "kis_account_no": "12345678-01",
        "kis_base_url": ("https://openapivts.koreainvestment.com:29443" if mock
                         else "https://openapi.koreainvestment.com:9443"),
    }


def test_broker_reads_injected_creds_not_config(tmp_path):
    b = KISBroker(_creds(mock=True), token_path=tmp_path / "tok.json")
    assert b.app_key == "APPKEY"
    assert b.account_no == "12345678-01"
    assert b.is_mock is True
    assert b._acnt() == ("12345678", "01")


def test_token_file_is_per_uid_path(tmp_path):
    p = tmp_path / "kis_token.json"
    b = KISBroker(_creds(), token_path=p)
    b._save_token_file("TOKEN123", 9999999999.0)
    assert p.exists()
    loaded = b._load_token_file()
    assert loaded["access_token"] == "TOKEN123"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_broker_injection.py -v`
Expected: FAIL — `__init__` currently takes no args / reads config.

- [ ] **Step 3: Change `KISBroker.__init__` to accept injected creds + token path** (`infra/kis_broker.py:64`)

Replace:
```python
    def __init__(self):
        from config import KIS_APP_KEY, KIS_APP_SECRET, KIS_BASE_URL, KIS_ACCOUNT_NO
        self.app_key = KIS_APP_KEY; self.app_secret = KIS_APP_SECRET
        self.base_url = KIS_BASE_URL; self.account_no = KIS_ACCOUNT_NO
```
with:
```python
    def __init__(self, creds: dict, token_path=None):
        # Phase 2: credentials are injected per-uid. No more config globals.
        self.app_key = creds["kis_app_key"]; self.app_secret = creds["kis_app_secret"]
        self.base_url = creds.get("kis_base_url") or "https://openapi.koreainvestment.com:9443"
        self.account_no = creds["kis_account_no"]
        self._token_path = Path(token_path) if token_path else TOKEN_CACHE_FILE
```

- [ ] **Step 4: Point the token cache at the per-uid path** (`infra/kis_broker.py:88,98`)

In `_load_token_file` change `TOKEN_CACHE_FILE.exists()` → `self._token_path.exists()` and `TOKEN_CACHE_FILE.read_text(...)` → `self._token_path.read_text(...)`.
In `_save_token_file` change `TOKEN_CACHE_FILE.write_text(...)` → `self._token_path.write_text(...)`.
(Keep the module-level `TOKEN_CACHE_FILE` as the default for backward-compat callers.)

- [ ] **Step 5: Replace the global `get_broker()` singleton** (`infra/kis_broker.py:1034-1038`)

Replace:
```python
_broker: Optional[KISBroker] = None
def get_broker() -> KISBroker:
    global _broker
    if _broker is None: _broker = KISBroker()
    return _broker
```
with:
```python
# Phase 2: the global broker singleton is retired. Brokers are owned by UserContext
# (one per uid, built with that uid's injected credentials). This shim raises so any
# stray caller is caught loudly instead of silently trading on the wrong account.
def get_broker():
    raise RuntimeError(
        "get_broker() is retired in Phase 2 — use UserContext.broker (per-uid). "
        "A caller still references the global broker singleton.")
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_broker_injection.py -v`
Expected: PASS. (Other modules importing `get_broker` will be fixed in Task 4/7; this task isolates the broker change. Run only this test file here.)

- [ ] **Step 7: Commit**

```bash
git add infra/kis_broker.py tests/test_broker_injection.py
git commit -m "feat(mt): inject creds into KISBroker, per-uid token cache, retire get_broker singleton"
```

---

## Task 3: Inject credentials/model into BaseAgent + specialist factories

**Files:**
- Modify: `agents/base_agent.py:150-180` (`__init__`)
- Modify: `agents/specialists.py` (all `create_*` factories)
- Test: `tests/test_agent_injection.py`

- [ ] **Step 1: Write the failing test** (`tests/test_agent_injection.py`)

```python
from agents.base_agent import BaseAgent


def test_agent_uses_injected_deepseek_api_key():
    inj = {"deepseek_api_key": "OR-INJECTED",
           "deepseek_base_url": "https://DeepSeek.ai/api/v1",
           "model_overrides": {"quant_analyst": "deepseek-v4-pro"}}
    a = BaseAgent(name="t", role="quant_analyst", system_prompt="p",
                  model_key="quant_analyst", injection=inj)
    assert a.api_key == "OR-INJECTED"
    assert a.model == "deepseek-v4-pro"   # per-injection override wins


def test_agent_falls_back_to_config_when_no_injection():
    # No injection bundle → legacy behaviour (reads config). Must not crash.
    a = BaseAgent(name="t", role="quant_analyst", system_prompt="p",
                  model_key="quant_analyst")
    assert isinstance(a.model, str) and a.model
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_agent_injection.py -v`
Expected: FAIL — `BaseAgent.__init__` has no `injection` param.

- [ ] **Step 3: Add an `injection` bundle to `BaseAgent.__init__`** (`agents/base_agent.py:150`)

Add `injection: Optional[Dict[str, Any]] = None` to the signature. Replace the body from line 158 (`from config import ...`) through line 172 (`self.base_url = DEEPSEEK_BASE_URL`) with:

```python
        from config import (MODEL_ASSIGNMENTS, AGENT_MAX_TOKENS, ENABLE_PROMPT_CACHE,
                            AGENT_HISTORY_TURNS, DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL)
        self.name = name
        self.role = role
        self.system_prompt = system_prompt
        inj = injection or {}
        # Per-uid model override wins; else admin global override; else config default.
        _ov = (inj.get("model_overrides") or {}).get(model_key) or ""
        if not _ov:
            try:
                from infra import admin_config
                _ov = admin_config.get_model_override(model_key)
            except Exception:
                _ov = ""
        self.model = _ov or MODEL_ASSIGNMENTS.get(model_key, "deepseek-v4-flash")
        # Credentials: injected per-uid DeepSeek key, else config global (legacy/no-uid).
        self.api_key = inj.get("deepseek_api_key") or DEEPSEEK_API_KEY
        self.base_url = inj.get("deepseek_base_url") or DEEPSEEK_BASE_URL
```

(Leave lines 173-180 — `self.tools`, history, max_tokens, prompt-cache — unchanged.)

- [ ] **Step 4: Thread `injection` through specialist factories** (`agents/specialists.py`)

Each `create_*` factory currently builds a `BaseAgent(...)`. Add `injection=None` as the last param of every `create_*` function and forward it: `BaseAgent(..., injection=injection)`. Example for the quant analyst factory:

```python
def create_quant_analyst(injection=None):
    return BaseAgent(name="계량분석팀장", role="quant_analyst",
                     model_key="quant_analyst", system_prompt=QUANT_PROMPT,
                     injection=injection)
```
Apply the same pattern to: `create_macro_analyst`, `create_quant_analyst`, `create_news_analyst`, `create_trader`, `create_risk_guard`, `create_post_manager`, `create_ops_support` (grep `def create_` in `agents/specialists.py` for the full list — forward `injection` in each).

- [ ] **Step 5: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_agent_injection.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add agents/base_agent.py agents/specialists.py tests/test_agent_injection.py
git commit -m "feat(mt): inject DeepSeek key + per-uid model overrides into agents"
```

---

## Task 4: ArquantOrchestrator(ctx) — per-uid broker, equity, trade log, cost

**Files:**
- Modify: `main_swarm.py:1247-1321` (`__init__`), `:284-...` (`record_equity`), `:815-830` (broadcast), `:1432` (`_active_actor`), agent factory calls
- Modify: `agents/base_agent.py` cost log (per-uid tag) + `infra/user_paths.py` already has `cost_rollup_path`
- Test: `tests/test_orchestrator_ctx.py`

- [ ] **Step 1: Write the failing test** (`tests/test_orchestrator_ctx.py`)

```python
from infra.user_context import UserContext


def _ctx(uid, mock=False):
    return UserContext({
        "id": uid, "username": f"u{uid}", "is_admin": uid == 1,
        "kis_app_key": f"K{uid}", "kis_app_secret": f"S{uid}",
        "kis_account_no": f"{uid}-01",
        "kis_base_url": ("https://openapivts.koreainvestment.com:29443" if mock
                         else "https://openapi.koreainvestment.com:9443"),
        "deepseek_api_key": f"OR{uid}", "dart_key": "", "label": f"u{uid}",
    })


def test_orchestrator_owns_uid_and_per_uid_paths():
    from main_swarm import ArquantOrchestrator
    o1 = ArquantOrchestrator(_ctx(1))
    o2 = ArquantOrchestrator(_ctx(2, mock=True))
    assert o1.uid == 1 and o2.uid == 2
    assert o1.broker is not o2.broker
    assert o1.broker.app_key == "K1" and o2.broker.app_key == "K2"
    assert str(o1.equity_path).endswith("/1/equity_curve.json")
    assert str(o2.equity_path).endswith("/2/equity_curve.json")
    # agents got the per-uid DeepSeek key
    assert o1.orchestrator.api_key == "OR1"
    assert o2.orchestrator.api_key == "OR2"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_orchestrator_ctx.py -v`
Expected: FAIL — `ArquantOrchestrator.__init__` takes no `ctx`.

- [ ] **Step 3: Make `ArquantOrchestrator.__init__` take a `ctx`** (`main_swarm.py:1248`)

Change `def __init__(self):` → `def __init__(self, ctx):` and at the top of the body add:
```python
        self.ctx = ctx
        self.uid = ctx.uid
        self.is_admin = ctx.is_admin
        from infra import user_paths
        self.equity_path = user_paths.equity_path(ctx.uid)
        self.trade_log_path = user_paths.trade_log_path(ctx.uid)
        _inj = {"deepseek_api_key": ctx.creds.get("deepseek_api_key"),
                "deepseek_base_url": None}
```
Then:
- Pass `injection=_inj` to the two inline `BaseAgent(...)` constructions (`self.orchestrator` at :1249, `self.news_curator` at :1287).
- Pass `injection=_inj` to every `create_*()` call (:1277-1284): `create_macro_analyst(injection=_inj)`, etc.
- Replace `self.broker = get_broker()` (:1293) with `self.broker = ctx.broker`.

- [ ] **Step 4: Make `_active_actor` return this orchestrator's own identity** (`main_swarm.py:1432`)

Replace the `credentials.current()` lookup body with:
```python
    def _active_actor(self) -> tuple:
        """This orchestrator's (uid, is_admin). Phase 2: identity comes from the owning
        UserContext, not a global active account."""
        return self.uid, self.is_admin
```
(Remove the `@staticmethod` decorator if present — it now uses `self`.)

- [ ] **Step 5: Make `record_equity` write to a per-uid path**

`record_equity` (`main_swarm.py:284`) currently writes the module-global `_EQUITY_LOG`. Change its signature to `record_equity(ctx, bp, source="poll", holdings=None)` and use `ctx.equity_path` / `ctx.trade_log_path` instead of `_EQUITY_LOG` / `_RESPONSE_LOG` inside. Update the readers at `:447,533` and the caller in the poll loop to pass the relevant `ctx`. Keep `_EQUITY_LOG` defined only for the migration backup step (Task 6).

```python
def record_equity(ctx, bp: dict, source: str = "poll", holdings=None):
    equity_path = ctx.equity_path
    # ...existing body, with every _EQUITY_LOG replaced by equity_path...
```

- [ ] **Step 6: Tag the API cost log by uid** (`agents/base_agent.py:30,111-117`)

`_API_CALL_LOG` and `_record_api_call` are module-global. Add the agent's uid to each record so the per-uid roll-up (written to `user_paths.cost_rollup_path(uid)` by the swarm) is correct. Pass `self.uid` (set it in `BaseAgent.__init__` from `injection.get("uid")`) into `_record_api_call`. In `_inj` (Step 3) add `"uid": ctx.uid`.

```python
# base_agent.py __init__ (after api_key assignment):
self.uid = (injection or {}).get("uid")
# _record_api_call call site:
_record_api_call(self.model, self.name, _pt, _ct, uid=self.uid)
# _record_api_call signature gains: uid: Optional[int] = None  → store in the dict.
```

- [ ] **Step 7: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_orchestrator_ctx.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add main_swarm.py agents/base_agent.py tests/test_orchestrator_ctx.py
git commit -m "feat(mt): ArquantOrchestrator owns UserContext; per-uid broker/equity/trade/cost"
```

---

## Task 5: cycle_store gains a uid column

**Files:**
- Modify: `infra/cycle_store.py:22-58` (schema), `:83-108` (`record_cycle`), `:110-120` (`list_cycles`)
- Test: `tests/test_cycle_store_uid.py`

- [ ] **Step 1: Write the failing test** (`tests/test_cycle_store_uid.py`)

```python
import importlib


def test_cycles_are_filtered_by_uid(tmp_path, monkeypatch):
    import infra.cycle_store as cs
    monkeypatch.setattr(cs, "DB_PATH", tmp_path / "cycles.db")
    monkeypatch.setattr(cs, "_conn", None)
    cs.record_cycle({"started_at": "2026-05-26 10:00:00", "session": "KR_TRADING", "uid": 1})
    cs.record_cycle({"started_at": "2026-05-26 10:01:00", "session": "US_TRADING", "uid": 2})
    only1 = cs.list_cycles(uid=1)
    assert len(only1) == 1 and only1[0]["uid"] == 1
    assert len(cs.list_cycles(uid=2)) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_cycle_store_uid.py -v`
Expected: FAIL — no `uid` column / `list_cycles` has no `uid` arg.

- [ ] **Step 3: Add `uid` to schema + a migration ALTER** (`infra/cycle_store.py`)

In `_SCHEMA` add `uid INTEGER` to the `cycles` table. In `_get_conn()` after `executescript(_SCHEMA)`, add an idempotent column guard:
```python
        cols = {r[1] for r in _conn.execute("PRAGMA table_info(cycles)").fetchall()}
        if "uid" not in cols:
            _conn.execute("ALTER TABLE cycles ADD COLUMN uid INTEGER")
        _conn.execute("CREATE INDEX IF NOT EXISTS idx_cycles_uid ON cycles(uid)")
```

- [ ] **Step 4: Write `uid` in `record_cycle` and filter in `list_cycles`**

In `record_cycle`, add `"uid"` to the `cols` tuple (so it persists from `meta`). In `list_cycles`, add `uid: Optional[int] = None` and when provided filter:
```python
def list_cycles(limit: int = 50, offset: int = 0, uid: Optional[int] = None) -> List[Dict]:
    where = "WHERE uid=?" if uid is not None else ""
    args = ([int(uid)] if uid is not None else []) + [int(limit), int(offset)]
    rows = _get_conn().execute(
        f"SELECT * FROM cycles {where} ORDER BY id DESC LIMIT ? OFFSET ?", args).fetchall()
    return [dict(r) for r in rows]
```
Also pass `uid` from the orchestrator's `record_cycle({... "uid": self.uid})` call site in `main_swarm.py`.

- [ ] **Step 5: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_cycle_store_uid.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add infra/cycle_store.py main_swarm.py tests/test_cycle_store_uid.py
git commit -m "feat(mt): per-uid cycle_store column + filtered reads"
```

---

## Task 6: Boot migration — back up globals, start fresh

**Files:**
- Create: `infra/data_migration.py`
- Test: `tests/test_data_migration.py`

- [ ] **Step 1: Write the failing test** (`tests/test_data_migration.py`)

```python
def test_migration_moves_global_files_to_backup(tmp_path, monkeypatch):
    import infra.data_migration as dm
    data = tmp_path / "data"; data.mkdir()
    (data / "equity_curve.json").write_text("[1,2,3]")
    (data / "strategy_state.json").write_text("{}")
    monkeypatch.setattr(dm, "_DATA_DIR", data)

    moved = dm.migrate_once()
    assert moved is True
    # globals gone from top level, present in a backup dir
    assert not (data / "equity_curve.json").exists()
    backups = list(data.glob("_migration_backup_*"))
    assert backups and (backups[0] / "equity_curve.json").exists()
    # idempotent: second run does nothing
    assert dm.migrate_once() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_data_migration.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `infra/data_migration.py`**

```python
"""One-shot Phase 2 migration: move legacy single-tenant global state files into a
timestamped backup dir so every uid starts fresh (owner decision 2026-05-26).
Idempotent — a sentinel marks completion so reboots don't re-run it."""
from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("MIGRATION")
_DATA_DIR = Path(__file__).parent.parent / "data"

# Legacy global files that became per-uid in Phase 2.
_GLOBAL_FILES = ("equity_curve.json", "strategy_state.json", "strategy_history.json",
                 "cycles.db", "kis_token.json", "claude_response.json",
                 "api_cost_rollup.json")
_SENTINEL = ".phase2_migrated"


def migrate_once() -> bool:
    """Returns True if it performed the migration, False if already done / nothing to move."""
    sentinel = _DATA_DIR / _SENTINEL
    if sentinel.exists():
        return False
    present = [f for f in _GLOBAL_FILES if (_DATA_DIR / f).exists()]
    if not present:
        sentinel.write_text(datetime.now().isoformat(), encoding="utf-8")
        return False
    backup = _DATA_DIR / f"_migration_backup_{datetime.now():%Y%m%d_%H%M%S}"
    backup.mkdir(parents=True, exist_ok=True)
    for f in present:
        shutil.move(str(_DATA_DIR / f), str(backup / f))
        logger.info("Phase2 마이그레이션: %s → %s", f, backup.name)
    sentinel.write_text(datetime.now().isoformat(), encoding="utf-8")
    logger.info("Phase2 마이그레이션 완료 — %d개 파일 백업, 각 uid 빈 상태로 시작", len(present))
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_data_migration.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add infra/data_migration.py tests/test_data_migration.py
git commit -m "feat(mt): one-shot boot migration (back up globals, start fresh)"
```

---

## Task 7: server/app.py — registry wiring, per-uid lifecycle, drop set_active

**Files:**
- Modify: `server/app.py` — `_task`→per-uid tasks; `/api/start|stop|status|ceo`; WS; startup; remove `_activate_with_policy`/`set_active` calls (`:116-132,297,313,555-572,1122-1148`)
- Modify: `infra/credentials.py` — remove `set_active`/`account_switch_policy`/`reactivate_last`/`.active_account` (keep nothing the registry doesn't need)
- Test: `tests/test_app_per_uid_lifecycle.py`

- [ ] **Step 1: Write the failing test** (`tests/test_app_per_uid_lifecycle.py`)

```python
import asyncio
import pytest


def test_start_stop_are_per_uid(monkeypatch):
    """Starting uid=2's loop must not touch uid=1's task."""
    import server.app as app

    class FakeSwarm:
        def __init__(self): self.stopped = False
        async def start_continuous(self, directive=None):
            await asyncio.sleep(3600)
        def stop(self): self.stopped = True

    # registry returns a context whose .swarm is a FakeSwarm and .task is tracked
    from infra.user_context import UserContext
    ctxs = {}
    def fake_get_or_create(uid):
        if uid not in ctxs:
            c = UserContext.__new__(UserContext)
            c.uid = uid; c.is_admin = uid == 1; c._swarm = FakeSwarm(); c.task = None
            ctxs[uid] = c
        return ctxs[uid]
    monkeypatch.setattr(app.REGISTRY, "get_or_create", fake_get_or_create)

    async def run():
        await app._start_uid(1)
        await app._start_uid(2)
        assert ctxs[1].task is not None and not ctxs[1].task.done()
        await app._stop_uid(2)
        assert ctxs[2].swarm.stopped is True
        assert not ctxs[1].task.done()   # uid=1 untouched
        await app._stop_uid(1)
    asyncio.get_event_loop().run_until_complete(run())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_app_per_uid_lifecycle.py -v`
Expected: FAIL — `_start_uid`/`_stop_uid` don't exist; `REGISTRY` not imported in app.

- [ ] **Step 3: Add registry + per-uid lifecycle helpers to `server/app.py`**

Near the top imports add `from infra.user_context import REGISTRY`. Replace the single `_task` global (`:230`) with per-uid helpers:

```python
async def _start_uid(uid: int, directive: Optional[str] = None) -> None:
    ctx = REGISTRY.get_or_create(uid)
    if ctx.task and not ctx.task.done():
        raise HTTPException(409, "이미 감시 중")
    from infra import user_paths
    user_paths.running_marker(uid).write_text("1", encoding="utf-8")
    ctx.task = asyncio.create_task(_supervised_loop(ctx, directive))


async def _supervised_loop(ctx, directive):
    """Isolate one uid's loop: an unhandled exception must not kill other uids/web."""
    try:
        await ctx.swarm.start_continuous(directive)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.exception("uid=%s 매매 루프 비정상 종료: %s", ctx.uid, e)
        try:
            await ws_mgr.send_to_uid(ctx.uid, {"type": "status", "state": "ERROR",
                                               "detail": f"매매 루프 중단: {e}"})
        except Exception:
            pass


async def _stop_uid(uid: int) -> None:
    ctx = REGISTRY.get(uid)
    from infra import user_paths
    user_paths.running_marker(uid).unlink(missing_ok=True)
    if not ctx:
        return
    ctx.swarm.stop()
    if ctx.task and not ctx.task.done():
        ctx.task.cancel()
    ctx.task = None
```

- [ ] **Step 4: Rewrite the `/api/start|stop|status|ceo` endpoints to route by `request.state.user_id`**

```python
@app.post("/api/start")
async def start(req: StartReq, request: Request):
    await _start_uid(request.state.user_id, req.directive)
    return {"ok": True}

@app.post("/api/stop")
async def stop(request: Request):
    await _stop_uid(request.state.user_id)
    return {"ok": True}

@app.get("/api/status")
async def status(request: Request):
    ctx = REGISTRY.get(request.state.user_id)
    s = ctx.swarm.get_status() if ctx else {"state": "IDLE"}
    s["is_running"] = bool(ctx and ctx.task and not ctx.task.done())
    return s

@app.post("/api/ceo")
async def ceo(req: DirectiveReq, request: Request):
    ctx = REGISTRY.get_or_create(request.state.user_id)
    return {"reply": await ctx.swarm.ceo_directive(req.text)}
```
(Match `StartReq`/`DirectiveReq` to the existing request models; the existing `/api/start` used `req.directive`.)

- [ ] **Step 5: Remove `_activate_with_policy`, `set_active`, and the switch endpoint**

- Delete `_activate_with_policy` (`:116-132`) and its calls in `register` (`:297`) and `login` (`:313`). Login/register now just `_issue_session(...)`; they no longer seize a global account.
- Delete the `/api/switch` endpoint (`:516-522`) and `/api/accounts` "active" field references.
- In `infra/credentials.py`, delete `set_active`, `clear_active`, `current`, `account_switch_policy`, `reactivate_last`, `_apply_to_config`, `_reset_singletons`, and the `_ACTIVE_FILE` logic. (If any remaining import breaks, switch it to `REGISTRY`.)

- [ ] **Step 6: Rewrite startup to resume every uid whose `.running` marker is set** (`:1122-1148`)

```python
@app.on_event("startup")
async def _startup():
    auth_store.init()
    auth_store.migrate_passwords_and_bidx()
    from infra import data_migration
    data_migration.migrate_once()
    seeded = auth_store.bootstrap_from_env()
    from infra import user_paths
    for uid_dir in (user_paths._DATA_DIR).glob("*/"):
        try:
            uid = int(uid_dir.name)
        except ValueError:
            continue
        if (uid_dir / ".running").exists():
            try:
                await _start_uid(uid)
                logger.info("부팅 자동재개: uid=%s", uid)
            except Exception as e:
                logger.warning("uid=%s 자동재개 실패: %s", uid, e)
```

- [ ] **Step 7: WS broadcast → per-uid send.** In `main_swarm.set_broadcast_callback` / `_broadcast`, ensure the orchestrator calls the callback with its own uid so `app` routes via `ws_mgr.send_to_uid(uid, msg)`. Change `set_broadcast_callback(cb)` consumers so the swarm passes `self.uid`:

```python
# main_swarm: _broadcast becomes uid-aware
async def _broadcast(msg, uid=None):
    if _broadcast_cb:
        await _broadcast_cb(msg, uid)
# app wiring:
async def _route(msg, uid=None):
    if uid is None:
        await ws_mgr.broadcast(msg)        # system-wide (rare)
    else:
        await ws_mgr.send_to_uid(uid, msg)
set_broadcast_callback(_route)
```
Update orchestrator broadcast call sites to pass `self.uid`.

- [ ] **Step 8: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_app_per_uid_lifecycle.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add server/app.py infra/credentials.py tests/test_app_per_uid_lifecycle.py
git commit -m "feat(mt): per-uid start/stop/status/ceo, drop global set_active, resume all running uids"
```

---

## Task 8: Per-user CEO experience (directive routing + permission split)

**Files:**
- Modify: `main_swarm.py:1323-...` (`ceo_directive`) — route by tag, permission split using `self.is_admin`
- Create: `infra/standing_directives.py` companion (if not already per-uid) — store at `data/profiles/<uid>/standing_directives.json`
- Test: `tests/test_ceo_directive_routing.py`

- [ ] **Step 1: Write the failing test** (`tests/test_ceo_directive_routing.py`)

```python
import asyncio
from infra.user_context import UserContext


def _ctx(uid, admin):
    return UserContext({"id": uid, "username": f"u{uid}", "is_admin": admin,
        "kis_app_key": "K", "kis_app_secret": "S", "kis_account_no": "1-01",
        "kis_base_url": "https://openapivts.koreainvestment.com:29443",
        "deepseek_api_key": "OR", "dart_key": "", "label": "x"})


def test_non_admin_ops_support_cannot_modify_code(monkeypatch):
    from main_swarm import ArquantOrchestrator
    o = ArquantOrchestrator(_ctx(2, admin=False))
    # a code-modification style directive routed to ops_support must be refused for non-admin
    reply = asyncio.get_event_loop().run_until_complete(
        o.ceo_directive("@운용지원실장 소스 코드 고쳐서 배포해줘"))
    assert ("코드" in reply and ("불가" in reply or "ADMIN" in reply or "관리자" in reply))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_ceo_directive_routing.py -v`
Expected: FAIL (or wrong reply) until the permission split is explicit.

- [ ] **Step 3: Implement tag routing + permission split in `ceo_directive`**

In `ceo_directive` (`main_swarm.py:1323`), use `_auid, _admin = self._active_actor()` (now returns `self.uid, self.is_admin`). Routing:
- Parse a leading `@<agent>` tag against `self._agents_map`.
- **No tag:** send to `self.orchestrator`; if it cannot handle, ops_support replies "처리 불가".
- **Wrong tag:** hand off to the correct agent.
- **ops_support + non-admin + code-modification intent:** return the refusal text (reuse the existing non-admin message in `infra/profile_overrides.proposal_summary_text`: "소스 구조 변경은 ADMIN(hh09080) 전용입니다."). Only `self.is_admin` may trigger code modification.

```python
        if tagged == "운용지원실장" and not _admin and _is_code_modification(message):
            return ("ℹ️ 이 계정은 공유 소스 코드를 변경할 수 없습니다. 소스/배포 변경은 "
                    "ADMIN(hh09080) 전용입니다. 전략 설정값·주간 피드백 수준 튜닝만 가능합니다.")
```
where `_is_code_modification(message)` is a small keyword check (`"소스"`, `"코드"`, `"배포"`, `"재시작"`).

- [ ] **Step 4: Persist standing directives per-uid**

Standing ("from now on …") directives go to `data/profiles/<uid>/standing_directives.json`. Deletion by the user is permanent (no sentinel reseed — avoids the known resurrection bug). Inject them into the orchestrator prompt via the existing `build_orchestrator_directive_block(self.uid)` path (`main_swarm.py:2583`).

- [ ] **Step 5: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_ceo_directive_routing.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add main_swarm.py infra/standing_directives.py tests/test_ceo_directive_routing.py
git commit -m "feat(mt): per-uid CEO directive routing + admin-only code-mod permission split"
```

---

## Task 9: Isolation regression test (reproduce this incident) + full green + deploy

**Files:**
- Test: `tests/test_isolation_regression.py`
- Modify: any remaining `get_broker()`/`credentials.current()`/`set_active` references surfaced by the full suite

- [ ] **Step 1: Write the regression test** (`tests/test_isolation_regression.py`)

```python
import asyncio
from infra.user_context import UserRegistry, UserContext


def test_second_login_does_not_hijack_first(monkeypatch):
    """The exact incident: uid=1 running, uid=2 'logs in' → uid=1 unaffected."""
    creds = {
        1: {"id": 1, "username": "hh09080", "is_admin": True, "kis_app_key": "K1",
            "kis_app_secret": "S1", "kis_account_no": "111-01", "deepseek_api_key": "OR1",
            "kis_base_url": "https://openapi.koreainvestment.com:9443", "dart_key": "", "label": "a"},
        2: {"id": 2, "username": "hh0908", "is_admin": False, "kis_app_key": "K2",
            "kis_app_secret": "S2", "kis_account_no": "222-01", "deepseek_api_key": "OR2",
            "kis_base_url": "https://openapivts.koreainvestment.com:29443", "dart_key": "", "label": "b"},
    }
    import infra.user_context as ucm
    monkeypatch.setattr(ucm.auth_store, "get_user_credentials", lambda u: creds.get(int(u)))
    reg = UserRegistry()
    c1 = reg.get_or_create(1)
    b1 = c1.broker
    # uid=2 "logs in" → builds its own context; must not mutate c1
    c2 = reg.get_or_create(2)
    assert c1.broker is b1                      # uid=1 broker unchanged
    assert c1.broker.app_key == "K1"            # still hh09080's key
    assert c2.broker.app_key == "K2"            # hh0908 isolated
    assert c1.broker.is_mock is False and c2.broker.is_mock is True
    assert str(c1.equity_path).endswith("/1/equity_curve.json")
    assert str(c2.equity_path).endswith("/2/equity_curve.json")
```

- [ ] **Step 2: Run the regression test**

Run: `python3.11 -m pytest tests/test_isolation_regression.py -v`
Expected: PASS.

- [ ] **Step 3: Run the FULL suite and fix fallout**

Run: `python3.11 -m pytest`
Expected: all green. Likely fixes: any module still calling `get_broker()` (now raises) or `credentials.current()`/`set_active` (now removed). Grep and route each through `REGISTRY`/`ctx`. Also delete/skip `tests/test_*` that asserted the old global-active-account behaviour (e.g. account-switch policy tests) — replace with the per-uid equivalents.

```bash
grep -rn "get_broker\|credentials.current\|set_active\|account_switch_policy\|_active_actor\|\.active_account" --include=*.py . | grep -v tests/
```
Resolve every hit before declaring green.

- [ ] **Step 4: Commit**

```bash
git add tests/test_isolation_regression.py
git commit -m "test(mt): isolation regression (second login no longer hijacks first); full suite green"
```

- [ ] **Step 5: Deploy (owner-confirmed)**

Do NOT restart unilaterally. Report green suite + summary, then ask the owner to confirm:
```bash
sudo systemctl restart arquant.service
sudo systemctl status arquant.service   # health on port 8500
```
After restart, verify: log in as hh0908 while hh09080's loop runs → hh09080's status/equity/holdings unchanged; each account's WS events stay on its own connection.

---

## Self-review notes (coverage vs spec)

- Spec B layers 1–9 → Tasks 1–7 (creds/broker/swarm/equity/trade/news-shared/cycle/WS/ops). News (#6) intentionally stays shared — no task needed. Ops worker (#9) already applies per-profile; the admin-only code-mod gate is Task 8.
- Spec C (migration) → Task 6. Spec D (lifecycle/resume) → Task 7 Steps 3,6. Spec E (CEO) → Task 8. Spec F (WS) → Task 7 Step 7. Spec G (safety/error isolation) → Task 7 Step 3 (`_supervised_loop`) + Task 2 (token lock note below). Spec H (tests) → Tasks 1–9.
- **Open follow-up for execution:** per-uid token *file lock* (spec G) — add `fcntl.flock` (or a `threading.Lock` per uid) around `_save_token_file`/`_load_token_file` when implementing Task 2 if concurrent same-uid access is observed; for distinct uids the paths already differ so no lock is needed.
