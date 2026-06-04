"""
NPS Swarm v1.0 - Central Configuration
All environment variables, model mappings, and system constants.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

# ─── API Keys ───────────────────────────────────────────────────────────────
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
KIS_APP_KEY = os.getenv("KIS_APP_KEY", "")
KIS_APP_SECRET = os.getenv("KIS_APP_SECRET", "")
KIS_BASE_URL = os.getenv("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443")
KIS_ACCOUNT_NO = os.getenv("KIS_ACCOUNT_NO", "")
OPENDART_API_KEY = os.getenv("OPENDART_API_KEY", "")
# 사장 피드백 2026-05-15 (8차): Tavily 제거 — alibaba/tongyi-deepresearch가 검색 + 분류 + 리서치 통합 담당

# ─── Coresight RAG Paths ────────────────────────────────────────────────────
CORESIGHT_PATH = os.getenv("CORESIGHT_PATH", "/home/opc/ArcAI.ve/coresight_logs")
CORESIGHT_CHROMA_PATH = os.getenv("CORESIGHT_CHROMA_PATH", "/home/opc/ArcAI.ve/chroma_db")

# ─── Legacy Code Paths ──────────────────────────────────────────────────────
LEGACY_AUTOTRADING_PATH = Path("/home/opc/projects/매매자동화")
LEGACY_KRX_SIMULATOR_PATH = Path("/home/opc/projects/KRX Quant Simulator")

# ─── OpenRouter Model Assignments (by agent role) ───────────────────────────
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

MODEL_ASSIGNMENTS = {
    "chief_orchestrator": "deepseek/deepseek-v4-pro",   # 사장 지시 2026-05-19 — Kimi가 length-truncate/타임아웃으로 빈·degenerate 결정 빈발 → 안정적 deepseek-v4-pro로 교체
    "macro_analyst": "deepseek/deepseek-v4-flash",
    "quant_analyst": "deepseek/deepseek-v4-flash",          # 다양한 계량기법 — 무료 모델은 출력 망가짐 → flash로 상향
    "news_analyst": "deepseek/deepseek-v4-flash",           # 종목명 환각 방지 — flash로 상향
    "news_curator": "deepseek/deepseek-v4-flash",           # 헤드라인이 40개 초과 시 굵직한 40건 선별
    # 사장 지시 2026-06-01 (3회 반복): 뉴스 분류·매크로 리서치 기본 모델을 nousresearch/hermes-4-70b 로 고정.
    # tongyi-deepresearch 가 뉴스 분석을 이상하게 만들어 명시적으로 교체. ★기본값 자체를 hermes 로 둬서,
    # admin 패널 '전역 설정 저장'으로 model_overrides 가 비워져도(과거 회귀 원인) 절대 tongyi 로 폴백 안 함.
    # (회귀 방지: tests/test_model_default_hermes.py)
    "news_classifier":   "nousresearch/hermes-4-70b",
    "macro_researcher":  "nousresearch/hermes-4-70b",
    "trader": "deepseek/deepseek-v4-flash",  # 사장 피드백 2026-05-15 (3차) — free 모델 말투 어색 → flash로 격상. 자연어 보고 품질 향상
    "risk_guard": "openrouter/free",                     # DART 공시 기반 재심 (룰 게이트는 파이썬, 파싱 실패 시 fail-open)
    # policy_filter 폐지(사장 피드백 2026-05-18) — 역할 risk_guard 통합
    "post_manager": "deepseek/deepseek-v4-pro",     # 사장 지시 2026-05-24 — 매도 타이밍 결정자, kimi-k2.6에서 deepseek-v4-pro로 교체(안정성). ADMIN 오버라이드가 비더라도 이 기본값으로 적용
    "ops_support": "deepseek/deepseek-v4-pro",  # 사장 피드백 2026-05-15 — DeepSeek V4 Pro로 변경 (운용지원실장은 분류·조정만; 실제 코딩은 산하 팀장 워커가 수행)
    "fund_planner": "deepseek/deepseek-v4-flash",   # 사장 지시 2026-05-28 — 매수 직후 thesis 4줄(목표가/손절가/계획 보유/사유) 구조적 출력. flash로 충분.
}

# ─── Risk Management Constants ──────────────────────────────────────────────
MAX_SINGLE_STOCK_RATIO = 0.20       # 단일 종목 최대 20%
MAX_DRAWDOWN_LIMIT = 0.10           # 최대 손실 제한 10%
MAX_ORDER_RETRY = 3                 # 주문 재시도 최대 3회
MAX_VALIDATION_LOOP = 3             # 리스크 검증 재시도 최대 3회

# ─── LLM Cost-Reduction Knobs ───────────────────────────────────────────────
# Per-agent output token caps (replaces uniform 4096). Risk/Policy emit short
# structured JSON; analysts need more room.
AGENT_MAX_TOKENS = {
    "chief_orchestrator": 12000,  # deepseek-v4-pro로 교체(2026-05-19) — 비-reasoning이라 자연 완료 시 즉시 반환,
                                  # 12000은 안전한 상한(2패스 결정 출력 충분히 수용, 잠식 없음).
    "macro_analyst":      8000,   # 4000으로는 상세 매크로 리포트가 중간에 끊김 (2026-05-19 관측).
                                  # 6000으로 상향하여 완결된 응답 보장.
    "quant_analyst":      8000,   # 5500으로도 5개 섹션 + 점수/진입가 줄이 잘리는 사례 발생 → 8000으로 재상향
    "news_analyst":       2600,   # 6개 후보 + 보유 종목 감성 분석
    "news_curator":        600,   # 큐레이터는 번호 목록만 → 작게
    "news_classifier":   12000,   # 사장 피드백 2026-05-15 (8차) — alibaba는 reasoning 모델 → 내부 사고 토큰 + JSON 응답 모두 수용
    "macro_researcher":   8000,   # 매크로 리서치 — 시황·심리·정책 합성 응답
    "trader":             1500,   # 사장 피드백 (3차) — 자연어 보고용. 체결 결과 요약 + 매매 이유 정리
    "risk_guard":         2200,   # DART 공시 읽고 종목별 재심 + 사유
    # policy_filter 폐지(2026-05-18)
    "post_manager":      12000,   # deepseek-v4-pro(2026-05-24 교체) — chief_orchestrator와 동일 모델·동일 토큰 한도
    "ops_support":        8000,   # 코드 변경 JSON + 근거 설명 (사장 지시 2026-05-14 — 토큰 한도 상향)
    "fund_planner":       1200,   # 사장 지시 2026-05-28 — 4줄(목표가/손절가/계획 보유/사유) 정형 출력 + 한 단락 보강
}
ENABLE_PROMPT_CACHE = True            # Anthropic prompt caching via OpenRouter cache_control
AGENT_HISTORY_TURNS = 3               # trailing history messages to resend (was 10)

# Macro/DART recompute throttling (slow-moving inputs — don't regenerate every cycle)
MACRO_CACHE_TTL_SEC = 30 * 60         # 30 min
DART_CACHE_TTL_SEC  = 24 * 60 * 60    # 1 day

# Analysis-cycle trigger tuning.
# 감시/재료성 뉴스 트리거는 폐지 — 단순히 1시간마다 사이클 1회 + 한국/미국 장 개장 시 누적 뉴스로 1회.
# (구버전 ANALYSIS_NEWS_THRESHOLD / ANALYSIS_RAW_FALLBACK 트리거 파라미터는 사장 지시 2026-05-21로 완전 제거)
PERIODIC_CYCLE_SEC      = 1 * 60 * 60 # run a cycle this often while a market is open (1시간마다 1회)
HEADLINE_DEDUP_RATIO    = 0.85        # difflib ratio above which two headlines are "the same"
NEWS_PREFILTER_TRIGGER  = 40          # 누적 헤드라인이 이 수를 넘으면 큐레이터로 사전 선별
NEWS_PREFILTER_LIMIT    = 40          # 사전 선별 후 뉴스분석팀장에게 넘길 최대 헤드라인 수
MATERIAL_NEWS_KEYWORDS = [
    "실적", "어닝", "영업이익", "순이익", "적자", "흑자전환", "가이던스",
    "M&A", "인수", "합병", "분할", "지분", "최대주주", "경영권",
    "유상증자", "무상증자", "감자", "배당", "자사주", "자기주식",
    "상장", "상장폐지", "관리종목", "거래정지", "공개매수",
    "규제", "제재", "과징금", "리콜", "소송", "특허", "파산", "회생",
    "금리", "인하", "인상", "FOMC", "환율", "관세",
    "수주", "계약", "공급계약", "임상", "승인", "허가",
    # 완화된 기준: 시장 동향, 업종, 산업 키워드 추가
    "급등", "급락", "상한가", "하한가", "신고가", "신저가",
    "반도체", "AI", "2차전지", "바이오", "로봇", "자율주행",
    "매출", "성장", "하락", "상승", "전망", "분석",
    "투자", "펀드", "채권", "ETF", "공모", "사모",
    "수출", "수입", "무역", "물가", "고용", "GDP",
    "트럼프", "중국", "미국", "일본", "유럽",
]

# ─── Live Trading (KIS) ─────────────────────────────────────────────────────
LIVE_TRADING         = True           # actually place KIS orders in the EXECUTION step
MAX_TRADES_PER_CYCLE = 2              # at most this many real orders executed per analysis cycle (sells prioritized)

# Sell / rebalance rules (BUY-only was too restrictive — now we also take profit / cut loss / trim).
ENABLE_SELL_REBALANCE = True
TAKE_PROFIT_PCT       = 12.0          # holding P&L ≥ +12%  → sell the position
STOP_LOSS_PCT         = 7.0           # holding P&L ≤ -7%   → sell the position
TRIM_OVER_RATIO       = True          # if a holding's notional exceeds CONSERVATIVE_STOCK_RATIO of total → trim down
# 사장 피드백 2026-05-15(#24): 데이트레이딩 0.5일 미만 회피 원칙 폐기. 기본 True(허용)로 변경.
# False로 두면 사후관리실장에게 "0.5일 미만 보유 종목은 데이트레이딩 회피" 가이드가 다시 들어간다.
ALLOW_DAY_TRADING     = True
# 0.5일 미만 회피가 켜졌을 때(ALLOW_DAY_TRADING=False) 적용되는 최소 보유일.
MIN_HOLDING_DAYS_FOR_SELL = 0.5
# 사장 지시 2026-05-29: 펀드기획실장 거부권 — 진입 thesis(목표·손절·계획 보유기간) 대비
# 계획기간 미경과 + 손익이 ±노이즈밴드 이내 + 손절·목표 미해당인 매도결정을 '보유'로 결정론적 보류
# (무계획 단타 차단). 손절터치·목표도달·계획기간경과·진짜손실(밴드 밖) 매도는 비차단.
# 사장 지시 2026-06-04: 소폭'손실'까지 밴드로 확대 + ALLOW_DAY_TRADING 무관하게 발동(이 플래그로만 on/off).
THESIS_VETO_ENABLED   = True
# 노이즈밴드 폭(±%). 진입가 대비 이 폭 이내의 미세 손익은 '계획이 펼쳐지기 전 noise'로 보고 매도 보류.
# 진짜 손실(이 폭 밖, 예: -7% 손절)은 통과. 기본 3% — 오늘 churn(-1.5%/-0.6%)이 정확히 이 안에 들어온다.
THESIS_NOISE_BAND_PCT = 0.03

# When no target is affordable with available cash, look for a cheaper liquid name in whatever
# market is tradeable right now (KR session → KRX volume rank; US session → the shortlist below).
ENABLE_CHEAP_FALLBACK = False        # 대체 후보(엉뚱한 저가 종목 매수) 비활성 — 사장 지시(2026-05). 살 게 없으면 그냥 매수 생략.
# 1주 매수 예산 허용 초과율 — 1주 가격이 1종목 예산을 이 배율 이내로만 넘으면 1주 매수 허용 (사장 지시: +20%)
PER_ORDER_BUDGET_OVERSHOOT = 1.20
# (대체 후보가 켜졌을 때만 사용) 거래량 상위에서 제외할 위험·부적격 키워드 + 최소 가격
CHEAP_FALLBACK_EXCLUDE_KEYWORDS = ["레버리지", "인버스", "곱버스", "2X", "3X", "선물", "ETN", "TR", "커버드콜"]
CHEAP_FALLBACK_MIN_PRICE = 2000      # 이 가격(원) 미만 종목은 대체 후보에서 제외 (잡주/저가 ETN 회피)
# Liquid, generally low-priced US names used as the US-session cheap fallback universe (대체 후보).
CHEAP_FALLBACK_US_TICKERS = ["F", "BAC", "T", "PFE", "KO", "CSCO", "INTC", "SOFI", "NU", "WBD", "SIRI", "KVUE", "VALE"]

# Which instruments the autonomous engine may BUY. KR equities & ETFs are 6-digit codes (same path).
# US needs USD cash; derivatives (선물/옵션) need margin/contract handling — off by default (high risk).
ALLOW_US_STOCKS       = True
ALLOW_DERIVATIVES     = False         # KR/해외 선물·옵션 자동매매 (마진·만기·증거금 처리 필요 — 기본 비활성)
# Position sizing — orders are sized to the *actual* account, not a fixed share count.
MAX_ORDER_QTY        = 0              # tunable share-count ceiling per order; 0 = fall back to HARD_MAX_ORDER_QTY
# 사장 지시 2026-06-04: 저가주에 예산이 몰리면 budget/단가 = 큰 수량이 산출돼 1회 한도에 걸려
# 매수가 '전량 반려'되던 버그(052900 10,227주) 방지용 절대 상한. 초과분은 '반려'가 아니라 이 값으로
# clamp 해서 주문이 살아 나가게 한다(매수 한정 — 매도는 위험회피라 미적용). MAX_ORDER_QTY(>0) 우선.
HARD_MAX_ORDER_QTY   = 1000           # absolute per-order share ceiling (safety backstop; buys clamped, not rejected)
PER_ORDER_BUDGET_RATIO = 0.10         # one order may spend at most this fraction of available cash (예수금)
MAX_CYCLE_BUDGET_RATIO = 0.25         # all orders in one cycle may spend at most this fraction of cash
MIN_CASH_BUFFER       = 1.10          # require cash ≥ notional × this (slippage/fee headroom) — else reject
# Conservative risk gates (리스크관리실장 — tighter than the legacy 20%/-10% limits)
CONSERVATIVE_MDD       = 0.05         # block ALL new buys if account evaluation P&L ≤ -5%
CONSERVATIVE_STOCK_RATIO = 0.15       # a single position's notional may not exceed 15% of total eval (was 20%)

# ─── 전략 파라미터 확장 (사장 지시 2026-06-04) ───────────────────────────────
# 운용지원실장이 '급락장 대비'·'추세추종'·'역추세'·'모멘텀' 등 퀀트 전략 지시를 파라미터 조정만으로
# AI 매매에 반영할 수 있도록 추가한 '살아있는 노브'들. 전부 실제 매매 경로에 배선된다.
# spec: docs/superpowers/specs/2026-06-04-strategy-param-expansion-design.md
# (A) 종목 필터 — 매수 자격
MIN_QUANT_SCORE        = 6            # 결정론 게이트: 퀀트점수 < 이 값인 최종 매수대상은 제거(0~10)
MAX_BUY_VOLATILITY_PCT = 0.0          # 프롬프트: 연환산 변동성(%)이 이 값 초과면 매수부적합 (0=off)
RSI_OVERBOUGHT_SKIP    = 0            # 프롬프트: RSI 이 값 초과(과매수)면 신규매수 회피 (0=off)
MIN_ADX_FOR_BUY        = 0            # 프롬프트: ADX 이 값 미만(추세약)이면 매수부적합 (0=off, 추세추종용)
REQUIRE_FOREIGN_NET_BUY = False       # 프롬프트: 외국인 순매수(+) 종목만 매수 적격
MAX_PRICE_EXTENSION_PCT = 0.0         # 프롬프트: VWAP/이평 대비 이격(%)이 이 값 초과면 추격매수 회피 (0=off)
# (B) 퀀트 채점 가중치(QW_*) — 2026-06-04 결정론 점수 엔진 도입으로 **완전 폐기**. 더 세밀한 QIW_*(지표별)로 대체.
# (C) 레짐 대응
MACRO_STOCK_GATE_ENABLED = True       # 결정론: 매크로 권고 주식비중 ≤ 현재 주식비중이면 신규 매수평가 스킵

# ─── 결정론 점수 엔진 (사장 지시 2026-06-04: LLM 일관성 문제 → 점수는 무조건 파이썬) ───────
# spec: docs/superpowers/specs/2026-06-04-deterministic-score-engine-design.md
# 퀀트 점수 = 지표별 부호 가중치(QIW_*)로 결정론 산정. 뉴스·매크로와 함께 차원 가중치(DW_*)로 합성.
# 전부 signed(음수 허용) — 음수면 그 축을 반대로(예: QIW_VWAP 음수 = 과이격 추격 허용).
# 퀀트 지표 가중치 (신호명: rsi/macd/adx/vwap/vol/mom/cmf/flow/high52)
QIW_RSI   = 5     # 과매도→+ 과매수→− (평균회귀)
QIW_MACD  = 10    # 추세전환 모멘텀
QIW_ADX   = 8     # 추세 강도·방향
QIW_VWAP  = 8     # 과이격(추격) 페널티
QIW_VOL   = 8     # 고변동 페널티
QIW_MOM   = 12    # 1M·3M 모멘텀
QIW_CMF   = 8     # 매집/분산
QIW_FLOW  = 12    # 외인·기관 수급
QIW_HIGH52 = 8    # 신고가 근접(모멘텀)
# 차원 가중치 — 퀀트(기술) / 뉴스 / 매크로 레짐
DW_QUANT  = 60
DW_NEWS   = 25
DW_MACRO  = 15
# 점수 산정 주체 토글: True=파이썬 결정론(권장), False=구 LLM 채점으로 즉시 롤백
DETERMINISTIC_SCORING = True

# ─── 제도권 파이프라인 4기능 (사장 지시 2026-06-04) ───────────────────────────
# spec: docs/superpowers/specs/2026-06-04-institutional-pipeline-mimicry-design.md
# ① 퀀트 점수가 선정에 영향 — 한 사이클 최대 매수 종목 수(랭크 상위부터 자금배정)
MAX_BUY_NAMES          = 8            # 통과 후보가 이보다 많으면 퀀트점수 상위 N개만 매수(큰 값=무변화)
# ② 리스크기반 포지션 사이징
POSITION_SIZING_MODE   = "risk_weighted"  # 'equal'=균등(기존) / 'risk_weighted'=점수·역변동성 가중
SIZING_TILT_STRENGTH   = 0.5          # 0=완전균등 … 1=완전기울임 (균등분배와 raw가중 사이 보간)
SIZING_MAX_TILT        = 2.0          # 균등 대비 한 종목 최대/최소 배수(과집중 방지)
# ③ 유니버스 스크리닝 결정론화 (후보 풀 사전 배제 — 무음 금지, 로그 남김)
UNIVERSE_MIN_PRICE     = 0.0          # 현재가 < 이 값(원, US는 USD 별도 임계 미적용) 후보 배제 (0=off)
UNIVERSE_MIN_TURNOVER  = 0.0          # 일거래대금 < 이 값 후보 배제 (0=off)
UNIVERSE_EXCLUDE_LEVERAGED = True     # 레버리지/인버스/곱버스/ETN 후보 배제
# ④ 성과귀인 스코어카드 — 귀인 트레일링 윈도우(일)
SCORECARD_WINDOW_DAYS  = 30


def strategy_param_catalog_text():
    """STRATEGY_TUNABLE_KEYS + STRATEGY_KEY_META 로 전략 파라미터 카탈로그 텍스트를 만든다.
    운용지원실장 프롬프트에 런타임 주입 — 파라미터가 늘어도 카탈로그가 자동 최신화(stale 방지).
    각 줄: KEY (라벨) [type·범위·단위]: help. 효과: effect."""
    lines = []
    cur_group = None
    for k in STRATEGY_TUNABLE_KEYS:
        m = STRATEGY_KEY_META.get(k)
        if not m:
            continue
        g = m.get("group", "기타")
        if g != cur_group:
            lines.append(f"\n[{g}]")
            cur_group = g
        rng = ""
        if "min" in m and "max" in m:
            rng = f" {m['min']}~{m['max']}{m.get('unit','')}"
        eff = m.get("effect") or STRATEGY_KEY_EFFECT.get(k)
        eff_txt = f" 효과: {eff}" if eff else ""
        lines.append(f"- {k} ({m.get('label','')}) [{m.get('type','')}{rng}]: {m.get('help','')}.{eff_txt}")
    return "\n".join(lines)


# ─── Strategy presets ───────────────────────────────────────────────────────
# A "strategy" is the set of tunable trading parameters. The active preset is picked in the
# dashboard '전략' tab; runtime.py persists the choice and serves live overrides to the engine.
# Keys here MUST match module-level constant names above (runtime.get() falls back to those).
STRATEGY_TUNABLE_KEYS = [
    "PER_ORDER_BUDGET_RATIO", "PER_ORDER_BUDGET_OVERSHOOT", "MAX_CYCLE_BUDGET_RATIO", "MIN_CASH_BUFFER",
    "CONSERVATIVE_MDD", "CONSERVATIVE_STOCK_RATIO",
    "MAX_TRADES_PER_CYCLE", "MAX_ORDER_QTY",
    # (A) 종목 필터 — 매수 자격 (사장 지시 2026-06-04)
    "MIN_QUANT_SCORE", "MAX_BUY_VOLATILITY_PCT", "RSI_OVERBOUGHT_SKIP", "MIN_ADX_FOR_BUY",
    "REQUIRE_FOREIGN_NET_BUY", "MAX_PRICE_EXTENSION_PCT",
    # (B) 결정론 점수 엔진 — 퀀트 지표 가중치 + 차원 가중치 + 토글 (사장 지시 2026-06-04, QW_* 대체)
    "QIW_RSI", "QIW_MACD", "QIW_ADX", "QIW_VWAP", "QIW_VOL", "QIW_MOM", "QIW_CMF", "QIW_FLOW", "QIW_HIGH52",
    "DW_QUANT", "DW_NEWS", "DW_MACRO", "DETERMINISTIC_SCORING",
    # 제도권 파이프라인 4기능 (사장 지시 2026-06-04)
    "MAX_BUY_NAMES", "POSITION_SIZING_MODE", "SIZING_TILT_STRENGTH", "SIZING_MAX_TILT",
    "UNIVERSE_MIN_PRICE", "UNIVERSE_MIN_TURNOVER", "UNIVERSE_EXCLUDE_LEVERAGED",
    "SCORECARD_WINDOW_DAYS",
    # (C) 레짐 대응
    "MACRO_STOCK_GATE_ENABLED",
    "ENABLE_SELL_REBALANCE", "TAKE_PROFIT_PCT", "STOP_LOSS_PCT", "TRIM_OVER_RATIO",
    "ALLOW_DAY_TRADING", "MIN_HOLDING_DAYS_FOR_SELL", "THESIS_VETO_ENABLED", "THESIS_NOISE_BAND_PCT",
    "ENABLE_CHEAP_FALLBACK", "ALLOW_US_STOCKS", "ALLOW_DERIVATIVES",
]

# ─── Strategy parameter metadata (사장 지시 2026-05-14: UI에 한국어 라벨로 표시) ────
# type:
#   pct_ratio  → 저장값 0.10, UI 표시 10 (단위 %)
#   pct_raw    → 저장값 12.0, UI 표시 12.0 (단위 %)
#   multiplier → 저장값 1.20, UI 표시 1.20 (단위 ×)
#   int        → 정수 그대로
#   bool       → true/false 토글
STRATEGY_KEY_META = {
    "PER_ORDER_BUDGET_RATIO":     {"label": "1주문 예수금 사용 비율", "type": "pct_ratio", "unit": "%",
                                   "help": "한 번 매수 시 예수금의 최대 X%까지 사용 (예: 10 = 예수금의 10%)",
                                   "min": 1, "max": 100, "step": 1, "group": "사이징"},
    "PER_ORDER_BUDGET_OVERSHOOT": {"label": "1주 매수 예산 허용 초과율", "type": "multiplier", "unit": "×",
                                   "help": "1주 가격이 1주문 예산을 이 배율 이내로만 넘으면 매수 허용 (1.20 = +20%까지)",
                                   "min": 1.0, "max": 2.0, "step": 0.05, "group": "사이징"},
    "MAX_CYCLE_BUDGET_RATIO":     {"label": "사이클당 최대 예수금 사용", "type": "pct_ratio", "unit": "%",
                                   "help": "한 사이클의 모든 주문이 사용할 수 있는 예수금의 최대 비율",
                                   "min": 1, "max": 100, "step": 1, "group": "사이징"},
    "MIN_CASH_BUFFER":            {"label": "현금 안전 마진 (체결 슬리피지·수수료 여유)", "type": "multiplier", "unit": "×",
                                   "help": "주문 노티오날 × 이 배율보다 예수금이 적으면 거부 (1.10 = +10% 여유 요구)",
                                   "min": 1.0, "max": 1.5, "step": 0.01, "group": "사이징"},
    "CONSERVATIVE_MDD":           {"label": "계좌 최대 손실 한도 (도달 시 신규 매수 차단)", "type": "pct_ratio", "unit": "%",
                                   "help": "계좌 평가손익이 -X% 이하면 신규 매수 전면 차단",
                                   "min": 1, "max": 30, "step": 1, "group": "리스크"},
    "CONSERVATIVE_STOCK_RATIO":   {"label": "단일 종목 최대 비중", "type": "pct_ratio", "unit": "%",
                                   "help": "한 종목 평가액이 총자산의 X%를 초과하면 매수 차단·초과분 매도",
                                   "min": 5, "max": 50, "step": 1, "group": "리스크"},
    "MAX_TRADES_PER_CYCLE":       {"label": "사이클당 최대 매매 건수", "type": "int", "unit": "건",
                                   "help": "한 사이클에서 실제 체결 시도할 주문의 최대 개수 (매도 우선)",
                                   "min": 0, "max": 10, "step": 1, "group": "리스크"},
    "MAX_ORDER_QTY":              {"label": "1주문 최대 수량 (0 = 제한 없음)", "type": "int", "unit": "주",
                                   "help": "한 주문이 살 수 있는 최대 주식 수. 0이면 사이징 룰에만 의존",
                                   "min": 0, "max": 10000, "step": 1, "group": "리스크"},
    # (A) 종목 필터 — 매수 자격 (사장 지시 2026-06-04)
    "MIN_QUANT_SCORE":            {"label": "최소 퀀트 점수 (미달 매수 제외)", "type": "int", "unit": "점",
                                   "help": "계량분석팀장 점수가 이 값 미만인 최종 매수대상을 결정론적으로 제거 (0~10)",
                                   "min": 0, "max": 10, "step": 1, "group": "종목 필터"},
    "MAX_BUY_VOLATILITY_PCT":     {"label": "매수 허용 최대 변동성 (연환산 %)", "type": "pct_raw", "unit": "%",
                                   "help": "연환산 변동성이 이 값을 초과하는 종목은 매수부적합 처리 (0 = 제한 없음)",
                                   "min": 0, "max": 200, "step": 5, "group": "종목 필터"},
    "RSI_OVERBOUGHT_SKIP":        {"label": "RSI 과매수 매수회피 기준", "type": "int", "unit": "",
                                   "help": "RSI(14)가 이 값을 초과하면 신규 매수 회피 (0 = 제한 없음, 예: 70~75)",
                                   "min": 0, "max": 100, "step": 1, "group": "종목 필터"},
    "MIN_ADX_FOR_BUY":            {"label": "매수 요구 최소 추세강도 (ADX)", "type": "int", "unit": "",
                                   "help": "ADX(14)가 이 값 미만이면(추세 약함) 매수부적합 (0 = 제한 없음, 추세추종용)",
                                   "min": 0, "max": 60, "step": 1, "group": "종목 필터"},
    "REQUIRE_FOREIGN_NET_BUY":    {"label": "외국인 순매수 종목만 매수", "type": "bool",
                                   "help": "ON이면 외국인 수급이 순매수(+)인 종목만 매수 적격으로 본다",
                                   "group": "종목 필터"},
    "MAX_PRICE_EXTENSION_PCT":    {"label": "매수 허용 최대 이격도 (VWAP/이평 대비 %)", "type": "pct_raw", "unit": "%",
                                   "help": "현재가가 VWAP/이동평균 대비 이 값 초과로 위에 있으면 추격매수 회피 (0 = 제한 없음)",
                                   "min": 0, "max": 50, "step": 1, "group": "종목 필터"},
    # (B) 결정론 점수 엔진 — 퀀트 지표 가중치(signed, 음수 허용) (사장 지시 2026-06-04)
    "QIW_RSI":                    {"label": "지표 가중치: RSI(과매수/과매도)", "type": "int", "unit": "",
                                   "help": "RSI 신호 가중치. +면 과매도 우호·과매수 페널티(평균회귀). 음수면 반전(모멘텀)", "min": -50, "max": 50, "step": 1, "group": "퀀트 지표 가중치"},
    "QIW_MACD":                   {"label": "지표 가중치: MACD 모멘텀", "type": "int", "unit": "",
                                   "help": "MACD 히스토그램 신호 가중치. +면 상승 모멘텀 우호", "min": -50, "max": 50, "step": 1, "group": "퀀트 지표 가중치"},
    "QIW_ADX":                    {"label": "지표 가중치: ADX 추세강도", "type": "int", "unit": "",
                                   "help": "추세 강도·방향 가중치. +면 강한 상승추세 우호(추세추종)", "min": -50, "max": 50, "step": 1, "group": "퀀트 지표 가중치"},
    "QIW_VWAP":                   {"label": "지표 가중치: VWAP 이격", "type": "int", "unit": "",
                                   "help": "VWAP 이격 가중치. +면 과이격(추격) 페널티. 음수면 추격 허용", "min": -50, "max": 50, "step": 1, "group": "퀀트 지표 가중치"},
    "QIW_VOL":                    {"label": "지표 가중치: 변동성", "type": "int", "unit": "",
                                   "help": "σ20 가중치. +면 고변동 페널티(방어). 음수면 고변동 선호", "min": -50, "max": 50, "step": 1, "group": "퀀트 지표 가중치"},
    "QIW_MOM":                    {"label": "지표 가중치: 모멘텀(1M·3M)", "type": "int", "unit": "",
                                   "help": "수익률 모멘텀 가중치. +면 상승 추세 우호", "min": -50, "max": 50, "step": 1, "group": "퀀트 지표 가중치"},
    "QIW_CMF":                    {"label": "지표 가중치: CMF 자금흐름", "type": "int", "unit": "",
                                   "help": "Chaikin Money Flow 가중치. +면 매집 우호·분산 페널티", "min": -50, "max": 50, "step": 1, "group": "퀀트 지표 가중치"},
    "QIW_FLOW":                   {"label": "지표 가중치: 수급(외인·기관)", "type": "int", "unit": "",
                                   "help": "외인+기관 순매수 가중치. +면 순매수 우호", "min": -50, "max": 50, "step": 1, "group": "퀀트 지표 가중치"},
    "QIW_HIGH52":                 {"label": "지표 가중치: 52주 신고가 근접", "type": "int", "unit": "",
                                   "help": "신고가 근접 가중치. +면 신고가 모멘텀 우호", "min": -50, "max": 50, "step": 1, "group": "퀀트 지표 가중치"},
    # 차원 가중치 + 토글
    "DW_QUANT":                   {"label": "차원 가중치: 퀀트(기술)", "type": "int", "unit": "",
                                   "help": "최종 점수에서 퀀트(지표) 차원 비중(signed)", "min": -100, "max": 100, "step": 5, "group": "점수 차원"},
    "DW_NEWS":                    {"label": "차원 가중치: 뉴스", "type": "int", "unit": "",
                                   "help": "뉴스 감성 차원 비중(signed). 음수면 호재일수록 감점(역발상)", "min": -100, "max": 100, "step": 5, "group": "점수 차원"},
    "DW_MACRO":                   {"label": "차원 가중치: 매크로 레짐", "type": "int", "unit": "",
                                   "help": "매크로 권고 주식비중 차원(signed). +면 강세 매크로일수록 가점", "min": -100, "max": 100, "step": 5, "group": "점수 차원"},
    "DETERMINISTIC_SCORING":      {"label": "결정론 점수 사용(끄면 구 LLM 채점)", "type": "bool",
                                   "help": "ON=퀀트점수를 파이썬이 결정론적으로 산정(일관). OFF=구 LLM 정성 채점으로 롤백", "group": "점수 차원"},
    # (C) 레짐 대응
    "MACRO_STOCK_GATE_ENABLED":   {"label": "매크로 주식비중 매수게이트", "type": "bool",
                                   "help": "ON이면 매크로 권고 주식비중 ≤ 현재 주식 평가비중일 때 신규 매수평가를 건너뜀(매도/관리만)",
                                   "group": "레짐 대응"},
    "ENABLE_SELL_REBALANCE":      {"label": "자동 익절/손절/편중축소 매도 활성화", "type": "bool",
                                   "help": "보유 종목이 임계 도달 시 자동 매도 (사후관리실장이 별도 언급 안 한 종목만)",
                                   "group": "매도 규칙"},
    "TAKE_PROFIT_PCT":            {"label": "익절 기준 (보유 수익률 ≥)", "type": "pct_raw", "unit": "%",
                                   "help": "보유 종목 평가손익이 +X%를 넘으면 자동 전량 매도",
                                   "min": 1, "max": 100, "step": 0.5, "group": "매도 규칙"},
    "STOP_LOSS_PCT":              {"label": "손절 기준 (보유 손실률 ≥)", "type": "pct_raw", "unit": "%",
                                   "help": "보유 종목 평가손익이 -X% 이하로 떨어지면 자동 전량 매도",
                                   "min": 1, "max": 50, "step": 0.5, "group": "매도 규칙"},
    "TRIM_OVER_RATIO":            {"label": "단일 종목 비중 초과 시 자동 축소", "type": "bool",
                                   "help": "단일 종목 비중이 한도를 넘으면 초과분만큼 부분 매도",
                                   "group": "매도 규칙"},
    "ALLOW_DAY_TRADING":          {"label": "데이트레이딩 허용 (단기 매도 OK)", "type": "bool",
                                   "help": "ON이면 보유기간 무관하게 매도 가능. OFF면 최소 보유일 미만은 데이트레이딩 회피",
                                   "group": "매도 규칙"},
    "MIN_HOLDING_DAYS_FOR_SELL":  {"label": "최소 보유일 (데이트레이딩 OFF일 때만)", "type": "pct_raw", "unit": "일",
                                   "help": "데이트레이딩 비허용 시 이 일수 미만 보유 종목은 매도 회피 (사후관리실장)",
                                   "min": 0, "max": 30, "step": 0.5, "group": "매도 규칙"},
    "THESIS_VETO_ENABLED":        {"label": "펀드기획실장 거부권 (무계획 단타 차단)", "type": "bool",
                                   "help": "진입 thesis 대비 계획기간 미경과 + 손익이 ±노이즈밴드 이내(소폭손실 포함) + 손절·목표 미해당 "
                                           "매도결정을 '보유'로 보류. ALLOW_DAY_TRADING 무관(이 토글로만 on/off). 손절터치·목표도달·계획기간경과·진짜손실은 비차단",
                                   "group": "매도 규칙"},
    "ENABLE_CHEAP_FALLBACK":      {"label": "최종 종목 매수 불가 시 저가 대체 종목 매수", "type": "bool",
                                   "help": "OFF 권장 — 후보 모두 예산 초과 시 거래량 상위 저가주를 대신 매수",
                                   "group": "기타"},
    "ALLOW_US_STOCKS":            {"label": "미국 주식 매매 허용", "type": "bool",
                                   "help": "US 장 시간(KST 22:30~05:00)에 미국 상장 종목 거래",
                                   "group": "기타"},
    "ALLOW_DERIVATIVES":          {"label": "파생상품(선물/옵션) 매매 허용", "type": "bool",
                                   "help": "마진·증거금 처리 필요 — 기본 OFF",
                                   "group": "기타"},
    # 제도권 파이프라인 4기능 (사장 지시 2026-06-04)
    "MAX_BUY_NAMES":              {"label": "사이클당 최대 매수 종목 수", "type": "int", "unit": "개",
                                   "help": "퀀트점수 통과 후보가 이보다 많으면 점수 상위 N개만 매수(랭크 우선 자금배정)",
                                   "min": 1, "max": 20, "step": 1, "group": "종목 필터"},
    "POSITION_SIZING_MODE":       {"label": "포지션 사이징 방식", "type": "choice", "choices": ["equal", "risk_weighted"],
                                   "help": "equal=종목 균등분배(기존). risk_weighted=퀀트점수↑·변동성↓ 종목에 더 큰 비중",
                                   "group": "사이징"},
    "SIZING_TILT_STRENGTH":       {"label": "사이징 기울임 강도", "type": "pct_raw", "unit": "",
                                   "help": "0=완전 균등, 1=완전 기울임. risk_weighted일 때만 효과(0.5=절반 적용)",
                                   "min": 0.0, "max": 1.0, "step": 0.1, "group": "사이징"},
    "SIZING_MAX_TILT":            {"label": "사이징 최대 배수(과집중 방지)", "type": "multiplier", "unit": "×",
                                   "help": "한 종목이 균등분배 대비 받을 수 있는 최대/최소 배수(2.0=최대2배·최소0.5배)",
                                   "min": 1.0, "max": 4.0, "step": 0.5, "group": "사이징"},
    "UNIVERSE_MIN_PRICE":         {"label": "유니버스 최소 주가 (저가주 배제)", "type": "pct_raw", "unit": "원",
                                   "help": "현재가가 이 값 미만인 후보를 사전 배제(동전주 회피). 0=제한없음",
                                   "min": 0, "max": 10000, "step": 100, "group": "종목 필터"},
    "UNIVERSE_MIN_TURNOVER":      {"label": "유니버스 최소 일거래대금", "type": "pct_raw", "unit": "원",
                                   "help": "일거래대금이 이 값 미만인 후보를 사전 배제(유동성 확보). 0=제한없음",
                                   "min": 0, "max": 100000000000, "step": 100000000, "group": "종목 필터"},
    "UNIVERSE_EXCLUDE_LEVERAGED": {"label": "레버리지/인버스 ETF·ETN 배제", "type": "bool",
                                   "help": "ON이면 레버리지·인버스·곱버스·ETN 후보를 사전 배제",
                                   "group": "종목 필터"},
    "SCORECARD_WINDOW_DAYS":      {"label": "성과귀인 분석 기간(일)", "type": "int", "unit": "일",
                                   "help": "에이전트 스코어카드(IC·알파/베타)를 계산할 트레일링 윈도우",
                                   "min": 7, "max": 180, "step": 1, "group": "기타"},
}

# 사장 지시 2026-06-04: 각 파라미터를 '올리면/내리면(켜면/끄면) 어떤 방향'인지 — 운용지원실장이 전략
# 지시(급락장 대비·추세추종 등)를 파라미터로 충실히 번역하도록 카탈로그에 주입되는 effect 설명.
STRATEGY_KEY_EFFECT = {
    "PER_ORDER_BUDGET_RATIO": "올리면 한 종목에 더 크게 베팅(공격), 내리면 잘게 분산(방어).",
    "PER_ORDER_BUDGET_OVERSHOOT": "올리면 고가주도 1주 매수 허용, 내리면 예산 초과 매수 차단.",
    "MAX_CYCLE_BUDGET_RATIO": "올리면 한 사이클에 현금 더 투입(공격), 내리면 천천히(방어).",
    "MIN_CASH_BUFFER": "올리면 현금 여유 크게 요구→매수 보수적(급락장), 내리면 공격적.",
    "CONSERVATIVE_MDD": "내리면 작은 손실에도 신규매수 차단(방어), 올리면 손실 감내(공격).",
    "CONSERVATIVE_STOCK_RATIO": "내리면 종목당 비중 작게(분산·방어), 올리면 집중(공격).",
    "MAX_TRADES_PER_CYCLE": "내리면 매매 빈도↓(신중), 올리면 다종목 동시 매매(공격).",
    "MAX_ORDER_QTY": "0=제한없음. 줄이면 1회 주문 수량 상한(과대주문 방지).",
    "MIN_QUANT_SCORE": "올리면 더 엄선(고확신만 매수·방어/모멘텀), 내리면 폭넓게 매수.",
    "MAX_BUY_VOLATILITY_PCT": "내리면 고변동 종목 회피(급락장·방어), 0=제한없음(공격).",
    "RSI_OVERBOUGHT_SKIP": "내리면(예 70) 과매수 추격 회피(역추세·방어), 0=제한없음.",
    "MIN_ADX_FOR_BUY": "올리면 강한 추세 종목만 매수(추세추종), 0=제한없음.",
    "REQUIRE_FOREIGN_NET_BUY": "켜면 외국인 순매수 종목만(수급 방어), 끄면 무관.",
    "MAX_PRICE_EXTENSION_PCT": "내리면 이평/VWAP 멀리 뜬 종목 추격 회피(역추세), 0=제한없음.",
    "QIW_RSI": "+면 과매도 매수·과매수 회피(평균회귀). 음수면 반대(RSI 높을수록 가점=모멘텀).",
    "QIW_MACD": "+면 MACD 상승 모멘텀에 가점. 음수면 역추세.",
    "QIW_ADX": "+면 강한 상승추세에 가점(추세추종). 0이면 추세 무시.",
    "QIW_VWAP": "+면 평균(VWAP) 위로 많이 뜬 종목 감점(추격 회피). 음수면 추격 허용.",
    "QIW_VOL": "+면 고변동 종목 감점(방어). 음수면 고변동 선호(공격).",
    "QIW_MOM": "+면 1M·3M 상승 모멘텀에 가점. 음수면 역추세(낙폭과대).",
    "QIW_CMF": "+면 매집(자금유입) 가점·분산 감점. 음수면 반대.",
    "QIW_FLOW": "+면 외인·기관 순매수에 가점. 음수면 반대(역행).",
    "QIW_HIGH52": "+면 52주 신고가 근접에 가점(모멘텀). 음수면 고점 회피.",
    "DW_QUANT": "올리면 최종 점수에서 기술(지표) 비중↑.",
    "DW_NEWS": "올리면 뉴스 감성 비중↑. 음수면 호재일수록 감점(역발상).",
    "DW_MACRO": "올리면 매크로 레짐 비중↑(강세 매크로 가점·약세 감점). 음수면 역발상.",
    "DETERMINISTIC_SCORING": "켜면 파이썬 결정론 점수(일관). 끄면 구 LLM 정성 채점.",
    "MACRO_STOCK_GATE_ENABLED": "켜면 매크로가 주식 추가매수 불가일 때 신규매수 스킵(방어), 끄면 매크로 무시하고 매수평가.",
    "ENABLE_SELL_REBALANCE": "켜면 자동 익절/손절/편중축소, 끄면 사후관리실장 판단만.",
    "TAKE_PROFIT_PCT": "내리면 빨리 익절(보수), 올리면 길게 보유(추세추종·공격).",
    "STOP_LOSS_PCT": "내리면 타이트한 손절(급락장·방어), 올리면 느슨(공격).",
    "TRIM_OVER_RATIO": "켜면 비중 초과분 자동 부분매도(리밸런싱).",
    "ALLOW_DAY_TRADING": "켜면 보유기간 무관 매도, 끄면 최소보유일 미만 단타 회피.",
    "MIN_HOLDING_DAYS_FOR_SELL": "올리면 더 오래 보유 강제(데이트레이딩 OFF 시).",
    "THESIS_VETO_ENABLED": "켜면 계획 펼쳐지기 전 무계획 단타 차단(churn 방지).",
    "THESIS_NOISE_BAND_PCT": "올리면 더 넓은 손익대를 noise로 보고 단타 차단(보유 강화), 내리면 완화.",
    "ENABLE_CHEAP_FALLBACK": "OFF 권장 — 켜면 후보 매수불가 시 저가주 대체매수(권장X).",
    "ALLOW_US_STOCKS": "켜면 미국 주식 매매 허용.",
    "ALLOW_DERIVATIVES": "켜면 파생(선물/옵션) — 고위험, 기본 OFF.",
    "MAX_BUY_NAMES": "내리면 고확신 소수 종목에 집중(분산↓), 올리면 폭넓게 분산 매수.",
    "POSITION_SIZING_MODE": "risk_weighted=점수높고 변동성낮은 종목에 큰 비중(샤프 개선). equal=균등(기존).",
    "SIZING_TILT_STRENGTH": "올리면 점수·변동성 차이를 비중에 강하게 반영(공격), 0이면 균등.",
    "SIZING_MAX_TILT": "올리면 한 종목 비중 편차 허용(집중), 내리면 균등에 가깝게(분산·방어).",
    "UNIVERSE_MIN_PRICE": "올리면 저가·동전주 배제(품질 방어), 0=제한없음.",
    "UNIVERSE_MIN_TURNOVER": "올리면 거래 활발한 종목만(유동성·급락장 방어), 0=제한없음.",
    "UNIVERSE_EXCLUDE_LEVERAGED": "켜면 레버리지/인버스/ETN 배제(변동성 방어), 끄면 허용.",
    "SCORECARD_WINDOW_DAYS": "올리면 더 긴 기간으로 에이전트 성과 평가(안정), 내리면 최근 위주(민감).",
}
# 사장 지시 2026-05-14: 보수→공격 스펙트럼 순서로 노출 (방어형 → 보수형 → 균형형 → 공격형 → 초공격형)
STRATEGY_PRESETS = {
    "defensive": {"label": "방어형 — 변동성 시장·자산 보존 우선",
        "PER_ORDER_BUDGET_RATIO": 0.03, "PER_ORDER_BUDGET_OVERSHOOT": 1.05, "MAX_CYCLE_BUDGET_RATIO": 0.10, "MIN_CASH_BUFFER": 1.20,
        "CONSERVATIVE_MDD": 0.025, "CONSERVATIVE_STOCK_RATIO": 0.07, "MAX_TRADES_PER_CYCLE": 1, "MAX_ORDER_QTY": 0,
        "MIN_QUANT_SCORE": 7, "MAX_BUY_VOLATILITY_PCT": 40, "RSI_OVERBOUGHT_SKIP": 70, "MIN_ADX_FOR_BUY": 0,
        "REQUIRE_FOREIGN_NET_BUY": True, "MAX_PRICE_EXTENSION_PCT": 10,
        "QIW_RSI": 6, "QIW_MACD": 8, "QIW_ADX": 6, "QIW_VWAP": 12, "QIW_VOL": 16, "QIW_MOM": 6, "QIW_CMF": 10, "QIW_FLOW": 16, "QIW_HIGH52": 4,
        "DW_QUANT": 55, "DW_NEWS": 15, "DW_MACRO": 30, "DETERMINISTIC_SCORING": True, "MACRO_STOCK_GATE_ENABLED": True,
        "MAX_BUY_NAMES": 3, "POSITION_SIZING_MODE": "risk_weighted", "SIZING_TILT_STRENGTH": 0.6, "SIZING_MAX_TILT": 2.0,
        "UNIVERSE_MIN_PRICE": 2000, "UNIVERSE_MIN_TURNOVER": 1000000000, "UNIVERSE_EXCLUDE_LEVERAGED": True, "SCORECARD_WINDOW_DAYS": 30,
        "ENABLE_SELL_REBALANCE": True, "TAKE_PROFIT_PCT": 6.0, "STOP_LOSS_PCT": 3.5, "TRIM_OVER_RATIO": True,
        "ALLOW_DAY_TRADING": False, "MIN_HOLDING_DAYS_FOR_SELL": 1.0, "THESIS_VETO_ENABLED": True,
        "ENABLE_CHEAP_FALLBACK": False, "ALLOW_US_STOCKS": False, "ALLOW_DERIVATIVES": False},
    "conservative": {"label": "보수형 — 작게, 손절 빠르게, 사이클 드물게",
        "PER_ORDER_BUDGET_RATIO": 0.05, "PER_ORDER_BUDGET_OVERSHOOT": 1.10, "MAX_CYCLE_BUDGET_RATIO": 0.15, "MIN_CASH_BUFFER": 1.15,
        "CONSERVATIVE_MDD": 0.04, "CONSERVATIVE_STOCK_RATIO": 0.10, "MAX_TRADES_PER_CYCLE": 1, "MAX_ORDER_QTY": 0,
        "MIN_QUANT_SCORE": 7, "MAX_BUY_VOLATILITY_PCT": 50, "RSI_OVERBOUGHT_SKIP": 75, "MIN_ADX_FOR_BUY": 0,
        "REQUIRE_FOREIGN_NET_BUY": True, "MAX_PRICE_EXTENSION_PCT": 15,
        "QIW_RSI": 6, "QIW_MACD": 9, "QIW_ADX": 7, "QIW_VWAP": 10, "QIW_VOL": 12, "QIW_MOM": 9, "QIW_CMF": 9, "QIW_FLOW": 14, "QIW_HIGH52": 6,
        "DW_QUANT": 58, "DW_NEWS": 20, "DW_MACRO": 22, "DETERMINISTIC_SCORING": True, "MACRO_STOCK_GATE_ENABLED": True,
        "MAX_BUY_NAMES": 4, "POSITION_SIZING_MODE": "risk_weighted", "SIZING_TILT_STRENGTH": 0.5, "SIZING_MAX_TILT": 2.0,
        "UNIVERSE_MIN_PRICE": 1000, "UNIVERSE_MIN_TURNOVER": 500000000, "UNIVERSE_EXCLUDE_LEVERAGED": True, "SCORECARD_WINDOW_DAYS": 30,
        "ENABLE_SELL_REBALANCE": True, "TAKE_PROFIT_PCT": 8.0, "STOP_LOSS_PCT": 5.0, "TRIM_OVER_RATIO": True,
        "ALLOW_DAY_TRADING": False, "MIN_HOLDING_DAYS_FOR_SELL": 0.5, "THESIS_VETO_ENABLED": True,
        "ENABLE_CHEAP_FALLBACK": False, "ALLOW_US_STOCKS": False, "ALLOW_DERIVATIVES": False},
    "balanced": {"label": "균형형 — 기본값 (권장)",
        "PER_ORDER_BUDGET_RATIO": 0.10, "PER_ORDER_BUDGET_OVERSHOOT": 1.20, "MAX_CYCLE_BUDGET_RATIO": 0.25, "MIN_CASH_BUFFER": 1.10,
        "CONSERVATIVE_MDD": 0.05, "CONSERVATIVE_STOCK_RATIO": 0.15, "MAX_TRADES_PER_CYCLE": 2, "MAX_ORDER_QTY": 0,
        "MIN_QUANT_SCORE": 6, "MAX_BUY_VOLATILITY_PCT": 0, "RSI_OVERBOUGHT_SKIP": 0, "MIN_ADX_FOR_BUY": 0,
        "REQUIRE_FOREIGN_NET_BUY": False, "MAX_PRICE_EXTENSION_PCT": 0,
        "QIW_RSI": 5, "QIW_MACD": 10, "QIW_ADX": 8, "QIW_VWAP": 8, "QIW_VOL": 8, "QIW_MOM": 12, "QIW_CMF": 8, "QIW_FLOW": 12, "QIW_HIGH52": 8,
        "DW_QUANT": 60, "DW_NEWS": 25, "DW_MACRO": 15, "DETERMINISTIC_SCORING": True, "MACRO_STOCK_GATE_ENABLED": True,
        "MAX_BUY_NAMES": 8, "POSITION_SIZING_MODE": "risk_weighted", "SIZING_TILT_STRENGTH": 0.5, "SIZING_MAX_TILT": 2.0,
        "UNIVERSE_MIN_PRICE": 0, "UNIVERSE_MIN_TURNOVER": 0, "UNIVERSE_EXCLUDE_LEVERAGED": True, "SCORECARD_WINDOW_DAYS": 30,
        "ENABLE_SELL_REBALANCE": True, "TAKE_PROFIT_PCT": 12.0, "STOP_LOSS_PCT": 5.0, "TRIM_OVER_RATIO": True,
        "ALLOW_DAY_TRADING": True, "MIN_HOLDING_DAYS_FOR_SELL": 0.5, "THESIS_VETO_ENABLED": True,
        "ENABLE_CHEAP_FALLBACK": False, "ALLOW_US_STOCKS": True, "ALLOW_DERIVATIVES": False},
    "aggressive": {"label": "공격형 — 크게, 길게 보유, 사이클 잦게",
        "PER_ORDER_BUDGET_RATIO": 0.20, "PER_ORDER_BUDGET_OVERSHOOT": 1.30, "MAX_CYCLE_BUDGET_RATIO": 0.40, "MIN_CASH_BUFFER": 1.05,
        "CONSERVATIVE_MDD": 0.08, "CONSERVATIVE_STOCK_RATIO": 0.25, "MAX_TRADES_PER_CYCLE": 3, "MAX_ORDER_QTY": 0,
        "MIN_QUANT_SCORE": 5, "MAX_BUY_VOLATILITY_PCT": 0, "RSI_OVERBOUGHT_SKIP": 0, "MIN_ADX_FOR_BUY": 0,
        "REQUIRE_FOREIGN_NET_BUY": False, "MAX_PRICE_EXTENSION_PCT": 0,
        "QIW_RSI": 4, "QIW_MACD": 12, "QIW_ADX": 10, "QIW_VWAP": 5, "QIW_VOL": 4, "QIW_MOM": 18, "QIW_CMF": 7, "QIW_FLOW": 10, "QIW_HIGH52": 12,
        "DW_QUANT": 55, "DW_NEWS": 35, "DW_MACRO": 10, "DETERMINISTIC_SCORING": True, "MACRO_STOCK_GATE_ENABLED": True,
        "MAX_BUY_NAMES": 10, "POSITION_SIZING_MODE": "risk_weighted", "SIZING_TILT_STRENGTH": 0.7, "SIZING_MAX_TILT": 3.0,
        "UNIVERSE_MIN_PRICE": 0, "UNIVERSE_MIN_TURNOVER": 0, "UNIVERSE_EXCLUDE_LEVERAGED": False, "SCORECARD_WINDOW_DAYS": 30,
        "ENABLE_SELL_REBALANCE": True, "TAKE_PROFIT_PCT": 18.0, "STOP_LOSS_PCT": 10.0, "TRIM_OVER_RATIO": False,
        "ALLOW_DAY_TRADING": True, "MIN_HOLDING_DAYS_FOR_SELL": 0.0, "THESIS_VETO_ENABLED": True,
        "ENABLE_CHEAP_FALLBACK": False, "ALLOW_US_STOCKS": True, "ALLOW_DERIVATIVES": False},
    "ultra_aggressive": {"label": "초공격형 — 최대 베팅·고위험 고리워드",
        "PER_ORDER_BUDGET_RATIO": 0.35, "PER_ORDER_BUDGET_OVERSHOOT": 1.50, "MAX_CYCLE_BUDGET_RATIO": 0.70, "MIN_CASH_BUFFER": 1.02,
        "CONSERVATIVE_MDD": 0.15, "CONSERVATIVE_STOCK_RATIO": 0.40, "MAX_TRADES_PER_CYCLE": 5, "MAX_ORDER_QTY": 0,
        "MIN_QUANT_SCORE": 4, "MAX_BUY_VOLATILITY_PCT": 0, "RSI_OVERBOUGHT_SKIP": 0, "MIN_ADX_FOR_BUY": 0,
        "REQUIRE_FOREIGN_NET_BUY": False, "MAX_PRICE_EXTENSION_PCT": 0,
        "QIW_RSI": 3, "QIW_MACD": 14, "QIW_ADX": 12, "QIW_VWAP": 3, "QIW_VOL": 2, "QIW_MOM": 22, "QIW_CMF": 6, "QIW_FLOW": 8, "QIW_HIGH52": 16,
        "DW_QUANT": 50, "DW_NEWS": 40, "DW_MACRO": 10, "DETERMINISTIC_SCORING": True, "MACRO_STOCK_GATE_ENABLED": True,
        "MAX_BUY_NAMES": 12, "POSITION_SIZING_MODE": "risk_weighted", "SIZING_TILT_STRENGTH": 1.0, "SIZING_MAX_TILT": 4.0,
        "UNIVERSE_MIN_PRICE": 0, "UNIVERSE_MIN_TURNOVER": 0, "UNIVERSE_EXCLUDE_LEVERAGED": False, "SCORECARD_WINDOW_DAYS": 14,
        "ENABLE_SELL_REBALANCE": True, "TAKE_PROFIT_PCT": 30.0, "STOP_LOSS_PCT": 15.0, "TRIM_OVER_RATIO": False,
        "ALLOW_DAY_TRADING": True, "MIN_HOLDING_DAYS_FOR_SELL": 0.0, "THESIS_VETO_ENABLED": True,
        "ENABLE_CHEAP_FALLBACK": False, "ALLOW_US_STOCKS": True, "ALLOW_DERIVATIVES": False},
}
DEFAULT_STRATEGY = "balanced"

# ─── Server ──────────────────────────────────────────────────────────────────
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("APP_PORT", "8500"))
