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
    # 사장 피드백 2026-05-15 (8차): 검색 특화 모델 alibaba/tongyi-deepresearch-30b-a3b로 통합.
    # ① 뉴스 분류: 기업명 → 상장 시장 동적 lookup (긴 화이트리스트 불필요)
    # ② 매크로 리서치: 전략리서치팀장의 시황·정책·심리 검색 (Tavily 대체)
    "news_classifier":   "alibaba/tongyi-deepresearch-30b-a3b",
    "macro_researcher":  "alibaba/tongyi-deepresearch-30b-a3b",
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
# 계획기간 미경과 + 소폭이익 + 손절·목표 미해당인 매도결정을 '보유'로 결정론적 보류(무계획 단타 차단).
# 손절·손실·목표도달·계획기간 경과 매도는 비차단. ALLOW_DAY_TRADING=True 면 거부권 비활성(결합).
THESIS_VETO_ENABLED   = True

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
MAX_ORDER_QTY        = 0              # hard share-count ceiling per order; 0 = no ceiling (size by ratio only)
PER_ORDER_BUDGET_RATIO = 0.10         # one order may spend at most this fraction of available cash (예수금)
MAX_CYCLE_BUDGET_RATIO = 0.25         # all orders in one cycle may spend at most this fraction of cash
MIN_CASH_BUFFER       = 1.10          # require cash ≥ notional × this (slippage/fee headroom) — else reject
# Conservative risk gates (리스크관리실장 — tighter than the legacy 20%/-10% limits)
CONSERVATIVE_MDD       = 0.05         # block ALL new buys if account evaluation P&L ≤ -5%
CONSERVATIVE_STOCK_RATIO = 0.15       # a single position's notional may not exceed 15% of total eval (was 20%)

# ─── Strategy presets ───────────────────────────────────────────────────────
# A "strategy" is the set of tunable trading parameters. The active preset is picked in the
# dashboard '전략' tab; runtime.py persists the choice and serves live overrides to the engine.
# Keys here MUST match module-level constant names above (runtime.get() falls back to those).
STRATEGY_TUNABLE_KEYS = [
    "PER_ORDER_BUDGET_RATIO", "PER_ORDER_BUDGET_OVERSHOOT", "MAX_CYCLE_BUDGET_RATIO", "MIN_CASH_BUFFER",
    "CONSERVATIVE_MDD", "CONSERVATIVE_STOCK_RATIO",
    "MAX_TRADES_PER_CYCLE", "MAX_ORDER_QTY",
    "ENABLE_SELL_REBALANCE", "TAKE_PROFIT_PCT", "STOP_LOSS_PCT", "TRIM_OVER_RATIO",
    "ALLOW_DAY_TRADING", "MIN_HOLDING_DAYS_FOR_SELL", "THESIS_VETO_ENABLED",
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
                                   "help": "진입 thesis 대비 계획기간 미경과·소폭이익·손절목표 미해당 매도결정을 '보유'로 보류. "
                                           "데이트레이딩 허용 시 자동 비활성. 손절·손실·목표·계획기간 경과 매도는 비차단",
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
}
# 사장 지시 2026-05-14: 보수→공격 스펙트럼 순서로 노출 (방어형 → 보수형 → 균형형 → 공격형 → 초공격형)
STRATEGY_PRESETS = {
    "defensive": {"label": "방어형 — 변동성 시장·자산 보존 우선",
        "PER_ORDER_BUDGET_RATIO": 0.03, "PER_ORDER_BUDGET_OVERSHOOT": 1.05, "MAX_CYCLE_BUDGET_RATIO": 0.10, "MIN_CASH_BUFFER": 1.20,
        "CONSERVATIVE_MDD": 0.025, "CONSERVATIVE_STOCK_RATIO": 0.07, "MAX_TRADES_PER_CYCLE": 1, "MAX_ORDER_QTY": 0,
        "ENABLE_SELL_REBALANCE": True, "TAKE_PROFIT_PCT": 6.0, "STOP_LOSS_PCT": 3.5, "TRIM_OVER_RATIO": True,
        "ALLOW_DAY_TRADING": False, "MIN_HOLDING_DAYS_FOR_SELL": 1.0, "THESIS_VETO_ENABLED": True,
        "ENABLE_CHEAP_FALLBACK": False, "ALLOW_US_STOCKS": False, "ALLOW_DERIVATIVES": False},
    "conservative": {"label": "보수형 — 작게, 손절 빠르게, 사이클 드물게",
        "PER_ORDER_BUDGET_RATIO": 0.05, "PER_ORDER_BUDGET_OVERSHOOT": 1.10, "MAX_CYCLE_BUDGET_RATIO": 0.15, "MIN_CASH_BUFFER": 1.15,
        "CONSERVATIVE_MDD": 0.04, "CONSERVATIVE_STOCK_RATIO": 0.10, "MAX_TRADES_PER_CYCLE": 1, "MAX_ORDER_QTY": 0,
        "ENABLE_SELL_REBALANCE": True, "TAKE_PROFIT_PCT": 8.0, "STOP_LOSS_PCT": 5.0, "TRIM_OVER_RATIO": True,
        "ALLOW_DAY_TRADING": False, "MIN_HOLDING_DAYS_FOR_SELL": 0.5, "THESIS_VETO_ENABLED": True,
        "ENABLE_CHEAP_FALLBACK": False, "ALLOW_US_STOCKS": False, "ALLOW_DERIVATIVES": False},
    "balanced": {"label": "균형형 — 기본값 (권장)",
        "PER_ORDER_BUDGET_RATIO": 0.10, "PER_ORDER_BUDGET_OVERSHOOT": 1.20, "MAX_CYCLE_BUDGET_RATIO": 0.25, "MIN_CASH_BUFFER": 1.10,
        "CONSERVATIVE_MDD": 0.05, "CONSERVATIVE_STOCK_RATIO": 0.15, "MAX_TRADES_PER_CYCLE": 2, "MAX_ORDER_QTY": 0,
        "ENABLE_SELL_REBALANCE": True, "TAKE_PROFIT_PCT": 12.0, "STOP_LOSS_PCT": 5.0, "TRIM_OVER_RATIO": True,
        "ALLOW_DAY_TRADING": True, "MIN_HOLDING_DAYS_FOR_SELL": 0.5, "THESIS_VETO_ENABLED": True,
        "ENABLE_CHEAP_FALLBACK": False, "ALLOW_US_STOCKS": True, "ALLOW_DERIVATIVES": False},
    "aggressive": {"label": "공격형 — 크게, 길게 보유, 사이클 잦게",
        "PER_ORDER_BUDGET_RATIO": 0.20, "PER_ORDER_BUDGET_OVERSHOOT": 1.30, "MAX_CYCLE_BUDGET_RATIO": 0.40, "MIN_CASH_BUFFER": 1.05,
        "CONSERVATIVE_MDD": 0.08, "CONSERVATIVE_STOCK_RATIO": 0.25, "MAX_TRADES_PER_CYCLE": 3, "MAX_ORDER_QTY": 0,
        "ENABLE_SELL_REBALANCE": True, "TAKE_PROFIT_PCT": 18.0, "STOP_LOSS_PCT": 10.0, "TRIM_OVER_RATIO": False,
        "ALLOW_DAY_TRADING": True, "MIN_HOLDING_DAYS_FOR_SELL": 0.0, "THESIS_VETO_ENABLED": True,
        "ENABLE_CHEAP_FALLBACK": False, "ALLOW_US_STOCKS": True, "ALLOW_DERIVATIVES": False},
    "ultra_aggressive": {"label": "초공격형 — 최대 베팅·고위험 고리워드",
        "PER_ORDER_BUDGET_RATIO": 0.35, "PER_ORDER_BUDGET_OVERSHOOT": 1.50, "MAX_CYCLE_BUDGET_RATIO": 0.70, "MIN_CASH_BUFFER": 1.02,
        "CONSERVATIVE_MDD": 0.15, "CONSERVATIVE_STOCK_RATIO": 0.40, "MAX_TRADES_PER_CYCLE": 5, "MAX_ORDER_QTY": 0,
        "ENABLE_SELL_REBALANCE": True, "TAKE_PROFIT_PCT": 30.0, "STOP_LOSS_PCT": 15.0, "TRIM_OVER_RATIO": False,
        "ALLOW_DAY_TRADING": True, "MIN_HOLDING_DAYS_FOR_SELL": 0.0, "THESIS_VETO_ENABLED": True,
        "ENABLE_CHEAP_FALLBACK": False, "ALLOW_US_STOCKS": True, "ALLOW_DERIVATIVES": False},
}
DEFAULT_STRATEGY = "balanced"

# ─── Server ──────────────────────────────────────────────────────────────────
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("APP_PORT", "8500"))
