# ArQuant Phase 2 — True Multi-Tenant Isolation (Design)

**Date:** 2026-05-26
**Status:** Design approved — ready for implementation plan.
**Supersedes/builds on:** `2026-05-19-arquant-multitenant-phase2-requirements.md` (requirements & decision log).
**Trigger:** Live incident — logging in as `hh0908` (모의/mock, uid=2) hijacked the global
active account away from `hh09080` (admin, uid=1) and cross-contaminated the shared
`equity_curve.json`. Root cause: the trading engine is single-process / single-global-active-account
by design (Phase 2 was deferred); per-request HTTP identity is isolated but the engine is not.

## Goal

Let 5–15 accounts trade **concurrently and independently** from their own KIS/로컬 LLM
credentials, with no cross-contamination of state, orders, equity, logs, or WebSocket events.

## Kickoff decisions (2026-05-26)

| Decision | Choice |
|---|---|
| Scale | 5–15 concurrent accounts (small / beta) |
| Concurrency model | **Single process + per-uid asyncio** (swarm/broker/task in a registry dict) |
| Isolation mechanism | **UserContext registry + explicit injection** (remove `config` globals & singletons) |
| Scope | Core infra isolation **+** per-user CEO experience (one spec) |
| Data migration | **Back up global files, both uids start fresh** (no contamination cleanup) |

## A. Architecture

```
Single process (systemd arquant.service, port 8500)
│
├── Shared layer (uid-agnostic)
│   ├── FastAPI routing + auth middleware (request.state.user_id)
│   ├── WS manager (uid-tagged → send_to_uid routing)
│   ├── News collection (market-wide — stays shared)
│   └── auth_store (accounts / sessions DB)
│
└── UserRegistry: dict[uid] -> UserContext
        UserContext(uid):
          .creds    decrypted credentials from auth_store (held in memory)
          .broker   KISBroker(creds)            ← injected, not read from config globals
          .swarm    ArquantOrchestrator(ctx)    ← owns uid, paths, broker
          .task     asyncio.Task | None          ← this uid's trading loop
          .paths    data/<uid>/ (equity, trade, token, cycle, cost)
```

**Key change:** retire `credentials.set_active()` (global rewrite) and
`account_switch_policy` (stop-loop-then-switch). Login no longer seizes a global active
account; it simply ensures the uid's context exists in the registry. This directly fixes
the "logging in as hh0908 tangles hh09080" incident.

## B. The 9 global layers → per-uid

| # | Layer | Current global | Phase 2 isolation |
|---|---|---|---|
| 1 | Credentials | `config` globals + `_active` + `.active_account` | `UserContext.creds` injected; globals retired |
| 2 | Swarm/loop | `_swarm` singleton + single `_task` | `ctx.swarm` + `ctx.task` per uid |
| 3 | KIS broker | `_broker` singleton + shared `kis_token.json` | `KISBroker(creds)`; token at `data/<uid>/kis_token.json` + file lock |
| 4 | Equity log | global `data/equity_curve.json` | `data/<uid>/equity_curve.json` |
| 5 | Trade log | global `claude_response.json` | `data/<uid>/trade_log.json` |
| 6 | News | shared | **stays shared** (market-wide, efficient) |
| 7 | Cycle DB | shared `cycles.db` | add `uid` column + filter on read |
| 8 | WebSocket | global broadcast | swarm sends via `send_to_uid(ctx.uid, …)` |
| 9 | Ops worker | single process | spawn per uid; applies to `data/profiles/<uid>/` (already separated) |

**Extra global caught outside the table:** cost accounting —
`base_agent._API_CALL_LOG` + `api_cost_rollup.json` are global. Tag by uid and split to
`data/<uid>/` so per-account cost display is correct.

## C. Data layout & migration

```
data/
├── _migration_backup_2026-05-26/      ← whole copy of current global files
│   ├── equity_curve.json  strategy_state.json  strategy_history.json  cycles.db  …
├── 1/   (hh09080)  equity_curve.json  trade_log.json  kis_token.json  strategy_state.json  cost_rollup.json
├── 2/   (hh0908)   …(starts empty)
└── profiles/<uid>/  (existing overrides.json / ops_history.json — unchanged)
```

Migration = **back up, then start fresh** (owner decision). Runs once at boot, idempotent:
if a global file exists, move it into `_migration_backup_2026-05-26/`; each uid starts empty.
No contamination-cleanup logic needed.

## D. Per-user lifecycle

- `/api/start|stop` act on **`request.state.user_id`'s task only** — other uids unaffected.
- Retire `.active_account` and the single resume marker → per-uid `data/<uid>/.running` marker.
  On boot, resume the loop for **every uid** whose marker is present.
- A uid's loop exception is isolated (per-task supervisor) — it must **not** kill other uids
  or the shared web/WS layer. A per-uid watchdog detects a dead task and notifies that uid.

## E. Per-user "CEO experience" (directive routing)

Each user is the CEO of their own swarm. `/api/ceo` (current `ceo_directive`) routes through
the uid's context:

```
user message → ctx.swarm.ceo_directive(msg)
  ├─ no tag      → most-suitable agent handles it;
  │                if unhandleable, 운용지원실장 replies "cannot process"
  ├─ correct tag → that agent handles it
  └─ wrong tag   → hand off to the correct agent
```

Permission split (requirements #3/#4; partly implemented):
- **Non-admin:** 운용지원실장 limited to strategy setting-values / weekly-feedback-level
  tuning. **Never modifies code** (source/server untouchable). Applied to `data/profiles/<uid>/`.
- **Admin (hh09080):** retains code-modification capability. Code changes are **global-scope**
  by nature (single process, single shared source) and apply to all uids on restart — this is
  intentional and admin-only (requirements #3). The admin's *trading/directive* actions, by
  contrast, operate within the admin's own uid context like any other user.

**Memory vs one-shot:** default is **one-shot**. "Standing directives" (e.g. "from now on,
sell everything on a macro collapse") are explicitly persisted to
`data/profiles/<uid>/standing_directives` and injected into the orchestrator prompt. To avoid
the known "standing-directive resurrection" bug, deletion by the user is permanent (no
sentinel-reseed on profile reset).

## F. WebSocket per-uid routing

- Connections are already uid-tagged (`ws_mgr.connect(uid=…)`).
- The swarm's `_broadcast` global callback → the context knows its own uid and calls
  `send_to_uid(ctx.uid, msg)`. One user's cycle logs / alerts never leak to another.
- Admin-only events (member feedback, etc.) keep using `send_to_admins`.

## G. Safety & error isolation

- **Drop account-switch policy:** login no longer seizes globals, so `account_switch_policy`
  is unnecessary → removed.
- **Per-uid exception isolation:** wrap each uid task in a supervisor so an unhandled
  exception can't kill the event loop, other uids, or the web layer; notify only that uid.
- **KIS token race removed:** per-uid token file + file lock.
- **Live-order safety unchanged:** keep the fail-closed-avoidance / multi-fallback send
  principle (CLAUDE.md). Isolation changes routing only — it must not touch order-path reliability.
- **default-deny preserved:** if uid/admin can't be determined, treat as non-admin.

## H. Testing

- Unit: `UserContext`/`UserRegistry` — two uids' broker/creds/paths never mix.
- **Isolation regression (reproduces this incident):** with uid=1's loop running, log in as
  uid=2 → assert uid=1's active/equity/broker are unchanged.
- Per-uid equity/trade files recorded separately.
- WS routing: a uid=2 event never reaches a uid=1 connection.
- Keep the existing 196+ tests green (`python3.11 -m pytest`).

## I. Build order (one spec, staged execution)

1. Introduce `UserContext`/`UserRegistry`; make `KISBroker(creds)` injected (drop config globals).
2. `BaseAgent` key injection + per-uid cost-accounting tag.
3. `ArquantOrchestrator(ctx)` — owns uid/paths/broker; equity & trade logs per uid.
4. Data migration (back up, fresh start) + `cycle_store` uid column.
5. `server/app.py` — `_task`→per-uid; route start/stop/ceo/status by `request.state.user_id`;
   remove `set_active`/`account_switch_policy`.
6. WS per-uid send + per-uid lifecycle/resume markers.
7. CEO experience (directive routing, permission split, standing directives).
8. Isolation regression tests + full green + deploy (single restart).

## Non-goals (Phase 2)

Email recovery, CSRF tokens, per-user Fernet key derivation (tracked in Phase 1 §8),
per-user API rate-limit/cost ceilings (each user uses their own KIS/로컬 LLM keys, so cost
is naturally isolated — YAGNI for beta scale).
