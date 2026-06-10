# 넥스트레이드(NXT) 시간외 매매 강화 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** KR 프리마켓(08:00–08:50)·애프터마켓(15:50–20:00)을 정식 매매 세션으로 추가해, NXT(넥스트레이드) 거래소 경유로 정규장과 동일하게 매수+매도하도록 한다.

**Architecture:** 정규장은 KRX 구 TR(`TTTC0802U`/`TTTC0801U`) 경로를 **무변경**으로 보존하고, 시간외만 NXT 신 TR(`TTTC0012U`/`TTTC0011U`)로 분기한다. 세션→거래소 결정은 단일 헬퍼군으로 집약해 "KR/US 비대칭 버그"류 재발을 막는다. 시간외는 지정가 한정이므로 NXT 현재가 ± 슬리피지 밴드로 지정가를 산정하고, 모의서버 NXT 미지원 가능성은 능력감지 + graceful 폴백으로 흡수한다.

**Tech Stack:** Python 3.11, asyncio, aiohttp(KIS REST), pydantic(OrderDraft), pytest. KIS Open API 국내주식 주문/시세.

**근거 스펙:** `docs/superpowers/specs/2026-06-08-nxt-extended-hours-trading-design.md`

---

## 규약 (모든 태스크 공통)

- **테스트는 반드시 `python3.11 -m pytest`** — 기본 `python`은 argon2 import에서 죽는다.
- **커밋은 사장/auto-Backup에 위임** (CLAUDE.md 규칙: 커밋은 사장 명시 요청 시에만). 각 태스크 끝의 "Checkpoint"는 *해당 테스트 통과 확인*까지만 한다 — `git commit`을 직접 실행하지 않는다. 외부 Backup 도구가 주기적으로 휩쓸어 담는다.
- **서버 반영 = `sudo systemctl restart arquant.service`** — 코드 변경은 재시작해야 적용. 단 이 plan 안에서는 재시작을 실행하지 않고 최종 태스크에서 사장 승인 후 안내한다.
- 기존 KRX 정규장 경로의 **회귀(behavior change) 0**가 최우선 — KRX 주문 body/TR이 바이트 단위로 동일해야 한다.

## File Structure (변경/생성 파일)

- **Modify** `main_swarm.py` — SCHEDULE에 2세션 추가, `get_current_session`, 신규 세션 헬퍼군, 흩어진 세션 리터럴 치환, `is_market_session_now` NXT 창, `_MARKET_OPEN_SESSIONS`/`_LIVE_SESSIONS`, 주문 데코레이션 `_finalize_kr_order_for_session` + 2개 디스패치 지점 결선, 시간외 능력감지 스킵.
- **Modify** `infra/kis_broker.py` — `kr_price`/`kr_last_price`에 `market` 인자, 모듈레벨 순수 헬퍼(`kr_tick_size`/`round_to_tick`/`compute_nxt_limit_price`), `_KR_ORD_TR` 매핑, `kr_buy`/`kr_sell`에 `exchange` 인자 + NXT body, `OrderDraft.exchange` 필드, `place_order` 라우팅, `_nxt_supported`/`nxt_supported()` 능력감지.
- **Modify** `config.py` — 4개 신규 플래그 + `STRATEGY_TUNABLE_KEYS` 등록 + `STRATEGY_KEY_META` 항목.
- **Create** `tests/test_nxt_session_schedule.py`, `tests/test_kr_session_helpers.py`, `tests/test_kr_order_exchange_routing.py`, `tests/test_nxt_limit_pricing.py`, `tests/test_nxt_capability_fallback.py`, `tests/test_is_market_session_now_nxt.py`, `tests/test_kr_last_price_market_param.py`.

---

## Task 1: 세션 스케줄 + get_current_session 에 NXT 2세션 추가

**Files:**
- Modify: `main_swarm.py:37-43` (SCHEDULE), `main_swarm.py:150-154` (get_current_session)
- Test: `tests/test_nxt_session_schedule.py`

- [ ] **Step 1: Write the failing test**

`tests/test_nxt_session_schedule.py`:
```python
"""NXT 시간외 세션(프리/애프터) 시각→세션 매핑."""
from datetime import datetime, timezone, timedelta
import main_swarm

KST = timezone(timedelta(hours=9))

def _at(h, m, monkeypatch):
    monkeypatch.setattr(main_swarm, "_now_kst", lambda: datetime(2026, 6, 8, h, m, tzinfo=KST))

def test_pre_market_window(monkeypatch):
    _at(8, 20, monkeypatch)
    assert main_swarm.get_current_session() == "KR_PRE_MARKET"

def test_pre_market_gap_before_open(monkeypatch):
    _at(8, 55, monkeypatch)   # 08:50~09:00 사이 = 장외
    assert main_swarm.get_current_session() == "OFF_HOURS"

def test_regular_session_unchanged(monkeypatch):
    _at(10, 0, monkeypatch)
    assert main_swarm.get_current_session() == "KR_TRADING"

def test_close_review_unchanged(monkeypatch):
    _at(15, 40, monkeypatch)
    assert main_swarm.get_current_session() == "KR_CLOSE_REVIEW"

def test_after_market_window(monkeypatch):
    _at(17, 0, monkeypatch)
    assert main_swarm.get_current_session() == "KR_AFTER_MARKET"

def test_after_market_starts_after_review(monkeypatch):
    _at(15, 48, monkeypatch)   # 리뷰 구간 — 아직 애프터 아님
    assert main_swarm.get_current_session() == "KR_CLOSE_REVIEW"

def test_after_market_ends_2000(monkeypatch):
    _at(20, 1, monkeypatch)
    assert main_swarm.get_current_session() == "OFF_HOURS"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_nxt_session_schedule.py -v`
Expected: FAIL — `test_pre_market_window`/`test_after_market_window` 가 "OFF_HOURS" 반환(아직 세션 미정의).

- [ ] **Step 3: Add the two schedule entries**

`main_swarm.py:37-43`, SCHEDULE 를 다음으로 교체:
```python
SCHEDULE = {
    # 넥스트레이드(NXT) 프리마켓 — 지정가 한정, 실제 거래 가능 시장 (2026-06-03 폐지된 '분석전용 프리장'과 다름)
    "kr_pre_market":   {"start":(8,0),  "end":(8,50),  "desc":"NXT 프리마켓"},
    "kr_trading":      {"start":(9,0),  "end":(15,30), "desc":"KRX 장중"},
    "kr_close_review": {"start":(15,35),"end":(15,50), "desc":"장 마감 리뷰"},
    # NXT 애프터마켓 연속지정가 — 마감리뷰(15:35–15:50) 이후 15:50 시작 (15:30–15:40 시가단일가 제외)
    "kr_after_market": {"start":(15,50),"end":(20,0),  "desc":"NXT 애프터마켓"},
    "us_trading":      {"start":(22,30),"end":(5,0),   "desc":"US 장중 (야간)"},
}
```

- [ ] **Step 4: Add the two session branches**

`main_swarm.py:150-154`, `get_current_session` 를 다음으로 교체:
```python
def get_current_session():
    if _in_schedule("kr_pre_market"):   return "KR_PRE_MARKET"
    if _in_schedule("kr_trading"):      return "KR_TRADING"
    if _in_schedule("kr_close_review"): return "KR_CLOSE_REVIEW"
    if _in_schedule("kr_after_market"): return "KR_AFTER_MARKET"
    if _in_schedule("us_trading"):      return "US_TRADING"
    return "OFF_HOURS"
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_nxt_session_schedule.py -v`
Expected: PASS (7 passed)

- [ ] **Step 6: Checkpoint** — `python3.11 -m pytest tests/test_nxt_session_schedule.py -q` 통과 확인. (commit은 사장/auto-Backup 위임)

---

## Task 2: 세션→거래소 중앙 헬퍼군 (비대칭 버그 방어)

**Files:**
- Modify: `main_swarm.py` (line 154 직후, `get_current_session` 아래에 추가)
- Test: `tests/test_kr_session_helpers.py`

- [ ] **Step 1: Write the failing test**

`tests/test_kr_session_helpers.py`:
```python
"""세션→거래소 결정을 한 곳으로 집약하는 헬퍼군."""
import main_swarm as m

def test_is_kr_session():
    for s in ("KR_TRADING", "KR_PRE_MARKET", "KR_AFTER_MARKET", "KR_CLOSE_REVIEW"):
        assert m.is_kr_session(s) is True
    for s in ("US_TRADING", "OFF_HOURS"):
        assert m.is_kr_session(s) is False

def test_is_kr_tradable_excludes_review():
    assert m.is_kr_tradable("KR_TRADING") is True
    assert m.is_kr_tradable("KR_PRE_MARKET") is True
    assert m.is_kr_tradable("KR_AFTER_MARKET") is True
    assert m.is_kr_tradable("KR_CLOSE_REVIEW") is False   # 리뷰는 매매 X
    assert m.is_kr_tradable("US_TRADING") is False

def test_is_kr_extended_hours():
    assert m.is_kr_extended_hours("KR_PRE_MARKET") is True
    assert m.is_kr_extended_hours("KR_AFTER_MARKET") is True
    assert m.is_kr_extended_hours("KR_TRADING") is False
    assert m.is_kr_extended_hours("KR_CLOSE_REVIEW") is False

def test_kr_exchange_for_session():
    assert m.kr_exchange_for_session("KR_TRADING") == "KRX"
    assert m.kr_exchange_for_session("KR_CLOSE_REVIEW") == "KRX"
    assert m.kr_exchange_for_session("KR_PRE_MARKET") == "NXT"
    assert m.kr_exchange_for_session("KR_AFTER_MARKET") == "NXT"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_kr_session_helpers.py -v`
Expected: FAIL — `AttributeError: module 'main_swarm' has no attribute 'is_kr_session'`

- [ ] **Step 3: Add the helpers**

`main_swarm.py`, `get_current_session` 정의 **직후**(line 154 아래)에 추가:
```python
KR_SESSIONS          = ("KR_TRADING", "KR_PRE_MARKET", "KR_AFTER_MARKET", "KR_CLOSE_REVIEW")
KR_TRADABLE_SESSIONS = ("KR_TRADING", "KR_PRE_MARKET", "KR_AFTER_MARKET")  # 리뷰는 매매 X

def is_kr_session(s):        return s in KR_SESSIONS
def is_kr_tradable(s):       return s in KR_TRADABLE_SESSIONS
def is_kr_extended_hours(s): return s in ("KR_PRE_MARKET", "KR_AFTER_MARKET")
def kr_exchange_for_session(s):  # "KRX" | "NXT"
    return "NXT" if is_kr_extended_hours(s) else "KRX"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_kr_session_helpers.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Checkpoint** — 통과 확인.

---

## Task 3: 흩어진 세션 리터럴을 헬퍼로 치환 (KR_AFTER_MARKET 누락 방지)

**배경:** `session in ("KR_TRADING", "KR_PRE_MARKET", "KR_CLOSE_REVIEW")` 류 리터럴이 여러 곳에 있다. `KR_AFTER_MARKET`가 빠지면 애프터마켓에서 보유평가·매매·"장 마감" 가드가 오작동한다. 의미별로 1:1 치환한다.

**Files:**
- Modify: `main_swarm.py` (아래 라인들 — 치환 전 각 지점을 Read 로 현재 내용 확인)
- Test: 기존 전체 스위트 회귀 (`python3.11 -m pytest`)

- [ ] **Step 1: 치환 대상 식별**

Run: `grep -nE 'session in \(|sess not in \(|_sess in \(|is_kr = session in|is_kr_session\(' main_swarm.py`
각 매치를 Read 로 열어 의미를 판정한다:
- **"매매(매수/매도) 가능한가" 판정** → `is_kr_tradable(s)` 로 치환 (예: line 2246 `mkt=="KR" and session not in (...)`, line 2413/2435 "장 마감" 가드, line 2189 cheap-fallback 게이트, line 2163 가격조회 게이트).
- **"KR 보유종목 평가 대상 세션인가" 판정** → `is_kr_session(s)` 로 치환 (예: line 1195/1242/1871/1926 `is_kr = session in (...)` — 보유·평가 맥락).
- `_post_manager_session_hint`(line 165) 및 line 1372, 1946 의 분기도 동일 기준으로 `is_kr_session`/`is_kr_tradable` 적용.

- [ ] **Step 2: 각 지점 치환**

예시 (line 2246 부근):
```python
# Before
if mkt == "KR" and session not in ("KR_TRADING", "KR_PRE_MARKET"):
# After
if mkt == "KR" and not is_kr_tradable(session):
```
예시 ("장 마감" 가드, line 2413·2435 부근):
```python
# Before
if (is_kr and sess not in ("KR_TRADING", "KR_PRE_MARKET")) or (not is_kr and sess != "US_TRADING"):
# After
if (is_kr and not is_kr_tradable(sess)) or (not is_kr and sess != "US_TRADING"):
```
예시 (보유평가 맥락, line 1195/1242/1871/1926 부근):
```python
# Before
is_kr = session in ("KR_TRADING", "KR_PRE_MARKET", "KR_CLOSE_REVIEW")
# After
is_kr = is_kr_session(session)
```
> 주의: 의미를 바꾸지 말 것. 매매 가능 여부 가드에 `is_kr_session`(리뷰 포함)을 쓰면 리뷰 구간에 매매가 열린다 — 반드시 `is_kr_tradable` 사용. 보유 평가 맥락엔 `is_kr_session`.

- [ ] **Step 3: 잔여 리터럴 확인**

Run: `grep -nE '"KR_TRADING"|"KR_PRE_MARKET"|"KR_AFTER_MARKET"|"KR_CLOSE_REVIEW"' main_swarm.py`
Expected: SCHEDULE/get_current_session/헬퍼군 정의부 외에 **튜플 멤버십 리터럴이 남지 않음**(상태 라벨/캐시키 같은 단일 비교는 예외 — 의미 보존이면 둠).

- [ ] **Step 4: 전체 회귀**

Run: `python3.11 -m pytest -q`
Expected: PASS — 기존 테스트 전부 통과(치환이 의미 보존이면 회귀 없음). 실패 시 해당 지점의 헬퍼 선택(tradable vs session)을 재검토.

- [ ] **Step 5: Checkpoint** — 전체 스위트 그린 확인.

---

## Task 4: is_market_session_now 에 NXT 시간외 창 추가 (평가곡선 기록)

**Files:**
- Modify: `main_swarm.py:397-412` (`is_market_session_now`)
- Test: `tests/test_is_market_session_now_nxt.py`

- [ ] **Step 1: Write the failing test**

`tests/test_is_market_session_now_nxt.py`:
```python
"""NXT 시간외 창에도 평가곡선 기록이 열리되, 주말/휴장은 닫힌다."""
from datetime import datetime, timezone, timedelta
import main_swarm as m

KST = timezone(timedelta(hours=9))
def _dt(y, mo, d, h, mi): return datetime(y, mo, d, h, mi, tzinfo=KST)

def test_pre_market_weekday_open(monkeypatch):
    monkeypatch.setattr(m, "_market_day_verified_closed", lambda *a, **k: False)
    assert m.is_market_session_now(_dt(2026, 6, 8, 8, 20)) is True   # 월요일 08:20

def test_after_market_weekday_open(monkeypatch):
    monkeypatch.setattr(m, "_market_day_verified_closed", lambda *a, **k: False)
    assert m.is_market_session_now(_dt(2026, 6, 8, 17, 0)) is True

def test_after_market_weekend_closed(monkeypatch):
    monkeypatch.setattr(m, "_market_day_verified_closed", lambda *a, **k: False)
    assert m.is_market_session_now(_dt(2026, 6, 6, 17, 0)) is False   # 토요일

def test_pre_market_holiday_closed(monkeypatch):
    monkeypatch.setattr(m, "_market_day_verified_closed", lambda mkt, dt=None: True)
    assert m.is_market_session_now(_dt(2026, 6, 8, 8, 20)) is False

def test_gap_between_review_and_after_still_closed(monkeypatch):
    monkeypatch.setattr(m, "_market_day_verified_closed", lambda *a, **k: False)
    # 08:50~09:00, 15:30~15:50 등 비거래 구간은 기록 안 함
    assert m.is_market_session_now(_dt(2026, 6, 8, 8, 55)) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_is_market_session_now_nxt.py -v`
Expected: FAIL — 08:20/17:00 이 현재 False (정규장 창만 True).

- [ ] **Step 3: Add NXT windows**

`main_swarm.py:397-412`, `is_market_session_now` 의 `t = dt.hour * 60 + dt.minute` 줄 다음, 정규장 KR 분기 **앞**에 추가:
```python
    # NXT 프리마켓(08:00–08:50)·애프터마켓(15:50–20:00) — KRX와 동일 거래일(주말/휴장 게이트 공유)
    if (8*60 <= t < 8*60+50) or (15*60+50 <= t < 20*60):
        return not is_kr_weekend(dt) and not _market_day_verified_closed("KR", dt)
```
(기존 KR 정규장·US 분기는 그대로 둔다.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_is_market_session_now_nxt.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Checkpoint** — 통과 + `python3.11 -m pytest tests/test_market_open_verify.py -q` 회귀 확인.

---

## Task 5: config 플래그 4종 + 런타임 등록 + 메타

**Files:**
- Modify: `config.py:129-130` 인근(플래그 정의), `config.py:259`(STRATEGY_TUNABLE_KEYS), `config.py:382` 인근(STRATEGY_KEY_META)
- Test: `tests/test_nxt_config_flags.py`

- [ ] **Step 1: Write the failing test**

`tests/test_nxt_config_flags.py`:
```python
"""NXT 시간외 플래그가 config 와 런타임 오버라이드 카탈로그에 존재."""
import config

def test_flags_defined():
    assert config.ENABLE_NXT_EXTENDED_HOURS is True
    assert config.ENABLE_NXT_PRE_MARKET is True
    assert config.ENABLE_NXT_AFTER_MARKET is True
    assert abs(config.EXT_HOURS_LIMIT_SLIPPAGE_PCT - 0.5) < 1e-9

def test_flags_runtime_overridable():
    for k in ("ENABLE_NXT_EXTENDED_HOURS", "ENABLE_NXT_PRE_MARKET",
              "ENABLE_NXT_AFTER_MARKET", "EXT_HOURS_LIMIT_SLIPPAGE_PCT"):
        assert k in config.STRATEGY_TUNABLE_KEYS, f"{k} 런타임 오버라이드 미등록"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_nxt_config_flags.py -v`
Expected: FAIL — `AttributeError: module 'config' has no attribute 'ENABLE_NXT_EXTENDED_HOURS'`

- [ ] **Step 3: 플래그 정의 추가**

`config.py`, line 129 (`THESIS_NOISE_BAND_PCT = 0.03`) 다음 빈 줄 뒤에 추가:
```python
# ─── 넥스트레이드(NXT) 시간외 매매 (사장 지시 2026-06-08) ─────────────────────
# 프리마켓(08:00–08:50)·애프터마켓(15:50–20:00)을 NXT 거래소 경유로 매매. 정규장은 KRX 유지.
ENABLE_NXT_EXTENDED_HOURS = True   # 마스터 스위치 (끄면 시간외 세션을 OFF_HOURS처럼 취급)
ENABLE_NXT_PRE_MARKET     = True   # 프리마켓 08:00–08:50 on/off
ENABLE_NXT_AFTER_MARKET   = True   # 애프터마켓 15:50–20:00 on/off
EXT_HOURS_LIMIT_SLIPPAGE_PCT = 0.5 # 시간외 지정가 밴드(%) — 매수=현재가×(1+x%), 매도=×(1−x%)
```

- [ ] **Step 4: 런타임 오버라이드 등록**

`config.py:259`, `STRATEGY_TUNABLE_KEYS` 리스트의 `"ENABLE_CHEAP_FALLBACK", "ALLOW_US_STOCKS", "ALLOW_DERIVATIVES",` 줄 다음에 추가:
```python
    # NXT 시간외 매매 (사장 지시 2026-06-08)
    "ENABLE_NXT_EXTENDED_HOURS", "ENABLE_NXT_PRE_MARKET", "ENABLE_NXT_AFTER_MARKET",
    "EXT_HOURS_LIMIT_SLIPPAGE_PCT",
```

- [ ] **Step 5: 대시보드 메타 추가**

`config.py`, `STRATEGY_KEY_META` 딕셔너리의 `"ENABLE_CHEAP_FALLBACK"` 항목(line 380-382) 다음에 추가:
```python
    "ENABLE_NXT_EXTENDED_HOURS":  {"label": "넥스트레이드(NXT) 시간외 매매", "type": "bool",
                                   "help": "ON이면 프리마켓(08:00–08:50)·애프터마켓(15:50–20:00)에 NXT 경유 매매",
                                   "group": "시간외(NXT)"},
    "ENABLE_NXT_PRE_MARKET":      {"label": "프리마켓(08:00–08:50) 매매", "type": "bool",
                                   "help": "넥스트레이드 프리마켓 지정가 매매 (마스터 스위치 ON 전제)",
                                   "group": "시간외(NXT)"},
    "ENABLE_NXT_AFTER_MARKET":    {"label": "애프터마켓(15:50–20:00) 매매", "type": "bool",
                                   "help": "넥스트레이드 애프터마켓 지정가 매매 (마스터 스위치 ON 전제)",
                                   "group": "시간외(NXT)"},
    "EXT_HOURS_LIMIT_SLIPPAGE_PCT": {"label": "시간외 지정가 밴드", "type": "pct_raw", "unit": "%",
                                   "help": "시간외 지정가 = 현재가 ± 이 폭. 체결확률↑ vs 슬리피지 상한 트레이드오프",
                                   "min": 0, "max": 5, "step": 0.1, "group": "시간외(NXT)"},
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_nxt_config_flags.py -v`
Expected: PASS (2 passed)

- [ ] **Step 7: Checkpoint** — 통과 확인.

---

## Task 6: kr_price / kr_last_price 에 market 인자 (J/NX/UN)

**Files:**
- Modify: `infra/kis_broker.py:326-329` (`kr_price`), `infra/kis_broker.py:691-700+` (`kr_last_price`)
- Test: `tests/test_kr_last_price_market_param.py`

- [ ] **Step 1: Write the failing test**

`tests/test_kr_last_price_market_param.py`:
```python
"""kr_price/kr_last_price 가 market 인자를 FID_COND_MRKT_DIV_CODE 로 전달."""
import asyncio
from infra.kis_broker import KISBroker

class _Resp:
    def __init__(self, p): self._p = p
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def json(self): return self._p

class _Sess:
    def __init__(self): self.params = []
    def get(self, url, headers=None, params=None):
        self.params.append(params)
        return _Resp({"output": {"stck_prpr": "70000"}})

_CREDS = {"kis_app_key": "K", "kis_app_secret": "S",
          "kis_account_no": "12345678-01",
          "kis_base_url": "https://openapi.koreainvestment.com:9443"}

def _broker(monkeypatch, sess):
    b = KISBroker(_CREDS)
    async def _tok(): return "T"
    async def _s(): return sess
    monkeypatch.setattr(b, "token", _tok)
    monkeypatch.setattr(b, "_s", _s)
    return b

def test_default_market_is_krx(monkeypatch):
    s = _Sess(); b = _broker(monkeypatch, s)
    asyncio.run(b.kr_price("005930"))
    assert s.params[0]["FID_COND_MRKT_DIV_CODE"] == "J"

def test_nxt_market_param(monkeypatch):
    s = _Sess(); b = _broker(monkeypatch, s)
    asyncio.run(b.kr_price("005930", market="NX"))
    assert s.params[0]["FID_COND_MRKT_DIV_CODE"] == "NX"

def test_kr_last_price_threads_market(monkeypatch):
    s = _Sess(); b = _broker(monkeypatch, s)
    px = asyncio.run(b.kr_last_price("005930", market="UN"))
    assert px == 70000.0
    assert s.params[0]["FID_COND_MRKT_DIV_CODE"] == "UN"
```
> 참고: `_get_json` 은 내부에서 `self._s()` 세션의 `get` 을 호출한다(시세 경로는 `_authed_json` 미경유, line 198-206 참조). 위 _Sess 가 그 지점을 모킹한다.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_kr_last_price_market_param.py -v`
Expected: FAIL — `kr_price() got an unexpected keyword argument 'market'`

- [ ] **Step 3: kr_price 에 market 인자 추가**

`infra/kis_broker.py:326-329` 를 교체:
```python
    async def kr_price(self, code: str, market: str = "J") -> Dict:
        # market: J=KRX, NX=NXT, UN=통합 (FID_COND_MRKT_DIV_CODE)
        d = await self._get_json("/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100",
            {"FID_COND_MRKT_DIV_CODE": market, "FID_INPUT_ISCD": code})
        return d.get("output", {})
```

- [ ] **Step 4: kr_last_price 에 market 인자 추가**

`infra/kis_broker.py:691`, 시그니처와 KIS 1차 조회 호출을 교체:
```python
    async def kr_last_price(self, code: str, market: str = "J") -> float:
```
그리고 같은 함수 본문 line 695 `d = await self.kr_price(code)` 를:
```python
            d = await self.kr_price(code, market=market)
```
(이하 네이버 폴백 로직은 그대로 — KIS가 0/실패면 KRX 근사가로 폴백, 시간외 최후수단으로 허용)

- [ ] **Step 5: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_kr_last_price_market_param.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Checkpoint** — 통과 + `grep -n "kr_price(" infra/kis_broker.py main_swarm.py` 로 기존 호출부가 인자 없이도(기본 J) 정상인지 확인.

---

## Task 7: 호가단위·NXT 지정가 순수 헬퍼

**Files:**
- Modify: `infra/kis_broker.py` (모듈 상단 헬퍼 영역 — line 88 `_us_exchange_map` 근처 모듈레벨에 추가)
- Test: `tests/test_nxt_limit_pricing.py`

- [ ] **Step 1: Write the failing test**

`tests/test_nxt_limit_pricing.py`:
```python
"""KR 호가단위 반올림 + NXT 시간외 지정가(밴드) 산정 — 순수 함수."""
from infra.kis_broker import kr_tick_size, round_to_tick, compute_nxt_limit_price

def test_tick_size_bands():
    assert kr_tick_size(1500)   == 1
    assert kr_tick_size(3000)   == 5
    assert kr_tick_size(12000)  == 10
    assert kr_tick_size(45000)  == 50
    assert kr_tick_size(150000) == 100
    assert kr_tick_size(300000) == 500
    assert kr_tick_size(800000) == 1000

def test_round_to_tick_snaps_to_valid():
    assert round_to_tick(12345) == 12340     # 호가단위 10 → 가장 가까운 유효호가
    assert round_to_tick(12346) == 12350
    assert round_to_tick(45070) == 45050      # 호가단위 50

def test_buy_limit_adds_band_and_snaps():
    # last=10000 → 호가단위 10(5천~2만). +0.5% = 10050 → 단위 10에 이미 정합
    px = compute_nxt_limit_price(10000, side="buy", slippage_pct=0.5)
    assert px == 10050        # 10000×1.005=10050
    assert isinstance(px, int)

def test_sell_limit_subtracts_band():
    px = compute_nxt_limit_price(10000, side="sell", slippage_pct=0.5)
    assert px == 9950          # 10000×0.995=9950

def test_zero_last_returns_zero():
    assert compute_nxt_limit_price(0, side="buy", slippage_pct=0.5) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_nxt_limit_pricing.py -v`
Expected: FAIL — `ImportError: cannot import name 'kr_tick_size'`

- [ ] **Step 3: 헬퍼 구현**

`infra/kis_broker.py`, 모듈레벨(line 88 `def _us_exchange_map` 류 헬퍼 근처)에 추가:
```python
def kr_tick_size(price: float) -> int:
    """KRX/NXT 공통 호가단위 (2023~ 개정 기준)."""
    p = float(price or 0)
    if p < 2000:    return 1
    if p < 5000:    return 5
    if p < 20000:   return 10
    if p < 50000:   return 50
    if p < 200000:  return 100
    if p < 500000:  return 500
    return 1000

def round_to_tick(price: float) -> int:
    """가장 가까운 유효 호가로 반올림(nearest). 밴드가 이미 공격성을 부여하므로 방향성 라운딩 불필요."""
    p = float(price or 0)
    if p <= 0:
        return 0
    t = kr_tick_size(p)
    return int(round(p / t) * t)

def compute_nxt_limit_price(last_price: float, *, side: str, slippage_pct: float) -> int:
    """시간외 지정가 = 현재가 ± 슬리피지 밴드, 호가단위 반올림. last_price<=0 이면 0(주문 보류 신호)."""
    last = float(last_price or 0)
    if last <= 0:
        return 0
    band = (float(slippage_pct or 0) / 100.0)
    raw = last * (1 + band) if side == "buy" else last * (1 - band)
    return round_to_tick(raw)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_nxt_limit_pricing.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Checkpoint** — 통과 확인.

---

## Task 8: kr_buy/kr_sell exchange 인자 + NXT body + OrderDraft.exchange + place_order 라우팅

**Files:**
- Modify: `infra/kis_broker.py` — `_KR_ORD_TR` 매핑(클래스 상수), `kr_buy`(421-432), `kr_sell`(494-517), `OrderDraft`(95-99), `place_order`(1549-1556)
- Test: `tests/test_kr_order_exchange_routing.py`

- [ ] **Step 1: Write the failing test**

`tests/test_kr_order_exchange_routing.py`:
```python
"""정규장(KRX)=구 TR 무변경 회귀 + 시간외(NXT)=신 TR + EXCG_ID_DVSN_CD."""
import asyncio
from infra.kis_broker import KISBroker, OrderDraft

class _Resp:
    def __init__(self, p): self._p = p
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def json(self): return self._p

class _Sess:
    def __init__(self): self.posts = []
    def get(self, url, headers=None, params=None):
        return _Resp({"rt_cd": "0", "output": [], "ctx_area_fk100": "", "ctx_area_nk100": ""})
    def post(self, url, headers=None, json=None):
        self.posts.append({"url": url, "tr_id": (headers or {}).get("tr_id"), "json": json})
        return _Resp({"rt_cd": "0", "msg1": "정상처리 되었습니다"})

_CREDS = {"kis_app_key": "K", "kis_app_secret": "S",
          "kis_account_no": "12345678-01",
          "kis_base_url": "https://openapi.koreainvestment.com:9443"}

def _broker(monkeypatch, sess, is_mock=False):
    b = KISBroker(_CREDS)
    b.is_mock = is_mock
    async def _tok(): return "T"
    async def _s(): return sess
    monkeypatch.setattr(b, "token", _tok)
    monkeypatch.setattr(b, "_s", _s)
    return b

def test_krx_buy_unchanged_legacy_tr(monkeypatch):
    s = _Sess(); b = _broker(monkeypatch, s)
    asyncio.run(b.kr_buy("005930", 1, price=70000))           # exchange 기본 KRX
    o = [p for p in s.posts if p["url"].endswith("/order-cash")][0]
    assert o["tr_id"] == "TTTC0802U"                          # 구 TR 보존
    assert "EXCG_ID_DVSN_CD" not in o["json"]                 # KRX는 EXCG 미포함
    assert o["json"]["ORD_DVSN"] == "00"                      # 지정가(price>0)

def test_krx_sell_unchanged_legacy_tr(monkeypatch):
    s = _Sess(); b = _broker(monkeypatch, s)
    asyncio.run(b.kr_sell("005930", 1, price=0))              # 시장가
    o = [p for p in s.posts if p["url"].endswith("/order-cash")][0]
    assert o["tr_id"] == "TTTC0801U"
    assert "EXCG_ID_DVSN_CD" not in o["json"]
    assert o["json"]["ORD_DVSN"] == "01"                      # 무가격=시장가 (기존 거동)

def test_nxt_buy_new_tr_and_excg(monkeypatch):
    s = _Sess(); b = _broker(monkeypatch, s)
    asyncio.run(b.kr_buy("005930", 1, price=70000, exchange="NXT"))
    o = [p for p in s.posts if p["url"].endswith("/order-cash")][0]
    assert o["tr_id"] == "TTTC0012U"                          # 신 TR
    assert o["json"]["EXCG_ID_DVSN_CD"] == "NXT"
    assert o["json"]["ORD_DVSN"] == "00"                      # 시간외 지정가 강제
    assert o["json"]["ORD_UNPR"] == "70000"

def test_nxt_sell_new_tr_and_sll_type(monkeypatch):
    s = _Sess(); b = _broker(monkeypatch, s)
    asyncio.run(b.kr_sell("005930", 1, price=69000, exchange="NXT"))
    o = [p for p in s.posts if p["url"].endswith("/order-cash")][0]
    assert o["tr_id"] == "TTTC0011U"
    assert o["json"]["EXCG_ID_DVSN_CD"] == "NXT"
    assert o["json"]["SLL_TYPE"] == "01"
    assert o["json"]["ORD_DVSN"] == "00"

def test_nxt_mock_tr_conversion(monkeypatch):
    s = _Sess(); b = _broker(monkeypatch, s, is_mock=True)
    asyncio.run(b.kr_buy("005930", 1, price=70000, exchange="NXT"))
    o = [p for p in s.posts if p["url"].endswith("/order-cash")][0]
    assert o["tr_id"] == "VTTC0012U"                          # T→V 변환

def test_place_order_routes_exchange(monkeypatch):
    s = _Sess(); b = _broker(monkeypatch, s)
    od = OrderDraft(ticker="005930", side="buy", qty=1, price_type="limit",
                    limit_price=70000, market="KR", exchange="NXT", approved=True)
    asyncio.run(b.place_order(od))
    o = [p for p in s.posts if p["url"].endswith("/order-cash")][0]
    assert o["tr_id"] == "TTTC0012U"
    assert o["json"]["EXCG_ID_DVSN_CD"] == "NXT"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_kr_order_exchange_routing.py -v`
Expected: FAIL — `kr_buy() got an unexpected keyword argument 'exchange'`

- [ ] **Step 3: TR 매핑 상수 추가**

`infra/kis_broker.py`, `KISBroker` 클래스 내 `_MOCK_TR_OVERRIDE`(line 303) 근처에 추가:
```python
    # (side, exchange) → tr_id.  KRX = 검증된 구 TR 그대로 (EXCG 미포함). NXT = 신 통합주문 TR.
    _KR_ORD_TR = {
        ("buy",  "KRX"): "TTTC0802U", ("sell", "KRX"): "TTTC0801U",
        ("buy",  "NXT"): "TTTC0012U", ("sell", "NXT"): "TTTC0011U",
    }
```

- [ ] **Step 4: kr_buy 에 exchange 분기**

`infra/kis_broker.py:421-432` `kr_buy` 를 교체:
```python
    async def kr_buy(self, code: str, qty: int, price: int = 0, exchange: str = "KRX") -> str:
        s = await self._s(); c, p = self._acnt()
        ord_dvsn = "00" if (price or exchange == "NXT") else "01"   # NXT=지정가 강제
        body = {"CANO":c,"ACNT_PRDT_CD":p,"PDNO":code,"ORD_DVSN":ord_dvsn,
                "ORD_QTY":str(qty),"ORD_UNPR":str(price) if price else "0","CTAC_TLNO":""}
        if exchange == "NXT":
            body["EXCG_ID_DVSN_CD"] = "NXT"; body["CNDT_PRIC"] = ""
        tr = self._KR_ORD_TR[("buy", exchange)]
        async def _do(tk):
            async with s.post(f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash",
                headers=self._h(tk, tr), json=body) as r:
                return await r.json()
        d = await self._authed_json(_do)
        ok = d.get("rt_cd") == "0"
        self._note_nxt_result(exchange, d)
        return f"[국내매수] {code} {qty}주 → {_clean_kis_msg(d.get('msg1',''))}" if ok else f"[실패] {_clean_kis_msg(d.get('msg1',''))}"
```

- [ ] **Step 5: kr_sell 에 exchange 분기**

`infra/kis_broker.py:507-517` `kr_sell` 의 body/전송부(펜딩 취소 로직 이후)를 교체:
```python
        s = await self._s(); c, p = self._acnt()
        ord_dvsn = "00" if (price or exchange == "NXT") else "01"
        body = {"CANO":c,"ACNT_PRDT_CD":p,"PDNO":code,"ORD_DVSN":ord_dvsn,
                "ORD_QTY":str(qty),"ORD_UNPR":str(price) if price else "0","CTAC_TLNO":""}
        if exchange == "NXT":
            body["EXCG_ID_DVSN_CD"] = "NXT"; body["SLL_TYPE"] = "01"; body["CNDT_PRIC"] = ""
        tr = self._KR_ORD_TR[("sell", exchange)]
        async def _do(tk):
            async with s.post(f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash",
                headers=self._h(tk, tr), json=body) as r:
                return await r.json()
        d = await self._authed_json(_do)
        ok = d.get("rt_cd") == "0"
        self._note_nxt_result(exchange, d)
        return f"[국내매도] {code} {qty}주 → {_clean_kis_msg(d.get('msg1',''))}" if ok else f"[실패] {_clean_kis_msg(d.get('msg1',''))}"
```
또한 `kr_sell` 시그니처(line 494)에 `exchange: str = "KRX"` 추가:
```python
    async def kr_sell(self, code: str, qty: int, price: int = 0, exchange: str = "KRX") -> str:
```

- [ ] **Step 6: `_note_nxt_result` 임시 스텁 추가 (Task 9에서 본 구현)**

Step 4/5가 참조하는 `_note_nxt_result` 를 우선 no-op 으로 추가(클래스 메서드), Task 9에서 능력감지 본 구현으로 대체:
```python
    def _note_nxt_result(self, exchange: str, resp: dict) -> None:
        pass  # Task 9에서 능력감지 구현
```

- [ ] **Step 7: OrderDraft.exchange 필드 + place_order 라우팅**

`infra/kis_broker.py:98` `OrderDraft` 에 필드 추가:
```python
    market: str = "KR"; exchange: str = "KRX"; reason: str = ""; approved: bool = False
```
`infra/kis_broker.py:1552-1556` `place_order` KR 분기를 교체:
```python
        if order.market == "KR":
            if order.side == OrderSide.BUY:
                return await self.kr_buy(order.ticker, order.qty, int(order.limit_price or 0), exchange=order.exchange)
            else:
                return await self.kr_sell(order.ticker, order.qty, int(order.limit_price or 0), exchange=order.exchange)
```

- [ ] **Step 8: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_kr_order_exchange_routing.py -v`
Expected: PASS (6 passed)

- [ ] **Step 9: 회귀 — 기존 KR 주문/펜딩 테스트**

Run: `python3.11 -m pytest tests/test_kr_sell_cancels_pending.py tests/test_order_clamp.py tests/test_order_maxqty_clamp.py -q`
Expected: PASS — KRX 경로 무변경이므로 전부 그린. 실패 시 ORD_DVSN/TR/ body 키 순서가 아닌 *값* 동일성 확인.

- [ ] **Step 10: Checkpoint** — 통과 확인.

---

## Task 9: NXT 능력감지 + graceful 폴백

**Files:**
- Modify: `infra/kis_broker.py` — `__init__`(인스턴스 필드), `_note_nxt_result`(Task 8 스텁 대체), `nxt_supported()` 추가
- Test: `tests/test_nxt_capability_fallback.py`

- [ ] **Step 1: Write the failing test**

`tests/test_nxt_capability_fallback.py`:
```python
"""NXT 주문 미지원 응답 → _nxt_supported=False 고정, 이후 시간외 스킵 판정."""
import asyncio
from infra.kis_broker import KISBroker

_CREDS = {"kis_app_key": "K", "kis_app_secret": "S",
          "kis_account_no": "12345678-01",
          "kis_base_url": "https://openapi.koreainvestment.com:9443"}

class _Resp:
    def __init__(self, p): self._p = p
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def json(self): return self._p

class _Sess:
    def __init__(self, payload):
        self.payload = payload
    def get(self, url, headers=None, params=None):
        return _Resp({"rt_cd": "0", "output": [], "ctx_area_fk100": "", "ctx_area_nk100": ""})
    def post(self, url, headers=None, json=None):
        return _Resp(self.payload)

def _broker(monkeypatch, payload, is_mock=True):
    b = KISBroker(_CREDS); b.is_mock = is_mock
    async def _tok(): return "T"
    async def _s(): return _Sess(payload)
    monkeypatch.setattr(b, "token", _tok)
    monkeypatch.setattr(b, "_s", _s)
    return b

def test_initial_supported_is_none():
    b = KISBroker(_CREDS)
    assert b.nxt_supported() is None        # 미탐 — 1회 시도 허용

def test_unsupported_response_trips_flag(monkeypatch):
    # 모의서버 미지원 시그니처 (rt_cd≠0 + 미지원 메시지)
    b = _broker(monkeypatch, {"rt_cd": "1", "msg_cd": "40570000",
                              "msg1": "모의투자 미지원 거래소입니다"})
    asyncio.run(b.kr_buy("005930", 1, price=70000, exchange="NXT"))
    assert b.nxt_supported() is False

def test_success_sets_supported_true(monkeypatch):
    b = _broker(monkeypatch, {"rt_cd": "0", "msg1": "정상처리 되었습니다"})
    asyncio.run(b.kr_buy("005930", 1, price=70000, exchange="NXT"))
    assert b.nxt_supported() is True

def test_krx_orders_do_not_set_nxt_flag(monkeypatch):
    b = _broker(monkeypatch, {"rt_cd": "0", "msg1": "정상"})
    asyncio.run(b.kr_buy("005930", 1, price=70000))   # KRX
    assert b.nxt_supported() is None                  # NXT 무관 주문은 플래그 불변

def test_ordinary_nxt_rejection_does_not_trip(monkeypatch):
    # 잔고부족 등 '지원은 되나 거부' → 미지원으로 오판하면 안 됨
    b = _broker(monkeypatch, {"rt_cd": "1", "msg_cd": "40310000",
                              "msg1": "주문가능금액을 초과하였습니다"})
    asyncio.run(b.kr_buy("005930", 1, price=70000, exchange="NXT"))
    assert b.nxt_supported() is not False             # None 유지(지원 여부 미확정)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_nxt_capability_fallback.py -v`
Expected: FAIL — `nxt_supported` 미정의 / 플래그 미설정.

- [ ] **Step 3: 인스턴스 필드 초기화**

`infra/kis_broker.py` `KISBroker.__init__` 본문에 추가(다른 인스턴스 필드 초기화 근처):
```python
        self._nxt_supported = None   # None=미탐, True=지원확인, False=미지원(시간외 스킵)
```

- [ ] **Step 4: `_note_nxt_result` 본 구현 (Task 8 스텁 대체)**

```python
    # NXT 미지원으로 판정할 메시지 시그니처(모의서버). 일반 거부(잔고부족 등)와 구분.
    _NXT_UNSUPPORTED_HINTS = ("미지원", "지원하지", "지원되지", "제공되지", "사용할 수 없")

    def _note_nxt_result(self, exchange: str, resp: dict) -> None:
        """NXT 주문 응답으로 거래소 지원 여부 학습. KRX 주문은 무관."""
        if exchange != "NXT":
            return
        if (resp or {}).get("rt_cd") == "0":
            if self._nxt_supported is not True:
                self._nxt_supported = True
            return
        msg = str((resp or {}).get("msg1", ""))
        if any(h in msg for h in self._NXT_UNSUPPORTED_HINTS):
            if self._nxt_supported is not False:
                logger.warning(f"[NXT] 거래소 미지원 감지 — 시간외 매매 비활성화. msg={msg}")
                self._nxt_supported = False
        # 그 외 거부(잔고부족 등)는 지원 여부 미확정 → 플래그 불변

    def nxt_supported(self):
        return self._nxt_supported
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_nxt_capability_fallback.py -v`
Expected: PASS (5 passed)

- [ ] **Step 6: Checkpoint** — 통과 + Task 8 테스트 재실행(`python3.11 -m pytest tests/test_kr_order_exchange_routing.py -q`) 회귀 없음 확인.

---

## Task 10: 오케스트레이터 결선 — 거래소 데코레이션 + 시간외 지정가 + 능력감지 스킵 + 개장 트리거

**Files:**
- Modify: `main_swarm.py` — 신규 메서드 `_finalize_kr_order_for_session`·`_extended_hours_blocked`는 **`_ExecutionMixin`**(class 정의 line 1982)에 추가(상속으로 `_entry_watch_task`·`_cyc_stage_execute` 양쪽에서 호출 가능). `place_order` 디스패치 2곳(2446=`_entry_watch_task`, 4198=`_cyc_stage_execute`) 전처리 삽입, `_MARKET_OPEN_SESSIONS`/`_LIVE_SESSIONS`(2902-2903), 사이클 루프(line 3007 부근, `ArquantOrchestrator`) 능력감지 스킵
- Test: `tests/test_finalize_kr_order_for_session.py`

- [ ] **Step 1: Write the failing test**

`tests/test_finalize_kr_order_for_session.py`:
```python
"""주문을 세션 거래소로 데코레이트 + 시간외 지정가 산정 + 시세결손 보류."""
import asyncio
import main_swarm
from infra.kis_broker import OrderDraft

class _StubRuntime:
    def __init__(self, params): self._p = params
    def get(self, k, default=None): return self._p.get(k, default)

class _StubBroker:
    def __init__(self, px): self._px = px; self.calls = []
    async def kr_last_price(self, code, market="J"):
        self.calls.append(market); return self._px

def _orch():
    # ArquantOrchestrator(_OpsRouterMixin, _MarketCalendarMixin, _ExecutionMixin) — 메서드는 _ExecutionMixin 소속
    o = main_swarm.ArquantOrchestrator.__new__(main_swarm.ArquantOrchestrator)
    return o

def test_regular_session_sets_krx_no_pricing(monkeypatch):
    monkeypatch.setattr(main_swarm, "runtime", _StubRuntime({"EXT_HOURS_LIMIT_SLIPPAGE_PCT": 0.5}))
    o = _orch(); o.broker = _StubBroker(10000)
    od = OrderDraft(ticker="005930", side="buy", qty=1, price_type="market", market="KR", approved=True)
    res, skip = asyncio.run(o._finalize_kr_order_for_session(od, "KR_TRADING"))
    assert res.exchange == "KRX"
    assert skip is None
    assert o.broker.calls == []          # 정규장은 시세 재조회 안 함

def test_extended_session_sets_nxt_and_limit(monkeypatch):
    monkeypatch.setattr(main_swarm, "runtime", _StubRuntime({"EXT_HOURS_LIMIT_SLIPPAGE_PCT": 0.5}))
    o = _orch(); o.broker = _StubBroker(10000)
    od = OrderDraft(ticker="005930", side="buy", qty=1, price_type="market", market="KR", approved=True)
    res, skip = asyncio.run(o._finalize_kr_order_for_session(od, "KR_AFTER_MARKET"))
    assert res.exchange == "NXT"
    assert res.price_type.value == "limit"
    assert res.limit_price == 10050       # 10000×1.005, 호가단위 정합
    assert skip is None
    assert o.broker.calls == ["NX"]       # NXT 시세 조회

def test_extended_session_no_price_holds(monkeypatch):
    monkeypatch.setattr(main_swarm, "runtime", _StubRuntime({"EXT_HOURS_LIMIT_SLIPPAGE_PCT": 0.5}))
    o = _orch(); o.broker = _StubBroker(0)   # 시세 결손
    od = OrderDraft(ticker="005930", side="buy", qty=1, market="KR", approved=True)
    res, skip = asyncio.run(o._finalize_kr_order_for_session(od, "KR_PRE_MARKET"))
    assert skip is not None and "시세" in skip   # 보류 사유 반환(조용히 누락 금지)

def test_us_order_untouched(monkeypatch):
    monkeypatch.setattr(main_swarm, "runtime", _StubRuntime({"EXT_HOURS_LIMIT_SLIPPAGE_PCT": 0.5}))
    o = _orch(); o.broker = _StubBroker(10000)
    od = OrderDraft(ticker="AAPL", side="buy", qty=1, market="US", approved=True)
    res, skip = asyncio.run(o._finalize_kr_order_for_session(od, "KR_AFTER_MARKET"))
    assert res.exchange == "KRX"          # 기본값 유지(US는 무관)
    assert skip is None
    assert o.broker.calls == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_finalize_kr_order_for_session.py -v`
Expected: FAIL — `_finalize_kr_order_for_session` 미정의.

- [ ] **Step 3: 메서드 구현**

`main_swarm.py`, **`_ExecutionMixin`** 클래스 내(line 1982~2607, 예: `_poll_fills_until_confirmed` 위)에 추가. 모듈 상단 import 에 `from infra.kis_broker import compute_nxt_limit_price, PriceType` 가 없으면 추가(기존에 `OrderDraft`/`OrderSide` import 라인에 합쳐도 됨):
```python
    async def _finalize_kr_order_for_session(self, od, session):
        """KR 주문을 세션 거래소로 데코레이트. 시간외면 NXT 지정가 산정.
        반환 (order, skip_reason). skip_reason 이 있으면 호출부가 주문 보류 + 사유 발화(조용히 누락 금지)."""
        if getattr(od, "market", "KR") != "KR":
            return od, None
        od.exchange = kr_exchange_for_session(session)
        if not is_kr_extended_hours(session):
            return od, None
        # 시간외 = 지정가 한정 → NXT 현재가 ± 밴드. NX→UN→J 폴백.
        last = 0.0
        for mkt in ("NX", "UN", "J"):
            try:
                last = await self.broker.kr_last_price(od.ticker, market=mkt)
            except Exception:
                last = 0.0
            if last and last > 0:
                break
        if not last or last <= 0:
            return od, f"NXT 시세 결손 — {od.ticker} 시간외 지정가 산정 불가, 주문 보류(시장가 대체 불가)"
        slip = float(runtime.get("EXT_HOURS_LIMIT_SLIPPAGE_PCT", 0.5) or 0.5)
        side = od.side.value if hasattr(od.side, "value") else str(od.side)
        od.limit_price = compute_nxt_limit_price(last, side=side, slippage_pct=slip)
        od.price_type = PriceType.LIMIT
        return od, None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_finalize_kr_order_for_session.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: 디스패치 지점 결선 (place_order 직전 2곳)**

`main_swarm.py:2446` (`res = await self.broker.place_order(od)`) **직전**에 삽입:
```python
            od, _nxt_skip = await self._finalize_kr_order_for_session(od, get_current_session())
            if _nxt_skip:
                await self._emit({"type":"trade_failed", "message": f"⚠️ {_nxt_skip}"})
                return
```
`main_swarm.py:4194` (`res = ""` retry 루프 시작) **직전**에 삽입(들여쓰기 해당 블록에 맞춤):
```python
                    od, _nxt_skip = await self._finalize_kr_order_for_session(od, get_current_session())
                    if _nxt_skip:
                        await self._emit({"type":"trade_failed", "message": f"⚠️ {_nxt_skip}"})
                        continue   # 다음 주문으로 (조용히 누락하지 않고 사유 발화)
```
> 주의: 4194 지점의 `continue`/`return` 은 그 루프 구조에 맞춰 결정. 해당 함수가 주문 리스트를 순회하면 `continue`, 단일 주문이면 `return`. Read 로 확인 후 적용.

- [ ] **Step 6: 개장 트리거·라이브 세션에 NXT 추가**

`main_swarm.py:2902-2903` 를 교체:
```python
    _MARKET_OPEN_SESSIONS = ("KR_PRE_MARKET", "KR_TRADING", "KR_AFTER_MARKET", "US_TRADING")
    _LIVE_SESSIONS        = ("KR_PRE_MARKET", "KR_TRADING", "KR_AFTER_MARKET", "US_TRADING")
```

- [ ] **Step 7: 시간외 능력감지·플래그 스킵**

`main_swarm.py`의 메인 사이클 루프(`ArquantOrchestrator`, line 3007 `session = get_current_session()` 부근)에서 사이클 진입 직후, 시간외 세션이면 스킵 조건을 평가하는 가드를 추가. 다음 헬퍼를 **`_ExecutionMixin`** 에 추가(상속으로 루프에서 `self._extended_hours_blocked(...)` 호출 가능):
```python
    def _extended_hours_blocked(self, session) -> Optional[str]:
        """시간외 세션을 건너뛸 사유. None 이면 진행."""
        if not is_kr_extended_hours(session):
            return None
        if not runtime.get("ENABLE_NXT_EXTENDED_HOURS", True):
            return "NXT 시간외 매매 비활성(마스터 OFF)"
        if session == "KR_PRE_MARKET" and not runtime.get("ENABLE_NXT_PRE_MARKET", True):
            return "프리마켓 비활성"
        if session == "KR_AFTER_MARKET" and not runtime.get("ENABLE_NXT_AFTER_MARKET", True):
            return "애프터마켓 비활성"
        if self.broker.nxt_supported() is False:
            return "이 계정은 NXT 미지원(모의 등) — 시간외 스킵"
        return None
```
그리고 사이클 루프에서 `session` 확정 직후, 분석/매매를 돌리기 전에:
```python
                _xh_block = self._extended_hours_blocked(session)
                if _xh_block:
                    await self._set_status("OFF_HOURS", f"시간외 스킵 — {_xh_block} ({_now_kst().strftime('%H:%M')} KST)")
                    await asyncio.sleep(30)
                    continue
```
> Read 로 루프 구조(들여쓰기·변수명 `session`)를 확인한 뒤, `market_open`/분석 트리거 평가보다 **앞**에 둔다. 뉴스 수집은 별도 경로이므로 영향 없음.

- [ ] **Step 8: 회귀 + 신규 통과**

Run: `python3.11 -m pytest tests/test_finalize_kr_order_for_session.py tests/test_cheap_fallback_guard.py tests/test_poll_fills.py -q`
Expected: PASS — 신규 통과 + 기존 회귀 없음.

- [ ] **Step 9: Checkpoint** — 통과 확인.

---

## Task 11: 라이브 검증 항목 (코드 아님 — 실증 후 보강)

> 이 태스크는 라이브 KIS(실거래 계정 hh09080)에서만 확정 가능한 동작을 점검한다. 사장 입회/승인 하에 **소액·관측** 위주로 진행하고, 발견된 사실을 본 plan/스펙에 반영한다. 추측으로 코드를 박지 말 것.

- [ ] **Step 1: NXT 펜딩 주문 취소 TR 확인**

라이브에서 NXT 지정가 매수 1주를 멀리 떨어진 가격으로 넣고:
- `kr_pending_orders()`(`TTTC0084R`)가 그 NXT 주문을 반환하는지, 행에 거래소 식별 필드(`EXCG_ID_DVSN_CD` 등)가 붙는지 확인.
- `kr_cancel()`(`TTTC0803U`)로 취소 성공하는지 확인. **실패 시**: NXT 취소 전용 TR(예: `TTTC0013U` 계열) 필요 — KIS 포털 `order-rvsecncl` 문서/샘플 재확인 후 `kr_cancel` 에 exchange 분기 추가(별도 후속 태스크).

- [ ] **Step 2: 매수가능/매도가능 조회의 NXT 영향 확인**

`kr_psbl_order`(`TTTC8908R`)·`kr_psbl_sell_qty`(`TTTC8408R`)가 NXT 주문 사이징에 그대로 쓰여도 되는지(계좌 단위 예수금/보유는 거래소 무관) 확인. 증거금·거래소별 차이가 없으면 KRX 수치 그대로 사용(보수적). 차이 발견 시 EXCG 파라미터 추가.

- [ ] **Step 3: 모의서버 NXT 응답 시그니처 확인**

모의 계정(hh0908)에서 NXT 주문 1회 발사 → 실제 거부 메시지(`msg1`)를 확인하고, Task 9의 `_NXT_UNSUPPORTED_HINTS` 가 그 문구를 잡는지 검증. 안 잡으면 힌트 문자열을 실제 메시지에 맞춰 보강.

- [ ] **Step 4: NXT 체결의 통합 잔고 반영 확인**

NXT 소액 체결 후 `_poll_fills_until_confirmed`(보유 diff 폴링)가 체결을 잡는지(= `inquire-balance`/`kr_holdings` 가 NXT 체결을 포함하는지) 확인. 누락 시 통합 체결조회(`inquire-ccnl` total/nxt) 경로 추가(별도 후속 태스크).

- [ ] **Step 5: 발견사항 문서화** — 확인된 사실을 스펙 섹션 I 에 반영하고, 후속 태스크가 필요하면 plan 에 추가.

---

## Task 12: 전체 회귀 + 배포 (사장 승인)

- [ ] **Step 1: 전체 스위트**

Run: `python3.11 -m pytest -q`
Expected: PASS — 기존 + 신규 전부 그린. 실패 시 회귀 원인 추적(KRX 경로 값 동일성·세션 리터럴 치환 의미).

- [ ] **Step 2: 신규 테스트 집계 확인**

Run: `python3.11 -m pytest tests/test_nxt_session_schedule.py tests/test_kr_session_helpers.py tests/test_kr_order_exchange_routing.py tests/test_nxt_limit_pricing.py tests/test_nxt_capability_fallback.py tests/test_is_market_session_now_nxt.py tests/test_kr_last_price_market_param.py tests/test_nxt_config_flags.py tests/test_finalize_kr_order_for_session.py -v`
Expected: 모두 PASS.

- [ ] **Step 3: 배포 안내 (사장 승인 후)**

사장에게 보고: "전체 N✓ 통과. NXT 시간외 매매 배포 준비 완료. `sudo systemctl restart arquant.service` 로 반영하시겠습니까? 첫 시간외 세션(다음 08:00 프리마켓 또는 15:50 애프터마켓)에 Task 11 라이브 검증을 함께 관측 권장."
> 재시작은 위험·되돌리기 어려운 작업 — 사장이 직접 실행하거나 명시 승인 시에만. 능력감지·런타임 토글이 안전망.

- [ ] **Step 4: Checkpoint** — 전체 그린 + 사장 배포 결정 확인.

---

## Self-Review 결과 (작성자 점검)

- **스펙 커버리지:** 스펙 A→Task1, B→Task2·3, C→Task8, D→Task6·7·10, E→Task9, F→Task4, G→Task10(skip 사유 발화), H→Task5, I→Task11, J(테스트)→각 태스크. 누락 없음.
- **타입 일관성:** `kr_exchange_for_session`/`is_kr_extended_hours`(Task2) ↔ Task10 사용 일치. `compute_nxt_limit_price(last, side=, slippage_pct=)`(Task7) ↔ Task10 호출 일치. `OrderDraft.exchange`(Task8) ↔ `_finalize_kr_order_for_session`(Task10) 일치. `_note_nxt_result`(Task8 스텁→Task9 본구현) 일치.
- **플레이스홀더:** Task3·Task10의 일부 디스패치 지점은 "Read 후 적용"으로 표기 — 대상 파일이 4000+줄이라 정확한 줄 내용 확인이 필요한 곳에 한해 grep 타깃·삽입 코드·들여쓰기 주의를 명시했다(맹목 치환 금지).
