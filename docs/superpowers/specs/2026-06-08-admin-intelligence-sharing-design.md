# ADMIN 단일 인텔리전스 생산자 · 정시 동기화 케이던스 · API 비용 절감 설계

**작성일:** 2026-06-08
**상태:** 승인됨(사장 확정 2026-06-08) — 구현 계획 대기

## 배경 / 동기

사장 지시: API 비용 절감. 두 갈래가 한 축("hh09080=단일 지능 생산자 + 벽시계 정시 동기화")으로 묶인다.

1. **뉴스 크롤링은 이미 전역 1회 공유** — `tools/news_monitor.py`의 `get_monitor()`가 프로세스 전역 싱글턴. 모든 uid가 `data/news_history.json`을 공유하고 `/api/news`로 동일 제공. 출처가 네이버 금융 **무료 HTML 크롤**이라 크롤 자체엔 API 비용 0원.
2. **실제 비용은 LLM 호출의 per-uid 중복** — uid마다 별도 `ArquantOrchestrator`가 **시장 전역에 동일한** 인텔리전스(`macro_researcher`·`macro_analyst`·`news_analyst`)를 계정 수만큼 중복 호출. 활성 계정 N개면 N배.
3. **사이클이 시작 시각에 고정돼 정시와 어긋남** — 현재 `start_continuous`는 진입 즉시(`first_run`) 1회 돌고, 이후 `(time.time() - _last_cycle_at) >= PERIODIC_CYCLE_SEC(3600)` 으로 트리거. 08:37에 시작/재시작하면 08:37→09:37→… 로 **벽시계 정시와 어긋나고 재시작마다 위상이 바뀐다.** 부팅 자동재개(`server/app.py`의 `.running` 마커)도 진입 즉시 1회 돈다. → ADMIN의 잦은 서버 재시작이 클라이언트 사이클 위상을 흔들고, 위상이 어긋나면 생산자/소비자 동기화가 깨진다.
4. **ADMIN은 이미 hh09080 단독** — `infra/auth_store.py`의 `ADMIN_USERNAMES = frozenset({"hh09080"})`. 추가로 "타 계정 승격 잠금"과 "비관리자 뉴스 활동 표시 게이팅"이 필요.

참조 레포(`clawhub.ai/tgparkk/kis-trading`)는 기본 KIS 샘플로 ArQuant가 상회 — 본 설계 범위 외.

## 목표

1. 시장 전역 LLM 인텔리전스를 hh09080이 1회 산출·공유 → 활성 계정 수에 비례하던 중복 비용 제거. 단일 출처 장애 시 자체 계산 폴백(graceful degradation).
2. 매매 사이클을 **벽시계 정시(:00)에 고정** → 누가 언제 (재)시작하든 사이클은 9:00·10:00·11:00…에 떨어지고 전 계정이 동일 사이클에 정렬.
3. **같은 사이클 내 순서 보장(producer-first)**: ADMIN이 그 :00 사이클에서 매크로/뉴스 분석을 실시간으로 끝내면, 소비자는 그 신선한 결과를 받아(대기-후-수신) 개별 분석을 이어서 실행. 1시간 지연 없음.
4. 비관리자 화면에서 뉴스 *활동/로그*를 가리고, ADMIN을 hh09080로 영구·단독 고정.

## 비목표 (Non-Goals)

- **매수/매도 판단 공유 금지** — `orchestrator`·`post_manager`·`quant_analyst`·`trader`/`fund_planner`는 보유·예수금 의존 → 계정별 유지.
- **헤드라인 자체 숨김 금지** — 비관리자도 `/api/news` 헤드라인은 봄. 가리는 건 크롤·분석 *활동/로그*뿐.
- **케이던스 값을 바꾸지 않음** — 시간당 1회(`PERIODIC_CYCLE_SEC=3600`) 유지. 바꾸는 건 *앵커*(시작시각→벽시계 :00)뿐. 세션 개시(market_open) 트리거도 유지.
- **시작/정지 버튼 유지** — 가입 기간 중 "거래 on/off" enable 스위치. 제거하지 않음.
- **멀티유저 과금 정산 미구현** — 현재 전 계정 운영자 소유라 ADMIN LLM 크레딧으로 지불이 적절(YAGNI).
- **모바일 앱 재빌드 불필요** — 표시 게이팅은 서버측 처리.

## 사장 결정 사항 (확정)

| 항목 | 결정 |
|------|------|
| 공유 범위 | **매크로+뉴스** — `macro_researcher` + `macro_analyst` + `news_analyst` 결과 공유 |
| 동기화 방식 | **같은 사이클 producer-first** — 소비자는 ADMIN이 그 시각(hour) 결과를 게시할 때까지 대기 후 수신(stale 재사용 아님). 미게시 시 타임아웃→자체계산 폴백 |
| 비관리자 뉴스 표시 | **헤드라인만 표시** — 헤드라인 목록은 보이되 크롤·분석 활동/로그는 admin만 |
| ADMIN 고정 | hh09080 **영구·단독**(타 계정 승격 거부, hh09080 강등 거부, 부팅 스윕) |
| 정시 정렬 범위 | **모든 시작에 적용** — 수동 '시작'·부팅 자동재개 모두 다음 :00에 첫 사이클(즉시 시작 안 함). 세션 개시 트리거는 유지 |

## 아키텍처

### 생산자/소비자 동기화 모델

```
   t=:00  전 계정 동시 기상(1시간 케이던스 정렬)
   ┌──────────────────────────────────────────────────────────────┐
   │ hh09080(생산자)  │ macro_research→publish │ macro_analyst→publish │ news→publish │ …개별분석(주문)
   │                  │        │                      │                │
   │ 비관리자(소비자) │  wait──┘ 수신·재사용     wait──┘ 수신       wait─┘ 수신   →…개별분석(주문)
   └──────────────────────────────────────────────────────────────┘
   * 생산자는 아무도 안 기다리니 항상 선행. 소비자는 각 공유 단계에서 그 시각(hour) 결과 게시까지만 대기.
   * 미게시(생산자 부재/지연) → SHARE_PRODUCER_WAIT_SEC 타임아웃 → 자체계산 폴백.
```

**핵심 불변식:**
- **ADMIN만 게시한다.** 소비자 폴백 결과는 스토어에 쓰지 않는다 → 단일 출처 유지, "ADMIN 크레딧만 지불".
- **성공 결과만 게시한다.** 에러/빈 결과는 게시 금지 → 오염 방지.
- **그 시각(hour) 결과만 유효.** 소비자는 *현재 hour_key* 와 일치하는 게시물만 수신. 직전 시각 결과(stale)는 쓰지 않는다.
- **대기는 항상 폴백으로 끝난다.** 타임아웃 시 자체계산 → 단일 출처 장애 모드 없음.

### 컴포넌트

| 파일 | 책임 | 신규/수정 |
|------|------|-----------|
| `infra/market_intel.py` | `MarketIntelligenceStore`(publish/peek/wait_for) + `get_intel_store()` 싱글턴 | **신규** |
| `main_swarm.py` | `_shared_or_compute` 헬퍼 + 3개 호출지점 배선 + `self.is_admin` + `_producer_absent_this_cycle` 플래그 | 수정 |
| `main_swarm.py` | `start_continuous` 정시 케이던스(앵커=벽시계 :00, `first_run` 제거) + `_current_hour_key` 헬퍼 | 수정 |
| `config.py` | 공유 토글·생산자 대기 타임아웃 + 튜너블 메타 | 수정 |
| `server/app.py` (+ WS 송출부) | 비관리자 연결에 뉴스 활동/분석 메시지 게이팅 | 수정 |
| `infra/auth_store.py` | `set_admin` 잠금 + 부팅 스윕 | 수정 |

## 상세 설계

### 1. `MarketIntelligenceStore` (infra/market_intel.py)

`news_monitor`와 동형의 프로세스 전역 싱글턴. asyncio `Condition`으로 대기-알림.

```python
INTEL_KINDS = ("macro_research", "macro_analyst", "news_report")

class MarketIntelligenceStore:
    def __init__(self):
        self._d = {}            # kind -> {hour_key, result, fingerprint, produced_at, uid}
        self._cond = None       # asyncio.Condition (러닝 루프 내 lazy 생성)

    def _ensure_cond(self):
        if self._cond is None:
            import asyncio
            self._cond = asyncio.Condition()
        return self._cond

    def peek(self, kind, hour_key, fingerprint):
        e = self._d.get(kind)
        if not e or e["hour_key"] != hour_key:
            return None                          # 미게시 또는 직전 시각(stale) → 무효
        if fingerprint is not None and e["fingerprint"] != fingerprint:
            return None
        return e["result"]

    async def publish(self, kind, hour_key, result, fingerprint, *, uid, now):
        cond = self._ensure_cond()
        async with cond:
            self._d[kind] = {"hour_key": hour_key, "result": result,
                             "fingerprint": fingerprint, "produced_at": now, "uid": uid}
            cond.notify_all()

    async def wait_for(self, kind, hour_key, fingerprint, *, timeout):
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
```

- `hour_key`: `main_swarm._current_hour_key()` 가 만든 KST 시(hour) 내림 datetime의 직렬화값(예: `"2026-06-08 10"`). 생산자·소비자가 같은 :00 창에선 동일.
- `now`/`timeout` 주입 → 테스트 결정성. `timeout`은 테스트에서 작은 값 전달.
- asyncio 단일스레드라 `peek`은 순수 읽기(락 불필요), `publish`/`wait_for`만 Condition 사용.

### 2. `_shared_or_compute` 헬퍼 (main_swarm.py)

```python
async def _shared_or_compute(self, kind, fingerprint, compute):
    if not config.SHARE_MARKET_INTELLIGENCE:
        return await compute()
    store = get_intel_store()
    hk = _current_hour_key_str()
    if self.is_admin:                                   # hh09080 = 생산자
        r = await compute()
        if r:                                           # 성공/비어있지 않음만 게시
            await store.publish(kind, hk, r, fingerprint, uid=self.uid, now=time.time())
        return r
    # 소비자
    if self._producer_absent_this_cycle:                # 이번 사이클 이미 부재 확정
        return await compute()
    hit = store.peek(kind, hk, fingerprint)
    if hit is None:
        hit = await store.wait_for(kind, hk, fingerprint,
                                   timeout=config.SHARE_PRODUCER_WAIT_SEC)
    if hit is not None:
        return hit
    self._producer_absent_this_cycle = True             # 첫 타임아웃→이후 공유단계 즉시 폴백
    return await compute()
```

- `self.is_admin`: `UserContext.is_admin`(이미 존재) → `ArquantOrchestrator.__init__`에서 `self.is_admin = bool(getattr(ctx, "is_admin", False))`.
- `self._producer_absent_this_cycle`: 매 사이클 시작 시 `False`로 리셋(3중 대기 방지).
- `compute`: 기존 분석 호출을 감싼 zero-arg async 클로저.

**배선 지점(3곳):**

3개 기존 `await` 호출 지점을 개별 래핑(최소 침습). 모두 `fingerprint=None`(hour_key만) — 전역 뉴스풀이라 같은 :00 창에선 입력이 동일.

| kind | 호출지점(현 코드) | fingerprint |
|------|----------|-------------|
| `news_report` | `self.news_analyst.think(...)` (main_swarm.py:3316, `not _sell_only` 분기) | `None` (hour_key만) |
| `macro_research` | `self._research_macro_themes(...)` (main_swarm.py:3352) | `None` |
| `macro_report` | `self.macro_analyst.think(...)` (main_swarm.py:3384) | `None` |

### 3. 정시 동기화 케이던스 (main_swarm.py `start_continuous`)

**앵커 전환**: "프로세스 시작 시각" → "벽시계 시(hour)".

- 헬퍼 `_current_hour_key()` → `_now_kst().replace(minute=0, second=0, microsecond=0)`; `_current_hour_key_str()` → 그 직렬화(예: `"2026-06-08 10"`). 날짜 포함이라 자정·익일 경계 무모호.
- **진입 시(수동·부팅 공통):** `first_run` 즉시 발화 **제거**. `self._last_cycle_hour_key = _current_hour_key()` 초기화 → 같은 시(hour) 안에선 발화 안 함 → **다음 :00 대기**. 진입 직후 상태 로그: `다음 사이클 HH:00 예정`.
- **주기 트리거 교체:** `periodic_due = (time.time()-_last_cycle_at)>=PERIODIC_CYCLE_SEC` → `periodic_due = (_current_hour_key() != self._last_cycle_hour_key)`.
- **세션 개시(market_open) 트리거 유지:** 세션 전환은 벽시계 전역 결정(KR 09:00, US 22:30, 애프터 15:50)이라 전 계정 동시 관측 → 동기화 유지.
- **발화 후 갱신:** market_open·periodic 무엇이든 사이클 실행 후 `self._last_cycle_hour_key = _current_hour_key()`. 사이클 시작 시 `self._producer_absent_this_cycle = False` 리셋.
- **게이트 보존:** 발화는 `is_trading_hours()` 통과 시에만(OFF_HOURS·휴장 무발화). 대기 중 뉴스 크롤은 지속.

**restart-invariance:** 08:37 시작/재시작 → `_last_cycle_hour_key=08:00` → 09:00에 시(hour) 변경 → 첫 사이클 09:00 → 10:00 → 11:00…. 분(minute) 무관, 항상 :00.

### 4. 표시 게이팅 (server/app.py + WS 송출부)

- `/api/news`(헤드라인 목록): **전 계정 유지**. 변경 없음 → 비관리자도 헤드라인은 봄.
- **소스측 게이팅(main_swarm.py)**: `_emit`은 이미 per-uid 라우팅이고 오케스트레이터는 `self.is_admin`을 안다. 따라서 **뉴스 크롤 활동 emit**(`{"type":"news",...}`, main_swarm.py:3103)과 **`news_analyst` 분석 메시지**(`agent_msg`, main_swarm.py:3313·3327)를 `if self.is_admin:` 가드로 감싸 비관리자에겐 emit 자체를 생략. (별도 WS 필터/태그 불필요 — 더 단순.)
- 비관리자는 소비자라 분석 메시지를 거의 생성 안 함. 폴백 시 생성돼도 가드로 비노출.
- 모바일 앱은 동일 WS 소비 → 소스측 게이팅, 앱 재빌드 불필요.

### 5. ADMIN 잠금 (infra/auth_store.py)

- `ADMIN_USERNAMES = frozenset({"hh09080"})` 유지.
- `set_admin(uid, value)`:
  - `value=True`인데 username ≠ `"hh09080"` → **거부**: `return False`+경고 로그(예외 금지 — 관리 UI가 500 안 받게). DB 미변경.
  - `value=False`인데 username == `"hh09080"` → **거부**: `return False`+경고 로그. DB 미변경.
  - 정당한 경우(hh09080 승격 / 비-hh09080 강등)만 DB 반영 후 `return True`.
- 부팅 스윕(`init()`/시드 직후): hh09080.is_admin=1 보장 + 그 외 `is_admin=1` 행 0으로 강등.

## 신규 설정 키

`config.py`에 추가하고 `STRATEGY_TUNABLE_KEYS`/`STRATEGY_KEY_META`/`STRATEGY_KEY_EFFECT`에 등재(노브 완전성 불변식 유지):

| 키 | 기본값 | 의미 |
|----|--------|------|
| `SHARE_MARKET_INTELLIGENCE` | `True` | 마스터 토글. False면 전 계정 자체 계산(현행) |
| `SHARE_PRODUCER_WAIT_SEC` | `120` | 소비자가 ADMIN 게시를 기다리는 단계별 최대 초. 초과 시 자체계산 폴백(생산자 부재 감지) |

`PERIODIC_CYCLE_SEC`(3600)·`NEWS_CHECK_INTERVAL`(900)은 **변경 없음** — 앵커만 바뀐다.

## 엣지 케이스

- **hh09080 미가동:** 게시 없음 → 소비자 첫 공유단계에서 `SHARE_PRODUCER_WAIT_SEC` 대기 후 `_producer_absent_this_cycle=True` → 그 사이클 나머지 공유단계 즉시 자체계산.
- **생산자 사이클 도중 크래시(예: macro 게시 후 news 전 사망):** 소비자는 news 대기 타임아웃 → 그 종류만 폴백.
- **콜드 스타트:** 첫 :00에 생산자가 게시하고 소비자가 대기·수신 → 같은 사이클 정상 동작(지연 없음).
- **빈/에러 결과:** 게시 금지 → 소비자 미수신 → 대기/폴백.
- **뉴스풀 미세 차이:** hour_key만으로 공유하므로 소비자는 ADMIN의 같은-시각 시장뉴스 요약을 사용(전역 풀이라 동일). per-uid 풀이 약간 달라도 시장 전반 요약엔 무해.
- **진입 시각이 정확히 :00:xx:** 같은 시(hour)로 초기화 → 다음 :00까지 대기(즉시 발화 안 함, 사장 지시와 일치).
- **세션 스큐:** 세션은 시각 기반 전역 → 동시 가동 오케스트레이터 동일 세션. 일시정지 소비자는 사이클 미실행이라 무관.
- **이중 admin:** 잠금 후 불가능(부팅 스윕 + set_admin 거부).
- **equity 폴러(5분):** 사이클과 독립 타이머 → 케이던스 변경 무영향.

## 테스트 (python3.11, TDD)

1. **`MarketIntelligenceStore`** (`tests/test_market_intel_store.py`)
   - `publish`→`peek` 같은 hour_key hit; 다른(직전) hour_key는 `None`; fingerprint 불일치 `None`; fingerprint=None은 hour_key만으로 판정.
   - `wait_for`: 미게시 상태에서 별도 태스크가 게시하면 수신; 게시 없으면 작은 `timeout` 후 `None`.
2. **`_shared_or_compute`** (`tests/test_shared_or_compute.py`)
   - admin: compute 호출 + 게시; admin 빈 결과면 미게시.
   - 소비자(게시 존재): compute **미호출**, 게시값 수신.
   - 소비자(미게시·타임아웃): compute 호출(폴백), 미게시, `_producer_absent_this_cycle=True`.
   - `_producer_absent_this_cycle=True`면 대기 없이 즉시 compute.
   - `SHARE_MARKET_INTELLIGENCE=False`: is_admin 무관 항상 compute.
3. **정시 케이던스** (`tests/test_hourly_aligned_cadence.py`)
   - `_current_hour_key`가 분/초 0으로 내림.
   - 진입 직후 같은 시(hour) 내 periodic 미발화.
   - `_now_kst` 모킹 시(hour) 롤오버 시 periodic 발화 + `_last_cycle_hour_key` 갱신.
   - market_open은 시(hour) 무관 세션 전환 시 발화.
   - 08:37·09:59 임의 분 시작 → 첫 periodic이 다음 :00 발화(restart-invariant).
4. **ADMIN 잠금** (`tests/test_admin_lockdown.py`)
   - 비-hh09080 `set_admin(True)` 거부; hh09080 `set_admin(False)` 거부; 부팅 스윕이 stray admin 강등 + hh09080 승격.
5. **표시 게이팅** (`tests/test_news_activity_gating.py`)
   - 비관리자 연결 송출에서 `news_activity` 제외; admin엔 포함; `/api/news` 헤드라인 양쪽 유지.

## 관련 메모

- `[[arquant-data-dir-and-news-pipeline]]` — 뉴스 싱글턴·pending 풀 재시작 리셋
- `[[arquant-model-override-system]]` — admin_config model_overrides(전역) vs runtime per-uid
- `[[arquant-multitenant-phase2-kickoff]]` — per-uid UserContext·asyncio task 구조
- `[[arquant-notif-and-intraday-benchmark]]` — per-connection WS 필터(게이팅 재사용)·equity 5분 폴링·is_market_session_now 게이트
- `[[arquant-nxt-extended-hours]]` — is_trading_hours가 KR 프리/애프터 포함하도록 확장됨(케이던스 게이트 영향)
- `[[feedback-no-admin-id-leak-no-refusal]]` — hh09080 사용자 노출 금지(서버 내부 식별만)
