# 🏛️ ArQuant v1.0 — AI Multi-Asset Quantitative Trading System

> **https://arquant.ai-ve.uk** | 앱 자체 로그인 (아이디/비밀번호 — 사장 피드백 2026-05-16, Cloudflare Access 제거)
>
> 9명의 AI 에이전트(+ 운용지원실장 산하 팀장 3명 + 뉴스 분류·큐레이터)가 협업하여 **글로벌 지수(매크로) + Tavily 실시간 시황·해설 + 3년치 일봉/수급 + 실시간 분봉 + DART 공시·재무제표 + 네이버 금융 증권 속보**를 종합 분석하고,
> **국내주식 · 해외주식 · 국내채권 · 펀드** 보유분을 통합 관리하며 실거래(KIS OpenAPI)를 수행하는 풀스택 시스템.
>
> 의사결정은 **운용전략실장의 2패스 흐름**(① 뉴스 분석 기반 후보 6개 → ② 종목별 계량 평가(통일 5섹션 양식) → 최종 1~3개 매수)으로 진행되며,
> 계량분석팀장이 진입가/관망 지시를 내리면 **트레이딩팀장이 분봉 단위로 모니터링**하다가 트리거 도달 시 매수합니다.
> 주문서(OrderDraft)와 1차 리스크 검증은 **결정론적 파이썬**이, 매수 2차 재심은 **DART 공시 + 직전연도 요약재무 기반 AI**가, 보유 종목 매도는 **사후관리실장**이 담당합니다.
> 운용전략실장 산하 트레이딩팀장이 **실행 직후** 체결 결과(실제 체결가 포함)를 한국어 산문으로 보고합니다.
> 운용지원실장은 **직접 코딩하지 않고** 사이클 결과를 도메인(investment/operations/finance)별로 분류해 산하 팀장 워커에 위임합니다.

---

## 📁 프로젝트 구조

```
Arquant/
├── .env                       # API 키 (OpenRouter / KIS / Tavily / OpenDart)
├── config.py                  # 중앙 설정 — 모델 배정, 출력 토큰 상한, 리스크 한도, 전략 프리셋(5종), 폴백 정책, STRATEGY_KEY_META
├── runtime.py                 # 전략 프리셋 영속화 + 런타임 오버라이드 + 사용자 정의 프리셋 (data/user_presets.json)
├── main_swarm.py              # FSM 오케스트레이터 — 감시 루프 + 2패스 분석 사이클 + 종목별 퀀트 + 사후관리 + DART 재심 + 분봉 진입 타이밍 watch loop
├── start_server.sh / stop_server.sh / supervise.sh
├── claude_response.json       # 모든 이벤트/에이전트 응답 전문 로그 (영속, 4000 entry soft cap)
├── README.md
├── data/
│   ├── kis_token.json         # KIS 접근 토큰 캐시 (24h, .gitignore — 재시작 시 재발급 방지)
│   ├── strategy_state.json    # 활성 전략 프리셋 스냅샷 (runtime.py가 관리)
│   ├── strategy_history.json
│   ├── user_presets.json      # 사용자 정의 프리셋 (전략 탭 커스터마이즈에서 저장)
│   ├── cycles.db              # SQLite — 분석 사이클 영속화 (백테스트·장기 분석용)
│   ├── news_classifier.db     # SQLite — 뉴스 분류 결과 vs 실 매매 결과 학습 로그
│   ├── ops_history.json       # 운용지원실장(+ 산하 팀장 3명) 자동 수정 이력
│   ├── ops_support.log/.spawn.log  # 워커 실행 로그
│   ├── equity_curve.json      # 평가금액 추이 시계열 (60s 최소 간격, 2000pt cap)
│   ├── daily_*.csv            # 종목별 3년 일봉 OHLCV
│   ├── investor_*.csv         # 종목별 수급(기관/외인) 누적
│   └── minute_*.csv           # 종목별 KIS 분봉(장중)
├── agents/
│   ├── base_agent.py          # OpenRouter BaseAgent — 프롬프트 캐시(Anthropic), history 윈도우, per-agent max_tokens, **API 비용 추적**
│   ├── specialists.py         # macro / quant / news / trader (자연어 보고) / post_manager / ops_support
│   └── guardrails.py          # validate_order_draft() 결정론 검증 (KR=원/US=$ 분리) + risk_guard(DART + 재무 재심) / policy 페르소나
├── infra/
│   ├── kis_broker.py          # KIS OpenAPI — 국내/해외 주식·채권 시세·주문·잔고, 멀티거래소 시세, 토큰 파일 캐시
│   ├── ops_support_worker.py  # 운용지원실장 + 산하 팀장 3명 — 별도 프로세스로 코드 수정 및 서버 재시작 (FORBIDDEN_PATTERNS 가드)
│   ├── cycle_store.py         # cycles.db CRUD + 보유기간 추적
│   ├── news_classifier_log.py # 뉴스 분류 학습 로그 (주간 리뷰에서 키워드 가중치 조정에 활용)
│   ├── ops_history.py         # 운용지원실장 변경 이력 누적
│   └── weekly_review.py       # 주간 피드백 루프 (수요일 자동)
├── tools/
│   ├── market_data.py         # 글로벌 지수 + 3년치 일봉/수급 크롤러
│   ├── news_monitor.py        # 네이버 금융 '증권 속보' 크롤러 + KR/US/BOTH 분류
│   ├── dart_disclosure.py     # OpenDart 공시 + corpCode.xml 매핑 + 직전연도 요약재무 (BS+IS)
│   ├── coresight_rag.py · global_search.py · quant_indicators.py · naver_search.py
├── server/
│   ├── app.py                 # FastAPI — REST + WebSocket + 사용자 프리셋 CRUD
│   └── static/index.html      # 대시보드 UI (단일 HTML, 4탭: 대시보드/수익률/뉴스/전략)
├── arquant_mobile/             # Android Compose UI — 웹 백엔드와 동일 API 사용
│   └── app/src/main/java/com/arquant/mobile/
│       ├── ui/MainScreen.kt
│       ├── ui/components/SideDrawer.kt        # statusBarsPadding + LazyColumn 스크롤
│       ├── ui/screens/{DashboardTab, PnlTab, StrategyTab}.kt
│       └── viewmodel/DashViewModel.kt          # 마크다운 정리 cleanLog 정규식
│
├── requirements.txt / requirements-dev.txt   # 의존성 핀 고정 (재현 가능 배포) [신규 2026-05-18]
├── pytest.ini                                # 테스트 설정 [신규]
├── tests/                                    # 결정론 핵심 회귀 테스트 (pytest, 56케이스) [신규]
│   ├── test_guardrails.py        # validate_order_draft — KR원/US$ 통화 분리·MDD·편중·예수금·사이클예산
│   ├── test_order_sizing.py      # _affordable_one_share 경계
│   ├── test_parsing.py           # 매도결정/진입가/코드추출 파서
│   ├── test_ops_worker_guards.py # 자가수정 롤백·diff상한·보호패턴 불변식
│   ├── test_news_weight_tuner.py # 뉴스분류 폐루프 진단
│   ├── test_backtest.py          # 백테스트 결정론·룩어헤드 없음
│   └── test_notifier_metrics.py  # 알림/메트릭 '절대 예외 안 던짐'
├── backtest/                                 # 프리셋 리스크/청산 규칙 백테스트 [신규]
│   ├── engine.py                 # 과거 일봉 워크포워드 (LLM 픽은 SMA 프록시로 고정)
│   └── report.py                 # 5개 프리셋 비교표 (python3.11 -m backtest.report)
└── infra/ 추가 모듈 [신규]
    ├── notifier.py               # 운영자 실패 알림 (중복억제·파일싱크·선택적 웹훅)
    ├── metrics.py                # 경량 구조화 메트릭 (data/metrics.jsonl)
    └── news_weight_tuner.py      # 뉴스분류 폐루프 결정론 진단기
```

---

## 🏛️ 에이전트 조직도 (9 + 1 + 3)

| # | 에이전트 | 역할 | 모델 | LLM 호출 |
|---|---------|------|------|---------|
| 1 | **운용전략실장** | Chief Orchestrator — **2패스**: ① 매크로 + 뉴스분석팀장 분석(원문 안 봄) → 후보 6종목 선정 / ② **종목별** 계량 평가 받아 최종 1~3개 매수 결정 + 자산 배분 권고(확대/축소/유지)를 매수 종목 수에 반영. 사이클 요약도 작성 | `moonshotai/kimi-k2.6` | ✅ 사이클당 3회 |
| 2 | **전략리서치팀장** | Macro Analyst — 글로벌 지수·**Tavily 실시간 시황·해설**·뉴스·공시로 매크로 방향성 (30분 캐시) + **직전 사이클 자산 배분 권고를 컨텍스트로 기억** | `deepseek/deepseek-v4-flash` | ✅ |
| 3 | **계량분석팀장** | Quant Analyst — **종목당 별개 호출**로 통일 5섹션 양식(추세·평균회귀·변동성·수급·뉴스연계)으로 평가, `퀀트점수: 코드=0~10` + 가중치 명시 + 선택적 `진입가: 코드=시장가/숫자/관망±X%` | `deepseek/deepseek-v4-flash` | ✅ 종목당 1회 |
| 4 | **뉴스분석팀장** | News Analyst — **운용전략실장보다 먼저** 증권 속보를 KR/US/공통으로 분류해 종목/업종·이벤트·매크로 시사점 정리 → 후보 선정의 최우선 입력 | `deepseek/deepseek-v4-flash` | ✅ |
| ⚙️ | *뉴스 큐레이터* (내부) | 누적 헤드라인이 `NEWS_PREFILTER_TRIGGER`(40)건 초과면 **결정론적 키워드 스코어링**으로 굵직한 40건 선별 → **LLM 미호출, 파싱 실패 X** | (파이썬 결정론) | — |
| ⚙️ | *뉴스 분류기* (내부) | 매 크롤마다 신규 헤드라인을 **LLM 배치 분류** → KR/US/BOTH 정확 라벨링. 검색 능력으로 기업명 → 상장 시장 자체 lookup → **화이트리스트·anti-example 불필요**. | `alibaba/tongyi-deepresearch-30b-a3b` (8차) | ✅ 크롤 시 |
| ⚙️ | *매크로 리서치* (내부) | 매크로 분석가 호출 직전, 세션별 1개 종합 쿼리로 외국인 수급·정책·심리·지정학 합성 (Tavily 대체) | `alibaba/tongyi-deepresearch-30b-a3b` (8차) | ✅ 매크로마다 |
| 5 | **트레이딩팀장** | Trader — **실행 직후** 체결 결과(체결가 포함) + 매매 사유를 한국어 산문으로 보고. JSON/표/마크다운 금지 | `deepseek/deepseek-v4-flash` | ✅ 실행 후 |
| 6 | **리스크관리실장** | Risk Guard — ① 파이썬 결정론 룰 게이트(KR=원/US=$ 자동 분리 표기) → ② 통과한 **매수** 종목의 **DART 최근 공시 + 직전연도 요약재무(재무상태표/손익계산서) 재심** | 룰 엔진 (Python) + `openrouter/free` | ✅ 매수 있을 때 |
| 7 | **사후관리실장** | Post-Management — **현재 세션 시장의 보유 종목**만 자유 재결정, 반대편 시장 종목은 자동 `보유`. 매크로 → 계량 → 뉴스 → 평가손익 순 가중 (`매도결정: 코드=전량/절반/보유`) | `moonshotai/kimi-k2.6` | ✅ 보유 있을 때 |
| 8 | **수탁자책임실장** | Policy Filter — 수탁자 책임·정책 적합성 필터 (간단 분류) | `openrouter/free` | ✅ |
| 9 | **운용지원실장** | Ops Support **조정자** — 사이클 결과 → 도메인 자동 분류(investment/operations/finance) → 산하 팀장 워커에 위임. spawn↔완료 메시지를 **`OPS#N` 마커**로 시각적 연결. 직접 코드 수정 X | `deepseek/deepseek-v4-pro` | ✅ 사이클 후 |
| 9a | **투자관리팀장** | Investment Sub-lead — 전략 프리셋, 후보 필터링/사이징, 매도/익절/손절 로직, 퀀트 임계값. 코드 수정 후 즉시 서버 재시작 | `deepseek/deepseek-v4-pro` | ✅ 위임 시 |
| 9b | **경영관리팀장** | Operations Sub-lead — server/app.py 엔드포인트, 대시보드 UX, 로깅·모니터링·자동 재시작 흐름 | `deepseek/deepseek-v4-pro` | ✅ 위임 시 |
| 9c | **재무관리팀장** | Finance Sub-lead — 예산 비율, 리스크 한도, P&L·equity curve 정확성, **리스크 표시 단위(원/달러)**, 환율·평가액 산출 | `deepseek/deepseek-v4-pro` | ✅ 위임 시 |

**모델 배정 원칙** (config.py:MODEL_ASSIGNMENTS)
- 최종 매수 결정자 (운용전략실장·사후관리실장) → **Kimi K2.6** (3000 tok) — 고지능 추론
- 매크로·계량·뉴스·트레이더 → **DeepSeek V4 Flash** (1800/4096/2600/1500 tok) — 빠르고 저렴
- **뉴스 분류기** → **`tencent/hy3-preview`** (12K tok, reasoning 모델) — 정확한 KR/US/BOTH 분류
- 리스크·정책 → **`openrouter/free`** — 비용 0, 폴백·단순 분류
- 운용지원실장 + 산하 팀장 → **DeepSeek V4 Pro** (8000 tok) — 코드 변경의 정확성 우선
- 변경: `config.py`의 `MODEL_ASSIGNMENTS` / `AGENT_MAX_TOKENS` 한 곳에서.

**사이드바 계층 표시** (사장 피드백 2026-05-15 4차)
```
운용전략실장              ← 최상위
  └ 전략리서치팀장       (Macro)
  └ 계량분석팀장         (Quant · 종목별)
  └ 뉴스분석팀장         (News)
  └ 트레이딩팀장         (Trader · 실행 보고)
리스크관리실장           ← 독립
사후관리실장             ← 독립
수탁자책임실장           ← 독립
운용지원실장              ← 최상위
  └ 투자관리팀장
  └ 경영관리팀장
  └ 재무관리팀장
```
'시스템' 라벨은 사이드바에서 제거됨. 시스템 broadcast는 적절한 에이전트 이름으로 재배정 (지수 수집 → 전략리서치팀장, 데이터 수집 → 계량분석팀장, 후보 사전 필터 → 운용전략실장 PASS1 메시지에 통합).

### 운용지원실장 위임 구조 (사장 피드백 #16)
1. 사이클 종료 직후 `main_swarm._classify_ops_role()`이 실행 결과를 분석해 도메인 판정:
   - **investment**: 주문 실패/거부, 종목·전략 변경, 퀀트 지표 임계값 → 투자관리팀장
   - **operations**: UI/로그/API/모니터링 → 경영관리팀장
   - **finance**: 예산 초과, 예수금 부족, $/원 단위 문제 → 재무관리팀장
   - 매칭 안 되면 통합 책임자(ops_support) 직접 처리
2. `infra/ops_support_worker.py`를 별도 프로세스로 spawn (`--role investment|operations|finance|ops_support`)
3. 산하 팀장 워커가 본인 도메인 안에서 보호 패턴(`FORBIDDEN_PATTERNS`) 가드를 통과하면 코드 수정 → 서버 재시작

### 운용지원실장 안전 규칙 (수정 금지 대상 — 변경 불가)
- `main_swarm.py`의 핵심 매매 로직: `_run_analysis_cycle`, `_build_orders`, `start_continuous`
- `config.py`의 API 키 / 계좌번호 (`KIS_APP_KEY`, `KIS_APP_SECRET`, `KIS_ACCOUNT_NO`)
- `infra/kis_broker.py`의 주문 집행부: `place_order`, `kr_buy`, `kr_sell`, `us_buy`, `us_sell`
- 위험·불가능하면 `"수정 없음 — 사유"`로 명시 응답 (`"(요약 없음)"` 같은 빈 응답 금지).

### 리스크 게이트 — 결정론적 (LLM 미사용)
`agents/guardrails.py::validate_order_draft()`가 매 사이클 주문 배치를 검증합니다. 규칙이 전부 결정론적 수치 비교이므로 LLM을 안 거칩니다.

| 게이트 | 균형형 한도 | 출처 |
|--------|------------|------|
| 보수적 MDD | 계좌 평가손익 ≤ −5% → 모든 신규 매수 반려 | `CONSERVATIVE_MDD` |
| 단일 종목 비중 | notional > 총평가 × 15% → 반려 | `CONSERVATIVE_STOCK_RATIO` |
| 예수금 버퍼 | notional × 1.10 > 예수금 → 반려 | `MIN_CASH_BUFFER` |
| 사이클 누적 예산 | 사이클 매수 합 > 예수금 × 25% → 반려 | `MAX_CYCLE_BUDGET_RATIO` |
| 수량/사유 | qty ≤ 0, qty > 1000, reason 누락 → 반려 | 구조 검증 |
| 시세 조회 | 현재가 미확보 시 사이즈 검증 불가 → 보수적 반려 | — |

**표시 단위 자동 분리 (사장 피드백 #7)** — `r["ticker"]`가 6자리 숫자면 `원`, 영문이면 `$` + 환산 원화 병기. 예:
- `✅ 승인 005930 buy x3 @ 58,000원 (≈174,000원)` ← KR
- `✅ 승인 JD buy x1 @ $33.06 (≈$33.06 / ≈49,590원)` ← US

### 2차 리스크 재심 — DART 공시 + 직전연도 요약재무 기반 AI (매수만)
`_dart_risk_review()`가 1차 통과한 **매수** 주문의 종목별 다음 정보를 LLM에 넘기고, 리스크 신호가 있으면 그 종목만 반려합니다:

1. **최근 14일 공시 요약** — `search_disclosures(corp_name=…)`
2. **직전연도 요약재무 (사장 피드백 #7 신규)** — `get_financial_summary_by_stock_code(code)`:
   - 첫 호출 시 `corpCode.xml` 다운로드 → stock_code(6자리) ↔ corp_code(8자리) 매핑 캐시 (메모리, ~3,965건)
   - `fnlttSinglAcntAll.json` (연결 → 별도 폴백) 호출 후 핵심 계정만 추출:
     - **재무상태표**: 자산총계 / 부채총계 / 자본총계 / 유동·비유동 분리
     - **손익계산서**: 매출액 / 영업이익 / 당기순이익 등

리스크 신호:
- 관리종목 지정 / 거래정지 / 상장폐지 심사
- 횡령·배임 / 회계감리 / 불성실공시법인 지정
- 감자 / 대규모 유상증자(희석) / 전환사채·신주인수권 대량 발행
- 주요 소송·제재·과징금·영업정지·리콜
- 최대주주 변경·경영권 분쟁

응답은 `최종승인: 코드, 코드` 한 줄. 파싱 실패·LLM 에러 시 **fail-open**(1차 승인 유지). 매도는 리스크 감소 행위라 재심 안 거침.

#### 조회 실패 vs 공시 없음 명확 구분 (2026-05-19 ITEM2a)
DART 결과를 `DartResult` 데이터클래스의 **3-state**로 구분한다(`tools/dart_disclosure.py`):
- **`OK`** — 조회 성공, 공시/재무 존재.
- **`NO_DISCLOSURE`** — 조회는 성공했으나 해당 기간 공시·재무가 없음 → **정상**(특이사항 없음). 매수 차단 사유 아님.
- **`QUERY_FAILED`** — API 키 없음·HTTP 오류·타임아웃·파싱 실패 → **시스템 리스크 신호**.
  "신호 없음"을 "악재 없음"으로 오독하지 않도록, 조회 실패는 절대 안전 신호로 취급하지 않는다.

#### 재무상태표 정합성 가드 — 부채>자산 오판 방지 (2026-05-19 ITEM2b)
`parse_balance_sheet_sanity()`가 추출한 재무상태표를 검증한다:
- `OK` / `PARSE_FAILED` / **`IMPOSSIBLE`**(부채총계>자산총계, 또는 자본 ≠ 자산−부채 회계항등식 붕괴).
- `IMPOSSIBLE`이면 `debt_ratio=None`으로 두고 **`QUERY_FAILED`로 격상** → 데이터 오류를 근거로
  "자본잠식/부실" 판정을 내리는 것을 차단. (2026-05-19 12:45:17에 리스크관리실장이
  파싱 오류 수치로 잘못된 `부채총계>자산총계` 판단을 내렸던 사건의 재발 방지.)

---

## ⚙️ 의사결정 흐름

```
[1]  글로벌 지수 수집 (10종, 네이버) — agent: 전략리서치팀장
       · KOSPI/KOSDAQ/KPI200/DJI/SPX/IXIC/SHS/NKY/USDKRW/WTI
       · **모든 가격 인용의 1차 권위 출처** (Tavily 가격 인용 금지, 매크로 분석가가 여기서만 인용)
       ↓
[2]  DART 전체 공시 (최근 3일) — **KR 세션 전용 + 30분 캐시** (US/장외엔 호출 생략, 사장 피드백 #20)
       ↓
[3.0] 뉴스 큐레이터  →  누적 헤드라인 > 40건이면 **결정론적 키워드 스코어**로 굵직한 40건 선별 (LLM 미호출)
       ↓
[3.0b] 뉴스 LLM 분류 (tencent/hy3-preview)  →  매 크롤마다 새 헤드라인을 KR/US/BOTH 정확 라벨링
       · decision tree(STEP1 기업명 검출 → 그 상장국, STEP2 매크로 테마)
       · 한국 100+ 기업·미국 50+ 기업 화이트리스트
       · anti-examples (실제 오분류 사례 ❌→✅로 학습)
       · reasoning 모델 → max 12K tok + reasoning 필드 폴백
       ↓
[3.5] 뉴스분석팀장  →  증권 속보 KR/US/공통 분류 + 언급 종목/업종·이벤트·매크로 시사점 정리
       ↓
[3.7] 🌐 Tavily 매크로 검색 (전략리서치팀장 — 30분 캐시)
       · 세션별 4쿼리 (sentiment·positioning·outlook·policy 중심, **가격 키워드 배제**)
       · KR: 외국인 수급·한은 정책 / US: 연준·고용/물가 / 공통: 유가 수급·미중관계
       · 결과는 **시황 해설·정책 전망·심리 분석만 사용** (가격은 절대 인용 금지)
       ↓
[3]  매크로 분석 (전략리서치팀장, 30분 캐시) — **직전 사이클 자산 배분 권고를 컨텍스트로 주입** (사장 피드백 #18)
       · 가격 인용 우선순위: ① 검증된 지수(네이버) → ② Tavily 해설(가격 X) → ③ 뉴스 이벤트
       ↓
[PASS 1] 운용전략실장  →  후보 6종목  (`후보종목: ...`)
         · 뉴스분석팀장이 짚은 종목을 최우선
         · 부족분만 매크로 판단으로 채움
         · 대형주 편중 금지 (시총 상/중/소형 + 다른 업종)
         · 1주 매수 예산(총평가×PER_ORDER_BUDGET_RATIO) 안내받아 초고가 종목 회피
         · 세션 인지: US 정규장 → 미국 티커만 / KR 세션 → 국내 위주
         · 후보 사전 필터로 예산 1.5배 초과 종목 자동 제외
       ↓
[4]  후보 6 ∪ 보유(현재 세션 시장) 종목 데이터 수집 — agent: 계량분석팀장
       · 3년 일봉 + 수급 + 분봉 (네이버 + KIS)
       · 종목별 DART 최근 14일 공시 + **직전연도 요약재무** (자산/부채/자본 + 매출/영업이익/순이익)
       · 종목명 맵 (이름 환각 방지)
       · **상장폐지/거래정지 의심 종목 자동 감지·표기** (사장 피드백 #23)
       ↓
[5]  KIS 거래량 순위
       ↓
[6]  계량분석팀장  →  **종목당 1회 호출** (사장 피드백 #23) → 별개 메시지로 출력
         · **통일 5섹션 양식** 강제 (사장 피드백 2026-05-15 3차):
           ▶ 1. 추세·모멘텀 (SMA 배열, ADX, 1M/3M 수익률, 신고가 근접도)
           ▶ 2. 평균회귀·과열 (RSI, %B, z-score)
           ▶ 3. 변동성·리스크 (σ20, ATR, 레짐)
           ▶ 4. 수급·거래량 (기관·외인, 거래량 추세, VWAP)
           ▶ 5. 뉴스·이벤트 연계 (감성 인용, 매크로 부합)
           ▶ 결론 (점수 + 가중치 명시 + 핵심 리스크)
         · 첫 호출 실패 시 1회 자동 retry
         · 마지막 두 줄: `퀀트점수: 코드=0~10` + (선택) `진입가: 코드=시장가|숫자|관망±X%`
       ↓
[PASS 2] 운용전략실장  →  최종 매수 종목 (`최종종목: ...`)
         · 후보 6개 중 N개 이하 (N = MAX_TRADES_PER_CYCLE, 프리셋별 1/2/3)
         · **매크로 자산 배분 권고 반영** — 주식 비중 확대→N개 풀, 축소→적게, 유지→절반 (사장 피드백 #2)
         · 퀀트 점수 낮거나 뉴스 부정적이면 제외 (0개 허용)
         · 후보 목록 외 코드는 코드 측에서 교집합 필터로 차단
       ↓
[6.5] 사후관리실장  →  **현재 세션 시장 보유 종목만** 분석 (`매도결정: 코드=전량/절반/보유`)
         · 매크로 → 계량 → 뉴스 → 평가손익 순 가중
         · **반대편 시장 보유 종목은 자동 `보유` 주입** (사장 피드백 #5, #13)
         · `ALLOW_DAY_TRADING` 토글로 데이트레이딩 룰 제어 (기본 ON)
         · 손실 회피 편향 경계 (펀더멘털 망가지면 손절)
       ↓
[7]  파이썬 OrderDraft 조립 (LLM 미호출) — agent: 트레이딩팀장 (시스템 상태만 broadcast)
         · 매수 = 최종종목, 사이징:
           - **KR**: floor(min(예수금·PER_ORDER_BUDGET_RATIO, 총평가·CONSERVATIVE_STOCK_RATIO) / 가격)
           - **US**: floor(예산_USD / 주가) — `예산_USD = 예산_원화 / 1500` (사장 피드백 #14: 8달러 SOUN을 5주 살 수 있도록)
         · 1주 가격이 예산 초과해도 **PER_ORDER_BUDGET_OVERSHOOT(기본 +20%)** 이내면 1주 매수 허용
         · **진입가 directive 첨부** (entry_mode=market/limit/watch)
         · 매도 = 사후관리실장 결정 (미언급 종목은 자동 익절/손절/편중축소 안전망)
         · 세션 인지 필터: KR 주문은 KR 시간대에만, US 주문은 US 시간대에만
       ↓
[8]  리스크 검증
         (a) 결정론 룰 게이트 (위 표 참조) — KR=원, US=$ 자동 분리 표기
         (b) 매수 종목에 한해 DART 공시 + 직전연도 요약재무 재심 (`최종승인:`)
       ↓
[9]  실행 (LIVE_TRADING=True) — **entry_mode별 분기 (사장 피드백 #4)**
         · `market`: 즉시 시장가 매수 (기존 동작)
         · `limit`: KIS 지정가 큐잉 (장 마감 시 KIS 자동 취소)
         · `watch`: `_entry_watch_task()` spawn — 1분 폴링으로 최대 3시간 대기 → 트리거 도달 시·타임아웃 시 시장가, 장 마감 시 취소
         · **주문 전후 holdings 스냅샷** → 매수 체결가는 `(after_avg×after_qty − before_avg×before_qty) ÷ buy_qty`로 정확히 역산, 매도 체결가는 직전 `kr_last_price` 스냅샷 사용 (시장가 매도 = 직전 호가 근사)
         · 매도(사후관리 우선) + 매수 최대 N건
         · 초당 거래건수 제한 → 최대 3회 재시도 + 1.5~2.5s 페이스
         · **접수 ≠ 체결** (사장 피드백 #8, #15): KR은 잔고 변동 확인 후에만 누적 카운트 + 사이클 카운트
         · 미체결 주문은 5분 후 `_reverify_fills` 백그라운드 태스크로 재확인
         · 각 trade event에 `fill_price`(실제) + `fill_currency` 필드 저장
       ↓
[9.5] 트레이딩팀장 (deepseek-v4-flash) → **체결 결과 + 매매 사유를 한국어 산문으로 보고** (사장 피드백 3차)
         · 호출 시점: 리스크 검증·실행 직후 (조립 직후가 아님)
         · 입력: 종목별 결과(체결 확인/접수만/실패), 매수 사유, 매도 사유, 누적 통계
         · 출력: "005430 1주 매도해 보유 1→0주 확인됐고, 사후관리 손절 판단대로… 024110 3주 매수 진입…"
         · JSON·코드 블록·`**` 강조 금지, 산문체로만
       ↓
[10] 운용전략실장 사이클 요약 → 곧장 감시 상태 복귀 (쿨다운 없음)
       ↓
[11] 운용지원실장  →  도메인 자동 분류 → 산하 팀장 워커 spawn (investment/operations/finance)
         · 사이클 결과(실행 결과, 에러 패턴)로 도메인 판정
         · **`OPS#N` 마커**로 spawn 메시지 ↔ 완료 메시지 시각적 연결 (사장 피드백 4차)
         · spawn 메시지에 분류 근거·사이클 요약·예상 소요(1~3분) 명시
         · 완료 메시지는 분류된 결과로 첫 줄 헤드라인 (`✅ N건 수정 완료` / `ℹ️ 변경 없음` / `⛔ 보호 패턴 거부` / `⚠️ JSON 파싱 실패`)
         · 보호 패턴 가드 통과 시 코드 수정 → 서버 재시작 (`RESUME_ON_BOOT` 마커로 감시 자동 재개)
```

**에이전트 발언 → 의사결정 반영 경로 (요약)**

| 출력 | 어떻게 쓰이나 |
|------|--------------|
| 글로벌 지수 (네이버) | 모든 가격 인용의 1순위 출처 (Tavily 가격 인용 금지) |
| **Tavily 매크로 검색** | 매크로 분석가에 시황 해설·정책 전망·심리·수급 분석 주입 (가격은 무시) |
| 매크로 보고 + **직전 권고 메모리** | PASS 1·PASS 2·사후관리실장 프롬프트에 주입. PASS 2가 자산 배분 권고에 따라 매수 종목 수 조절 |
| 뉴스 큐레이터 선별 (결정론) | 뉴스분석팀장이 받는 헤드라인 수 캡 (40건) — LLM 미호출 |
| **뉴스 LLM 분류** (tencent/hy3) | 각 헤드라인 KR/US/BOTH 정확 라벨 → 세션별 풀에 라우팅 |
| 뉴스분석팀장 분석 | **PASS 1의 최우선 입력** (뉴스 종목 우선 후보화) + PASS 2/계량/사후관리 참고 |
| 운용전략실장 후보(`후보종목:`) | 분석 대상 종목 집합 (정확히 6개, 사전 필터로 예산 초과 제거) |
| 계량분석팀장(`퀀트점수:`) | PASS 2 최종 선정 + 사후관리 판단 (종목당 별개 호출, 통일 5섹션) |
| 계량분석팀장(`진입가:`) | 트레이딩팀장 분봉 모니터링 / KIS 지정가 큐잉 |
| 운용전략실장 최종(`최종종목:`) | **실제 매수 종목** (후보 교집합·N 이하·매크로 권고 반영) |
| 사후관리실장(`매도결정:`) | **현재 세션 시장 보유 종목** 매도/보유 (반대편은 자동 보유) |
| 리스크관리실장(`최종승인:`) | 결정론 룰 통과한 매수의 DART + 재무 재심 결과 |
| **트레이딩팀장** (자연어, 실행 후) | 체결 결과·실제 체결가·매매 사유 한국어 보고 (대시보드 통신 로그) |
| 운용지원실장 분류 | `OPS#N` 마커로 spawn → 도메인 판정 → investment/operations/finance/ops_support 중 1개 워커 spawn |

---

## ⏰ 무한 시장 감시 루프

### 트리거 — 단 세 가지
1. **▶ 실행 직후 첫 회** — 누적된 뉴스로 즉시 1회 (장중일 때)
2. **장 개장 순간** — 한국 08:50 `KR_PRE_MARKET` 진입 / 미국 22:30 `US_TRADING` 진입 시 그때까지 누적된 뉴스로 1회
3. **1시간 정기** — `PERIODIC_CYCLE_SEC` 초마다, 단 장이 열려 있는 동안만

> 트리거 충돌(예: 첫 회·개장이 겹침) 시 한 번만 실행. 사이클 후 쿨다운 없음.

### 뉴스 파이프라인 (사장 피드백 #10, #17, #22)
- **10분마다** (`NEWS_CHECK_INTERVAL=600`) 네이버 금융 '증권 속보'(`news_list.naver?mode=LSS2D&section_id=101&section_id2=258`) 크롤링 — 레이아웃 못 읽으면 메인 뉴스(`mainnews.naver`)로 폴백
- 중복 제거: URL + 정규화 제목 difflib ≥ 0.86 (재게재/재배포 차단, 최근 800건 기억)
- **크롤 시점에 KR/US/공통(BOTH) 자동 분류** (`tools/news_monitor.py::classify_market`):
  - **KR**: 코스피·코스닥·삼성전자·SK하이닉스·국내 증권사 등 한국 키워드 + 6자리 종목코드 매칭
  - **US**: 나스닥·다우·S&P500·FOMC·연준·NVDA·AAPL 등 미국 키워드 + 미국 티커 매칭
  - **공통**: 반도체 업종 전반·AI·유가·환율·미중 관계 — KR/US 양쪽 사이클 모두 참고
- 세션별 라우팅: KR 세션엔 KR+공통, US 세션엔 US+공통 뉴스만 사용 (반대편 뉴스는 별도 풀에 누적)
- **40건 초과 시 사전 큐레이터** — 결정론적 키워드 스코어링 (LLM 미호출, `MATERIAL_NEWS_KEYWORDS` 매칭 + 종목코드 가중치) → 굵직한 40건만 뉴스분석팀장에게 전달
- 뉴스 피드는 **별도 탭(웹) / 사이드바 안내(모바일)**에서 KR/US/공통 필터로 조회. 사이드바는 에이전트 목록 전용

### 세션 (KST)
| 세션 | 시간 | 동작 |
|------|------|------|
| `KR_PRE_MARKET` | 08:50 ~ 09:00 | 매크로 수집, 전략 수립, **개장 트리거** |
| `KR_TRADING` | 09:00 ~ 15:30 | KRX 장중 거래, 정기 사이클 활성 |
| `KR_CLOSE_REVIEW` | 15:35 ~ 15:50 | 장 마감 리뷰 |
| `US_TRADING` | 22:30 ~ 05:00 | 미국 야간 거래, **개장 트리거** |
| `OFF_HOURS` | 그 외 | 뉴스만 수집, 거래 없음, 상태 배지는 **전환 시 1회만** 브로드캐스트 |

세션 인지는 두 단계에 작동:
- **추천 단계** — PASS 1·PASS 2 프롬프트에 세션별 힌트(US장엔 미국 티커만, KR장엔 국내 위주)
- **주문 조립 단계** — KR 주문은 KR 세션, US 주문은 US 세션에서만 통과 (장운영일자 불일치 방지)

---

## 💼 KIS 통합 포트폴리오

`portfolio_holdings()`가 4개 카테고리를 병합:

| 카테고리 | 조회 API | 비고 |
|---------|----------|------|
| 국내주식 | `inquire-balance` | `category:"국내주식"` |
| 해외주식 | `TTTS3012R` (NASD/NYSE/AMEX 순) | `category:"해외주식"`, USD 평가, 거래소 자동 탐지 + 티커별 캐시 |
| 국내채권 | `domestic-bond/.../inquire-balance` (`CTSC8407R`) | `category:"국내채권"`, best-effort |
| 펀드 | — | KIS 공개 펀드 잔고 API 없음 → `[]` |

**예수금/총평가 산정 규칙** (D+2 정산 톱니파 방지)
- 예수금 우선순위: `prvs_rcdl_excc_amt`(D+2) → `nxdy_excc_amt`(D+1) → `dnca_tot_amt`(D+0)
- 총평가: `nass_amt` → `tot_evlu_amt` → `유가증권평가 + 예수금` 재구성 보정 (3% 오차 이상이면 재구성)

**KIS 접근 토큰 (24h 유효) — `data/kis_token.json`에 캐싱**
- 메모리 → 디스크(만료 10분 전까지 재사용, 재시작/멀티클라이언트 무관) → 만료 시에만 1회 신규 발급 후 디스크에 저장
- 발급 실패(EGW00133 등) 시 디스크에 남은 유효 토큰으로 폴백 → **재시작해도 24h 안에는 재발급 안 됨**(KIS "새 토큰 발급" 메일도 안 옴)

**대시보드 잔고 자동 갱신**
- 자동 폴링 **10분 주기**, **OFF_HOURS면 폴링 스킵**(잔고 변할 일 없음)
- 카드 헤더에 **🔄 새로고침** 버튼 → 세션 무관 즉시 강제 갱신
- bpLine 끝에 `HH:MM KST 갱신` 표기

---

## 📈 평가금액 추이 (수익률 탭)

`data/equity_curve.json`에 60s 최소 간격으로 `{ts, total_eval, cash, pnl_ratio, src}` 누적(2000 pt cap). 사이클 시점과 `/api/balance` 폴링 시점에 기록.

### 3가지 뷰 토글
| 뷰 | 정의 | 라벨 |
|----|------|------|
| **실시간** | KR 09:00~15:30 + US 22:30~05:00 KST 포인트만 + 10분 버킷의 마지막값으로 다운샘플 | `MM-DD HH:M0` |
| **일별** | KST 일자별 마지막 포인트 | `YYYY-MM-DD` |
| **월별** | KST 월별 마지막 포인트 | `YYYY-MM` |

> naive 타임스탬프(과거 UTC 기록)는 자동으로 UTC로 간주 → KST 변환. 신규 기록은 `datetime.now(KST).isoformat()`(+09:00 박힘).

---

## 🎛️ 전략 프리셋

`config.py::STRATEGY_PRESETS` — 5단계 (방어형/보수형/균형형/공격형/초공격형) + 사용자 정의 프리셋(`data/user_presets.json`). 대시보드 '전략' 탭에서 선택·커스터마이즈, `runtime.py`가 영속화 + 라이브 오버라이드.

| 키 | 방어형 | 보수형 | **균형형 (기본)** | 공격형 | 초공격형 | 의미 |
|----|--------|--------|------------------|--------|----------|------|
| `PER_ORDER_BUDGET_RATIO` | 3% | 5% | **10%** | 20% | 35% | 1주문당 예수금 비율 |
| `PER_ORDER_BUDGET_OVERSHOOT` | 1.05 | 1.10 | **1.20** | 1.30 | 1.50 | 1주 예산 +X% 이내면 1주 매수 허용 |
| `MAX_CYCLE_BUDGET_RATIO` | 10% | 15% | **25%** | 40% | 70% | 사이클 누적 예산 |
| `MIN_CASH_BUFFER` | 1.20 | 1.15 | **1.10** | 1.05 | 1.02 | 예수금 안전여유 |
| `CONSERVATIVE_MDD` | 2.5% | 4% | **5%** | 8% | 15% | 계좌 평가손익 이 이상 마이너스면 신규 매수 차단 |
| `CONSERVATIVE_STOCK_RATIO` | 7% | 10% | **15%** | 25% | 40% | 단일 종목 비중 한도 |
| `MAX_TRADES_PER_CYCLE` | 1 | 1 | **2** | 3 | 5 | 사이클당 최대 매수 (= PASS 2의 N) |
| `MAX_ORDER_QTY` | 0 | 0 | **0** | 0 | 0 | 1주문 수량 상한 (0=무제한) |
| `TAKE_PROFIT_PCT` | 6% | 8% | **12%** | 18% | 30% | 자동 익절 |
| `STOP_LOSS_PCT` | 3.5% | 5% | **7%** | 10% | 15% | 자동 손절 |
| `TRIM_OVER_RATIO` | True | True | **True** | False | False | 종목 비중 초과 시 자동 트림 |
| **`ALLOW_DAY_TRADING`** | False | False | **True** | True | True | **데이트레이딩 허용** (사장 피드백 #24) |
| **`MIN_HOLDING_DAYS_FOR_SELL`** | 1.0 | 0.5 | **0.5** | 0.0 | 0.0 | 데이트레이딩 OFF일 때만 — 최소 보유일 |
| `ENABLE_SELL_REBALANCE` | True | True | **True** | True | True | 매도 룰 활성 |
| `ENABLE_CHEAP_FALLBACK` | False | False | **False** | False | False | 저가 종목 대체 후보 매수 (사장 지시로 전부 비활성) |
| `ALLOW_US_STOCKS` | False | False | **True** | True | True | 해외주식 매수 허용 |
| `ALLOW_DERIVATIVES` | False | False | **False** | False | False | 파생상품 자동매매 |

### 전략 커스터마이즈 (웹 대시보드 — 사장 피드백 #24)
- 전략 탭의 **🛠 전략 커스터마이즈** 박스(평소엔 납작, 클릭 시 펼침)
- 각 파라미터를 **한국어 라벨** + **단위 표시(%, ×, 일, 건)**로 직접 입력
  - 예: `1주문 예수금 사용 비율: 10 %`, `데이트레이딩 허용: ON/OFF`
- **즉시 적용** 버튼 → `runtime.set_strategy("custom", custom={...})` 으로 라이브 오버라이드
- **프리셋으로 저장** → `data/user_presets.json`에 영구 저장, 사이드바에 사용자 프리셋으로 노출(삭제 가능)

### 데이트레이딩 룰 (사장 피드백 #24 — 0.5일 규칙 폐기)
- 직전 버전에는 사후관리실장 프롬프트에 "0.5일 미만은 데이트레이딩 회피"가 하드코딩되어 있었으나, 사장 지시로 **전략 프리셋의 토글 파라미터**로 분리:
  - `ALLOW_DAY_TRADING=True` (균형형 이상 기본): 보유기간 무시, 신호 따라 자유 매도
  - `ALLOW_DAY_TRADING=False`: `MIN_HOLDING_DAYS_FOR_SELL` 미만 보유 종목은 사후관리실장이 매도 회피 권고
- 사후관리실장 프롬프트에 동적으로 가이드 주입

### 대체 후보 (현재 OFF)
`ENABLE_CHEAP_FALLBACK=False`라 최종 지정 종목을 못 사면 그냥 신규 매수 생략. 코드는 `_build_orders` ③에 남아 있어 전략 탭에서 재활성 가능. 켤 경우 순서: ① 1차 후보 6개 중 예산 내 최저가 → ② KR: 거래량 상위에서 `CHEAP_FALLBACK_EXCLUDE_KEYWORDS`(레버리지/인버스/곱버스/2X/3X/선물/ETN/TR/커버드콜) + 2,000원 미만 제외 후 최저가 / US: `CHEAP_FALLBACK_US_TICKERS`(F·BAC·T·PFE·KO·CSCO·INTC·SOFI·NU·WBD·SIRI·KVUE·VALE) 중 최저가.

---

## ⏱️ 분봉 진입 타이밍 (사장 피드백 #4 — 신규)

계량분석팀장이 종목당 평가 마지막에 선택적으로 진입가 directive를 출력하면, 트레이딩팀장이 분봉 단위로 모니터링하다가 트리거 도달 시·타임아웃 시 매수합니다.

```
퀀트점수: 005930=8
진입가: 005930=시장가          ← (default) 즉시 시장가 매수
진입가: 005930=58000           ← KIS 지정가 큐잉 (장 마감 시 자동 취소)
진입가: 005930=관망 -1.5%      ← 현재가 -1.5% 도달 시 분봉 모니터링 후 시장가 매수
진입가: 005930=관망 +1.0%      ← 현재가 +1.0% 돌파 시 모멘텀 추격 매수
```

**3가지 모드**

| 모드 | 동작 | 트리거 조건 | 타임아웃 | 장 마감 시 |
|------|------|-------------|----------|-----------|
| `market` | 즉시 시장가 매수 | — | — | — |
| `limit` | KIS 지정가 주문으로 큐잉 | 지정가 도달 | 장 마감 (KIS 자동 취소) | KIS 자동 취소 |
| `watch` | `_entry_watch_task()` 백그라운드 spawn | `watch_pct` % 도달 | **3시간** 후 시장가 fire | 즉시 취소 (broadcast) |

**watch 모드 동작 (`main_swarm._entry_watch_task`)**
- 1분 주기 폴링 (`kr_last_price` 또는 `us_last_price`)
- 시작가 대비 `watch_pct` % 변동 도달 시 → 시장가 매수 발동
- 5분 단위로 진행 상황 broadcast: `⏱ 005930 분봉 모니터 (5분 경과): 현재가 X / 목표 Y (Z%)`
- 3시간 경과 시 → 타임아웃, 시장가 매수 (계량 의도대로 일단 진입)
- 도중에 세션이 닫히면 (`is_kr and sess not in ("KR_TRADING","KR_PRE_MARKET")` 등) → 매수 취소
- 사용자가 `⏹ 중지` 누르면 → 매수 취소
- 매수 후 체결 확인은 일반 경로와 동일 (잔고 변동 확인 후 누적 카운트)

---

## 📜 거래 내역 상세 보기 (사장 피드백 2026-05-15 5~6차 — 신규)

수익률 탭의 거래 내역 행을 클릭하면 추정 가격·FIFO 매칭·실현 손익이 인라인으로 펼쳐집니다.

### 실제 체결가 추적 (사장 피드백 6차)

KIS 시장가 주문은 응답에 정확한 체결가를 안 주지만, **주문 전후 holdings 스냅샷**으로 정확히 계산합니다:

| 방향 | 정확한 체결가 산출 방법 | 정확도 |
|------|------------------------|--------|
| **매수** | `(after_avg×after_qty − before_avg×before_qty) ÷ buy_qty` ← 수학적으로 정확 | ✅ 정확 |
| **매도** | 주문 직전 `kr_last_price()` 스냅샷 (시장가 매도 = 직전 호가 ±1틱) | ≈ 매우 근사 |

각 trade event에 `fill_price`, `fill_currency`, `price_source: "actual"` 필드 저장. 폴백 우선순위:
1. 실제 체결가 (`fill_price`) ← 새 거래의 표준
2. cycle reason의 `≈X원` 추정 (`est_price`) ← 서버 재시작 전 기록
3. `평가손익 %` × 매수가 역산 (`pnl_pct_hint`) ← 매도 reason에서

### FIFO 매칭 P&L

`main_swarm._enrich_trade_history()`가 모든 trade event를 시간순으로 처리:
- 같은 종목의 매수를 FIFO 큐로 누적 (qty, price, ts)
- 매도 시 큐 head부터 차감 → 매도가-매수가 차이로 실현 손익 lot별 계산
- 한 매도가 여러 매수 lot에 걸치면 분리 표시 (한국 회계 관행)

### UI 표시

**요약 행** (한 줄):
```
🔻 매도 024110 ×3 @62,708원 (-1,326원) ✓체결 — 보유 3→0주 확인 · 체결가 ≈62,708원
```

**클릭 펼침** (인라인 detail):
```
🔻 매도 체결 · 024110 3주 · 2026-05-15 14:40:57  (사이클 #17)
  · 수량: 3주
  · 매도 체결가: 62,708원
    (주문 직전 last_price — 시장가 매도 근사)
  · 매도 시점 평가손익(인용): -0.70%

📊 FIFO 매칭 — 매수 ↔ 매도
  1. 3주 매수 (05-15 12:43) @63,150원
     → 매도 @62,708원 = -1,326원 (-0.70%)

💰 실현 손익 합계: -1,326원

원본 응답: ✅ 실매매 체결확인 — [국내매도] 024110 3주 → 주문 전송 완료...
```

웹: 한 번에 하나의 행만 펼쳐짐 (새 행 클릭 시 기존 detail 자동 닫힘).
모바일: `mutableStateOf(false)` per row, `▾/▴` 토글 인디케이터.

---

## 🔎 매크로 종합 리서치 (사장 피드백 2026-05-15 8차 — Tavily 대체)

매크로 분석 직전, 전략리서치팀장이 **`alibaba/tongyi-deepresearch-30b-a3b`** (deep-research agent)로 **시황 해설·정책 전망·심리·수급** 분석을 종합 합성합니다. Tavily는 완전 제거됨.

### 역할 분리 — 가격 vs 해설

| 출처 | 용도 | 인용 가능? |
|------|------|-----------|
| 글로벌 지수 (네이버 크롤) | 모든 가격·등락률·지수값 | ✅ **1순위 — 여기서만 인용** |
| 매크로 리서치 (alibaba) | 시황 해설·정책 전망·심리·수급·지정학 합성 | ✅ 해설만, ❌ **가격 수치 인용 금지** |
| 뉴스분석팀장 | 이벤트·종목 감성 흐름 | ✅ 3순위 |

### Tavily 대비 변화

| 항목 | Tavily (이전) | alibaba (현재) |
|------|--------------|----------------|
| API 키 | 별도 `TAVILY_API_KEY` 필요 | OpenRouter 통합 |
| 호출 패턴 | 4개 단일 쿼리 × 검색 결과 raw 반환 | 1개 종합 쿼리 → 합성 답변 |
| 응답 형식 | 검색 결과 리스트 (raw URL + 발췌) | 한국어 합성 분석 (해설 + 출처 인용) |
| 사이클당 비용 | ~$0.02–0.04 | ~$0.01–0.02 (reasoning 토큰 포함) |
| 응답 시간 | ~9초 (4 쿼리 직렬) | ~8초 (1 쿼리, reasoning 포함) |

### 세션별 종합 쿼리

| 세션 | 4가지 관점 종합 (한 쿼리) |
|------|------------------------|
| **KR** | 외국인 수급 동향 · 한은 금리 정책 전망 · 코스피·코스닥 투자심리 · 원/달러 환율 영향 |
| **US** | 연준 통화정책 방향 · 美 노동·물가 매크로 · S&P/Nasdaq 포지셔닝 · 美 국채·달러 영향 |
| **OFF** | 글로벌 위험선호 사이클 · 미중 관계 동향 · 지정학 리스크 · 원자재·유가 수급 |

### 캐싱·비용

- **30분 캐시** (`_research_cache[session]`) — 매크로 분석 캐시와 동기화
- `max_tokens=8000` (reasoning 모델이라 내부 사고 + 합성 답변 모두 수용)
- 사이클당 평균 ~$0.01–0.02 (캐시 hit 시 $0)
- **일 $0.3–0.6, 월 $10–20** 수준 (Tavily보다 절감)

### 프롬프트 가이드 (specialists.py:create_macro_analyst)

```
## 가격 인용 규칙
- 모든 가격·등락률·지수값은 '검증된 글로벌 지수' 표에서만 인용.
- 매크로 리서치(alibaba) 결과에 가격이 보여도 절대 인용 금지 (출처·시점 불명).
- 리서치에서 가져올 것: "외국인 매도가 펀더멘털 약화 아닌 리밸런싱",
  "연준 도비시 스탠스", "OPEC 감산 가능성" 같은 해설·전망·심리만.
```

---

## 📰 뉴스 LLM 분류 (사장 피드백 2026-05-15 4~8차 — 정확도 100%)

크롤된 모든 새 헤드라인을 **`alibaba/tongyi-deepresearch-30b-a3b`** (검색·reasoning 통합 모델)로 KR/US/BOTH 라벨링. 모델이 기업명 → 상장 시장을 자체적으로 lookup하므로 화이트리스트·anti-example 같은 긴 컨텍스트 **불필요**.

### 알고리즘 (시스템 프롬프트)

**STEP 1**: 헤드라인에 *구체적 기업명*이 있는가?
- 한국 기업(KOSPI/KOSDAQ 100+ 화이트리스트) → **KR**
- 미국 기업(NYSE/NASDAQ 50+ 화이트리스트) → **US**
- 둘 다 → **BOTH** (예: "삼성전자·엔비디아 HBM 협력")

**STEP 2**: 구체적 기업명이 없을 때만 매크로 분류
- 한국 거시(한은·원달러·한국 GDP) → KR
- 미국 거시(Fed·미국 CPI·국채) → US
- 글로벌(WTI·미중·지정학·반도체 사이클·환율 전반) → BOTH

### Anti-Examples (실제 오분류 케이스 학습)

```
❌ "농심 1분기 영업익 674억"            → 공통 (틀림)
✅                                       → KR  (농심 KOSPI 004370)

❌ "두나무 키운 카카오, 1조 벌었다"     → 공통 (틀림)
✅                                       → KR

❌ "엔비디아 시총 5조 달러 돌파"        → 공통 (틀림)
✅                                       → US

✅ "WTI 원유 101달러대 유지"            → BOTH (글로벌 매크로, 기업 없음)
✅ "미중 정상회담 훈풍"                 → BOTH (양국 관계)
```

### 모델 특성 — Search-Capable Reasoning

`alibaba/tongyi-deepresearch-30b-a3b`는 **MoE deep-research agent** (30B params, 3B active):
- 내부 사고 + 검색 능력 → 헤드라인의 기업명을 동적으로 상장 시장에 매핑
- 짧은 프롬프트로도 동작 (이전 130줄 화이트리스트 → 13줄 핵심 규칙)
- `content`가 비면 `reasoning` 필드에서 JSON 추출 폴백
- 청크 크기 20건 — reasoning 모델 안정성 + 비용 균형
- 키워드 분류는 폴백으로 유지 (LLM 실패 시 자동 회복)

### 정확도 검증

- 8/8 (alibaba, 3초) — 사용자 표본 데이터 전수 정답
- 이전 (tencent/hy3): 10/10
- 이전 (openrouter/free + 130줄 프롬프트): 26/26

---

## 💬 CEO 직접 개입 (@멘션)

하단 제어 바에서 `@에이전트명 ...` 형태로 즉시 지시:

```
@운용전략실장 미국 기술주 비중 60%로 세팅
@계량분석팀장 005930, 000660 기술적 분석 좀
@뉴스분석팀장 최근 반도체 업종 뉴스 요약
@사후관리실장 보유 종목 점검
@리스크관리실장 현재 편중도 보고
@운용지원실장 뉴스 크롤링 주기를 5분으로 바꿔줘
```

- **운용지원실장만이** 코드/설정을 실제로 바꾸고 서버를 재시작합니다. 다른 에이전트는 대화 페르소나.
- 설정 변경성 지시를 트레이더/운용전략실장에게 하면 시스템이 자동으로 *"그 변경은 @운용지원실장에게 지시하셔야 시스템에 반영됩니다"* 라고 안내합니다(무성의한 거절 방지 가드).
- `@에이전트명` 없이 입력하면 운용전략실장에게 자동 라우팅.

---

## 📌 계정별 상시 지시사항 (2026-05-19 ITEM6 — 신규)

`/api/ceo` @멘션은 **일회성**이라 사이클마다 휘발된다. 사장님이 포트폴리오 운용
원칙을 **본인 계정 한정**으로 영속 저장하고, 운용전략실장이 매 사이클 반영하도록 하는 메커니즘.

- **저장**: `infra/standing_directives.py` → `data/profiles/<uid>/standing_directives.json`
  (런타임 데이터, `.gitignore`). uid별 분리 — 다른 계정 지시는 절대 섞이지 않음.
- **주입 지점**: 활성 계정 uid의 지시 블록을 **운용전략실장(오케스트레이터) Pass1·Pass2
  프롬프트에만** 삽입(`main_swarm.py:1958` `build_orchestrator_directive_block`).
  ⚠️ **결정론적 파이썬 리스크/guardrail 게이트(`agents/guardrails.py`)는 미참조** →
  상시 지시가 안전 게이트를 우회해 실매매를 강제할 수 없다(프롬프트-only 영향).
- **표현 강도**: "참고 지침 — 다른 신호·리스크 게이트와 균형 있게 반영"으로 프레이밍.
  파이썬 리스크/guardrail이 항상 최종 우선임을 footer에 명시.
- **영구 삭제 보장**: 최초 시드 시 `.standing_seed_done` sentinel 기록 →
  사용자가 `clear/remove`로 지시를 지우면 재시작해도 **부활하지 않음**(`a05f1f7`).
- **CRUD**: `append_directive` / `load` / `clear_directives` / `remove_directive`
  (멱등 — 동일 내용 SHA256 12자 id 중복 시 추가 생략, 계정당 최대 50건).
- 사장님(hh09080) 계정에는 **매크로 붕괴 시나리오 대응 지시**가 1회 시드됨
  (달러 단기국채·MMF 핵심축, 퀄리티 팩터 우량주·인버스 헤지, 금·비트코인 배제,
  리밸런싱 트리거·현금화 계획 보고 — 사용자 명시 승인 항목).

## 🛰️ Coresight 관리자 전용 게이팅 + 승인 인박스 (2026-05-19 — 신규)

Coresight는 인증 없는 로컬-JSON 키워드 RAG(`tools/coresight_rag.py`)였다.
관리자 계정에서만 활성화되고, 유입 투자 로직을 **자동 실행 없이** 사장님 지시로 승격하는 게이트를 추가.

- **조회 게이팅**: `query_coresight()`는 `_is_admin_active()`로 게이트(deny-by-default,
  fail-soft). 비관리자 활성 시 "[Coresight] 비활성…" 메시지 반환, 도구 노출도 관리자에게만.
- **승인 인박스**(`infra/coresight_inbox.py`): `scan_and_enqueue` → 신규 항목을
  `coresight_pending.json`에 적재 → 관리자가 **명시 승인/거절**.
  `approve`는 `standing_directives.append_directive(uid, "[Coresight 유래] …")`로만 반영
  (자동 매매·자동 실행 없음). 처리 이력은 `coresight_seen.json`.
- **엔드포인트(관리자 전용, 비관리자 403)**: `GET /api/coresight/pending` ·
  `POST /api/coresight/approve` · `POST /api/coresight/reject`.

> 설계 상세: `Implementation/Implementation.md` (ML+유전 알고리즘 파라미터 튜닝,
> Markowitz GMV 포트폴리오 로직, Coresight 관리자 전용 설계 §3).

---

## 🔐 인증 & 계정 (로그인 오버홀 2026-05-19 — branch `feature/login-overhaul`)

Cloudflare Access(Zero Trust)를 제거하고 **앱 자체 로그인**으로 전환. 2026-05-19
로그인 오버홀로 비밀번호 저장·계정 복구·자격증명 정책을 전면 강화했다.
세션은 여전히 불투명 토큰(`secrets.token_urlsafe(32)`, 7일 TTL).

### 모델
- **로그인**: 아이디 + 비밀번호만.
- **최초 등록**: 아이디(중복 확인, 3자 이상) + 비밀번호(**10자 이상 + 특수문자 1개 이상**, 서버가 최종 강제) +
  OpenRouter API Key(필수) · KIS App Key/Secret · **한국투자증권 계좌번호** · Base URL.
  - **제거됨(5-5)**: 사용자별 DART Key 입력칸 + 계정 이름(선택) 필드. DART 공시는
    이제 서버 소유 단일 `OPENDART_API_KEY` 환경변수(`config.py:18`)로 전 계정 공통 처리
    → API 키 하드코딩·GitHub 유출 위험 제거, 사용자 입력 표면 축소.
- **멀티 계정**: 여러 계정 등록 가능. 스왐은 단일 프로세스라 **로그인한 계정 1개가 봇을 장악**(활성 계정).
- 로그인하면 그 계정의 API 자격증명이 런타임에 주입됨(`config` 전역 재할당 + 브로커/스왐 싱글턴 리셋).

### 비밀번호 — argon2id 해시 (평문 비교 폐기)
- 비밀번호는 **argon2id 해시**(`infra/auth_store.py:183` `PasswordHasher`)로만 저장.
  `password_enc` 컬럼은 DEPRECATED(항상 `''`, 하위호환 위해 컬럼만 유지).
- **부팅 1회 멱등 마이그레이션** `migrate_passwords_and_bidx()`:
  legacy 행의 `password_enc`(Fernet 평문) → argon2 해시로 승격 + 누락된 블라인드
  인덱스 백필. 이미 마이그레이션된 행은 건너뜀. 로그: `auth 마이그레이션 완료: 해시승격 N, bidx백필 M`.
- legacy 행 로그인 시에도 복호-비교 후 **즉시 argon2로 승격**(`verify_password` 경로).

### 계정 복구 — 블라인드 인덱스(HMAC) (신규)
사용자가 아이디/비밀번호를 잊었을 때, **암호화된 자격증명을 복호하지 않고** 복구한다.
- 복구 인자(3종, 본인만 알 수 있는 값): **KIS App Key + KIS App Secret + OpenRouter Key**.
- 저장 형태: 각 값의 `HMAC-SHA256` 블라인드 인덱스 컬럼
  (`kis_app_key_bidx` / `kis_app_secret_bidx` / `openrouter_key_bidx`).
  HMAC 키는 Fernet 원본키에서 `HKDF`(`info=b"arquant-bidx-v1"`)로 파생 — raw 키는 어디에도 평문 저장 안 됨.
- **아이디 찾기**(`POST /api/recover_id`): 3인자 일치 → username 반환.
- **비밀번호 재설정**(`POST /api/recover_password`): username + 3인자 일치 → 새 비밀번호 설정.
- **열거 오라클 차단(I1)**: 새 비밀번호 정책 검증을 **인자 매칭보다 먼저** 수행 →
  "인자 틀림" vs "정책 위반" 응답이 계정 존재 여부를 누설하지 않음.

### 영속성 — 모든 유저, 재시작 후에도 계정 유지
- 계정·세션은 `data/arquant_auth.db`, Fernet 키는 `data/.fernet.key`(0600).
- 둘 다 **서버 디스크에 영속** → 재시작/코드 갱신 후에도 계정·세션(7일) 유지(`.gitignore`는 git 추적만 차단).
- ⚠️ **`data/.fernet.key`는 별도 안전 백업 필수.** 분실 시 자격증명·블라인드 인덱스 복구 불능.
  키가 없는데 계정이 이미 있으면 서버는 새 키를 만들지 않고 503(`fernet_key_lost`)으로 잠근다.

### 보안
- 세션 쿠키: `HttpOnly` + `SameSite=Lax` + **`Secure`**(기본 켜짐). https=쿠키 / 로컬 http=`X-Session` 이중화.
- WebSocket(`/ws`)은 `?token=` 또는 쿠키로 세션 검증, 미인증 거부.
- **레이트 리미터 + 감사 로그(JSONL)**: 로그인·등록·복구 시도에 공유 스로틀 + 감사 기록.
  클라이언트 IP는 `CF-Connecting-IP` 우선(Cloudflare 터널 뒤 X-Forwarded-For 스푸핑 방지).

### 엔드포인트
`POST /api/register` · `POST /api/login` · `POST /api/logout` · `GET /api/me` ·
`GET /api/accounts` · `POST /api/switch` · `GET /api/auth_status`(공개) ·
`GET /api/check_username`(공개) · **`POST /api/recover_id`(공개)** · **`POST /api/recover_password`(공개)**.
그 외 모든 `/api/*`는 세션 필요(미인증 401).

> 모바일 앱(arquant_mobile): 네이티브 `LoginScreen.kt`(Compose)로 로그인/등록 +
> 아이디·비밀번호 찾기 폼 제공. 대시보드는 index.html WebView. APK 빌드는 Android SDK 환경에서 수행
> (이 서버엔 SDK 미설치 — 코드+자체검토만 완료, `1f45601`).

---

## 🚀 실행

```bash
cd /home/opc/projects/Arquant
pip install fastapi uvicorn python-dotenv aiohttp pydantic pandas numpy beautifulsoup4 lxml requests

bash start_server.sh          # 8500 점유 정리 → 포트 해제 대기(Errno98 방지) → uvicorn → cloudflared → 헬스 확인
# 또는 직접:
python3.11 -m uvicorn server.app:app --host 0.0.0.0 --port 8500
cloudflared tunnel run hyfe-iqc
```

→ **https://arquant.ai-ve.uk**

> ⚠️ 서버를 재시작해도 **모든 유저 계정·로그인 세션(7일)은 유지**된다(`data/arquant_auth.db` 영속).
> 단 실행 중이던 감시 루프는 종료되므로 대시보드에서 다시 **▶ 실행**(자동재개 마커가 있고 활성 계정이 있을 때만 자동 재개).
> KIS 토큰은 `data/kis_token.json`에 캐싱돼 24h 안이면 재발급되지 않는다.

### 대시보드 사용
1. **▶ 실행** — 무한 감시 루프 시작 (재접속 시 `/api/status.is_running`으로 버튼 자동 동기화)
2. 1시간마다 + 한국/미국 장 개장 시 2패스 분석 사이클 자동
3. `@에이전트명` + 지시로 직접 개입 / **⏹ 중지**로 정지
4. **제목바**: 제목 우측에 [💵 $X.XXX/h (N콜)] 표시 — 최근 1시간 API 비용·호출 수 (OpenRouter usage 기반 추정)
5. **탭 4개**:
   - **📊 대시보드** — 세션·시간(KST)·감지 뉴스·다음 사이클·완료 사이클·실매매·전략·장 상태 + 에이전트 통신 로그 (마크다운 잔여물 자동 정리)
   - **💰 수익률** — (좌) 평가금액 추이 [실시간/일별/월별 토글] / (우) 보유 종목·잔고 [카테고리 배지, 🔄 새로고침, OFF_HOURS면 자동폴링 일시정지·10분 주기] / (하) 전체 거래 내역 [KST 시각, 🗑️ 비우기]
   - **📰 뉴스** (사장 피드백 #10 — 신규 탭) — KR/US/공통 필터 버튼, 종목별 마켓 배지(🇰🇷/🇺🇸/🌐), 크롤 시각 표시
   - **⚙️ 전략** — 프리셋 선택·적용 + 🛠 커스터마이즈 박스(한국어 라벨, 즉시 적용/프리셋 저장)

> 모든 이벤트·에이전트 응답(전문, 무삭제)은 `claude_response.json`에 실시간 기록 (KST aware ISO).

### 💵 API 비용 추적 (사장 피드백 #7 — 신규)
- `agents/base_agent.py`가 OpenRouter `usage` 필드(prompt_tokens, completion_tokens)를 모델별 단가에 곱해 누적
- 모델별 추정 단가 (USD per 1M tokens):
  - `moonshotai/kimi-k2.6`: $0.73 in / $3.49 out (context 262K)
  - `deepseek/deepseek-v4-flash`: $0.10 in / $0.30 out
  - `deepseek/deepseek-v4-pro`: $0.50 in / $1.50 out
  - `tencent/hy3-preview` (reasoning): ~$0.40 in / $1.20 out
  - `openrouter/free`: 무료 ($0)
- `/api/status` 응답에 `api_cost: {cost_usd, calls, window_sec}` 필드 추가
- **실측치**:
  - KR 균형형 사이클 1회당 약 $0.05–0.10 (13~15콜 — 종목별 퀀트 7회 + 매크로 + 뉴스 + 리스크 + 트레이더 + Pass1/2 + 사후관리)
  - + Tavily 매크로 검색: 사이클당 ~$0.02–0.04 (캐시 hit 시 $0)
  - + 뉴스 LLM 분류: 크롤당 ~$0.01–0.02 (10분 주기)
  - **사이클 총 비용 ≈ $0.08–0.15 / 일일 $2–5 / 월 $60–150**

---

## 📱 모바일 앱 (`arquant_mobile/`)

Compose UI 기반 Android 앱. 웹 대시보드와 동일한 백엔드를 사용하며 앱 자체 로그인(아이디/비밀번호 → X-Session 토큰)으로 인증.

### 로그인 화면 오버홀 (2026-05-19 — 웹 `index.html` + 네이티브 `LoginScreen.kt` 동시 적용)
- **5-1 로고 통일**: 로그인/등록 화면 로고를 대시보드 좌상단 로고와 동일하게(웹 SVG / Compose `ArQuantLogoBox` Canvas).
- **5-2 계정 복구 UI**: "아이디 찾기"(KIS Key+Secret+OpenRouter Key) / "비밀번호 찾기"(아이디+3인자) 패널.
- **5-3 모바일 배지 미러**: "에이전트 통신 로그" 텍스트 우측에 상태 배지 부착(`#badgeMirror`).
- **5-4 안전영역 패딩**: 등록 버튼이 홈 인디케이터/내비바와 겹치지 않게(`navigationBarsPadding`/`imePadding`, 웹 safe-area).
- **5-5 입력 필드 정리**: 계정 이름(선택)·DART API Key 칸 제거(서버 소유 키 사용).
- **5-6 라벨**: "KIS 계좌번호" → **"한국투자증권 계좌번호"**.
- **ITEM3 통신 로그 표 렌더링 수정**: 운용전략실장의 종합 평가표·후보 5종목 근거표가
  사용자 채팅창에 **공란**으로 보이던 버그. 근본 원인 = `_cleanLog()`의
  `^\|.*\|$` 정규식이 마크다운 표 행 자체를 삭제. 수정 = 에이전트 메시지를
  XSS-안전 `<table>`로 렌더(`_renderAgentContent()`) → 표가 정상 표시.

### 핵심 화면
- **사이드바** (사장 피드백 모바일 #1): `statusBarsPadding()` 적용해 X 닫기 버튼이 상태바와 안 겹침. LazyColumn으로 전체 에이전트(9 + 3) 스크롤. 뉴스 피드는 별도 탭으로 이동했다는 안내 표시
- **대시보드** (사장 피드백 모바일 #2): 평소엔 핵심 3개 박스(장 상태 / 완료 사이클 / 실매매 체결)만 한 줄로, **▾ V 토글** 클릭 시 나머지 5개 박스(세션/시간/감지 뉴스/다음 사이클/전략) 펼침 → 통신 로그 가시성 확보
- **에이전트 통신 로그** (사장 피드백 모바일 #3): `cleanLog()` 정규식으로 `**`, `##`, `---`, `|...|`, ` ``` ` 마크다운 잔여물 자동 제거
- **수익률 탭 — 거래 내역 클릭 펼침** (사장 피드백 5~6차):
  - 행 탭 → `▾/▴` 토글로 인라인 detail panel 열림/닫힘
  - `remember(t.ts, t.ticker) { mutableStateOf(false) }`로 행별 expand state 유지
  - 매수/매도/미체결 분기 + FIFO 매칭표 + 실현 손익 합계 (🟢/🔴 색상)
  - 실제 체결가는 "체결가 (KIS holdings 평균단가 차이로 역산 — 정확)" 라벨, 추정은 "추정 체결가 (시장가 → reason ≈X 근사치)" 라벨
- **전략 탭** (사장 피드백 모바일 #4): 코드(`PER_ORDER_BUDGET_RATIO=0.1`) 대신 한국어 라벨(`1주문 예수금 사용 비율: 10%`)로 표시. 빌트인/사용자 프리셋 모두 노출

### 빌드 & 배포 (Compose UI는 컴파일되므로 변경 후 재빌드 + 재설치 필수)
- Android Studio Hedgehog 이상, 또는:
```bash
cd /home/opc/projects/Arquant/arquant_mobile
./gradlew assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```
- `local.properties`의 `cf.access.*` 설정은 불필요(제거됨) — 앱 실행 후 로그인 화면에서 아이디/비밀번호로 인증

---

## 🔑 사용 중인 API 키

| 키 | 사용처 | 호출 빈도 |
|----|--------|----------|
| `OPENROUTER_API_KEY` | 모든 LLM (`BaseAgent` · `news_classifier`(tencent) · `ops_support_worker`) | 사이클당 13~15회 + 크롤당 1회 분류 |
| `KIS_APP_KEY` + `KIS_APP_SECRET` | KIS OpenAPI 토큰·시세·주문·잔고·체결확인 | 토큰 1회/24h + 주문·시세 다수 |
| `OPENDART_API_KEY` | DART 공시 + corpCode 매핑 + 직전연도 요약재무(BS+IS) — **서버 소유 단일 env, 전 계정 공통**(2026-05-19 사용자별 입력칸 제거) | KR 세션 30분 간격 + 후보별 1회 |
| ~~`TAVILY_API_KEY`~~ | **제거됨** (사장 피드백 8차) — `alibaba/tongyi-deepresearch`가 검색·분류·리서치 통합 | — |

`CORESIGHT_PATH` / `CORESIGHT_CHROMA_PATH`는 로컬 파일시스템 경로 (API 키 아님).

---

## 🔌 API 엔드포인트

| Method | Path | 설명 |
|--------|------|------|
| GET | `/health` | 서버 상태 |
| GET | `/api/status` | 감시 상태(`is_running`), 현재 상태/세션, `next_cycle_sec`, 지수 워치리스트, **`api_cost`(USD·콜수)** |
| POST | `/api/start` / `/api/stop` | 감시 시작 / 중지 |
| POST | `/api/ceo` | 사장 직접 지시 (`@에이전트명`) |
| POST | `/api/register` / `/api/login` / `/api/logout` | 계정 등록 / 로그인 / 로그아웃 |
| POST | `/api/recover_id` | **아이디 찾기**(공개) — KIS Key+Secret+OpenRouter Key 3인자 일치 시 username 반환 |
| POST | `/api/recover_password` | **비밀번호 재설정**(공개) — username + 3인자 일치 시 새 비번 설정 (정책 선검증) |
| GET | `/api/coresight/pending` | **관리자 전용** — Coresight 승인 대기 항목 (비관리자 403) |
| POST | `/api/coresight/approve` / `/api/coresight/reject` | **관리자 전용** — 항목 승인(→상시 지시 승격)/거절 |
| GET | `/api/balance` | 통합 포트폴리오 — 국내·해외·채권·펀드 + 예수금/총평가 |
| GET | `/api/news` | 최근 뉴스(최대 20) + 크롤 상태, 항목별 `market` (KR/US/BOTH) |
| GET | `/api/equity?view=realtime\|daily\|monthly&limit=N` | 평가금액 추이 |
| GET | `/api/trades?limit=N` | 전체 거래 내역 (newest first) |
| POST | `/api/trades/clear` | **거래 내역만** 초기화 (시스템 로그는 유지) |
| GET | `/api/history` | 분석 사이클 이력 |
| GET | `/api/events?limit=N` / POST `/api/events/clear` | 통합 이벤트 로그 / 전체 초기화 |
| GET | `/api/agents` | 에이전트 9명 + 모델 |
| GET | `/api/dart?corp_name=X&days=N` | DART 공시 |
| GET | `/api/price/kr/{code}` / `/api/price/us/{ticker}` | 국내 / 해외 현재가 (US는 거래소 자동 탐지) |
| GET | `/api/rank/volume` | 거래량 순위 |
| GET | `/api/strategy` / POST `/api/strategy` | 활성 전략·프리셋 조회 / 변경 (커스텀 params 지원) |
| POST | `/api/strategy/preset` / DELETE `/api/strategy/preset/{name}` | 사용자 정의 프리셋 저장/삭제 |
| GET | `/api/cycles?limit=N&offset=N` / `/api/cycles/{id}` | SQLite에 영속화된 분석 사이클 |
| GET | `/api/ops_history?limit=N` | 운용지원실장(+ 산하 팀장) 자동 수정 이력 |
| GET | `/api/alerts?limit=N&level=CRITICAL` | **운영자 실패 알림** — 조용히 삼켜졌던 주문/체결/equity/루프 실패 표면화 [신규] |
| GET | `/api/metrics?window_sec=N` | **메트릭 집계** — 사이클 소요시간·주문 성공/실패·오류 카운트 [신규] |
| WS | `/ws` | 실시간 로그·에이전트 메시지·상태 배지 (+ `type:alert` 푸시) |

---

## 🗓️ 주요 설정 상수 (`config.py`)

```python
# 사이클 트리거
PERIODIC_CYCLE_SEC       = 1 * 60 * 60   # 1시간마다 + 장 개장 시
NEWS_CHECK_INTERVAL      = 600 (main_swarm.py)  # 10분마다 뉴스 크롤
COOLDOWN_AFTER_CYCLE     = 0 (main_swarm.py)    # 쿨다운 없음

# 뉴스 사전 필터 (사장 피드백 #22 — 결정론적, LLM 미호출)
NEWS_PREFILTER_TRIGGER   = 40
NEWS_PREFILTER_LIMIT     = 40
HEADLINE_DEDUP_RATIO     = 0.85

# 캐시 (사장 피드백 #20 — DART는 KR 세션에서만 30분 간격)
MACRO_CACHE_TTL_SEC      = 30 * 60     # 매크로 분석 + Tavily 검색 동기화
DART_CACHE_TTL_SEC       = 24 * 60 * 60   # KR 세션 외엔 호출 자체 생략

# 매매
LIVE_TRADING             = True
ENABLE_CHEAP_FALLBACK    = False
PER_ORDER_BUDGET_OVERSHOOT = 1.20   # 1주 예산 +20%까지 매수 허용

# 매도 룰 (사장 피드백 #24 — 신규)
ALLOW_DAY_TRADING        = True       # 기본 ON. False면 MIN_HOLDING_DAYS_FOR_SELL 미만 보유 종목 매도 회피
MIN_HOLDING_DAYS_FOR_SELL = 0.5       # ALLOW_DAY_TRADING=False일 때만 적용

# 모델 배정 (사장 피드백 다수 차수에 걸쳐 진화)
MODEL_ASSIGNMENTS = {
    "chief_orchestrator": "moonshotai/kimi-k2.6",    # 운용전략실장·사후관리실장
    "macro_analyst":      "deepseek/deepseek-v4-flash",
    "quant_analyst":      "deepseek/deepseek-v4-flash",
    "news_analyst":       "deepseek/deepseek-v4-flash",
    "news_curator":       "deepseek/deepseek-v4-flash",       # 결정론 폴백 — 평소엔 LLM 미호출
    "news_classifier":    "tencent/hy3-preview",              # 사장 피드백 5차 — KR/US/BOTH 정확 분류
    "trader":             "deepseek/deepseek-v4-flash",       # 사장 피드백 3차 — free → flash로 격상
    "risk_guard":         "openrouter/free",
    "policy_filter":      "openrouter/free",
    "post_manager":       "moonshotai/kimi-k2.6",
    "ops_support":        "deepseek/deepseek-v4-pro",         # 산하 팀장 3명도 동일 모델 공유
}

# 프롬프트 캐싱
ENABLE_PROMPT_CACHE      = True      # Anthropic 모델에 한해 cache_control 적용
AGENT_HISTORY_TURNS      = 3
```

---

## 🧠 설계 원칙 요약

1. **"무엇을 살지" 판단은 단일 책임 분리** — 매크로(전략리서치) → 뉴스(분석) → 운용전략실장(후보 6) → **종목별 계량(통일 5섹션)** → 운용전략실장(최종) → 리스크(결정론 + DART + 재무). 토론형 합의가 아니라 단계별 게이트 + 오케스트레이터 결정 모델.
2. **LLM = 판단·서술, 파이썬 = 검증·실행** — OrderDraft 조립과 1차 리스크 검증은 결정론 코드. LLM 호출은 비결정성을 검사 게이트 뒤에 가둠. 트레이더 LLM은 한국어 자연어 보고용(실행 후 체결 결과 정리)으로만 사용.
3. **Fail-open vs Fail-safe** — 매수 게이트는 fail-safe(LLM 에러 시 1차 승인 유지로 거래는 계속), 데이터 가드는 fail-safe(시세 없으면 매수 안 함). 비정상 응답(None JSON 등)도 가드 후 retry 로직으로 자동 회복.
4. **세션 인지 — 추천·실행 모두** — 후보 선정·계량 평가·사후관리·주문 조립까지 4단계에서 세션을 본다:
   - 미국장에 KR 코드 추천 자동 제거
   - KR 마감 후 KR 주문 자동 차단
   - **사후관리실장은 현재 세션 시장 보유분만 분석**, 반대편 시장은 자동 보유
5. **체결 ≠ 접수** — 접수만 된 주문은 카운트하지 않음. KR은 잔고 변동 확인 후, US는 잠정 카운트 후 5분 뒤 재확인 백그라운드 태스크로 보정.
6. **가격 데이터 단일 권위 출처** — 모든 가격·등락률·지수값은 **검증된 네이버 크롤(글로벌 지수 10종)에서만** 인용. Tavily는 시황 해설·정책·심리·수급 분석만 활용 (가격 인용 금지). 매크로 분석가 프롬프트에 강제.
7. **실제 체결가 추적** — 추정값(`est_price`) 대신 holdings avg_price 변화로 매수 체결가 정확 역산, 매도는 직전 last_price 스냅샷. `fill_price`/`price_source: "actual"` 필드로 추적.
8. **에이전트 응답 양식 통일** — 계량분석팀장은 5섹션·가중치 강제, 트레이더는 산문체·JSON 금지, 뉴스 큐레이터는 결정론 키워드 스코어, 뉴스 분류기는 decision tree + anti-examples. 일관성이 다음 단계 의사결정의 비교 가능성을 보장.
9. **비용 최적화 레버** — 사이클 빈도(1h + 개장) · 매크로/DART/Tavily 캐시 · 무료 모델로 폴백 역할 위임 · 뉴스 큐레이터 결정론화 · API 비용 추적으로 사이클당 비용 가시화. 사이클당 $0.08–0.15, 월 $60–150 수준.
10. **재시작 친화** — KIS 토큰 디스크 캐시, equity_curve/strategy_state 영속화, claude_response.json 무삭제 로그, `RESUME_ON_BOOT` 마커로 운용지원실장 코드 수정 후 자동 감시 재개.
11. **운용지원실장 = 조정자, 산하 팀장 = 수정자** — 직접 코드를 만지지 않고 도메인 분류(investment/operations/finance) 후 산하 팀장 워커에 위임. spawn↔완료를 `OPS#N` 마커로 시각적 연결. 보호 패턴(FORBIDDEN_PATTERNS)이 핵심 매매 로직 침범을 막음.
12. **조용한 실패 금지 + 검증된 결정론 핵심** (사장 피드백 2026-05-18) — 돈이 걸린 결정론 코드(주문 검증·사이징·파서)는 `pytest` 회귀 테스트로 고정, 의존성은 핀 고정, 머니패스의 삼켜진 예외는 `notifier`로 운영자에게 표면화(동작 보존·중복억제), 자가수정은 컴파일 실패 시 **디스크 전면 롤백**으로 부분 적용 불일치 차단.
13. **지시는 프롬프트에만, 게이트는 불변** (2026-05-19) — 사장님 상시 지시·Coresight 승격 지시는 운용전략실장 LLM 프롬프트에만 주입되고 `agents/guardrails.py` 결정론 리스크 게이트는 절대 참조하지 않는다. 어떤 지시도 안전 게이트를 우회해 실매매를 강제할 수 없으며, 계정별로 완전 격리된다(uid 분리 파일 + sentinel 영구 삭제 보장).
14. **신호 없음 ≠ 악재 없음** (2026-05-19) — DART 조회 실패(`QUERY_FAILED`)를 공시 부재(`NO_DISCLOSURE`)와 엄격히 구분하고, 재무상태표가 회계항등식을 깨면(`IMPOSSIBLE`) 그 수치로 부실 판정을 내리지 않고 `QUERY_FAILED`로 격상. 데이터 결손을 안전 신호로 오독하지 않는다.

---

## 🧪 운영 안전·테스트·관측성 (사장 피드백 2026-05-18 — 신규)

실거래(`LIVE_TRADING=True`) 시스템의 최우선은 기능이 아니라 **잘못된 주문 차단**이다. 다음 인프라가 추가되었다.

### 1) 결정론 핵심 회귀 테스트 (`tests/`, pytest 56케이스)
```bash
python3.11 -m pip install -r requirements-dev.txt   # 최초 1회
python3.11 -m pytest                                # 전체 (<2초)
```
- `validate_order_draft`: **KR(원)/US($) 통화 분리**(2026-05-16 버그) 회귀, 단일종목 편중·계좌 MDD·예수금 버퍼·사이클 예산 게이트.
- `_affordable_one_share`·매도결정/진입가/코드추출 파서, 자가수정 가드, 뉴스분류 진단, 백테스트 결정론.
- **CI 권장**: 배포·자가수정 전 `pytest` 그린이 게이트.

### 2) 의존성 핀 고정 (`requirements.txt`)
라이브러리 자동 업그레이드로 주문 경로가 조용히 깨지는 것을 차단. 업그레이드는 의도적으로 + `pytest` 통과 후에만.

### 3) 운영자 실패 알림 (`infra/notifier.py`)
- 주문/체결 재확인/equity 기록/감시 루프 크래시 등 **삼켜졌던 실패**를 `data/alerts.json` + 로그 + (선택)웹훅 + 대시보드로 표면화.
- `dedup_key` + 억제 윈도우(기본 30분)로 **알림 폭주 방지** (1시간 사이클이 매번 같은 이유로 실패해도 1회만).
- 외부 채널: 환경변수 `ARQUANT_ALERT_WEBHOOK`(범용 JSON POST). 텔레그램/디스코드는 `notifier._post_webhook`만 교체.
- **동작 보존 원칙**: 기존 except 의미(재-raise 안 함)는 그대로, 가시성만 추가.

### 4) 경량 메트릭 (`infra/metrics.py`, `data/metrics.jsonl`)
사이클 소요시간(`metrics.timer`)·주문 체결/미체결·오류 카운트. `GET /api/metrics` 로 최근 집계 조회.

### 5) 백테스트 하네스 (`backtest/`)
```bash
python3.11 -m backtest.report
```
- ⚠️ **정직성 경계**: LLM 종목 선정은 오프라인 재현 불가 → 진입 신호는 고정 SMA 프록시로 통일하고 **프리셋의 결정론 규칙(사이징·익절·손절·MDD 차단)만** 비교. 절대 수익률이 아니라 *프리셋 간 상대 리스크/회전율* 로만 해석.
- 검증 결과 프리셋 스펙트럼이 설계대로 단조(방어형 MDD 최소·샤프 최고 → 초공격형 수익률 최대·MDD 최대).

### 6) 자가수정 안전 강화 (`infra/ops_support_worker.py`)
- **전면 롤백**: 최종 컴파일 실패 시 그 플랜이 만든 *모든* 변경을 원장 역순으로 백업 원복(기존엔 깨진 파일이 디스크에 남아 다음 재기동 시 전체 다운).
- `append`도 백업, **변경 크기 상한**(`MAX_CHANGE_BYTES`/`MAX_NET_NEW_LINES`)으로 작은 앵커로 파일 전체 갈아엎기 차단.
- 롤백 발생 시 `notifier` CRITICAL 알림.

### 7) 뉴스 분류 폐루프 결정론화 (`infra/news_weight_tuner.py`)
주간 피드백이 LLM의 "알아서 제안"에만 의존하던 것을, **분류 분포 vs 실제 매매 분포의 불일치**를 수치 권고로 변환해 directive 최상단에 배치(LLM이 약해도 신호 생존).

> **남은 권장 작업**: `main_swarm._run_analysis_cycle`(849줄)·`ArquantOrchestrator`(~1960줄) 단계별 분해. 지금은 위험(라이브 머니·자가수정 대상 파일)하므로 *블라인드 리팩터링 대신* 테스트 안전망 + `metrics.timer` 관측을 먼저 깔았다. 분해는 파이프라인 단계별로 테스트를 붙이며 점진 수행 권장.

---

**Built by ArQuant AI Team** | Powered by OpenRouter + KIS OpenAPI + OpenDart + Naver Finance
