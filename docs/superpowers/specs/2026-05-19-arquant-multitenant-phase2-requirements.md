# ArQuant Phase 2 — True Multi-Tenant Isolation (Requirements & Decisions Capture)

**Date:** 2026-05-19
**Status:** Requirements/decision log — **not yet a full spec.** Seeds Phase 2 brainstorming.
**Depends on:** Phase 1 (`2026-05-19-arquant-login-overhaul-design.md`) — the auth foundation.

> This document preserves the design conversation so Phase 2 can resume without re-deriving
> context. Phase 2 starts with its own brainstorming → spec → plan → implementation cycle.

## Why Phase 2 exists

Owner wants multiple distinct users to trade stably from their own accounts
concurrently. Current ArQuant is **single-active-account**; true isolation requires
separating **9 global single-state layers** (mapped below).

## 9 global single-state points (must each become per-user)

| # | Layer | Location | Current global state |
|---|---|---|---|
| 1 | Credentials | `infra/credentials.py` `_active` + `config` globals; `data/.active_account` | one active account; `set_active` rewrites config globals |
| 2 | Swarm/loop | `main_swarm.py` `_swarm` singleton; `server/app.py:154 _task` | one orchestrator, one trading task |
| 3 | KIS broker | `infra/kis_broker.py` `_broker` singleton; `data/kis_token.json` | singleton; shared token cache → race |
| 4 | Equity log | `main_swarm.py` `_EQUITY_LOG=data/equity_curve.json` | all users share file |
| 5 | Trade log | `main_swarm.py` `_RESPONSE_LOG=claude_response.json` | all users share file |
| 6 | News | `tools/news_monitor.py` `data/news_history.json` | shared **by design** (market-wide) |
| 7 | Cycle DB | `infra/cycle_store.py` `cycles.db` | shared |
| 8 | WebSocket | `server/app.py ws_mgr` global broadcast | no per-user routing — leakage |
| 9 | Ops worker | `infra/ops_support_worker.py` | single process; auto-modifies shared code |

**Existing good pattern to extend:** `infra/profile_overrides.py` already uses
`data/profiles/<uid>/` (overrides.json, ops_history.json). The per-user data layout
should follow this (`data/<uid>/…`).

## Decisions locked 2026-05-19

1. **Phasing:** 2-phase split. Phase 1 (login overhaul + CRITICAL auth fixes) ships
   first as the foundation; Phase 2 is this multi-tenant rework.
2. **Concurrency model: DEFERRED.** Decide at Phase 2 design kickoff based on expected
   concurrent-user count and host resources. Candidates: single process + per-user
   asyncio tasks/orchestrators/brokers in dicts (simpler, logical isolation) vs.
   per-user OS process/worker (strong isolation, heavier ops). Recommendation pending
   scale numbers.
3. **`ops_support_worker` (auto code-modify + restart):** **ADMIN-only + global-scope
   only.** Only `hh09080` (ADMIN) may trigger code modification, applied to the shared
   codebase. **Normal users can NEVER modify code.** Feature disabled for non-admin.
4. **Per-user "사장님 (CEO) experience" — core Phase 2 product requirement:**
   Every user is the CEO of *their own* swarm and can issue directives:
   - User issues a command → an agent responds.
   - **No tag:** the most-suitable agent handles it; if unhandleable, the
     운용지원실장 (ops support manager) replies that it cannot be processed.
   - **Tagged but wrong owner:** hand off / escalate to the correct agent.
   - **운용지원실장 for normal users:** **no code modification.** Only strategy
     setting-value changes, or weekly-feedback-level adjustments.
   - **Admin (hh09080):** retains full code-modification capability (current behavior
     preserved).
   - **Memory vs one-shot:** whether a user's (and admin's) Q&A is persisted to their
     profile (remembered) or handled as a one-shot command is **delegated to the
     implementing subagent's judgment** at build time — not pre-decided here.
5. **"사장님 지시사항" management finding:** today it is (a) ~200 scattered dated inline
   comments (`# 사장 지시/피드백 YYYY-MM-DD`) with **no central index**, plus (b) the
   runtime `/api/ceo` → `ceo_directive()` feature. No single source of truth. In
   Phase 2 this becomes the **per-user CEO product feature above**, not a static doc.

## Open questions for Phase 2 design kickoff

- Concurrency model (decision #2) — needs expected concurrent users + host budget.
- Data migration: move existing single user (`hh09080`) global files →
  `data/<uid>/` without losing history (equity/trades/cycles).
- News (#6): keep shared global collection (market-wide, efficient) — confirm.
- KIS token cache (#3): per-user path + file lock to kill the race.
- WebSocket (#8): tag connections with `user_id`; `broadcast(msg, user_id=…)` filter.
- Background workers (#9): per-user identity vs single admin-scoped worker (ties to
  decision #3).
- API rate-limit / cost ceilings per user (N concurrent KIS + DeepSeek accounts).
- Per-user swarm lifecycle: start/stop/resume markers per user (replace
  `data/.active_account`, `data/.resume_on_boot`).

## Non-goals (Phase 2)

Email recovery, CSRF tokens, per-user Fernet key derivation (still tracked in Phase 1 §8).
