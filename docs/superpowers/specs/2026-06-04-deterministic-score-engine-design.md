# 결정론 점수 엔진 (퀀트·뉴스·매크로) — 순수 파이썬 점수

- 작성일: 2026-06-04
- 사장 지시(확정): 퀀트 점수 산정을 **지표별 가중치(음수 허용)** 로 조정 가능하게. 뉴스·매크로도 동일. 운용지원실장이 자유롭게 조정. **LLM은 일관성이 없으니 점수는 무조건 파이썬 결정론 계산.** 계량분석팀장 LLM은 **해설자로 유지**(점수는 못 바꾸고 코멘트만).
- 선행: 2026-06-04 전략 파라미터 19→31 확장([[arquant-strategy-param-expansion]]). 이 스펙은 그 위에 결정론 점수 엔진을 올리고, 오늘 추가한 LLM 카테고리 가중치 `QW_*` 를 더 세밀한 `QIW_*` 로 대체한다.

## 배경 / 결정 근거

현재 퀀트 점수는 지표(Python 계산) → 텍스트 → **LLM 정성 채점**이다. 같은 종목·데이터라도 LLM이 매번 다른 가중·점수를 내 **일관성·재현성·비교가능성**이 약하다(오늘 #152에서 5종목 점수가 LLM 주관으로 산출됨). 지표는 이미 Python 숫자이므로, 점수를 **순수 Python 가산식**으로 확정하면 일관·재현·100% 튜닝충실·테스트 용이.

**"결정론"의 범위**: *합산 산수*가 Python이라는 뜻. 입력 중 뉴스 감성(+0.85)·매크로 권고 주식%는 LLM(뉴스/전략리서치팀장)이 만든 **단일 숫자**지만, 그건 일관성 문제였던 '가중합'이 아니라 단일 판단값 — Python이 기계적으로 곱해 쓰므로 일관적이다.

## 목표 / 비목표
**목표**: 퀀트·뉴스·매크로 점수를 **순수 Python 결정론**으로 산정. 지표별·차원별 **부호 가중치(음수 허용)** 로 ops가 자유 조정. 계량분석팀장은 그 점수·기여내역을 한국어로 해설(점수 불변).
**비목표**: 지표 계산식 변경(CMF만 신규) / 계량분석팀장 LLM 제거(해설자로 유지) / 신규 데이터 소스.

## 점수 모델 (전부 0~10, 순수 Python)

두 계층 모두 **스케일 안정 정규화** `norm(w, s) = 5 + 5·Σ(wᵢ·sᵢ)/Σ|wᵢ|` (가중치 크기 무관, 부호·비율만 의미, 결과 0~10 고정; `Σ|w|=0`이면 중립 5).

```
S_quant = norm(QIW, indicator_signals)      # 지표 엔진 (아래)
S_news  = 5 + 5·clamp(sentiment, −1, +1)     # 후보별 뉴스 감성 → 0~10
S_macro = 5 + 5·clamp((stock_pct−15)/25, −1, +1)   # 매크로 권고 주식% → 0~10 (15%=중립)
최종점수 = norm({DW_QUANT, DW_NEWS, DW_MACRO}, {S_quant−5)/5, (S_news−5)/5, (S_macro−5)/5})
        = 5 + 5·Σ(DW_d·(S_d−5)/5)/Σ|DW_d|     # 0~10
```
- `DW_QUANT/DW_NEWS/DW_MACRO`: 차원 가중치(signed). 음수면 그 차원 반전(예: DW_NEWS<0 = 호재일수록 감점, 역발상).
- 결손/파싱 실패 차원은 합성에서 제외(분자·분모 모두). 전 차원 결손이면 중립 5.
- 이 최종점수가 곧 `_quant_scores[code]` — `MIN_QUANT_SCORE` 게이트·운용전략실장 선정의 입력.

### 퀀트 지표 엔진 (S_quant)
`compute_indicator_score(ind, QIW)->(0~10, breakdown)`. 각 지표를 신호 `s∈[-1,+1]`(+1=매수 우호)로 정규화:

| 신호 | 원천 | 매핑(요지) |
|---|---|---|
| `rsi` | RSI14 | 과매도→+ 과매수→−. `clamp((50−rsi)/30,−1,1)` |
| `macd` | MACD_hist | 양→+ 음→−. tanh |
| `adx` | ADX14(±DI) | 강추세·상승우위→+. `clamp((adx−20)/20,0,1)·dir` |
| `vwap` | VWAP 이격% | 과이격→−(추격). `clamp(−dev/15,−1,1)` |
| `vol` | σ20 | 고변동→−. `clamp((40−σ)/40,−1,1)` |
| `mom` | 1M·3M 수익률 | 양→+. 합성 후 tanh |
| `cmf` | **신규** Chaikin Money Flow(20) | 매집→+ 분산→−. ±1 |
| `flow` | 외인+기관 5/20일 누적 | 순매수→+ |
| `high52` | 52w 근접도 | 신고가 근접→+(모멘텀). `clamp((prox−0.5)/0.5,−1,1)` |

캐논 방향 고정, **부호 전환은 가중치(QIW)로**. 결손 지표는 분자·분모 제외. `Σ|QIW|=0`이면 균형 기본 프로파일 폴백.

## 계량분석팀장 = 해설자
- 퀀트 단계에서 **Python이 먼저 `최종점수` + breakdown(지표별 기여·뉴스·매크로 기여)** 을 산출.
- 계량분석팀장 호출 프롬프트에 그 점수·breakdown을 주입하고, "이 점수는 시스템 확정값이다. 바꾸지 말고, 사람이 읽을 한국어 해설(왜 이 점수인지·핵심 리스크)을 쓰라"고 지시.
- 시스템은 **LLM 응답에서 점수를 파싱하지 않는다**(Python 값 사용). 진입가/매도가 directive 등 비점수 출력은 종전대로 파싱.
- 보유 종목 매도 분석도 동일: 점수는 Python, 코멘트는 LLM.

## 신규 파라미터 (전부 signed·음수 허용·ops 튜너블)
- 퀀트 지표 가중치: `QIW_RSI · QIW_MACD · QIW_ADX · QIW_VWAP · QIW_VOL · QIW_MOM · QIW_CMF · QIW_FLOW · QIW_HIGH52`.
- 차원 가중치: `DW_QUANT · DW_NEWS · DW_MACRO`.
- 안전 토글: `DETERMINISTIC_SCORING`(bool, 기본 **True**). False면 **구 LLM 채점으로 즉시 롤백**(점수 파싱 복원) — 블렌드 폐기로 잃은 무위험 폴백을 대체.
- 폐기: `QW_TREND/QW_MEANREV/QW_VOLATILITY/QW_FLOW`(LLM 채점용이라 무의미해짐) → STRATEGY_TUNABLE_KEYS에서 제거(deprecated). `QW_NEWS` → `DW_NEWS`로 승계.

## 배선 (파일별)
- `tools/quant_indicators.py`/`market_data.py`: `compute_quant_indicators(code)->dict`(숫자 dict, 기존 텍스트 포맷과 계산 공유) + CMF 추가.
- 신규 `tools/quant_score.py`(순수 함수): `indicator_signals(ind)` · `compute_indicator_score(ind, QIW)` · `combine_dimensions({S},{DW})` · `news_score(sent)` · `macro_score(stock_pct)`.
- `main_swarm.py` 퀀트 단계: 후보별 indicators 계산 → S_quant; 뉴스 감성 파싱 → S_news; 매크로% 파싱 → S_macro(사이클 공통); 합성 → `_quant_scores[code]`. breakdown을 계량분석팀장 프롬프트에 주입. `DETERMINISTIC_SCORING=False`면 종전 LLM 파싱 경로.
- `config.py`: 신규 상수 + 메타/effect/튜너블 등록 + 프리셋 값 + `QW_*` 폐기/`QW_NEWS→DW_NEWS` 마이그레이션.
- `agents/specialists.py`: 계량분석팀장 프롬프트를 '해설자'로 변경(점수 주어짐·불변·코멘트만). 채점 가중치 문구 제거.
- `infra/ops_support_worker.py`: 카탈로그 동적 노출 + 플레이북 예시(예: "역추세 → QIW_RSI↑·QIW_VWAP 음수", "뉴스 경시 → DW_NEWS↓/음수", "매크로 추종 강화 → DW_MACRO↑").

## 프리셋 (요지, 구현 시 표 확정)
- DW: 균형 = DW_QUANT 우위 + DW_NEWS·DW_MACRO 보조. 공격형 DW_NEWS↑, 방어형 DW_MACRO·DW_QUANT↑.
- QIW: 방어형 QIW_VOL·QIW_FLOW↑, 공격형 QIW_MOM·QIW_HIGH52↑, 균형 중립.
- `DETERMINISTIC_SCORING`: 전 프리셋 True.

## 테스트 (TDD)
- `indicator_signals` 경계(과매수/과매도·고/저변동·순매수/매도)·결손 제외.
- `compute_indicator_score` 스케일 안정(가중치 ×10 무영향)·음수 부호 전환·전부0 폴백·결손 분모 제외.
- `news_score`/`macro_score` 매핑·clamp.
- `combine_dimensions` 3차원 합성·DW 음수 반전·일부 차원 결손·전 차원 결손=중립.
- CMF 정확성(알려진 입력).
- 통합: 결정론 경로가 `_quant_scores`를 채우고 LLM 점수 파싱 안 함 / `DETERMINISTIC_SCORING=False`면 종전 동작.
- 프리셋 정합 + `QW_*` 폐기·`QW_NEWS→DW_NEWS` 마이그레이션.

## 롤아웃 / 안전
- **점수 산정 주체가 LLM→Python으로 전환** — α=0 같은 무위험 폴백이 없으므로, 기본 `QIW_*`/`DW_*` 프로파일을 합리적으로 잡아 급격한 행동 변화 방지 + 충분한 테스트. 문제 시 `DETERMINISTIC_SCORING=False`로 즉시 롤백.
- 전 노브 runtime 튜너블(재시작 불필요). 코드 반영은 `arquant.service` 재시작(자동재개 `.running` 마커 필요).
- 결손·파싱 실패는 전부 폴백(차원 제외/중립) — 엔진이 죽거나 주문 왜곡 없음.

## 확정 사항 (사장 2026-06-04: 추천안대로 확정 + 자율 구현)
- ① `QW_TREND/QW_MEANREV/QW_VOLATILITY/QW_FLOW` **폐기**(STRATEGY_TUNABLE_KEYS·메타·프리셋에서 제거), `QW_NEWS`→`DW_NEWS` 승계. 기존 프로필 오버라이드에 옛 키가 있으면 조용히 무시(런타임이 튜너블 키만 적용).
- ② `DETERMINISTIC_SCORING` 기본 **True**(바로 결정론 전환). OFF면 즉시 구 LLM 채점 롤백.
- ③ 기본 QIW·DW 프로파일은 **현행과 큰 괴리 없는 균형 캘리브레이션**(모멘텀/추세/수급 양(+), 과이격/고변동 음(−), DW_QUANT 우위 + 뉴스·매크로 보조). 프리셋별 색깔은 방어=변동성·수급·매크로↑, 공격=모멘텀·뉴스↑.
- ④ 뉴스 감성↔후보 코드 매핑 파싱: 실패 시 S_news 제외(중립 폴백) — 구현 중 파싱 정확도 확인.
