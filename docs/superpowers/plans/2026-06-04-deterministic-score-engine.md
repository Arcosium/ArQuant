# 결정론 점수 엔진 구현 계획

> **For agentic workers:** TDD task-by-task. Steps use `- [ ]`.

**Goal:** 퀀트·뉴스·매크로 점수를 순수 Python 결정론으로 산정(지표별·차원별 부호 가중치, ops 튜너블). 계량분석팀장 LLM은 해설자. `DETERMINISTIC_SCORING` 토글로 구 LLM 채점 즉시 롤백.

**Architecture:** 순수 함수 모듈 `tools/quant_score.py` + 구조화 지표 `tools/market_data.py::compute_quant_indicators`(+CMF). main_swarm 퀀트 단계가 후보별 결정론 점수를 산출해 `_quant_scores`에 넣고, 그 점수·기여내역을 계량분석팀장 프롬프트에 주입(LLM은 코멘트만, 점수 파싱 안 함). config에 신규 파라미터(QIW_*·DW_*·DETERMINISTIC_SCORING), QW_* 폐기.

**Tech Stack:** Python 3.11, pandas(지표), pytest, runtime override 시스템.

spec: docs/superpowers/specs/2026-06-04-deterministic-score-engine-design.md

---

## File Structure
- Create `tools/quant_score.py` — 순수 함수: `indicator_signals`, `compute_indicator_score`, `news_score`, `macro_score`, `combine_dimensions`.
- Create `tests/test_quant_score.py`, `tests/test_quant_indicators_cmf.py`, `tests/test_deterministic_score_config.py`, `tests/test_deterministic_score_pipeline.py`.
- Modify `tools/market_data.py` — `_cmf`, `compute_quant_indicators(code)->dict`.
- Modify `config.py` — QIW_*(9)·DW_QUANT/NEWS/MACRO·DETERMINISTIC_SCORING; QW_TREND/MEANREV/VOLATILITY/FLOW 폐기; QW_NEWS→DW_NEWS; META/EFFECT/프리셋.
- Modify `main_swarm.py` — 퀀트 단계 결정론 점수 조립 + 뉴스감성 파서 + 매크로점수; `format_strategy_param_block`에서 QW 가중치 제거; 결정론 토글 분기.
- Modify `agents/specialists.py` — 계량분석팀장=해설자 프롬프트.
- Modify `infra/ops_support_worker.py` — 플레이북 신규 노브 예시.

---

### Task 1: quant_score.py 순수 함수 + 테스트
**Files:** Create `tools/quant_score.py`, `tests/test_quant_score.py`

- [ ] Step1 실패 테스트(`indicator_signals` 경계, `compute_indicator_score` 스케일안정·음수반전·전부0폴백·결손제외, `news_score`/`macro_score` clamp, `combine_dimensions` 합성·DW음수·결손).
- [ ] Step2 실패 확인.
- [ ] Step3 구현:
  - `_clamp(x,lo,hi)`, `_norm(weights, sigs)` = `5+5*Σ(w*s)/Σ|w|` (Σ|w|=0 또는 sig 없음 → 5.0).
  - `indicator_signals(ind: dict)->dict`: rsi/macd/adx/vwap/vol/mom/cmf/flow/high52 — spec 매핑. 키 없는 지표는 결과에서 생략.
  - `compute_indicator_score(ind, qiw: dict)->(float, dict)`: sigs=indicator_signals; 존재하는 신호만 w·s; `_norm`; breakdown={sig:contrib}.
  - `news_score(sent)->float|None`: sent None→None; else `5+5*_clamp(sent,-1,1)`.
  - `macro_score(stock_pct)->float|None`: None→None; else `5+5*_clamp((pct-15)/25,-1,1)`.
  - `combine_dimensions(scores: dict, dw: dict)->float`: scores={'QUANT':x,'NEWS':y|None,'MACRO':z|None}; None 차원 제외; `_norm(dw_present, (s-5)/5)`; 전부 None→5.0.
- [ ] Step4 통과 확인. Step5 (자동백업이 커밋).

### Task 2: CMF + compute_quant_indicators (market_data.py)
**Files:** Modify `tools/market_data.py`; Create `tests/test_quant_indicators_cmf.py`
- [ ] Step1 CMF 테스트(알려진 OHLCV→기대 CMF 부호/범위) + `compute_quant_indicators(code)` dict 키 존재 테스트(rsi14·macd_hist·adx·vwap_dev·sigma20·mom_1m·mom_3m·high52_prox·cmf·flow). 데이터 없으면 빈 dict 폴백.
- [ ] Step2 실패. Step3 구현: `_cmf(daily, n=20)` = Σ MFV / Σ Vol, MFV=((C-L)-(H-C))/(H-L)*V; `compute_quant_indicators`는 format_quant_data_for_agent와 동일 계산을 숫자 dict로 반환(중복 최소화 — 공통 계산 헬퍼 추출 가능, 무리면 병렬 계산). Step4 통과.

### Task 3: config.py 파라미터 (QIW/DW/토글, QW 폐기) + 테스트
**Files:** Modify `config.py`; Create `tests/test_deterministic_score_config.py`
- [ ] Step1 테스트: 신규 키(QIW_RSI..QIW_HIGH52·DW_QUANT/NEWS/MACRO·DETERMINISTIC_SCORING) 상수·튜너블·메타 존재; QW_TREND/MEANREV/VOLATILITY/FLOW·QW_NEWS 가 STRATEGY_TUNABLE_KEYS에서 제거됨; 프리셋에 신규키 존재; DETERMINISTIC_SCORING 기본 True.
- [ ] Step2 실패. Step3 구현: 상수 추가(기본 균형 프로파일 — QIW: MOM/ADX/FLOW/HIGH52 +, VWAP/VOL −, RSI/CMF 소폭+; DW_QUANT 우위·NEWS·MACRO 보조), STRATEGY_TUNABLE_KEYS 갱신(QW 4종 제거, QW_NEWS 제거, 신규 추가), STRATEGY_KEY_META/EFFECT 추가, 프리셋 5종 값. Step4 통과.

### Task 4: main_swarm 결정론 점수 조립 + 뉴스감성 파서 + 토글
**Files:** Modify `main_swarm.py`; Create `tests/test_deterministic_score_pipeline.py`
- [ ] Step1 테스트(순수 헬퍼): `parse_news_sentiment(news_report, code, name)->float|None`(감성 +0.85 파싱·실패 None); `assemble_quant_score(ind, sent, macro_pct, weights)->(score, breakdown)`(=compute_indicator_score+news+macro+combine 합성). 결손 차원 폴백.
- [ ] Step2 실패. Step3 구현 헬퍼 + 퀀트 단계 배선: `DETERMINISTIC_SCORING`이면 후보별 `compute_quant_indicators`→`assemble_quant_score`로 `_quant_scores[code]` 채우고 LLM 점수 파싱 생략(보유종목 매도 분석도 동일); breakdown을 계량분석팀장 프롬프트에 주입; False면 종전 경로. 매크로% 파싱은 사이클 1회. Step4 통과.

### Task 5: specialists 계량분석팀장=해설자 + format_strategy_param_block 정리
**Files:** Modify `agents/specialists.py`, `main_swarm.py`
- [ ] 계량분석팀장 시스템 프롬프트: "점수는 시스템(파이썬)이 확정—바꾸지 말고 한국어 해설·리스크만. 마지막 줄 퀀트점수는 주어진 값 그대로." 채점 가중치 문구 제거. `format_strategy_param_block`에서 QW 채점 가중치 줄 제거(필터 advisory는 유지), 결정론 점수·breakdown 주입 추가. 관련 기존 테스트(test_quant_prompt_format) 갱신.

### Task 6: ops 플레이북 신규 노브 예시
**Files:** Modify `infra/ops_support_worker.py`
- [ ] 플레이북에 예시 추가: "역추세→QIW_RSI↑·QIW_VWAP 음수", "뉴스경시→DW_NEWS↓/음수", "매크로추종→DW_MACRO↑", "변동성회피→QIW_VOL 음수 크게". 카탈로그는 자동(STRATEGY_KEY_EFFECT 추가분 포함).

### Task 7: 전체 회귀 + 배포 + 라이브 검증
- [ ] `python3.11 -m pytest` 전체 통과(기존+신규). 폐기된 QW_* 참조 테스트 갱신.
- [ ] 스모크: 프리셋별 점수 산출·DETERMINISTIC_SCORING 토글·실데이터 1종목 점수.
- [ ] 마커 보강 후 `arquant.service` 재시작, 자동재개·헬스 확인, 다음 전체 사이클에서 결정론 점수 산출 확인.

## Self-Review
- 스펙 커버리지: 점수모델(T1/T4)·지표엔진+CMF(T1/T2)·뉴스/매크로(T1/T4)·파라미터/폐기(T3)·해설자(T5)·ops(T6)·롤백토글(T3/T4)·테스트(전)·배포(T7) — 전부 매핑.
- 플레이스홀더 없음(공식은 spec 참조, 핵심 시그니처 명시).
- 타입 일관: `compute_indicator_score`(ind,qiw), `combine_dimensions`(scores,dw), `assemble_quant_score`(ind,sent,macro_pct,weights) 일관 사용.
