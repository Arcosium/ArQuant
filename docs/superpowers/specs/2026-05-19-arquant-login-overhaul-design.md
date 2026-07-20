# ArQuant Login Overhaul — Design Spec

**Date:** 2026-05-19
**Status:** Approved (brainstorming) — pending implementation plan
**Clients affected:** Backend (`server/app.py`, `infra/auth_store.py`), Web (`server/static/index.html`), Android (`arquant_mobile/`)

## Context

ArQuant has app-native auth (added 2026-05-16, replacing Cloudflare Access). Credentials
live Fernet-encrypted in a gitignored SQLite DB (`data/arquant_auth.db`); the Fernet key is
`data/.fernet.key` / `ARQUANT_FERNET_KEY` (also gitignored). The Android dashboard is a
**WebView of `index.html`** (`WebDashboardScreen.kt`); only its login/registration screen
is native Compose (`LoginScreen.kt`). The web login overlay (`index.html:228 #loginOv`)
serves both login and "최초 등록" via a `.reg` class toggling `.regonly` fields.

Key code references (verified):
- Login: `server/app.py:202-210` → `auth_store.verify_password` (`infra/auth_store.py:291-298`, decrypt-then-plaintext-compare)
- Register: `server/app.py:174-200`; `RegisterReq` `app.py:136-146`; `upsert_user` `auth_store.py:194-239`
- Public paths: `app.py:30 _PUBLIC_PATHS`
- Admin: `auth_store.py:50 ADMIN_USERNAMES = frozenset({"hh09080"})`
- DART env already read in `auth_store.py:385 bootstrap_from_env` (`OPENDART_API_KEY`)
- Web overlay logo `index.html:231`; dashboard logo `index.html:272`; tabs `:234-237`;
  fields `f_or:243 f_appkey:244 f_appsecret:245 f_acct:246 f_envmode:248 f_dart:252 f_label:253`;
  submit `#authSubmit:256`; badge `#badge:280`; comm-log header `.ct:313`
- Android: `ui/screens/LoginScreen.kt` (login+register, 182 lines), `network/ArQuantApi.kt`
  (`RegisterRequest:213`, `@POST api/register:268`), `data/ArQuantRepository.kt:27 register()`,
  `viewmodel/AuthViewModel.kt`, `MainActivity.kt:74 AuthPhase`

## Goals (the 7 requested items + folded CRITICAL security fixes)

### §1 Logo unification
Make the login/registration overlay logo identical to the post-login dashboard logo.
Single web fix (shared overlay): replace bare gradient box `index.html:231` with the
dashboard `.logo` markup (same inline SVG, `border-radius`, `box-shadow`, size at `:272`).
Android: replicate the same icon treatment in `LoginScreen.kt`.

### §2 Account recovery (new public surface)
Two new endpoints, mirrored in web overlay + Android `LoginScreen.kt`:

- `POST /api/recover_id` — body `{kis_app_key, kis_app_secret, llm_key}`.
  On exact match of **all three** → returns `{username}`. No match → generic 404.
- `POST /api/recover_password` — body `{username, kis_app_key, kis_app_secret,
  llm_key, new_password}`. On exact match of all four → set new password
  (policy-enforced, written via §6 hash path). One-step (no separate verify call).

Both added to `_PUBLIC_PATHS`. Pydantic models `RecoverIdReq`, `RecoverPwReq`.

**Matching = blind index (decided).** Fernet is non-deterministic, so add deterministic
HMAC columns:
- New `users` columns: `kis_app_key_bidx`, `kis_app_secret_bidx`, `llm_key_bidx` (TEXT).
- `bidx(value) = HMAC_SHA256(K, value.strip()).hexdigest()` where `K` is **HKDF-SHA256
  derived from the existing Fernet key** (`info="arquant-bidx-v1"`). No new secret to
  back up; documented coupling to the Fernet key.
- Normalization MUST equal registration normalization: `.strip()` (matches
  `app.py:194-196` which `.strip()`s before storage).
- Lookup: `SELECT username FROM users WHERE kis_app_key_bidx=? AND kis_app_secret_bidx=?
  AND llm_key_bidx=?` (+ `username=?` for password reset). No bulk decrypt.
- `upsert_user` computes and stores all three bidx on create/update.
- Schema migration via `ALTER TABLE ADD COLUMN` following the existing `is_admin`
  migration pattern (`auth_store.py:172-181`); backfill in the one-shot startup
  migration (§6).

**Hardening:** all factors required; shared strict rate-limit (§7); audit-log every
attempt (IP, username-or-none, outcome — **never** the factor/key values); generic
failure text (no "which field was wrong").

**Recovery oracle closure (I1 — final-review fix):** `reset_password_by_factors`
validates password policy **before** the factor match. A weak-password probe therefore
returns 400 (policy) regardless of factor correctness — the attacker only learns that
their own chosen password is weak, which reveals nothing about the account. Factor
correctness is observable only via an actual policy-valid reset (200), which is a
destructive + audited operation — the already-accepted "factors ⇒ takeover" trade-off,
not a stealth non-destructive oracle. (The earlier "policy-after-factors" rationale
— "don't leak policy compliance to unauthenticated callers" — was mis-grounded:
password policy is public and the submitted password is attacker-chosen, so
"your password is weak" carries zero account/factor information.)

**Accepted trade-off (confirmed by owner):** possession of a user's KIS App
Key+Secret + 로컬 LLM key ⇒ account takeover. Rate-limit + audit are the compensating
controls. Documented, not mitigated further in this pass.

### §3 Mobile badge placement — web only
`#badge` (`:280`) is top-right header. CSS cannot move DOM nodes, so add a **mobile-only
mirror** badge inside the comm-log card header (`.ch`, beside `💬 에이전트 통신 로그`
at `:313`). The existing badge-update JS (locate `setBadge`/equivalent during planning)
writes class+text to **both**. CSS `@media` shows the header badge on desktop, the mirror
on mobile. Android inherits via WebView — **no native work**.

### §4 Mobile register button clipped — web + Android
Web: overlay inner (`:229`, `max-height:92vh;overflow-y:auto`) lets `#authSubmit` sit
under the device home/gesture bar in registration mode. Fix: inner container
`padding-bottom: max(24px, calc(env(safe-area-inset-bottom) + 28px))` + scroll bottom
padding. Android `LoginScreen.kt`: add `navigationBarsPadding()` / `imePadding()` and
ensure the form column scrolls so the submit button clears the gesture bar.

### §5 Remove 계정 이름 + DART field; global DART via env
- Remove `#f_label` (`:253`) and `#f_dart` (`:252`) from web form + the `authForm`
  submit JS payload (~`:510-539`). Same in Android `LoginScreen.kt`,
  `RegisterRequest` (`ArQuantApi.kt:213`), repository call.
- Backend `RegisterReq` keeps `dart_key`/`label` optional and **ignores** them
  (`label` already defaults to `username` in `upsert_user:205`). DB columns retained
  (back-compat); no destructive migration.
- DART crawling uses one **server-owned** key from `OPENDART_API_KEY` env (gitignored
  `.env`; already referenced at `auth_store.py:385`). Add a single resolver
  (e.g., `infra` helper) returning the env key for all users. Route the real DART
  consumer through it — `agents/ops.py` (exact call site to be pinned in the plan via
  grep for `dart`/`공시`/`OPENDART`). Per-user `dart_key` becomes dead data.
- **No DART key in tracked code.** Hardcoding is explicitly forbidden; CI/grep guard
  suggested in plan.

### §6 Password hashing (CRITICAL — folded in)
Replace decrypt-then-plaintext-compare (`auth_store.py:296`) with **argon2id**
(`argon2-cffi`, add to `requirements.txt`).
- New column `password_hash TEXT`. `password_enc` retained but blanked post-migration.
- **One-shot startup migration (decided):** at boot, for each user with empty
  `password_hash` + non-empty `password_enc`: decrypt → argon2id hash → store →
  blank `password_enc`. Same pass backfills §2 blind-index columns. Idempotent;
  safe to re-run. Tiny user base ⇒ instant.
- `verify_password`: argon2 verify against `password_hash`; defensive legacy fallback
  only if `password_hash` empty (then rehash). New register + password reset write
  `password_hash` directly; never store plaintext-encrypted passwords again.
- `password_policy_error` unchanged (≥10 chars, ≥1 special).

### §7 Rate limiting + audit (CRITICAL — folded in)
No throttling exists. Add an in-process limiter (no new dependency): sliding-window
keyed by (client-IP, scope) **and** (username, scope), applied to `/api/login`,
`/api/register`, `/api/recover_id`, `/api/recover_password`. Failure backoff/lockout.
Thresholds env-configurable (sensible defaults, e.g. 5 / 15 min). Auth audit log
(login/register/recover, outcome, IP, username-or-none) to logger + gitignored
`data/auth_audit.log`. **Caveat:** in-process limiter is per-worker — note in spec;
acceptable for single-process uvicorn (verify deployment in `deploy/`/`start_server.sh`
during planning).

## §8 Deployment / multi-user audit (report-only — owner re-inspection list)

Prioritized; only 🔴 items are fixed in this overhaul (§6/§7).

- **🔴 NOT truly multi-tenant (top concern).** `creds_layer` holds **one global
  active account**; the swarm trades for whoever is active and refuses switching
  while the loop runs (`app.py:99-115`). N registered users ≠ concurrent isolated
  trading. → **DECISION 2026-05-19: split into Phase 2.** Phase 1 (this spec) ships
  as-is; true per-user isolation (9 global-state layers) gets its own spec/plan/impl
  cycle. Requirements & decisions captured in
  `2026-05-19-arquant-multitenant-phase2-requirements.md`. Phase 1 is the auth
  foundation Phase 2 builds on.
- 🔴 Plaintext password compare / no hashing → fixed (§6).
- 🔴 No rate-limit / lockout anywhere → fixed (§7).
- 🟠 Single shared Fernet key for all users' secrets (known/accepted per `.gitignore`
  note; ensure off-host key backup). Not changed here.
- 🟠 `/api/check_username` (public, `app.py:166-172`) + recovery ⇒ username
  enumeration + targeted takeover. Mitigation: recovery rate-limit (§7); consider
  authenticating/throttling check_username later.
- 🟠 Hardcoded admin `hh09080` (`auth_store.py:50`); `upsert_user` is a true
  upsert/overwrite keyed by username — safe only because `/api/register`
  pre-checks existence (`app.py:183`). Latent footgun if called elsewhere.
- 🟡 CSRF: cookie POSTs rely on `SameSite=Lax` only (no token). Session 7d, no
  server-side revocation beyond explicit delete.
- 🟡 **Environment (not product):** CrowdStrike-Foundry prompt-injection hook fired
  on the dev box during this session — identify the emitting plugin/hook before
  trusting this machine for deploy.

## Non-goals (YAGNI / explicitly out of scope)

True multi-tenant trading isolation (→ **Phase 2**, see
`2026-05-19-arquant-multitenant-phase2-requirements.md`); email-based recovery;
CSRF tokens; per-user Fernet key derivation; authenticating `/api/check_username`.
Reported in §8 for separate scheduling, not built in Phase 1.

## Testing strategy

- **Backend (pytest, follow `tests/` + `pytest.ini`):** blind-index match
  positive/negative/normalization (whitespace); one-shot migration idempotency &
  `password_enc` blanked; argon2 verify (correct/incorrect); `upsert_user` writes
  `password_hash`+bidx and no plaintext; recovery endpoints success/fail/throttle;
  rate-limiter trips and resets; `_PUBLIC_PATHS` includes new endpoints; DART
  resolver returns env key and ignores per-user value.
- **Web/Android (manual checklist):** logo parity login↔dashboard (web + Compose);
  mobile badge appears beside comm-log header & stays state-synced; register button
  clears gesture bar (web + Android); 계정 이름/DART fields gone; label reads
  "한국투자증권 계좌번호"; full find-ID and reset-PW flows on web and Android.

## Cross-client scope matrix

| Item | Backend | Web index.html | Android native |
|---|---|---|---|
| §1 logo | — | ✓ | ✓ LoginScreen.kt |
| §2 recovery | ✓ endpoints + bidx + migration | ✓ overlay sub-forms | ✓ Login/Api/Repo/VM |
| §3 badge | — | ✓ mirror+@media+JS sync | inherited (WebView) |
| §4 button | — | ✓ safe-area padding | ✓ Compose padding |
| §5/§6label | ✓ ignore fields + DART resolver | ✓ remove fields, relabel | ✓ remove fields, relabel |
| §6 hashing | ✓ argon2 + migration | — | — |
| §7 limits | ✓ limiter + audit | — | — |
