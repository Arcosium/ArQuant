# 넥스트레이드(NXT) 시간외 매매 강화 — 설계

작성일: 2026-06-08
근거: 사장 지시 — "KIS API가 넥스트레이드 마켓도 지원하니, 프리마켓·애프터마켓에서 매매할 수 있도록 로직을 강화."

## 배경 — 확인된 사실

KIS Open API는 2025년 출범한 대체거래소 **넥스트레이드(NXT)**를 지원하며, 주문 API가 이를 위해 개편됐다(공식 레포 `koreainvestment/open-trading-api`, `examples_llm/domestic_stock/order_cash/order_cash.py`, 작성 2025-01-12 확인):

- **신 통합 주문 TR**: 매수 `TTTC0012U` / 매도 `TTTC0011U` (모의 `VTTC0012U`/`VTTC0011U`). `EXCG_ID_DVSN_CD`(거래소ID구분코드, `KRX`/`NXT`/`SOR`)가 **필수** 파라미터.
- **구 TR** `TTTC0802U`(매수)/`TTTC0801U`(매도)는 `EXCG_ID_DVSN_CD` 없는 **KRX 전용** 경로.
- **시세 분기**: `FID_COND_MRKT_DIV_CODE` = `J`(KRX) / `NX`(NXT) / `UN`(통합).

현재 ArQuant 상태:
- `infra/kis_broker.py`의 `kr_buy`(line 421)/`kr_sell`(line 494)은 **구 TR을 `EXCG_ID_DVSN_CD` 없이** 호출 → 사실상 KRX 정규장 전용.
- `kr_last_price`(line 691)는 `FID_COND_MRKT_DIV_CODE="J"`(KRX) 고정.
- `main_swarm.py`의 세션은 순수 시간창 기반: `KR_TRADING`(09:00–15:30) / `KR_CLOSE_REVIEW`(15:35–15:50) / `US_TRADING`(22:30–05:00) / `OFF_HOURS`. 프리장은 2026-06-03 사장 지시로 폐지(당일 거래량 검증 불가한 분석전용 구간이라 무의미했음).
- 시간외 거래 가능한 시장이 없으니 NXT 매매·시세·체결·평가곡선 어디에도 NXT 경로가 없다.

### 넥스트레이드 운영시간·호가 제약 (확정)

| 구간 | 시간(KST) | 가능 호가 |
|---|---|---|
| 프리마켓 | 08:00–08:50 | **지정가만** (일반/최유리/최우선), 시장가 ✕ |
| 메인마켓 | 09:00–15:20 | 지정가·시장가·IOC/FOK·중간가·스톱지정가 |
| 애프터(시가단일가) | 15:30–15:40 | 일반 지정가만 (단일가) |
| 애프터(연속) | 15:40–20:00 | 지정가만 (일반/최유리/최우선) |

→ **프리/애프터마켓은 지정가 한정**. 현재 무가격 시장가 폴백(`ORD_DVSN="01"`)을 그대로 쓰면 거부되므로 지정가 산정이 필수다.

## 목표

- KR 프리마켓(08:00–08:50)·애프터마켓(연속, 15:50–20:00)을 정식 분석·매매 세션으로 추가.
- 시간외 세션에서 정규장과 **동일하게 매수+매도 풀매매**(사장 결정).
- 정규장(09:00–15:30)은 **KRX 구 TR 경로 그대로**(거동 보존), 시간외만 **NXT 신 TR**(명시 라우팅, 사장 결정).
- 모의 계정의 NXT 미지원 가능성을 **능력감지 + graceful 폴백**으로 흡수(사장 결정).
- "KR/US 비대칭 버그"류 재발 방지 — 세션→거래소 결정을 **한 곳**으로 모음.

## 비목표 (YAGNI)

- **US 프리/애프터마켓**: KIS 해외 시간외는 별개 메커니즘 → 이번 범위 제외(향후 확장).
- **SOR(Smart Order Routing)**: 사장이 명시 라우팅 선택 → 미구현(향후 옵션).
- **NXT 시가단일가(15:30–15:40)**: 단일가 복잡성·`KR_CLOSE_REVIEW`(15:35–15:50) 충돌 회피 위해 애프터마켓을 **연속지정가 15:50–20:00**만 잡고 제외.
- **메인마켓 NXT 라우팅·SOR 최적체결**: 정규장은 KRX 유지(거동 보존).

## 결정된 기본값 (사장 "진행" — 추천값 채택)

- 애프터마켓 매매 시작 **15:50**.
- 지정가 슬리피지 밴드 **0.5%**(`EXT_HOURS_LIMIT_SLIPPAGE_PCT`).
- 마스터 플래그 `ENABLE_NXT_EXTENDED_HOURS` **기본 True**. 안전망: 런타임 토글(재시작 불필요)·능력감지·graceful 폴백·라이브 검증 항목(섹션 I) 확인.

---

## A. 세션 모델 (`main_swarm.py`)

`SCHEDULE`에 NXT 시간외 2개 세션 추가. 정규장·마감리뷰는 변경 없음.

```python
SCHEDULE = {
    "kr_pre_market":   {"start":(8,0),  "end":(8,50),  "desc":"NXT 프리마켓"},
    "kr_trading":      {"start":(9,0),  "end":(15,30), "desc":"KRX 장중"},
    "kr_close_review": {"start":(15,35),"end":(15,50), "desc":"장 마감 리뷰"},
    "kr_after_market": {"start":(15,50),"end":(20,0),  "desc":"NXT 애프터마켓"},
    "us_trading":      {"start":(22,30),"end":(5,0),   "desc":"US 장중 (야간)"},
}
```

`get_current_session()`에 두 세션 분기 추가:
```python
def get_current_session():
    if _in_schedule("kr_pre_market"):   return "KR_PRE_MARKET"
    if _in_schedule("kr_trading"):      return "KR_TRADING"
    if _in_schedule("kr_close_review"): return "KR_CLOSE_REVIEW"
    if _in_schedule("kr_after_market"): return "KR_AFTER_MARKET"
    if _in_schedule("us_trading"):      return "US_TRADING"
    return "OFF_HOURS"
```

> **2026-06-03 프리장 폐지와의 관계**: 폐지된 것은 "거래 가능한 시장이 없는 개장 전 분석전용 구간"이었다. 본 `KR_PRE_MARKET`은 실제 거래가 일어나는 NXT 프리마켓(08:00–08:50)으로 성격이 다르다 — 분석이 실거래로 직결되므로 부활 근거가 있다. 코드상 `KR_PRE_MARKET` 문자열을 참조하는 휴면 분기들(`_post_manager_session_hint` 등)이 이미 존재해 자연스럽게 재활성된다.

## B. 비대칭 버그 방어 — 세션→거래소 중앙 헬퍼 (`main_swarm.py`)

현재 `session in ("KR_TRADING","KR_PRE_MARKET","KR_CLOSE_REVIEW")` 리터럴이 약 12곳에 흩어져 있다(lines 1195, 1242, 1372, 1871, 1926, 1946, 2163, 2189, 2246, 2413, 2435 등). 여기 `KR_AFTER_MARKET`를 일일이 추가하면 한 곳만 빠뜨려도 "KR엔 되는데 시간외엔 안 됨"식 비대칭 버그가 난다. → **한 곳으로 집약**:

```python
KR_SESSIONS          = ("KR_TRADING", "KR_PRE_MARKET", "KR_AFTER_MARKET", "KR_CLOSE_REVIEW")
KR_TRADABLE_SESSIONS = ("KR_TRADING", "KR_PRE_MARKET", "KR_AFTER_MARKET")  # 리뷰는 매매 X

def is_kr_session(s: str) -> bool:        return s in KR_SESSIONS
def is_kr_tradable(s: str) -> bool:       return s in KR_TRADABLE_SESSIONS
def is_kr_extended_hours(s: str) -> bool: return s in ("KR_PRE_MARKET", "KR_AFTER_MARKET")
def kr_exchange_for_session(s: str) -> str:  # "KRX" | "NXT"
    return "NXT" if is_kr_extended_hours(s) else "KRX"
```

흩어진 리터럴을 이 헬퍼 호출로 치환한다. 매수 가능 세션에는 `KR_PRE_MARKET`·`KR_AFTER_MARKET`가 포함되도록 의미별로 `is_kr_tradable`(매매 가능) vs `is_kr_session`(KR 보유종목 평가 대상) 구분. 기존 로직 의미를 1:1로 보존하며 치환하는 게 핵심(과교정 금지).

## C. 주문 경로 (`infra/kis_broker.py` — `kr_buy`/`kr_sell`에 exchange 인자)

```python
# (side, exchange) → tr_id.  KRX = 검증된 구 TR 그대로 (거동 보존, EXCG 미포함)
_KR_ORD_TR = {
    ("buy",  "KRX"): "TTTC0802U", ("sell", "KRX"): "TTTC0801U",
    ("buy",  "NXT"): "TTTC0012U", ("sell", "NXT"): "TTTC0011U",
}

async def kr_buy(self, code: str, qty: int, price: int = 0, exchange: str = "KRX") -> str:
    ...
async def kr_sell(self, code: str, qty: int, price: int = 0, exchange: str = "KRX") -> str:
    ...
```

- **KRX 경로**(exchange="KRX"): body·TR·헤더 전부 현행 그대로 — `EXCG_ID_DVSN_CD` 미포함, 구 TR. **바이트 단위 동일** → 정규장 회귀 위험 0.
- **NXT 경로**(exchange="NXT"): 신 TR 사용 + body에 다음 추가/강제
  - `"EXCG_ID_DVSN_CD": "NXT"`
  - `"ORD_DVSN": "00"` — 지정가 강제(시간외 시장가 불가)
  - 매도 시 `"SLL_TYPE": "01"`(일반매도), `"CNDT_PRIC": ""`
  - `price > 0` 필수 — 0이면 주문 거부 대신 호출부(섹션 D)에서 지정가 산정 후 전달. 산정 실패 시 주문 스킵 + 사유 명시(섹션 G).
- 모의 변환: 기존 `_mock_tr`(line 305)이 `tr_id[0] in ("T","J","C")`면 `"V"+tr_id[1:]` → `TTTC0012U→VTTC0012U` 자동 처리. 추가 작업 불필요.
- 매도 사전 펜딩 취소 로직(`kr_sell` line 500~)은 NXT에도 적용 — `kr_pending_orders`/`kr_cancel`의 NXT 처리(섹션 I-1)에 의존.

## D. 지정가 산정 (`infra/kis_broker.py` + 호출부)

시간외엔 모든 주문에 가격이 필요하다.

1. **시세 확장**: `kr_last_price(self, code, market="J")` — `market` ∈ `J`/`NX`/`UN`을 `FID_COND_MRKT_DIV_CODE`로 전달. 시간외 호출부는 `market="NX"`(NXT) 사용.
2. **호가단위 반올림 헬퍼 신설**(현재 없음 — KIS는 호가단위 어긋난 지정가 거부):
   ```python
   def kr_tick_size(price: float) -> int:
       # 2023~ KRX 호가단위: <2천=1, <5천=5, <2만=10, <5만=50, <20만=100,
       #                      <50만=500, ≥50만=1000  (코스피·코스닥 공통)
       ...
   def round_to_tick(price: float) -> int:
       # 가장 가까운 유효 호가로 반올림(nearest tick). 밴드(±슬리피지)가 이미 공격성을
       # 부여하므로 방향성 라운딩 불필요 — 중립적 nearest 로 통일해 매수/매도 대칭 유지.
   ```
3. **지정가 = NXT 현재가 ± 슬리피지 밴드**(체결확률↑, 슬리피지 상한):
   - 매수 = `round_to_tick(last × (1 + EXT_HOURS_LIMIT_SLIPPAGE_PCT/100))`
   - 매도 = `round_to_tick(last × (1 − EXT_HOURS_LIMIT_SLIPPAGE_PCT/100))`
4. **시세 결손 폴백**: NXT(`NX`) 무응답 → 통합(`UN`) → KRX(`J`) 순. 그래도 없으면 **주문 스킵**(시장가 대체 불가) + 사유 명시 보고.

## E. 모의 능력감지 + graceful 폴백 (`infra/kis_broker.py` + 오케스트레이터)

- 브로커(계정) 인스턴스에 `_nxt_supported: Optional[bool] = None` 캐시.
- NXT 주문 응답 검사: `rt_cd != "0"`이고 미지원 시그니처(모의서버 "지원하지 않는" 류 msg_cd/msg1)면 `_nxt_supported = False`로 고정하고 **1회만** 경고 로그. 성공 시 `True`.
- `def nxt_supported(self) -> Optional[bool]: return self._nxt_supported` 노출.
- 오케스트레이터: 시간외 세션 진입 시 `broker.nxt_supported() is False`인 계정은 매매 사이클 **스킵**(뉴스 수집만, `OFF_HOURS` 유사 처리) → 모의에서 거부 주문 반복·로그 노이즈 없음. `None`(미탐)이면 1회 시도 허용해 자가 판정.

## F. 평가곡선·휴장 게이팅 (`main_swarm.py`)

- `is_market_session_now()`(line 397)에 NXT 시간외 창 추가 → 시간외 거래 시 평가곡선 포인트가 정상 기록·표시(현재는 정규장만 True라 시간외 구간 공백):
  ```python
  if 8*60 <= t < 8*60+50 or 15*60+50 <= t < 20*60:   # NXT 프리/애프터
      return not is_kr_weekend(dt) and not _market_day_verified_closed("KR", dt)
  ```
- 휴장 판정: NXT는 KRX와 동일 거래일 → 기존 `is_kr_weekend` + `_market_day_verified_closed("KR")` 재사용. **하드코딩 휴장일 추가 없음**(2026-06-03 정책 준수).

## G. 주문 누락 금지 룰과의 정합

"실주문 절대 조용히 누락 금지"(사장 룰)와 충돌하지 않도록:
- 시간외 지정가 산정 실패 → 주문을 **조용히 누락하지 않고**, 사이클 보고 메시지에 "NXT 시세 결손으로 OOO 주문 보류(시장가 대체 불가)"를 명시.
- NXT 주문 거부(능력감지 트립 포함) → 사유를 메시지에 직접 담아 보고(로그 포인터로 떠넘기지 않음).

## H. config·runtime 파라미터 (`config.py`)

| 키 | 기본 | 의미 |
|---|---|---|
| `ENABLE_NXT_EXTENDED_HOURS` | True | 마스터 스위치 |
| `ENABLE_NXT_PRE_MARKET` | True | 프리마켓(08:00–08:50) on/off |
| `ENABLE_NXT_AFTER_MARKET` | True | 애프터마켓(15:50–20:00) on/off |
| `EXT_HOURS_LIMIT_SLIPPAGE_PCT` | 0.5 | 지정가 밴드(%) |

- 4개 모두 런타임 오버라이드 리스트(`config.py:257` 인근 `RUNTIME_OVERRIDABLE`)에 등록 → 재시작 없이 대시보드 '전략' 탭에서 조정.
- 마스터/프리/애프터 토글이 모두 꺼지거나 해당 세션이 비활성이면 오케스트레이터는 그 세션을 `OFF_HOURS`처럼 취급(뉴스 수집만).

## I. 라이브 검증 필요 항목 (추측 금지 — 구현 중 실증)

설계 단계에서 단정 못 하는, 라이브 KIS에서 실증해야 할 동작. 구현 계획에 검증 단계로 포함한다.

1. **NXT 펜딩 주문 취소 TR** — KRX 취소는 `TTTC0803U`. NXT 주문 취소가 별도 TR인지(예: `TTTC0013U` 계열) 또는 `EXCG_ID_DVSN_CD`만 추가하면 되는지 확인. `kr_pending_orders`(`TTTC0084R`)가 NXT 주문도 반환하는지, NXT 주문엔 `EXCG_ID_DVSN_CD` 필드가 붙는지 확인.
2. **매수가능(`TTTC8908R`)·매도가능(`TTTC8408R`) 조회**에 NXT/EXCG 파라미터가 필요한지(증거금·거래소별 차이 가능성). 불필요하면 계좌 단위 KRX 수치를 보수적으로 사용.
3. **모의서버 NXT 주문 지원 여부** → 섹션 E 능력감지로 흡수(라이브에서 모의 첫 주문 응답 확인).
4. **NXT 체결의 보유 diff 반영** — `_poll_fills_until_confirmed`(line 2502)는 ccnl 엔드포인트가 아닌 보유 diff 폴링이라, NXT 체결이 통합 잔고(`inquire-balance`)에 반영되면 그대로 포착된다(전제: 통합 잔고가 NXT 체결 포함). 확인 필요.

## J. 테스트 (TDD — 모두 모의 KIS 응답, 라이브 호출 없음)

| 테스트 파일(신규) | 검증 |
|---|---|
| `test_nxt_session_schedule.py` | 시각→세션 매핑(프리/정규/리뷰/애프터/US/OFF), 평일/주말/휴장 게이팅 |
| `test_kr_session_helpers.py` | `is_kr_session`/`is_kr_tradable`/`is_kr_extended_hours`/`kr_exchange_for_session` |
| `test_kr_order_exchange_routing.py` | KRX 경로 body·TR **무변경(회귀)**; NXT 경로 신 TR + `EXCG_ID_DVSN_CD=NXT` + `ORD_DVSN=00` + price>0 필수 |
| `test_nxt_limit_pricing.py` | NXT 현재가 + 슬리피지 밴드 → 지정가, 호가단위 반올림, 시세결손 폴백→스킵+사유 |
| `test_nxt_capability_fallback.py` | NXT 미지원 응답→`_nxt_supported=False` 고정, 시간외 사이클 스킵, 1회 로그 |
| `test_is_market_session_now_nxt.py` | NXT 시간외 창 평일 True·주말/휴장 False |
| `test_kr_last_price_market_param.py` | `market` 인자→`FID_COND_MRKT_DIV_CODE` 분기(J/NX/UN) |

## 통합 지점 요약 (변경 파일)

- `main_swarm.py` — SCHEDULE, `get_current_session`, 세션 헬퍼 신설·리터럴 치환, `is_market_session_now`, 오케스트레이터 `_MARKET_OPEN_SESSIONS`/`_LIVE_SESSIONS`에 NXT 세션 추가, 주문 디스패치 시 `exchange=kr_exchange_for_session(session)` 전달, 시간외 지정가 산정 호출.
- `infra/kis_broker.py` — `_KR_ORD_TR`, `kr_buy`/`kr_sell` exchange 인자, NXT body, `kr_last_price(market=)`, 틱사이즈 헬퍼, `_nxt_supported`/`nxt_supported()`, 펜딩 취소 NXT 처리(I-1).
- `config.py` — 4개 신규 플래그 + `RUNTIME_OVERRIDABLE` 등록.
- `tests/` — 위 7개 신규 파일.
