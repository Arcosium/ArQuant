# 제도권 투자 파이프라인 4단계 이식 — 설계

- 날짜: 2026-06-04
- 맥락: 사장 지시 — 증권사 프롭/운용 5단계 파이프라인(발굴·리서치 → 전략수립 → 리스크심사/투심위 → 집행 → 사후/평가)을 ArQuant에 모방. 분석 결과 **모방할 가치 있는 4개**만 구현(분할집행·LLM투심위는 제외 — 리테일 규모엔 무의미/일관성 위험).
- 관련 메모리: `arquant-deterministic-score-engine`, `arquant-strategy-param-expansion`, `arquant-fund-planner-promotion-veto`, `arquant-kr-us-asymmetry-bugs`, `arquant-valuation-formula`, `feedback-never-drop-orders`.

## 공통 설계 원칙 (라이브 안전)

1. **모든 신규 동작은 튜너블 노브 뒤에 둔다.** 디폴트는 "기존 동작에 가깝게" 잡아 라이브 계좌 회귀 위험 0에서 출발, ops_support/사장이 점진적으로 dial-up.
2. **주문 스킵 금지**(`feedback-never-drop-orders`): 신규 필터는 *후보(아이디어 풀)*만 거른다. LLM이 명시 지정한 최종 주문은 건드리지 않는다. 무음 컷 금지 — 거른 내역은 로그로 남긴다.
3. **KR/US 패리티**(`arquant-kr-us-asymmetry-bugs`): 사이징·유니버스 로직은 공통 헬퍼로 구현하고 KR·US 양 경로에서 동일 호출. 패리티 테스트 필수.
4. **TDD**: 순수함수로 분리 가능한 로직(사이징 가중치·유니버스 스크린·귀인 엔진)은 먼저 테스트.
5. **1회 배포**: 4개 전부 구현·테스트 통과 후 단일 재시작. `.running` 마커 선생성(자동재개).

---

## ① 순서 반전 — 퀀트 점수가 *선정*에 영향

### 문제
현재 흐름: `_cyc_stage_select`(운용전략실장 PASS1이 후보 5개 선정) → `_cyc_stage_data_quant`(결정론 점수 계산, `cyc._quant_scores`) → `_cyc_stage_finalize_sell`에서 `filter_targets_by_score`로 `MIN_QUANT_SCORE` 미달만 *제거*. 점수는 사후 필터일 뿐 *선정/우선순위*에 영향 없음. 제도권은 리서치/퀀트가 랭킹 → PM이 상위 풀에서 선택.

### 설계 (2단)
- **(a) 루브릭 주입**: PASS1 프롬프트(`main_swarm.py:3402` 부근)에 현재 채점 루브릭 요약을 주입 — 어떤 지표가 가중되는지(부호 포함)와 `MIN_QUANT_SCORE`. → LLM이 루브릭 정렬된 후보를 제안. `format_strategy_param_block`(이미 PASS/계량 프롬프트에 쓰임)을 재사용/확장.
- **(b) 랭크-인지 최종 선정**: `filter_targets_by_score`를 확장 — (기존) 미달 제거 + (신규) 통과 종목을 퀀트점수 내림차순 정렬 + `MAX_BUY_NAMES` 상한 적용(상위부터). 점수 없는 종목은 보존(스킵 금지). 이후 사이징(②)이 상위 점수에 더 큰 예산.

### 신규 노브
- `MAX_BUY_NAMES` (int, 기본 큰 값 예: 8 → 사실상 무변화). 한 사이클 최대 매수 종목 수.

### 비목표
- 하드 컷(LLM 제안을 top-N으로 강제 절단)은 안 함 — 발굴 폭 유지.
- 전체 시장 유니버스를 사전 채점하지 않음(계산비용). 후보 풀 내 랭킹만.

### 테스트
- `filter_targets_by_score`가 정렬+캡 적용 후에도 미달 제거·미점수 보존·경계(==MIN) 보존 유지.
- `MAX_BUY_NAMES`보다 통과 종목이 많을 때 상위 점수만 남는다.
- 루브릭 블록이 PASS1 프롬프트 문자열에 포함된다.

---

## ② 리스크기반 포지션 사이징

### 문제
현재 여러 종목에 `cycle_budget / n` **균등** 분배(KR `main_swarm.py:1998` 부근, US `2066` 부근). `compute_quant_indicators`의 `sigma20`(20일 연환산 변동성)은 채점에만 쓰이고 사이징엔 미사용.

### 설계
종목별 사이징 가중치
```
raw_w_i = norm_score(score_i) / norm_vol(sigma_i)
w_i     = clamp(raw_w_i / mean(raw_w), 1/MAX_TILT, MAX_TILT) · TILT_STRENGTH 보간
budget_i = cycle_budget · w_i / Σ w
```
- `norm_score`: 결정론 점수(0~10) → 점수 높을수록 큰 비중. 점수 없으면 중립(1.0).
- `norm_vol`: σ 높을수록 작은 비중(역변동성). σ 없으면 중립.
- `TILT_STRENGTH ∈ [0,1]`: 0=완전 균등, 1=완전 기울임. 균등분배(`1.0`)와 `raw_w` 사이 선형보간.
- `MAX_TILT`: 균등분배 대비 한 종목이 받을 수 있는 최대/최소 배수(과집중 방지). 기존 `per_stock_cap`·편중 가드는 그대로 위에 적용(이중 안전).
- 공통 헬퍼 `compute_sizing_weights(codes, scores, sigmas, mode, strength, max_tilt) -> {code: weight}` (순수함수, `main_swarm` 또는 `tools/`). KR/US 양 경로에서 동일 호출.
- σ 전달: `_cyc_stage_data_quant`가 후보별 `sigma20`을 `cyc._quant_sigmas`(신규) 또는 기존 indicators 캐시에 저장 → `_build_orders`가 읽음. data_quant는 build_orders보다 먼저 실행되므로 가용.

### 신규 노브
- `POSITION_SIZING_MODE` (`equal` | `risk_weighted`, 기본 `risk_weighted`)
- `SIZING_TILT_STRENGTH` (float 0~1, 기본 0.5 — 약한 기울임)
- `SIZING_MAX_TILT` (float, 기본 2.0 — 균등 대비 최대 2배/0.5배)

### 디폴트 동작
`risk_weighted` + 약한 기울임으로 시작. `equal`로 토글하면 기존 균등분배 완전 복귀.

### 테스트
- `compute_sizing_weights`: 점수↑→비중↑, σ↑→비중↓, Σw 정규화, `MAX_TILT` 상하한 클램프, `strength=0`이면 전원 균등, 점수/σ 결측 종목 중립 처리.
- KR/US 패리티: 동일 입력에 동일 가중치(통화 환산 외 로직 동일).
- `mode=equal`이면 기존 수량과 동일(회귀).

---

## ③ 유니버스 스크리닝 결정론화

### 문제
레버리지/인버스/저가 제외 로직이 폴백 경로(`main_swarm.py:2091` 부근)에만 흩어져 있음. 사전 게이트 부재.

### 설계
`screen_universe(codes, side, prices, turnovers) -> (kept, dropped_with_reason)` 헬퍼 신설. 후보 확정 직후(`_resolve_candidate_codes` / `seed_candidates_from_news` 결과)와 폴백 후보 생성 시 적용. 제외 기준:
- 레버리지/인버스/곱버스·ETN: 종목명 패턴(`레버리지`,`인버스`,`곱버스`,`2X`,`ETN` 등) — `UNIVERSE_EXCLUDE_LEVERAGED`.
- 저가/동전주: 현재가 < `UNIVERSE_MIN_PRICE`(KR 원, US는 USD 환산 임계 별도 또는 0=off).
- 거래대금 미달: 일거래대금 < `UNIVERSE_MIN_TURNOVER`.
- 거른 내역 `dropped_with_reason`는 사이클 노트/로그에 기록(무음 금지).

### 신규 노브
- `UNIVERSE_MIN_PRICE` (기본: 현재 사실상 배제 중인 저가 임계, 예 KR 1000)
- `UNIVERSE_MIN_TURNOVER` (기본: 보수적, 0=off 허용)
- `UNIVERSE_EXCLUDE_LEVERAGED` (bool, 기본 True)

### 주문 스킵 안전
*후보 풀*만 거른다. LLM 명시 최종 주문엔 미적용. 풀이 비면 그 사이클은 정당하게 매수 아이디어 없음(로그). KR/US 동일 헬퍼.

### 테스트
- 레버리지/인버스 종목 제외, 저가 제외, 거래대금 미달 제외, 정상 종목 보존.
- 임계 0/False면 해당 기준 미적용(off).
- KR/US 패리티(통화 임계 분리).
- dropped에 사유 동반.

---

## ④ 성과 귀인 + 에이전트 스코어카드

### 문제
에이전트 예측(뉴스감성·퀀트점수·thesis·매도·리스크반려)은 cycles.db에 *자유텍스트 리포트*로만 존재 → retro-parse는 환각 위험. 체결결과(`trade_log.json`)·자산곡선·주문기록은 구조화돼 있음.

### 설계 (3부)

**(A) 신호 구조화 적재(전향적)** — `infra/scorecard_store.py` 신규
- 테이블 `agent_signals`: `id, cycle_id, uid, ts, code, name, news_sentiment(float|null), quant_score(int|null), det_breakdown(JSON|null), thesis_verdict(text|null), sell_decision(text|null), risk_verdict(text|null)`.
- 적재 지점(값이 계산되는 곳에서 추가 — 매매동작 불변):
  - `_cyc_stage_data_quant`: 후보별 `news_sentiment`(`parse_news_sentiment`)·`quant_score`·`det_breakdown`.
  - `_cyc_stage_finalize_sell`: 종목별 thesis 평가·매도 결정.
  - `_cyc_stage_risk`: 반려 사유.
- per-uid 분리(멀티테넌트).

**(B) 귀인 엔진** — `tools/agent_scorecard.py` 신규(순수함수)
- 입력: `agent_signals` 레코드 + `trade_log`(체결·실현손익) + 자산곡선 + 지수 시계열(KOSPI/SPY).
- 지표:
  - 뉴스: 감성 vs 후속 N일 수익률 — IC(순위상관).
  - 퀀트: 점수 vs 후속 N일 수익률 — IC.
  - macro: 권고 주식% vs 지수 방향 — 적중률.
  - 펀드기획: veto된 종목의 후속 가격 — 회피손실 vs 놓친수익.
  - 사후관리: 매도 후 가격흐름(하락=굿, 상승=배드).
  - 리스크: 반려 종목 후속 결과(손실=굿콜, 상승=기회손실).
  - 트레이딩: 결정시점가 vs 체결가 — 슬리피지(`trade_log`의 `fill_ts`/`price`).
  - 포트폴리오: 자산곡선 vs 지수 회귀 → 알파/베타.
- 후속수익 미성숙 구간은 표본에서 제외하고 그 사실을 함께 반환(희소 표기 — 무음 금지).

**(C) 노출**
- 서버 엔드포인트 `GET /api/scorecard`(`server/app.py`) → 지표 JSON(uid별).
- 사이클 리포트(`_cyc_stage_report`)에 스코어카드 요약 1~2줄.
- ops_support 프롬프트(`infra/ops_support_worker.py`)에 "에이전트 성과 요약" 주입 → 증거기반 튜닝.

### 신규 노브
- `SCORECARD_WINDOW_DAYS` (int, 기본 30 — 귀인 트레일링 윈도우). (선택: 상수로 둘 수도 있으나 ops 가시성 위해 노브화)

### 비목표 (이번 범위 제외)
- 신규 대시보드 탭/모바일 UI.
- 기존 자유텍스트 retro-parse 백필.

### 테스트
- `tools/agent_scorecard.py` 순수함수: 합성 데이터로 IC(완전상관=+1·역상관=−1·무상관≈0), 슬리피지, 알파/베타, 빈/희소 표본 graceful.
- `scorecard_store`: 적재·조회·uid 분리·결측 컬럼 허용.
- 적재가 매매 결과를 바꾸지 않음(부수효과 없음 회귀).
- `/api/scorecard` 200 + 스키마.

---

## 교차: 파라미터 등록 & 배포

### 신규 튜너블 노브 (총 ~8)
`MAX_BUY_NAMES`, `POSITION_SIZING_MODE`, `SIZING_TILT_STRENGTH`, `SIZING_MAX_TILT`, `UNIVERSE_MIN_PRICE`, `UNIVERSE_MIN_TURNOVER`, `UNIVERSE_EXCLUDE_LEVERAGED`, `SCORECARD_WINDOW_DAYS`.
- 각 노브 7단계 등록(`arquant-strategy-param-expansion` 패턴): 상수 정의 → `STRATEGY_TUNABLE_KEYS` → `STRATEGY_KEY_META` → `STRATEGY_KEY_EFFECT` → 5개 `STRATEGY_PRESETS` 전부 → `runtime.get(key, uid)` 호출 → 사용처.
- 튜너블 키 39 → ~47.
- ops 카탈로그·플레이북은 META/EFFECT에서 동적 생성이라 자동 반영(stale 없음). 플레이북에 신규 레짐 예시 추가(예: "급락장 대비 → `SIZING_TILT_STRENGTH↑`(고확신 저변동에 집중)·`UNIVERSE_MIN_TURNOVER↑`(유동성 확보)").

### 배포
- `python3.11 -m pytest` 전체 통과 후.
- `.running` 마커 선생성 → `sudo systemctl restart arquant.service` 1회 → 두 계정 자동재개 확인 → 헬스(HTTP 200)·실데이터 스모크.
- `DETERMINISTIC_SCORING` 등 기존 토글 불변.

## 미해결/리스크
- `UNIVERSE_MIN_TURNOVER` 일거래대금 소스: `crawl_company_full`/시세에 거래대금 필드 있는지 구현 중 확인(없으면 거래량×가격 근사 또는 해당 기준 0=off 기본).
- US σ/거래대금 데이터 결손(`arquant-us-intraday-data-gap`) 구간엔 사이징·스크린이 중립 폴백(결측=중립)이라 안전하나, 귀인 IC 표본이 US는 더 희소할 수 있음 — 윈도우/표본수 함께 노출.
