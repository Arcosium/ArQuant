# 멀티 자산슬리브 + 매도 종합 + ops tier 구분 — 설계

- 날짜: 2026-06-09
- 상태: 승인됨 (사장 "전부 반영해줘")
- 관련 사전 작업: `2026-06-08-bond-etf-bond-manager.md`(채권 트랙 신설), `2026-06-09-remove-strategy-presets.md`(단일 STRATEGY_DEFAULTS)

## 배경 / 문제

2026-06-08 채권 ETF 자동매매(채권운용실장)가 **독립 트랙**으로 들어왔다. 사장 지시 7건은
이를 (a) 원자재까지 일반화하고, (b) 매도 의사결정을 사람 회의처럼 종합하고, (c) ops
파라미터 권한을 사이클/주간으로 명확히 가르는 것이다.

현재 결함:
- 채권 매도가 오직 "목표비중 밴드 초과"로만 발생(`size_bond_action`→`skip`). **비중%가 맞으면
  신호가 악화돼도 채권을 팔지 않는다.**
- 채권은 사후관리실장 매도 트랙에서 **제외**돼 있어, 주식 매도와 채권 매도가 한 자리에서
  종합되지 않는다.
- ops 파라미터의 "매 사이클 조정 가능 vs 토요일 백테스트 후만" 구분이 **코드에 없다**
  (유일한 게이트 `OPS_PROTECTED_KEYS`=완전봉인 + LLM 톤 차이뿐).

## 결정 사항 (사장 확정)

1. **공통 자산슬리브 엔진** — 채권·원자재를 하나의 재사용 모듈로 일반화(복제 아님).
2. **ETF 종목은 본 설계가 조사·선정** — 검증된 코드만 화이트리스트에 등재.
3. **사후관리실장이 매도 최종 종합권** — 슬리브 매도 판단을 받아 주식+슬리브를 통합 결정.
4. **키별 cycle/weekly tier 강제 분류** — 코드로 enforced.

---

## 1. 공통 자산슬리브 엔진 (요청 #4 기반 아키텍처)

### 1.1 새 모듈 `infra/asset_sleeves.py`

`SleeveSpec` 한 개가 한 트랙(채권/원자재)을 완전 기술한다.

```python
@dataclass(frozen=True)
class SleeveSpec:
    key: str                  # "bond" | "commodity"  (thesis 파일·내부 키)
    manager_name: str         # "채권운용실장" | "원자재운용실장"  (라우팅 키, 한글)
    role: str                 # "bond_manager" | "commodity_manager"  (모델/토큰)
    macro_keyword: str        # "채권" | "원자재"  (매크로% 파싱)
    decision_keyword: str     # "채권결정" | "원자재결정"  (LLM 마지막 줄)
    pool_kr: list[tuple]      # (code, name, duration, kind, fx)
    pool_us: list[tuple]
    enable_key: str           # "ENABLE_BOND_ETF" | "ENABLE_COMMODITY_ETF"
    target_max_key: str       # "BOND_TARGET_MAX_PCT" | ...
    band_key: str             # "BOND_REBALANCE_BAND_PCT" | ...
    per_cycle_key: str        # "BOND_PER_CYCLE_RATIO" | ...
```

`SLEEVES = [BOND_SLEEVE, COMMODITY_SLEEVE]` 모듈 레벨 레지스트리.

### 1.2 기존 채권 함수 → 범용 승격

`main_swarm.py`의 채권 전용 함수를 `asset_sleeves.py`로 옮기고 `spec` 인자를 받게 한다.
이름은 채권 색을 빼고 일반화(채권 동작은 100% 보존):

| 기존 (main_swarm) | 신규 (asset_sleeves) |
|---|---|
| `_parse_macro_bond_pct(text)` | `parse_macro_sleeve_pct(text, keyword)` |
| `current_bond_weight(...)` | `current_sleeve_weight(holdings, total, pool_codes, usdkrw)` |
| `size_bond_action(...)` | `size_sleeve_action(rec_pct, cur_pct, total, max_pct, band)` |
| `cap_bond_buy_notional(...)` | `cap_sleeve_buy_notional(...)` |
| `assemble_bond_orders(...)` | `assemble_sleeve_orders(spec, action, ...)` |
| `bond_etf_pool_for_session(...)` | `sleeve_pool_for_session(spec, session, us_allowed)` |
| `_parse_bond_decisions(text, codes)` | `parse_sleeve_decisions(text, keyword, codes)` |
| `split_bond_holdings` / `all_bond_pool_codes` | `split_sleeve_holdings(holdings, all_sleeve_codes)` / `all_sleeve_pool_codes()` (전 슬리브 합집합) |

기존 채권 호출부는 `BOND_SLEEVE`를 넘기는 thin wrapper 또는 직접 신규 함수 호출로 교체.
`order["reason"]`은 `f"{spec.manager_name} 자산배분"`.

### 1.3 thesis 일반화 `infra/sleeve_thesis.py`

`infra/bond_thesis.py`를 `sleeve_thesis.py`로 일반화. 파일 경로
`data/<uid>/<sleeve.key>_thesis.json`. 공개 함수에 `sleeve_key` 인자 추가:
`record(uid, key, code, thesis)`, `get_all(uid, key)`, `sync_with_holdings(uid, key, codes)`.
`format_thesis_reminder`는 `agents/specialists.py`에서 슬리브 공용 포맷터로 통합
(아래 §3 참조). 기존 `bond_thesis.py`는 제거하고 호출부를 `sleeve_thesis`로 일괄 교체.

### 1.4 사이클 스테이지 `_cyc_stage_sleeves`

`main_swarm._cyc_stage_bonds`(line ~4436) → `_cyc_stage_sleeves`로 교체. 활성 슬리브를
순회:

```
for spec in SLEEVES:
    if not runtime.get(spec.enable_key, uid): continue
    pool = sleeve_pool_for_session(spec, session, us_allowed)
    if not pool: continue
    rec_pct = parse_macro_sleeve_pct(macro_report, spec.macro_keyword)
    cur_pct = current_sleeve_weight(sleeve_holdings, total_eval, pool_codes, usdkrw)
    action, notional = size_sleeve_action(rec_pct, cur_pct, total_eval, max_pct, band)
    # 매니저 LLM 호출 (3팀장 리포트 + thesis 상기 주입 — §2)
    decisions = parse_sleeve_decisions(resp, spec.decision_keyword, pool_codes)
    # 매수 주문은 즉시 조립(자산배분), 매도 제안은 cyc.sleeve_sell_proposals[spec.key]에 적재(§2)
```

산출물: `cyc.sleeve_buy_orders`(전 슬리브 매수), `cyc.sleeve_sell_proposals`(슬리브별 매도 제안),
`cyc.sleeve_price_map`, `cyc.sleeve_holdings_by_key`.

---

## 2. 매도 흐름 재설계 (요청 #1 + 결정 #3)

### 2.1 파이프라인 순서 변경

`_cyc_stage_sleeves`를 `_cyc_stage_finalize_sell` **앞**으로 옮긴다.

```
④ data_quant  (quant_report 생성)
⑤ sleeves     ← NEW 위치: 채권·원자재 매니저가 매도 판단 (현재 ⑥에서 이동)
⑥ finalize_sell  ← 사후관리실장이 주식+슬리브 매도 종합
⑦ build_orders → ⑧ risk → ⑨ execute → ⑩ report
```

### 2.2 슬리브 매니저: 매크로+뉴스 청취 + 신호 기반 매도

**사장 결정 2026-06-09 (구현 중 재확인): 채권·원자재는 주식식 계량분석(퀀트)이 부적합하므로
제외한다.** 채권=금리 전략(가격=f(금리·듀레이션)), 원자재=실물 매크로 — RSI·외인수급·신고가
같은 주식 팩터는 노이즈이고, 주식 계량분석팀장에게 평가시키면 부적합한 코멘트를 환각할 위험이
크다. 원자재 추세/모멘텀 판단은 매니저 LLM 자체 추론에 맡긴다.

슬리브 매니저 프롬프트에 **글로벌리서치팀장 매크로 + 마켓센티먼트팀장 뉴스** 리포트만
주입한다(계량분석 리포트 제외; 현재는 매크로만). 프롬프트 지시:

> 비중이 목표 밴드 안이어도, 금리/매크로/뉴스 신호가 악화됐으면 매도를 판단하십시오.
> "%가 맞으니 보류"는 금지 — 신호로 판단합니다.

`size_sleeve_action`이 `skip`(밴드 내)을 반환해도 매니저 LLM은 여전히 호출되어 **보유 종목별
매도/보유 판단**을 낸다. 즉 `action`은 **매수 예산 계산**에만 쓰고, **매도 평가는 항상 수행**.

### 2.3 사후관리실장 통합 (최종 종합권)

`_cyc_stage_finalize_sell`에서:
- 입력에 **슬리브 보유 + 슬리브 매니저의 매도 제안 요약**을 추가 주입.
- 사후관리실장 `매도결정` 라인이 **주식 + 슬리브 코드 모두** 포함하도록 프롬프트 확장.
- 프롬프트 명시: *"채권·원자재 매도 제안을 검토해 주식 매도와 함께 종합 결정하십시오.
  비중%가 적정하다는 이유만으로 무조건 보류하지 마십시오 — 신호로 판단."*
- 채권/원자재는 **여전히 사후관리실장 입력에서 자동 익절/손절(`_build_orders`) 대상은
  아님** — 매도는 오직 사후관리실장의 명시적 `매도결정`으로만(슬리브 매수는 자산배분이라
  슬리브가 집행). 즉:
  - 슬리브 **매수** 주문 = 슬리브 매니저 산출(`cyc.sleeve_buy_orders`) → build_orders 합류.
  - 슬리브 **매도** 주문 = 사후관리실장 `매도결정`의 슬리브 코드 → build_orders에서 조립.

### 2.4 가드레일

`agents/guardrails.py`의 채권 면제(집중도 상한=`*_TARGET_MAX_PCT`, 수량캡 면제,
`MAX_CYCLE_BUDGET_RATIO` 면제)를 **전 슬리브 코드 합집합**(`all_sleeve_pool_codes()`)으로
확장. 슬리브별 집중도 상한은 해당 슬리브 `target_max_key`.

---

## 3. 보유계획 일괄 상기 (요청 #2)

포트폴리오기획팀장이 매도 스테이지(⑤ 슬리브 / ⑥ 사후관리) **직전 한 번** "보유계획 상기"를
발화하고, 각 매니저 프롬프트에 해당 thesis를 주입:

- 사후관리실장 프롬프트 ← 주식 thesis (`position_thesis`)
- 채권운용실장 프롬프트 ← 채권 thesis (`sleeve_thesis`, key="bond")
- 원자재운용실장 프롬프트 ← 원자재 thesis (`sleeve_thesis`, key="commodity")

`agents/specialists.py`: 기존 `format_thesis_reminder`(주식)·`format_bond_thesis_reminder`를
**`format_sleeve_thesis_reminder(theses, holdings, now_iso, *, manager_name)`** 하나로
통합(주식=목표/손절 포함 버전, 슬리브=보유기간 버전은 인자로 분기). 대시보드에는 3개 슬리브를
아우르는 단일 포트폴리오기획팀장 발화 1건 emit.

---

## 4. 이름 변경: 운용전략실장 → 주식운용실장 (요청 #3)

- 라우팅 키이므로 전 구간 일괄: `main_swarm.py`(~41), `agents/specialists.py`(프롬프트 내 언급),
  `server/app.py`(roster·admin label), `server/static/index.html`(사이드바·placeholder),
  `infra/standing_directives.py`, `infra/error_log.py`, `tools/market_data.py`,
  `tools/gen_manual.js`, `arquant_mobile/.../DashViewModel.kt`, 테스트 9종(~10 assert/docstring).
- **내부 role 키 `chief_orchestrator`는 유지**(모델/토큰 매핑 불변 — 불필요한 위험 회피).
- 에이전트 객체 `name="주식운용실장"`, `agent_map` 키, @멘션 라우팅, 핸드오프 기본 타깃 모두 갱신.
- @멘션 placeholder 예시도 `@주식운용실장`으로.

## 5. 원자재운용실장 + 4분할 자산배분 (요청 #4)

- `config.MODEL_ASSIGNMENTS`·`AGENT_MAX_TOKENS`에 `commodity_manager` 추가
  (모델 `deepseek-v4-flash`, 토큰 2000 — 채권과 동일).
- `agents/specialists.py`: `create_commodity_manager(injection)` — 채권 매니저와 동형,
  성격="실물자산 매크로 전략가"(인플레·달러·지정학·수급으로 판단, 주식 퀀트 무관).
- `main_swarm`: `self.commodity_manager` 인스턴스화, `agent_map` 등록.
- 글로벌리서치팀장 매크로 출력 형식: `주식 X% / 채권 Y% / 원자재 W% / 현금 Z%`
  (specialists.py 프롬프트 §5 응답 형식·예시 갱신, 직전 권고 표기도 4분할).
- `parse_macro_sleeve_pct(text, "원자재")` 신설(범용 파서가 키워드로 처리).
- UI: 사이드바 FRONT에 채권운용실장 옆 원자재운용실장(`color:"#d97706"`, level:0) 추가;
  `server/app.py` roster·DashViewModel.kt 색상맵 추가.

## 6. 채권 자산군 확장 (요청 #5) — 검증된 코드

풀 태그 `(code, name, duration, kind, fx)`. `kind`∈{rate, govt, credit, tips},
`fx`∈{krw, hedged, exposed}. `duration`∈{short, mid, long, na}.

**`BOND_ETF_POOL_KR`** (기존 국고채 3종 유지 + 추가):
```
("153130","KODEX 단기채권","short","govt","krw")
("114260","KODEX 국고채3년","mid","govt","krw")
("148070","KOSEF 국고채10년","long","govt","krw")
("357870","TIGER CD금리투자KIS(합성)","short","rate","krw")    # CD 91일
("459580","KODEX CD금리액티브(합성)","short","rate","krw")     # 보수 0.02%
("273130","KODEX 종합채권(AA-이상)액티브","mid","credit","krw")
("451540","TIGER 종합채권(AA-이상)액티브","mid","credit","krw")
("458250","TIGER 미국채30년스트립액티브(합성H)","long","govt","hedged")  # 환헤지
```
**`BOND_ETF_POOL_US`** (기존 + 신용/물가):
```
("SHY","iShares 1-3Y Treasury","short","govt","exposed")
("IEF","iShares 7-10Y Treasury","mid","govt","exposed")
("TLT","iShares 20+Y Treasury","long","govt","exposed")
("LQD","iShares IG Corp Bond","mid","credit","exposed")
("HYG","iShares High Yield Corp","mid","credit","exposed")
("TIP","iShares TIPS","mid","tips","exposed")
```
환노출(exposed) 미국채는 US세션 풀(USD표시)이 자연 담당. 환헤지(hedged)는 KR 458250.

## 7. 원자재 자산군 (요청 #4·#5) — 검증된 코드

**`COMMODITY_ETF_POOL_KR`**:
```
("132030","KODEX 골드선물(H)","na","gold","hedged")
("261220","KODEX WTI원유선물(H)","na","oil","hedged")
("137610","TIGER 농산물선물Enhanced(H)","na","agri","hedged")
```
**`COMMODITY_ETF_POOL_US`**:
```
("GLD","SPDR Gold Shares","na","gold","exposed")
("USO","US Oil Fund","na","oil","exposed")
("DBA","Invesco Agriculture","na","agri","exposed")
("DBC","Invesco Commodity Index","na","broad","exposed")
```
(원자재는 듀레이션 개념 없음→`na`. `kind`로 종류 구분.)

## 8. 기본 ON (요청 #6)

`config.py` 모듈 상수와 `STRATEGY_DEFAULTS` 양쪽에서:
- `ENABLE_BOND_ETF = True` (현재 False)
- `ENABLE_COMMODITY_ETF = True` (신규)
신규 슬리브 파라미터 기본값: `COMMODITY_TARGET_MAX_PCT=0.20`, `COMMODITY_REBALANCE_BAND_PCT=0.03`,
`COMMODITY_PER_CYCLE_RATIO=0.10`. 모두 `STRATEGY_TUNABLE_KEYS`·`STRATEGY_KEY_META`·
`STRATEGY_KEY_EFFECT` 등재.

## 9. ops 사이클/주간 tier 강제 구분 (요청 #7 + 결정 #4)

### 9.1 메타데이터

`STRATEGY_KEY_META[key]`에 `"tier": "cycle" | "weekly"` 추가. 누락 키 기본값="cycle"
(보수적: 모르면 사이클 허용 — 단 분류는 전 키 명시).

**cycle tier (전술·반응형, 매 사이클 조정 가능):**
사이징(PER_ORDER_BUDGET_RATIO, PER_ORDER_BUDGET_OVERSHOOT, MAX_CYCLE_BUDGET_RATIO,
MIN_CASH_BUFFER), 리스크한도(CONSERVATIVE_MDD, CONSERVATIVE_STOCK_RATIO,
MAX_TRADES_PER_CYCLE, MAX_ORDER_QTY), 종목필터(MIN_QUANT_SCORE, MAX_BUY_VOLATILITY_PCT,
RSI_OVERBOUGHT_SKIP, MIN_ADX_FOR_BUY, REQUIRE_FOREIGN_NET_BUY, MAX_PRICE_EXTENSION_PCT),
매도룰(ENABLE_SELL_REBALANCE, TAKE_PROFIT_PCT, STOP_LOSS_PCT, TRIM_OVER_RATIO,
ALLOW_DAY_TRADING, MIN_HOLDING_DAYS_FOR_SELL), 레짐(MACRO_STOCK_GATE_ENABLED),
NXT(ENABLE_NXT_*, EXT_HOURS_LIMIT_SLIPPAGE_PCT), 공유(SHARE_*).

**weekly tier (구조·모델, 토요일 백테스트+실데이터 검증 후만):**
점수엔진(QIW_RSI/MACD/ADX/VWAP/VOL/MOM/CMF/FLOW/HIGH52, DW_QUANT/NEWS/MACRO),
사이징모델(POSITION_SIZING_MODE, SIZING_TILT_STRENGTH, SIZING_MAX_TILT),
유니버스(UNIVERSE_MIN_PRICE, UNIVERSE_MIN_TURNOVER, UNIVERSE_EXCLUDE_LEVERAGED),
MAX_BUY_NAMES, SCORECARD_WINDOW_DAYS, 슬리브 구조값(BOND/COMMODITY_TARGET_MAX_PCT,
*_REBALANCE_BAND_PCT, *_PER_CYCLE_RATIO), 마스터스위치(ENABLE_BOND_ETF, ENABLE_COMMODITY_ETF).

### 9.2 강제 (enforcement)

`infra/ops_param_clamp.py`에 `partition_by_tier(overrides, trigger) -> (apply, defer, notes)`:
- `trigger == "cycle"`: weekly-tier 키는 `defer`로 분리 → **신규 저장소
  `data/profiles/<uid>/weekly_deferred.json`**(`infra/weekly_defer_queue.py`,
  `enqueue/list/clear`)에 적재, `apply`는 cycle-tier만. (정책봉인용 `policy_approval_inbox`와
  별개 — defer는 사장 승인 없이 토요일 워커가 자동 재평가하므로 혼동 방지.)
- `trigger == "weekly"`: 전부 `apply`(백테스트가 이미 돈 컨텍스트).
- `trigger == "manual"`: 전부 `apply`(사장 권한, 기존과 동일).

OPS_PROTECTED_KEYS(ALLOW_US_STOCKS·ALLOW_DERIVATIVES·ENABLE_CHEAP_FALLBACK·
DETERMINISTIC_SCORING)는 tier와 무관하게 ops 자율 변경 불가(기존 게이트 유지) — META엔
일관성 위해 tier="weekly"로 표기하되 enforcement는 `partition_protected`가 먼저 처리.

`ops_support_worker._handle_param_tuning`에서 `partition_protected` **다음에**
`partition_by_tier`를 적용(정책봉인 → tier 순). rationale에 회부 사유 append.
`strategy_param_catalog_text()`에 각 키 tier 표기(`[사이클 조정 가능]`/`[토요일 검증 후]`)를
넣어 ops LLM이 사전 인지하게 한다.

### 9.3 토요일 적용

`infra/weekly_review.py`는 이미 백테스트(`_run_current_backtest`)를 돌려 주간 워커에 주입한다.
cycle 동안 `defer`된 weekly-tier 제안을 토요일 워커가 **백테스트 결과와 함께** 재평가해
적용하도록, 주간 directive에 "지난 주 보류된 구조 파라미터 제안 목록"을 포함한다.

---

## 테스트 전략 (TDD)

- **회귀 보호**: 기존 채권 테스트 9종(`test_bond_*.py`)은 슬리브 API로 마이그레이션하되
  **동일 동작을 핀**(채권=슬리브#1). 그린 유지가 리팩토링 안전망.
- **신규 테스트**:
  - `test_asset_sleeves.py` — `SleeveSpec` 범용 함수(weight/size/cap/assemble/parse) 채권·원자재 양쪽.
  - `test_sleeve_sell_synthesis.py` — 비중 밴드 내라도 신호 악화 시 매니저 매도 제안 + 사후관리실장
    통합 매도결정에 슬리브 코드 포함.
  - `test_commodity_manager_persona.py` — 페르소나·풀·세션 매핑.
  - `test_macro_four_way_allocation.py` — `주식/채권/원자재/현금` 4분할 파싱.
  - `test_thesis_reminder_broadcast.py` — 포트폴리오기획팀장이 3매니저에 thesis 일괄 주입.
  - `test_ops_tier_partition.py` — cycle 트리거가 weekly-tier 제안을 defer, weekly가 apply.
  - `test_persona_rename_stock_manager.py` — `주식운용실장` 라우팅·@멘션·핸드오프.
  - `test_commodity_config.py` / `test_commodity_default_on.py` — 기본 ON + 풀 코드 유효성.
- 전 구간 `python3.11 -m pytest` 그린 후 단일 재시작 배포.

## 영향 / 위험

- **라이브 채권 코드 리팩토링** — 슬리브로 일반화하며 동작 보존. 채권 테스트가 안전망.
- **매도 순서 변경** — 슬리브가 사후관리 앞으로. KR/US 세션 경계·"반대 시장 보유=보류" 규칙 보존.
- **기본 ON** — 전 프로필 채권·원자재 자동매매 활성화. US세션 슬리브는 `ALLOW_US_STOCKS` 종속 유지.
- **모바일 Kotlin** — 소스 문자열 갱신하되 APK 재빌드는 보류(요청 시 별도 ~20분).
- 배포: 전부 구현 후 `arquant.service` 재시작 1회(재시작 시 루프 OFF→대시보드 '시작' 필요).

## 비목표 (YAGNI)

- 슬리브 매수에 대한 사후관리실장 거부권(매수는 자산배분 — 슬리브 자율).
- 원자재 듀레이션/롤오버 비용 모델링(선물 ETF는 풀에 고정, LLM 해설만).
- 3번째 이상 슬리브(REITs 등) — 엔진은 일반화하되 현재 슬리브는 2개.
- 모바일 APK 재빌드(별도 요청).
