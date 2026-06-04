# 전략 파라미터 대폭 확장 + 운용지원실장 충실 번역 — 설계

- 작성일: 2026-06-04
- 사장 지시: "운용지원실장에게 '급락장에 대비해줘' 같은(그 외 다양한 퀀트 전략) 지시를 내렸을 때, 파라미터 조정만으로 전략이 AI 매매에 효과적으로 반영되도록 파라미터를 훨씬 더 상세히 추가하고, 운용지원실장이 충실히 조정할 수 있게 하라. HYFE_IQC 데이터필드 CSV는 참고 가능."
- 브레인스토밍 확정: **하이브리드 강제(결정론 게이트 + 프롬프트 주입)** · **풍부한 카탈로그 + LLM 자율 매핑** · **CSV는 카테고리 영감만**.

## 목표 / 비목표

**목표**
- 전략 파라미터를 20개 → 32개로 확장. **모든 신규 파라미터는 실제 매매 경로에 배선(살아있는 노브)** — 죽은 노브 금지(과거 `MIN_ORDER_NOTIONAL_RATIO` 교훈).
- 운용지원실장(ops_support)이 어떤 퀀트 전략 지시("급락장 대비", "추세추종", "역추세", "모멘텀", "고배당 방어" 등)를 받아도 적절한 파라미터 집합으로 **충실히 번역**.

**비목표(범위 밖)**
- `TRAILING_STOP_PCT` 등 포지션별 고점추적 신규 로직: 별도 작업(죽은 노브 방지).
- CSV 필드(value/quality/analyst-revision) 직접 연동: ArQuant 데이터 파이프라인에 없음 → 제외(카테고리 영감만).
- 매매 철학 토글(ALLOW_DAY_TRADING 등) 변경: 기존 유지.

## 핵심 원칙: 살아있는 노브

각 신규 파라미터는 아래 셋 중 **하나 이상**에 실제로 소비되어야 한다:
1. **결정론 게이트** — Python이 직접 강제(파싱된 퀀트점수/매크로비중 등으로 매수대상 필터).
2. **프롬프트 주입** — 계량분석팀장/운용전략실장 *호출 프롬프트*에 전략 파라미터 블록으로 주입(LLM이 채점·선정에 반영). 기존에 ALLOW_DAY_TRADING 가이드·매크로 배분이 프롬프트로 흐르는 것과 동일 메커니즘.
3. **사이징/리스크/매도 룰** — 기존 배선(해당 시).

## 신규 파라미터 (12개)

### 그룹 A — 종목 필터(매수 자격)
| 파라미터 | type | 기본 | 강제 | 배선 훅 |
|---|---|---|---|---|
| `MIN_QUANT_SCORE` | int 0~10 | 6 | 결정론 | PASS2 확정 후 `_quant_scores[code] < MIN_QUANT_SCORE` 인 target 제거 + 프롬프트에 기준 명시 (현 하드코딩 "6점"(main_swarm:2471) 대체) |
| `MAX_BUY_VOLATILITY_PCT` | pct_raw | 0=off | 프롬프트 | 계량분석팀장: 연환산 변동성 초과 종목 매수부적합·점수하향 |
| `RSI_OVERBOUGHT_SKIP` | int | 0=off | 프롬프트 | RSI 초과(과매수) 종목 신규매수 회피 |
| `MIN_ADX_FOR_BUY` | int | 0=off | 프롬프트 | ADX 미만(추세약) 매수부적합 — 추세추종 전략용 |
| `REQUIRE_FOREIGN_NET_BUY` | bool | false | 프롬프트 | 외국인 순매수(+) 종목만 매수 적격 |
| `MAX_PRICE_EXTENSION_PCT` | pct_raw | 0=off | 프롬프트 | VWAP/이평 대비 과이격(추격매수) 회피 |

### 그룹 B — 퀀트 채점 가중치 (합 100 자동정규화)
현재 `specialists.py:164`에 하드코딩된 가중치(추세30/평균회귀20/변동성15/수급20/뉴스15)를 런타임화.
| 파라미터 | 기본 |
|---|---|
| `QW_TREND` | 30 |
| `QW_MEANREV` | 20 |
| `QW_VOLATILITY` | 15 |
| `QW_FLOW` | 20 |
| `QW_NEWS` | 15 |

- 배선: 계량분석팀장 호출 프롬프트에 정규화된 가중치를 주입. 시스템 프롬프트는 "메시지에 전략 가중치가 있으면 그것을 우선 적용"으로 변경. 합이 0이거나 비정상이면 기본값(30/20/15/20/15)으로 폴백.

### 그룹 C — 레짐 대응
| 파라미터 | 기본 | 강제 | 배선 훅 |
|---|---|---|---|
| `MACRO_STOCK_GATE_ENABLED` | true | 결정론 | 2026-06-04 추가한 매크로 매수게이트(`_macro_blocks_new_buys`) on/off |

## 운용지원실장 번역 강화 (핵심)

1. **동적 카탈로그 주입**: `STRATEGY_KEY_META`에서 전체 튜너블 파라미터 카탈로그(라벨·단위·범위·effect)를 텍스트로 렌더하는 헬퍼 `strategy_param_catalog_text()`를 추가해 **ops 워커 프롬프트에 런타임 주입**. 파라미터가 늘어도 프롬프트가 자동 최신화(stale 방지).
2. **effect 메타 추가**: 각 `STRATEGY_KEY_META[key]`에 `"effect"` 필드("올리면 ~ / 내리면 ~")를 추가해 ops가 조정 방향을 정확히 인지.
3. **레짐 플레이북(예시)**: ops 프롬프트에 대표 시나리오별 파라미터 방향 예시를 제시. **하드코딩 레시피가 아니라 예시** — LLM 자율 매핑 유지.
   - 급락장 대비: `MIN_CASH_BUFFER`↑ · `PER_ORDER_BUDGET_RATIO`↓ · `MAX_CYCLE_BUDGET_RATIO`↓ · `STOP_LOSS_PCT` 타이트 · `CONSERVATIVE_MDD`↓ · `MAX_BUY_VOLATILITY_PCT`↓ · `REQUIRE_FOREIGN_NET_BUY`=true · `MIN_QUANT_SCORE`↑ · `QW_VOLATILITY`↑/`QW_FLOW`↑
   - 추세추종: `MIN_ADX_FOR_BUY`↑ · `QW_TREND`↑ · `RSI_OVERBOUGHT_SKIP` 완화 · `TAKE_PROFIT_PCT`↑(길게)
   - 역추세/평균회귀: `QW_MEANREV`↑ · `RSI_OVERBOUGHT_SKIP`↓ · `MAX_PRICE_EXTENSION_PCT`↓
   - 모멘텀: `QW_TREND`↑/`QW_NEWS`↑ · `MIN_QUANT_SCORE`↑ · `PER_ORDER_BUDGET_RATIO`↑
   - 고배당/방어: `MAX_BUY_VOLATILITY_PCT`↓ · `CONSERVATIVE_STOCK_RATIO`↓ · `MAX_TRADES_PER_CYCLE`↓
4. ops 출력은 기존대로 `param_overrides` JSON. 검증(STRATEGY_TUNABLE_KEYS 한정)·적용(runtime 프로필 오버라이드)은 기존 경로 재사용.

## 배선 지점 (파일별)

- `config.py`: 신규 상수 12개 · `STRATEGY_KEY_META` 12항목(+ 기존 항목에 `effect` 보강) · `STRATEGY_TUNABLE_KEYS` 등록 · 5개 프리셋에 값 채움.
- `runtime.py` 또는 `config.py`: `strategy_param_catalog_text()` 헬퍼(메타→텍스트), `normalize_quant_weights()` 헬퍼(합 100 정규화·폴백).
- `agents/specialists.py`:
  - `create_quant_analyst` 시스템 프롬프트: "메시지의 전략 가중치/필터 우선 적용" 규칙 추가(하드코딩 가중치 문구는 기본값으로 명시).
  - `create_ops_support` 시스템 프롬프트: 카탈로그·플레이북은 *호출 시 주입*(동적)하므로, 시스템 프롬프트엔 "주입된 카탈로그/effect를 근거로 조정" 규칙만.
- `main_swarm.py`:
  - 계량분석팀장 호출부(3446~): 전략 파라미터 블록(정규화 가중치 + 활성 필터) 주입.
  - PASS2 target 확정 직후: `MIN_QUANT_SCORE` 결정론 필터(미달 제거, 사유 로깅).
  - 매크로 게이트: `MACRO_STOCK_GATE_ENABLED` 반영.
  - ops 워커 프롬프트 구성부: `strategy_param_catalog_text()` + 레짐 플레이북 주입.

## 프리셋 값 (신규 파라미터)

| 파라미터 | defensive | conservative | balanced | aggressive | ultra |
|---|---|---|---|---|---|
| MIN_QUANT_SCORE | 7 | 7 | 6 | 5 | 4 |
| MAX_BUY_VOLATILITY_PCT | 40 | 50 | 0 | 0 | 0 |
| RSI_OVERBOUGHT_SKIP | 70 | 75 | 0 | 0 | 0 |
| MIN_ADX_FOR_BUY | 0 | 0 | 0 | 0 | 0 |
| REQUIRE_FOREIGN_NET_BUY | true | true | false | false | false |
| MAX_PRICE_EXTENSION_PCT | 10 | 15 | 0 | 0 | 0 |
| QW_TREND | 25 | 28 | 30 | 35 | 40 |
| QW_MEANREV | 15 | 18 | 20 | 15 | 10 |
| QW_VOLATILITY | 25 | 20 | 15 | 10 | 5 |
| QW_FLOW | 25 | 22 | 20 | 20 | 20 |
| QW_NEWS | 10 | 12 | 15 | 20 | 25 |
| MACRO_STOCK_GATE_ENABLED | true | true | true | true | true |

(QW_* 각 프리셋 합 = 100)

## 테스트 (TDD)

- `normalize_quant_weights`: 합 100 정규화 · 합 0/음수 → 기본값 폴백.
- `strategy_param_catalog_text`: 모든 튜너블 키 + effect 포함, 신규 키 포함.
- `MIN_QUANT_SCORE` 결정론 게이트: 점수 미달 target 제거 / 충족 통과 / 임계 경계.
- 프리셋 정합: 모든 프리셋이 신규 키 포함 · QW 합 100 · 범위 내.
- 계량분석팀장 호출 프롬프트에 가중치·활성 필터 문구 주입 확인.
- ops 워커 프롬프트에 카탈로그·플레이북 주입 확인.

## 롤아웃

- 기존 테스트 전부 통과 유지 + 신규 테스트. 적용은 `arquant.service` 재시작(에이전트/서버 코드 변경). 재시작 자동재개는 `data/<uid>/.running` 마커 필요.
- 신규 파라미터는 전부 runtime 튜너블 → 대시보드 '전략' 탭 자동 노출, 재시작 없이 조정 가능.
