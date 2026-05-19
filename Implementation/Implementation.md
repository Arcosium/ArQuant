# ArQuant 고도화 설계서 — ML·유전 알고리즘 파라미터 튜닝 / Markowitz 포트폴리오 / Coresight-Admin 연동

> **문서 성격**: 설계·기획 문서 (DOCUMENTATION ONLY). 본 문서는 코드 변경을 포함하지 않으며, 어떤 모듈도 신규 생성하지 않는다. 모든 pseudocode 는 *제안 스케치*일 뿐 실제 구현이 아니다.
> **근거 자료**:
> - `Implementation/1-s2.0-S0304405X9800052X-main.pdf` — Allen & Karjalainen (1999), *"Using genetic algorithms to find technical trading rules"*, Journal of Financial Economics 51, pp.245–271. (전 27p 중 p.1–17, 24–27 정독; 결과 표 본문)
> - `Implementation/ML.pdf` — Gu, Kelly & Xiu (2020), *"Empirical Asset Pricing via Machine Learning"*, Review of Financial Studies 33(5), pp.2223–2273. (전 51p 중 p.1–15(방법론) 정독, p.28–30·39–40(결과)·49–50(참고문헌) 발췌. 부록(Internet Appendix) 및 중간 결과표 일부는 본문 요약·캡션만 확인)
> - `Implementation/파생퀀트4조.pdf` — 노승종 외, *"ML/LLM 강화학습 기반 섹터 Rotation 전략"* (파생퀀트 4조 발표자료). 전 26p 전부 정독.

---

## 0. 개요

ArQuant 의 매매 의사결정은 9개 LLM 에이전트의 2-pass 협업으로 이루어지지만, **최종 체결을 통제하는 것은 LLM 이 아니라 파이썬 결정론 게이트**다. 구체적으로:

- `config.py` 의 `STRATEGY_PRESETS` (defensive ~ ultra_aggressive) + `STRATEGY_TUNABLE_KEYS` 가 사이징·청산·리스크의 *수치 파라미터*를 정의한다.
- `runtime.get(key)` 가 런타임에 활성 프리셋/프로필 오버라이드를 해석해 엔진에 제공한다 (`runtime.py:125`).
- `backtest/engine.py` 는 **종목 선정을 평가하지 않고**, 고정 SMA-돌파 프록시 신호 위에서 *프리셋 규칙이 리스크/MDD/회전율을 어떻게 바꾸는지*만 결정론적으로 재현한다 (`engine.py` 헤더 "정직성 경계" 참조).

본 문서가 다루는 세 가지 고도화는 모두 **이 결정론 레이어를 대상으로** 한다 — LLM 의 종목 픽 자체는 손대지 않는다.

| 절 | 주제 | 핵심 1줄 |
|---|---|---|
| 1 | ML + GA 별도 파라미터 튜닝 | 프리셋 수치를 GA/ML 루프가 백테스트 적합도로 진화시키되, 파이썬 게이트는 절대 우회 못 함 |
| 2 | Markowitz 포트폴리오 | 3년 일봉으로 공분산 추정 → 평균-분산 최적 비중을 운용전략실장 후보 사이징에 *권고치*로 주입 |

---

## 1. 머신러닝 + 유전 알고리즘 기반 별도 파라미터 적용 방안

### 1.1 문제 정의 — "무엇을" 튜닝하는가

ArQuant 에는 진화/학습으로 최적화 가능한 명확한 **연속·정수·불리언 파라미터 벡터**가 이미 존재한다 (`config.py:151` `STRATEGY_TUNABLE_KEYS`):

```
PER_ORDER_BUDGET_RATIO   (0.01~1.00)   ┐
PER_ORDER_BUDGET_OVERSHOOT (1.0~2.0)   │ 사이징
MAX_CYCLE_BUDGET_RATIO   (0.01~1.00)   │
MIN_CASH_BUFFER          (1.0~1.5)     ┘
CONSERVATIVE_MDD         (0.01~0.30)   ┐ 리스크 게이트
CONSERVATIVE_STOCK_RATIO (0.05~0.50)   ┘
MAX_TRADES_PER_CYCLE     (0~10, int)
TAKE_PROFIT_PCT / STOP_LOSS_PCT        매도 규칙
TRIM_OVER_RATIO / ALLOW_DAY_TRADING …  불리언
```

추가로 LLM 단계의 **메타 가중치**도 후보다:
- 후보 선정 가중치 — 파생퀀트4조의 `composite_score = 0.30·market + 0.35·flow + 0.35·rotation` 처럼, ArQuant 의 macro/news/quant 시그널 결합 비중이 현재 암묵적/프롬프트 내장이라면 이를 명시 벡터로 추출해 튜닝.
- 뉴스 가중 튜너(news weight tuner)의 임계값(`config.py` `HEADLINE_DEDUP_RATIO`, `NEWS_PREFILTER_TRIGGER` 등 — 단, deprecated 키 `ANALYSIS_NEWS_THRESHOLD` 류는 제외).

이들 ~15개 실수/정수 파라미터의 결합공간은 그리드 탐색이 비현실적이고 적합도 표면이 **불연속·다봉(multi-modal)** 이다(정수 `MAX_TRADES_PER_CYCLE`, 불리언 토글, 청산 임계의 계단형 효과). 이는 정확히 Allen & Karjalainen 이 GA를 권하는 조건과 일치한다.

> **출처 (JFE/Allen-Karjalainen, p.4 "Evolutionary algorithms offer a number of advantages…")**: GA는 미분 불가·불연속 목적함수, 다수의 국소 최적, 거대 탐색공간에 강하다. 단 — 같은 논문 p.4 후단·결론(p.25)에서 **거래비용 차감 후 buy-and-hold 초과수익은 일관되게 음(–)**, 즉 *GA가 찾은 규칙이 시장을 이긴다는 보장은 없다*. 따라서 ArQuant 적용 시 GA의 목표는 "초과 알파 발굴"이 아니라 **리스크/MDD/회전율 프로파일의 프리셋 튜닝**으로 한정해야 한다(이는 `backtest/engine.py` 헤더가 이미 선언한 평가 경계와 정확히 부합).

### 1.2 적합도 함수 (Fitness / Training Signal)

JFE 논문(식 (1)~(4), p.10)의 적합도는 *거래비용 차감 후 buy-and-hold 대비 초과 연속복리수익* `Δr = r − r_bh` 이다. ArQuant 의 백테스트 엔진은 이미 동치 지표를 산출한다 (`engine.py:_metrics`):

```
total_return_pct, max_drawdown_pct, sharpe_like, win_rate_pct, trades
```

제안 적합도(다목적 → 스칼라화):

```
fitness(θ) = w1·sharpe_like(θ)
           − w2·|max_drawdown_pct(θ)|
           − w3·turnover_penalty(trades(θ))     # JFE: 거래비용이 알파를 잠식 → 회전율 페널티 필수
           − w4·overfit_penalty(θ)              # ML.pdf: 정규화/검증분리
```

- `sharpe_like` 는 엔진이 `mean/vol·√252` 로 이미 계산 (`engine.py:167`).
- 회전율 페널티는 JFE p.16 "transaction costs lead to diminished forecasting ability" 및 파생퀀트4조 백테스트(Turnover A2=220 vs A0=33)에서 검증된 *회전율 비용*을 GA가 무시하지 않게 강제.
- 파생퀀트4조의 학습 보상 구성(p.7: **DSR, CALMAR, BM 대비 초과수익, 레짐별 특화 보상의 선형 결합**)을 그대로 차용할 수 있다 — CALMAR(=연수익/|MDD|)는 MDD 보존을 중시하는 ArQuant 보수 게이트 철학과 잘 맞는다.

### 1.3 데이터 소스 (이미 존재)

| 소스 | 경로 | 용도 |
|---|---|---|
| 일봉 OHLCV | `data/daily_<code>.csv` (KIS `kr_daily_chart_deep`, 최대 ~2~3년) | 백테스트 워크포워드 입력 (`engine.load_prices`) |
| 실거래 자산곡선 | `data/equity_curve.json` (253 pt, `{ts,total_eval,cash,pnl_ratio,holdings,external_flow_cum}`) | 온라인 적합도/드리프트 모니터 |
| 전략 변경 이력 | `data/strategy_history.json` | 어떤 파라미터셋이 언제 적용됐는지 — 백테스트 라벨링 |
| 운용 이력/피드백 | `data/ops_history.json`, `data/ops_feedback.json` | 제약 학습용 보조 신호 |
| 백테스트 엔진 | `backtest/engine.py` `run_backtest(preset_name, prices, ...)` | **적합도 평가 함수 (이미 결정론·룩어헤드 차단)** |

### 1.4 오프라인 GA 루프 설계 (Allen-Karjalainen 알고리즘을 파라미터 벡터에 사상)

JFE Table 1(p.12)의 1-trial 절차를 ArQuant 파라미터 벡터로 사상한다. **트리(genetic programming) 대신 고정 길이 실수/정수/불리언 벡터** → 고전 GA(Holland 식)로 충분(JFE p.5: GP는 규칙의 *구조*까지 진화시킬 때만 필요; 우리는 구조 고정·값만 진화).

룩어헤드/데이터 스누핑 방지는 JFE p.11 의 **train / selection / test 3분할**을 그대로 채택 (= ML.pdf §1.1 의 train/validation/test 와 동일 원칙):

```
PSEUDOCODE — 제안만, 모듈 생성 금지
────────────────────────────────────────────────────────
genome      := vector over STRATEGY_TUNABLE_KEYS (실수/정수/불리언, config 의 min/max/step 메타 사용)
prices      := backtest.engine.load_prices()                 # data/daily_*.csv
split prices.dates  →  TRAIN | SELECT | TEST  (시간순, 비중첩)  # JFE p.11 / ML §1.1

function fitness_on(window, genome):
    preset := materialize genome as a STRATEGY_PRESETS-shaped dict
    m := backtest.engine.run_backtest(preset, prices_restricted_to(window))
    return w1·m.sharpe_like − w2·|m.max_drawdown_pct| − w3·turnover(m.trades)

population := 200 random genomes (within config STRATEGY_KEY_META min/max)   # JFE used 500; 파라미터 수 적어 200 충분
best := argmax_g fitness_on(SELECT, g)                                       # JFE Step 2
repeat for ≤ 50 generations:                                                # JFE Step 3~4
    for 200 iters:
        p1,p2 := rank-biased pick(population)        # JFE rank-based selection (footnote 2)
        child := crossover(p1,p2);  mutate(child, p_m≈0.05)   # 실수=blend, 정수=±1, 불리언=flip
        clip child to STRATEGY_KEY_META bounds
        child.fit := fitness_on(TRAIN, child)
        replace a rank-biased *unfit* member with child
    cand := argmax_population fitness_on(SELECT, ·)
    if fitness_on(SELECT,cand) improves best:  best := cand   # JFE: selection-set early-save (과적합 방지)
    early-stop if no SELECT improvement for 15 generations    # JFE: 25 (데이터 짧으므로 축소)
report best, then fitness_on(TEST, best)   # 진짜 out-of-sample (튜닝에 미사용)
```

> **과적합 방지 (JFE p.11 + ML.pdf §1.1, §1.3)**: ① SELECT 셋 기준 best 저장(TRAIN 적합도만으로 채택 금지). ② `overfit_penalty` 로 모델복잡도/극단 파라미터 억제 — ML.pdf 가 강조하는 *정규화(regularization)* 의 GA 판. ③ TEST 셋은 보고 시 1회만 평가. ④ 파생퀀트4조 한계점(p.25)이 지적한 *"가중치 임의 설정"·"데이터 3년으로 레짐 부족"* 을 그대로 ArQuant 리스크로 명시(§적용 우선순위 표).

### 1.5 ML 보조 — 시그널 가중·기대수익 추정 (ML.pdf 차용)

GA가 *프리셋 수치*를 진화시키는 동안, ML.pdf 의 핵심 발견을 **시그널 결합/기대수익** 레이어에 적용한다:

- **ML.pdf p.5–7 핵심 결과**: 900+ 예측변수 중 OLS는 붕괴(R²oos −3.46%), **elastic net·PCR·PLS 같은 정규화/차원축소가 양(+)으로 전환**, 트리·신경망(NN3)이 최고. 가장 강한 예측변수는 **price trend(모멘텀·단기반전), 유동성(시가총액·거래대금·호가스프레드), 변동성(특이변동성·베타)**.
- **ArQuant 적용**:
  - ArQuant 가 이미 보유한 일봉으로 위 3계열(모멘텀/유동성/변동성) 특징을 만들고, **PLS 또는 elastic net** 로 후보 종목의 다음-기간 기대수익 `μ̂_i` 를 *권고치*로 산출 → §2 Markowitz 의 기대수익 입력으로 직결.
  - ML.pdf p.6 "shallow learning outperforms deeper learning" + p.30 NN3 정점 → ArQuant 데이터량(수백 종목·수년)은 적으므로 **얕은 모델(elastic net / PLS / RF 깊이 ≤5)** 만 권장. 깊은 NN 금지.
  - ML.pdf §1.2.1 의 **Huber 손실·가중최소자승**(시가총액/시점 역가중)으로 한국시장 소형주 노이즈·두꺼운 꼬리 흡수.
  - ML.pdf p.6 "portfolio-level R² ≫ stock-level" → ArQuant 도 개별 종목 예측을 직접 쓰기보다 **포트폴리오/섹터 레벨로 집계**해 사용(노이즈 평균화). 이는 파생퀀트4조의 *섹터 ETF 프록시* 접근과 일치.

### 1.6 온라인 vs 오프라인 — 가드레일 (절대 게이트 우회 금지)

- **오프라인(권장 기본)**: GA/ML 루프는 배치로 `data/daily_*.csv` 위에서만 실행 → best 파라미터셋을 *제안*으로 산출. 사람(또는 운용지원실장 라인)이 검토 후 `runtime.set_strategy(...)` 로 적용. 라이브 매매 경로와 분리.
- **온라인(선택, 보수적)**: `data/equity_curve.json` 의 실현 곡선으로 *드리프트 모니터*만 — "현재 프리셋의 실거래 Sharpe/MDD 가 백테스트 기대 대비 X 표준편차 이탈 시 재튜닝 알림". 자동 파라미터 교체 금지(사람 승인 필수).
- **불변식 (Hard Guardrails)**:
  1. GA 산출물은 `STRATEGY_TUNABLE_KEYS` 화이트리스트 안에서만, 각 키의 `STRATEGY_KEY_META.min/max` 범위로 **clip 후** 제안. 화이트리스트 밖 키(자격증명·API·실행 경로) 절대 노출 안 함 — `profile_overrides.whitelist()` (`profile_overrides.py:60`) 와 동일 원칙.
  2. GA 는 *권고치*만 생산. 실제 체결은 변함없이 `engine` 의 파이썬 게이트(`CONSERVATIVE_MDD`, `MIN_CASH_BUFFER`, `CONSERVATIVE_STOCK_RATIO`, `MAX_CYCLE_BUDGET_RATIO`)를 통과 — GA가 그 게이트의 *값*은 바꿔도, *게이트 자체를 끄거나 우회하는 코드 경로는 만들 수 없다*.
  3. JFE 결론(p.25) 명시: 거래비용 차감 시 초과수익 보장 없음 → GA 산출 프리셋도 "수익 보장"으로 표기 금지, MDD/회전율 개선 관점으로만 채택 판단.
  4. 적합도는 반드시 `backtest/engine.py`(룩어헤드 차단 검증된 코드)를 통해서만 계산 — GA가 자체 시뮬레이터를 만들어 룩어헤드를 도입하지 못하게.

---

## 2. Markowitz 포트폴리오 이론 적용 로직

### 2.1 동기

파생퀀트4조는 RL(PPO)로 현금↔위험자산을 배분하고 Rank 로 섹터를 고르지만, **위험자산 내부의 종목 간 비중**은 score 비례(A1) 또는 LLM(A2~A4)에 맡긴다 — 명시적 평균-분산 최적화는 없다. ArQuant 역시 운용전략실장이 후보를 고른 뒤 사이징은 `PER_ORDER_BUDGET_RATIO` 일률 비율이다. 여기에 **Markowitz 평균-분산(MV) 최적화를 *권고 비중 산출기*로 추가**해, "어떤 종목을 살지"(LLM)와 "각각 얼마나"(MV)를 분리한다.

### 2.2 입력

ArQuant 가 이미 수집하는 데이터로 전부 구성 가능:

- **공분산 Σ**: `data/daily_<code>.csv` (KIS `kr_daily_chart_deep`, `us_daily_chart`) 의 일별 종가 → 로그수익률 → 표본 공분산. 데이터가 짧고(2~3년) 종목 많아 표본 Σ 는 ill-conditioned 이므로 **Ledoit-Wolf 축소(shrinkage)** 적용:

  `Σ̂ = (1−δ)·Σ_sample + δ·F`  (F = 상수 상관 타깃, δ = 축소강도)

  > 이는 ML.pdf 의 정규화 철학(§1.3 elastic net/ridge: "draw estimates toward zero / a target")을 공분산 추정에 적용한 것. 표본 Σ 의 과적합을 타깃 F 쪽으로 끌어당김.

- **기대수익 μ**: 세 옵션, 보수적 순서로 권장
  1. (기본·가장 보수적) `μ_i = 0` 으로 두고 **최소분산 포트폴리오(GMV)** 만 — JFE 결론(기대수익 예측은 비용 차감 시 신뢰 어려움)에 부합, 추정오차 최소.
  2. (선택) §1.5 의 ML(PLS/elastic net) 산출 `μ̂_i` 를 *축소 적용*: `μ_used = (1−κ)·μ̄ + κ·μ̂` (κ 작게, 예 0.2) — Black-Litterman 식 사전분포 끌림.
  3. LLM 후보 점수(운용전략실장 conviction)를 ordinal → 완만한 μ tilt 로 변환(파생퀀트4조 A3 "single strong rank → 과도 집중 금지" 제약과 같은 정신).

- **제약(constraints)** — ArQuant 기존 게이트와 *동일 수치*로 강제:
  - 종목별 상한 `w_i ≤ runtime.get("CONSERVATIVE_STOCK_RATIO")` (현재 0.15)
  - 무공매도 `w_i ≥ 0` (ArQuant 는 롱 온리; `ALLOW_DERIVATIVES=False`)
  - 현금 버퍼 `Σ w_i ≤ 1 − cash_buffer`, `cash_buffer` 는 `MIN_CASH_BUFFER` 와 정합
  - KRX/US 분리: `Σ_{i∈US} w_i ≤ US_cap` (장 시간·`ALLOW_US_STOCKS` 연동; KST 22:30~05:00 US 세션 외엔 US_cap=0)
  - 사이클 예산 `Σ_{신규} w_i·NAV ≤ NAV·MAX_CYCLE_BUDGET_RATIO`

### 2.3 수식

평균-분산 최적화 (위험회피계수 λ, 또는 GMV):

```
일반형:   max_w    μᵀw  −  (λ/2)·wᵀΣ̂w
GMV:      min_w    wᵀΣ̂w                 (μ 미사용 — 기본 권장)
s.t.      Σ_i w_i = 1 − c_buffer
          0 ≤ w_i ≤ s_max            (s_max = CONSERVATIVE_STOCK_RATIO)
          Σ_{i∈US} w_i ≤ u_cap
          (선택) Σ_{i∈sector} w_i ≤ sec_cap   # 파생퀀트4조 섹터 집중 억제와 동조
```

효율적 프런티어 한 점(목표 변동성 σ*) 선택:

```
σ_p(w)  = sqrt( wᵀ Σ̂ w )            # 포트폴리오 변동성
μ_p(w)  = μᵀ w
Sharpe  = (μ_p − r_f) / σ_p          # r_f: KIS 무위험 ~ 또는 0
프런티어: σ_p 를 격자 스윕하며 각 σ_target 에서 μ_p 최대화 → 운용전략실장의
          위험성향(=활성 STRATEGY_PRESET 의 보수↔공격)에 매핑해 한 점 선택
          (defensive→GMV 근처, ultra_aggressive→고-σ 점)
```

폐형해(부등식 제약 없는 경우, 직관 제공용): `w* = (1/λ)·Σ̂⁻¹(μ − μ̄·1)` 형태. 실제로는 부등식 제약 때문에 **2차계획(QP)** 로 푼다 (예: `numpy`/`scipy.optimize` 또는 `cvxpy` — 이미 환경에 없으면 GMV는 KKT/투영경사로도 충분).

### 2.4 제안 모듈/함수 스케치 (PSEUDOCODE — 모듈 생성 금지)

```
# 제안 위치: portfolio/markowitz.py  (← 생성하지 않음, 설계만)
def recommend_weights(
        codes: list[str],                 # 운용전략실장이 고른 후보
        prices: dict[str, list[float]],   # backtest.engine.load_prices() 형태 재사용
        nav: float, cash: float,
        risk_preset: str,                 # runtime.get_strategy_name()
        mu: dict[str,float] | None = None # None → GMV
) -> dict[str, float]:                    # {code: 권고비중} — *권고치일 뿐*
    R   = log_returns_matrix(prices, codes)          # 결측·상장폐지 종목 drop
    Sig = ledoit_wolf_shrink(sample_cov(R))          # ML.pdf 정규화 정신
    s_max  = runtime.get("CONSERVATIVE_STOCK_RATIO")
    cbuf   = derive_cash_fraction(runtime.get("MIN_CASH_BUFFER"))
    ucap   = us_cap_now(runtime.get("ALLOW_US_STOCKS"))   # 세션 밖이면 0
    if mu is None:
        w = solve_GMV(Sig, box=(0,s_max), budget=1-cbuf, us_cap=ucap)
    else:
        lam = risk_aversion_from_preset(risk_preset)      # defensive=큰 λ
        w   = solve_MV(mu, Sig, lam, box=(0,s_max), budget=1-cbuf, us_cap=ucap)
    return clip_and_renormalize(w)        # 합=예산, 박스 재투영

# 호출 지점 (설계): 운용전략실장(orchestrator)이 후보 확정 후, 엔진에 넘기기 전
#  recommend_weights() 를 호출해 *권고 비중*을 컨텍스트로 첨부.
#  엔진은 이 비중을 PER_ORDER_BUDGET_RATIO 의 종목별 변형으로 환산하되,
#  반드시 기존 파이썬 게이트(MIN_CASH_BUFFER, CONSERVATIVE_STOCK_RATIO,
#  MAX_CYCLE_BUDGET_RATIO, CONSERVATIVE_MDD)를 그대로 통과시킨다.
```

### 2.5 리밸런싱 주기 & 기존 게이트와의 공존

- **주기**: 파생퀀트4조는 *익일 시가 리밸런싱*(매 거래일). ArQuant 는 사이클이 `PERIODIC_CYCLE_SEC = 1h`(`config.py:87`)이고 회전율 비용에 민감하므로 — **일 1회(장 개장 직후 사이클) 또는 비중 이탈 밴드 초과 시(예 절대편차 > 3%p)에만** 재계산 권장. JFE p.16 의 거래비용 경고와 직접 연동.
- **공존 원칙**: Markowitz 출력은 *권고 비중*이며 **사이징 게이트를 대체하지 않는다**. 우선순위:
  1. LLM(운용전략실장) — 어떤 종목 (정성)
  2. Markowitz — 그 종목들 사이 *권고* 상대 비중 (정량)
  3. 파이썬 게이트 — 최종 거부권 (MDD 차단·단일종목 한도·현금버퍼·사이클 예산). Markowitz가 0.15를 권해도 게이트가 0.15면 그게 상한.
  4. `ENABLE_SELL_REBALANCE`/`TRIM_OVER_RATIO` 의 청산 규칙은 그대로 유효 — Markowitz는 *신규/증분 배분*만 권고.
- **백테스트 검증**: `backtest/engine.py` 에 (설계상) "균등 비율 vs Markowitz 권고 비중"을 동일 SMA 신호 위에서 비교하는 모드를 *추가 설계*할 수 있으나 — 본 문서에서는 코드 미작성. 비교 지표는 엔진이 이미 내는 `sharpe_like / max_drawdown_pct / trades`.

---


## 4. 적용 우선순위 / 리스크

| # | 항목 | 출처 | 기대 효과 | 주요 리스크 | 가드레일 | 우선순위 |
|---|---|---|---|---|---|---|
| 1 | GA 오프라인 프리셋 튜닝 (sharpe/MDD/turnover 적합도) | JFE(Allen-Karjalainen) p.4·10·12·25; 파생퀀트4조 p.7 보상함수 | 프리셋 수치를 데이터로 보정, MDD/회전율 프로파일 개선 | 과적합·데이터 스누핑; JFE: 비용 차감 후 알파 미보장; 파생퀀트4조 p.25: 3년 데이터로 레짐 부족 | train/select/test 3분할, SELECT 기준 저장, TEST 1회, 화이트리스트+min/max clip, 게이트 우회 불가 | **높음** (기존 backtest/engine.py 재사용, 오프라인·저위험) |
| 2 | ML(PLS/elastic net) 기대수익·시그널 가중 | ML.pdf p.5–7 §1.3–1.5 | 후보 μ̂ 추정 → Markowitz 입력 품질↑; 정규화로 안정 | 낮은 신호대잡음비, 소형주 노이즈; 깊은 모델 과적합 | 얕은 모델만, Huber/가중손실, 포트폴리오 레벨 집계, 권고치 only | 중간 |
| 3 | Markowitz GMV 권고 비중 (μ=0 기본) | 파생퀀트4조(MV 부재 보완) + ML.pdf 정규화 | 균등비율 대비 변동성/집중도 개선; 권고-게이트 분리 | Σ 추정오차(짧은 데이터); μ 추정 시 오차 증폭 | Ledoit-Wolf 축소, 기본 GMV(μ 미사용), 기존 게이트가 최종 거부권 | 중간~높음 (GMV는 μ 불필요라 견고) |

### 권장 로드맵

1. **1단계 (저위험·즉시 가치)**: `backtest/engine.py` 를 적합도 함수로 쓰는 GA 오프라인 루프 — 사람 승인으로 `runtime.set_strategy` 적용. 게이트 불변.
2. **2단계**: Markowitz GMV 권고 비중을 운용전략실장 컨텍스트에 *읽기 전용 권고*로 첨부, 백테스트로 균등비율 대비 비교.
3. **3단계**: ML(PLS/elastic net) μ̂ 를 Markowitz 입력으로 *축소 적용*.

**공통 불변식**: 어떤 ML/GA/Markowitz 산출물도 `STRATEGY_TUNABLE_KEYS` 화이트리스트·`STRATEGY_KEY_META` 범위를 벗어나지 못하고, 최종 체결은 변함없이 `CONSERVATIVE_MDD / MIN_CASH_BUFFER / CONSERVATIVE_STOCK_RATIO / MAX_CYCLE_BUDGET_RATIO` 파이썬 게이트를 통과한다. JFE 결론(비용 차감 후 초과수익 미보장)에 따라 모든 산출 프리셋은 "수익 보장"이 아닌 "리스크 프로파일 튜닝"으로만 채택 판단한다.
