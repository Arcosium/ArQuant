"""
Arquant v1.0 - Continuous Market Surveillance & Trading Orchestrator
Watchlist: 10 global indices (KOSPI, KOSDAQ, S&P500, NASDAQ, etc.)
When strategist returns target stocks → 3yr daily + supply crawl + minute chart
"""
import json, re, asyncio, logging, time, difflib, subprocess, types
from enum import Enum
from typing import Optional, Dict, List, Any
from datetime import datetime, timezone, timedelta

from agents.base_agent import BaseAgent
from agents.specialists import (create_macro_analyst, create_quant_analyst, create_news_analyst,
                                create_trader, create_post_manager, create_ops_support)
from agents.guardrails import create_risk_guard, validate_order_draft
from infra.kis_broker import OrderDraft
from infra import cycle_store, news_classifier_log, notifier, metrics, admin_config
from infra.error_log import record_error
from tools.news_monitor import get_monitor
from tools.dart_disclosure import (search_disclosures, get_financial_summary_by_stock_code,
                                    DART_STATE_QUERY_FAILED, DART_STATE_NO_DISCLOSURE)
# 사장 피드백 2026-05-15 (8차): Tavily → alibaba/tongyi-deepresearch로 전환
from tools.global_search import deep_research
from tools.market_data import (
    crawl_index_snapshot, crawl_company_full, format_quant_data_for_agent, INDEX_WATCHLIST,
    get_index_data, format_indices_for_macro, get_stock_name, fetch_investor_data, _csv_row_count,
    resolve_kr_stock_code, get_usdkrw
)
from config import (HEADLINE_DEDUP_RATIO,
                    MACRO_CACHE_TTL_SEC, DART_CACHE_TTL_SEC, LIVE_TRADING, PERIODIC_CYCLE_SEC,
                    NEWS_PREFILTER_TRIGGER, NEWS_PREFILTER_LIMIT)
import runtime  # live strategy overrides — runtime.get("KEY") → override or config default

logger = logging.getLogger("ARQUANT")
KST = timezone(timedelta(hours=9))

SCHEDULE = {
    "kr_pre_market":   {"start":(8,50),"end":(9,0),"desc":"장 시작 전 매크로"},
    "kr_trading":      {"start":(9,0),"end":(15,30),"desc":"KRX 장중"},
    "kr_close_review": {"start":(15,35),"end":(15,50),"desc":"장 마감 리뷰"},
    "us_trading":      {"start":(22,30),"end":(5,0),"desc":"US 장중 (야간)"},
}
NEWS_CHECK_INTERVAL = 900     # 뉴스 크롤링 주기 15분 (사장 피드백 2026-05-16)
# 사장 피드백 2026-05-16: 뉴스 크롤링·분류는 유저별이 아니라 **단일 스왐 프로세스에서
# 한 번만** 수행된다(get_monitor()는 프로세스 전역 싱글턴, 활성 계정의 OpenRouter 키로 분류).
# 결과는 data/news_history.json 에 영속되고 /api/news 가 전체 유저에게 동일하게 제공 →
# 모든 유저가 같은 뉴스를 공유 (개별 크롤링 없음).
# 환율 폴백 — 5분 크롤 라이브 환율(get_usdkrw)이 비어 있을 때만 쓰는 기본값 (사장 지시 2026-05-22).
USDKRW_FALLBACK = 1510.0
# 최저가 KR 종목 1주(현실적 최저가 ~5,000원)도 못 살 예수금이면 분석 비용만 들므로 사이클 스킵.
MIN_TRADABLE_CASH_KRW = 5000


def _low_cash_skip_reason(snap) -> Optional[str]:
    """'예수금 부족' 사이클 스킵 사유 — *신뢰 가능한*(ok=True) 잔고 스냅샷에만 적용한다.
    조회 실패(ok=False)는 '돈이 없음'이 아니라 '데이터를 모름'이므로 스킵하지 않는다
    (사장 제보 2026-05-29 — 모의계좌 잔고 일시 실패로 사이클이 통째로 안 도는 버그). None=진행."""
    bp = (snap or {}).get("buying_power") or {}
    if not snap or not snap.get("ok") or not bp.get("ok"):
        return None
    cash = float(bp.get("cash", 0.0) or 0.0)
    if cash < MIN_TRADABLE_CASH_KRW:
        return f"가용 예수금 부족 ({cash:,.0f}원 < {MIN_TRADABLE_CASH_KRW:,}원) — 분석 비용만 듦, 스킵"
    return None


def _is_kr_code(code: Any) -> bool:
    """국내(KR) 종목코드 판정 — 6자리 숫자면 KR, 아니면 US(티커). 이 한 함수로 KR/US 분기를
    통일해 'kr_* 만 부르고 us_* 빠뜨리는' 비대칭 버그의 표면을 좁힌다."""
    s = str(code or "").strip()
    return s.isdigit() and len(s) == 6


def _is_us_code(code: Any) -> bool:
    """해외(US) 종목 판정 = KR 코드가 아닌 것(티커)."""
    return not _is_kr_code(code)


# ─── Cost-reduction helpers ────────────────────────────────────────────────
def _norm_title(title: str) -> str:
    return re.sub(r"[\s\W_]+", "", (title or "")).lower()

def _is_dup_title(title: str, existing_titles: List[str]) -> bool:
    n = _norm_title(title)
    if not n:
        return True
    for e in existing_titles:
        en = _norm_title(e)
        if not en:
            continue
        if n == en or difflib.SequenceMatcher(None, n, en).ratio() >= HEADLINE_DEDUP_RATIO:
            return True
    return False

# Simple TTL caches for slow-moving inputs (macro analysis text, DART disclosures, deep-research)
_macro_cache = {"ts": 0.0, "value": ""}
_dart_cache = {"ts": 0.0, "value": ""}
# 사장 피드백 2026-05-15 (8차): 세션별 alibaba 매크로 리서치 결과 30분 캐시 (Tavily 캐시 대체).
_research_cache: Dict[str, Dict] = {}  # session → {ts, value}

def _now_kst(): return datetime.now(KST)

# 사장 지시 2026-05-14: 한국 주식시장 휴장일 (음력 공휴일은 매년 추가 필요)
# 2025-2027 주요 휴장일 (KOSPI/KOSDAQ). 음력 공휴일은 매년 1월 ops_support 자동 갱신 후보.
KR_MARKET_HOLIDAYS = {
    # 2026 — 가장 가까운 해
    "2026-01-01",  # 신정
    "2026-02-16", "2026-02-17", "2026-02-18",  # 설 연휴(추정)
    "2026-03-01", "2026-03-02",  # 삼일절 + 대체
    "2026-05-01",  # 근로자의 날
    "2026-05-05",  # 어린이날
    "2026-05-25",  # 부처님오신날(추정)
    "2026-06-06",  # 현충일(토)
    "2026-08-17",  # 광복절 대체
    "2026-09-24", "2026-09-25",  # 추석(추정)
    "2026-10-03",  # 개천절(토)
    "2026-10-05",  # 한글날 대체(추정)
    "2026-12-25",  # 크리스마스
    "2026-12-31",  # 연말 휴장
    # 2027 — 미리 등록 (정확한 날짜는 ops_support가 매년 갱신)
    "2027-01-01",
    "2027-03-01", "2027-05-01", "2027-05-05", "2027-06-07",
    "2027-08-15", "2027-10-03", "2027-10-09", "2027-12-25", "2027-12-31",
}

def is_kr_weekend(d: Optional[datetime] = None) -> bool:
    """KST 기준 주말(토/일)이면 True. 주말은 KIS 호출 없이 자명하게 휴장 처리."""
    d = d or _now_kst()
    return d.weekday() >= 5  # 토(5)·일(6)

def is_kr_holiday(d: Optional[datetime] = None) -> bool:
    """오늘이 한국 휴장일이면 True. 주말은 별도 체크가 아니라 SCHEDULE에서 09:00-15:30이 평일이라
    가정함 — 그러나 휴장일+평일 케이스(공휴일)는 여기서 차단해야 사이클이 안 돈다.
    주: 이 하드코딩 목록은 이제 '폴백'이다 — 개장 5분 후 KIS 실시세 확인(_verify_market_open)이
    1순위 권위이고, 실확인이 불가할 때만 이 목록을 쓴다 (사장 지시 2026-05-24)."""
    d = d or _now_kst()
    if is_kr_weekend(d):
        return True
    return d.strftime("%Y-%m-%d") in KR_MARKET_HOLIDAYS

def is_us_weekend(d: Optional[datetime] = None) -> bool:
    """미 동부 기준 주말(토/일)을 KST 시각으로 환산해 판정. 주말은 KIS 호출 없이 자명 휴장.
    US 정규장은 KST 22:30~05:00 (미 동부 09:30~16:00)."""
    d = d or _now_kst()
    if d.hour < 5 and d.weekday() in (6, 0):  # KST 일/월 새벽 = 미 동부 토/일
        return True
    if d.hour >= 22 and d.weekday() == 4:  # KST 금 밤 = 미 동부 금 → 거래일(주말 아님)
        return False
    if d.hour >= 22 and d.weekday() in (5, 6):  # KST 토/일 밤 = 미 동부 토/일 → 주말
        return True
    return False

def is_us_holiday(d: Optional[datetime] = None) -> bool:
    """미국 시장 휴장일. NYSE 기준 고정 휴일만 — 음력·이동식은 ops_support가 추후 보완 가능.
    KST 기준 22:30~05:00이 US 정규장이므로 KST의 토/일 야간엔 휴장 (= 미 동부 금/토 낮).
    주: 고정 휴일 목록은 '폴백' — 개장 5분 후 KIS 실시세 확인이 1순위 (사장 지시 2026-05-24)."""
    d = d or _now_kst()
    # US 정규장은 KST 22:30~05:00. 미 동부 시간으로는 09:30~16:00.
    # KST 토 새벽 02:00 = 미 동부 금 12:00 (US 장중). KST 일 새벽 = 미 동부 토 = 휴장.
    if d.hour >= 22 and d.weekday() == 4:  # KST 금 밤 = 미 동부 금 → 정상 (마지막 거래일)
        return False
    if is_us_weekend(d):
        return True
    # 미 동부 고정 휴장일 (KST로 환산하면 +14h)
    us_fixed = {"2026-01-01","2026-01-19","2026-02-16","2026-04-03","2026-05-25","2026-06-19",
                "2026-07-03","2026-09-07","2026-11-26","2026-12-25",
                "2027-01-01","2027-01-18","2027-02-15","2027-03-26","2027-05-31","2027-06-18",
                "2027-07-05","2027-09-06","2027-11-25","2027-12-24"}
    # 미 동부 날짜 ≈ KST 날짜 - 14h. 자정 전후 보정.
    from datetime import timedelta as _td
    us_d = (d - _td(hours=14)).strftime("%Y-%m-%d")
    return us_d in us_fixed

def _in_schedule(name):
    h,m = _now_kst().hour, _now_kst().minute; t = h*60+m
    s = SCHEDULE[name]; st = s["start"][0]*60+s["start"][1]; en = s["end"][0]*60+s["end"][1]
    return (st<=t<en) if st<=en else (t>=st or t<en)
def is_trading_hours(): return _in_schedule("kr_trading") or _in_schedule("us_trading")
def is_pre_market(): return _in_schedule("kr_pre_market")
def get_current_session():
    if _in_schedule("kr_pre_market"): return "KR_PRE_MARKET"
    if _in_schedule("kr_trading"): return "KR_TRADING"
    if _in_schedule("kr_close_review"): return "KR_CLOSE_REVIEW"
    if _in_schedule("us_trading"): return "US_TRADING"
    return "OFF_HOURS"

def _post_manager_session_hint(session: str) -> str:
    """사후관리실장(매도 판단) 프롬프트에 넣을 '현재 어느 장이 열려 있는가' 안내문.
    버그 2026-05-23 04:47: 세션 정보가 없어 LLM이 'KR 장중이라 미국 시장 닫힘'으로 환각 →
    US_TRADING 인데도 매도 신호를 '매매 불가'로 무시하고 보유. 입력 보유 종목은 이미 현재
    세션에서 거래 가능한 것만 필터링돼 들어오므로, 여기서 '지금 매도 가능'을 사실로 못박는다."""
    if session == "US_TRADING":
        return ("⚠️ 현재 세션은 **미국 정규장(US_TRADING)** — 지금 열려 있는 시장은 미국이고 한국 장은 마감입니다. "
                "아래 보유 종목은 전부 미국 종목이며 **지금 즉시 매도 가능**합니다. "
                "'미국 시장이 닫혀 매매 불가' 같은 판단은 사실과 다르니 절대 쓰지 말고, 세션을 임의로 추측하지 마십시오.")
    if session in ("KR_TRADING", "KR_PRE_MARKET", "KR_CLOSE_REVIEW"):
        return ("⚠️ 현재 세션은 **한국 장(KR)** — 지금 열려 있는 시장은 한국이고 미국 장은 마감입니다. "
                "아래 보유 종목은 전부 국내 종목이며 **지금 즉시 매도 가능**합니다. "
                "'한국 시장이 닫혀 매매 불가' 같은 판단은 사실과 다르니 절대 쓰지 말고, 세션을 임의로 추측하지 마십시오.")
    return ("현재 장외 — 아래 보유 종목은 다음 개장 사이클에서 매매됩니다. "
            "매도/보유는 오직 매크로·퀀트·뉴스·손익 신호로만 판단하십시오.")

class SwarmState(str, Enum):
    IDLE="IDLE";MONITORING="MONITORING";MACRO_ANALYSIS="MACRO_ANALYSIS"
    QUANT_ANALYSIS="QUANT_ANALYSIS";NEWS_ANALYSIS="NEWS_ANALYSIS"
    ORDER_DRAFTING="ORDER_DRAFTING";RISK_VALIDATION="RISK_VALIDATION"
    EXECUTION="EXECUTION";REPORT="REPORT";COOLDOWN="COOLDOWN"
    ERROR="ERROR";STOPPED="STOPPED";OFF_HOURS="OFF_HOURS";DATA_COLLECTION="DATA_COLLECTION"

class SwarmCycleLog:
    def __init__(self):
        self.started_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"); self.state_logs: List[Dict] = []; self.final_report = ""
    def log(self, state, agent, message):
        self.state_logs.append({"timestamp":datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),"state":state,"agent":agent,"message":(message or "")[:20000]})
    def to_dict(self): return {"started_at":self.started_at,"logs":self.state_logs,"final_report":self.final_report}

# ─── Real-time event/response log (data/<uid>/trade_log.json) ───────────────
# Accumulates *forever* (per user request) until the dashboard "초기화" button calls
# clear_event_log(uid). Soft-capped so the file can't grow without bound.
#
# Phase 2 멀티테넌트: 과거엔 전역 claude_response.json 하나에 모든 유저의 에이전트/이벤트가
# 뒤섞여, 동시 운용되는 두 계정의 대시보드가 서로의 에이전트를 봤다(운용전략실장 등 페르소나
# 중복 표시). equity 와 동일하게 유저별 파일(data/<uid>/trade_log.json — user_paths.trade_log_path)
# 로 분리한다. log_response_event/get_recent_events/get_trade_history/clear_* 가 uid 인자를 받는다.
# uid 가 None 이면 파일 기록을 건너뛴다(WS 로만 전달 — 부팅 IDLE 등 전역 시스템 이벤트).
from pathlib import Path as _Path
_RESPONSE_LOG_CAP = 4000  # keep at most this many entries on disk
_DISPLAY_EVENT_TYPES = {"status","news","trigger","agent_msg","cycle_complete",
                        "execution_ready","execution_skipped","trade_executed","trade_failed","error",
                        # 사장 지시 2026-05-21: 체결 신청·장 마감 이벤트 (모바일 알림 4종 중 2종)
                        "order_submitted","market_close"}

def _response_log_path(uid):
    """그 유저의 이벤트/응답 로그 파일 경로. uid 가 None 이면 None(파일 미사용)."""
    if uid is None:
        return None
    from infra import user_paths
    return user_paths.trade_log_path(int(uid))

def _read_response_log(uid=None) -> list:
    p = _response_log_path(uid)
    if p is None:
        return []
    try:
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            return d if isinstance(d, list) else []
    except Exception:
        pass
    return []

def _now_kst_iso() -> str:
    """KST 연월일시분초 timestamp (e.g. '2026-05-13 17:13:57'). Used by every log/trade entry
    so the dashboard can render KST without guessing the server's tz (the OCI box runs UTC).
    Note: legacy callers may pass these to datetime.fromisoformat — '%Y-%m-%d %H:%M:%S' is still
    parseable by fromisoformat (Python 3.11+ accepts space separator)."""
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


def log_response_event(entry: dict, uid=None):
    """Append an event/response record to that uid's log (full text, no truncation).
    Phase 2 멀티테넌트: uid 가 주어지면 data/<uid>/trade_log.json 에 적는다. uid 가 None 이면
    파일 기록을 건너뛴다(이벤트는 _broadcast 의 WS 콜백으로 여전히 전달된다)."""
    p = _response_log_path(uid)
    if p is None:
        return  # 전역 시스템 이벤트(uid 없음) — 파일에 남기지 않는다(WS 로만).
    try:
        data = _read_response_log(uid)
        data.append({"ts": _now_kst_iso(), **entry})
        if len(data) > _RESPONSE_LOG_CAP:
            data = data[-_RESPONSE_LOG_CAP:]
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as _e:
        logger.warning(f"이벤트 로그 기록 실패(uid={uid}): {_e}")

def get_recent_events(limit: int = 500, uid=None) -> list:
    """UI replay: the display-relevant events (newest-last), so a page reload restores
    the trade/agent log without re-running anything. uid 별 로그만 반환한다."""
    evs = [e for e in _read_response_log(uid)
           if e.get("source") == "system_event" and e.get("type") in _DISPLAY_EVENT_TYPES]
    return evs[-max(1, int(limit)):]

# ─── Equity curve (for the 수익률 tab) ──────────────────────────────────────
# Phase 2 멀티테넌트: 전역 equity_curve.json 폐지. equity 는 유저별 data/<uid>/equity_curve.json
# 에 기록·조회되며, record_equity/get_equity_series/performance_kpis 가 equity_path 인자를 받는다.

# 사장 지시 2026-05-24: 거래 내역을 '비우기' 해도 승률·매도수·보유일 통계가 사라지지 않도록,
# 비우기 직전에 누적 실현손익 통계를 이 파일에 적립한다. performance_kpis 가 라이브 거래 통계에
# 이 베이스라인을 더해 표시하므로, 화면의 거래 리스트를 비워도 승률이 유지된다.
_PERF_BASELINE = _Path(__file__).parent / "data" / "perf_baseline.json"

def _read_perf_baseline() -> dict:
    try:
        if _PERF_BASELINE.exists():
            d = json.loads(_PERF_BASELINE.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
    except Exception:
        pass
    return {}

def _write_perf_baseline(d: dict):
    try:
        _PERF_BASELINE.parent.mkdir(parents=True, exist_ok=True)
        _PERF_BASELINE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as _e:
        logger.warning(f"perf_baseline 기록 실패: {_e}")

def _parse_ts_any(ts_str) -> Optional[datetime]:
    """Parse a stored ts that may be ISO ('2026-05-13T17:13:57.123+09:00') or
    new-format ('2026-05-13 17:13:57'). Returns naive local datetime (KST) or None."""
    s = (ts_str or "").strip()
    if not s:
        return None
    try:
        if "T" in s or "+" in s:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except Exception:
        try:
            return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
        except Exception:
            return None

def _detect_external_flow(prev: Optional[Dict], curr: Dict) -> float:
    """입출금(외부 현금흐름) 자동 감지 — 사장 지시 2026-05-21 비활성화.

    KIS 총평가/예수금이 폴 간 일시적으로 크게 흔들릴 때(해외종목 시세 일시 0/누락 추정)
    이를 '입금'으로 오탐해 누적 보정값을 오염시켰다: 2026-05-20 15:44 +3,263,616원 유령
    입금이 누적되어 자산곡선이 실제 ~3.76M 대신 ~415K로 표시됨(사장 보고). 금액 임계값으로는
    진짜 입금과 이 변동을 구분할 수 없어, 신뢰 불가한 자동 감지를 끈다.
    이제 adj_total_eval == total_eval (실계좌 총평가) 라 그래프가 실제 자산을 그대로 표시한다."""
    return 0.0

def record_equity(equity_path, bp: dict, source: str = "poll", holdings: Optional[List[Dict]] = None,
                  kospi: Optional[float] = None, nasdaq: Optional[float] = None):
    """Append a {ts,total_eval,cash,pnl_ratio,holdings,external_flow_cum,kospi,nasdaq} point — at most one per 60s.
    Caps at 2000 points. `holdings` (optional list of {code,qty}) is used to detect external cashflow
    (deposits/withdrawals) vs trade-driven changes — see _detect_external_flow().
    `kospi`/`nasdaq` (사장 지시 2026-05-21): 5분 폴링이 잔고와 함께 수집한 지수 현재값. equity 포인트와
    동일 타임스탬프에 저장돼, 벤치마크 오버레이가 일중 움직임을 보여준다(미검증 분봉 API 불필요).

    Phase 2 멀티테넌트: `equity_path`(유저별 data/<uid>/equity_curve.json)를 명시적으로 받아
    해당 유저 곡선에만 기록한다. 전역 _EQUITY_LOG 는 더 이상 쓰지 않는다."""
    equity_path = _Path(equity_path)
    if holdings is not None:
        try:
            bp = {**bp, "_holdings": holdings}
        except Exception: pass
    try:
        if not bp or not bp.get("ok"):
            return
        data = []
        if equity_path.exists():
            try: data = json.loads(equity_path.read_text(encoding="utf-8"))
            except Exception: data = []
        if not isinstance(data, list): data = []
        # 사장 보고 2026-05-21: ts 를 KST 로 저장해야 차트 가로축이 KST 로 표시된다.
        # (OCI 서버는 UTC 로 동작 → datetime.now() 는 UTC naive. _ts_to_kst 는 공백포맷을
        #  '이미 KST'로 간주하므로, 여기서 UTC 로 저장하면 9시간 어긋난 라벨이 나왔다.)
        now = datetime.now(KST)
        if data:
            try:
                last = _parse_ts_any(data[-1]["ts"])
                if last:
                    last_aware = last if last.tzinfo else last.replace(tzinfo=KST)
                    if (now - last_aware).total_seconds() < 60:
                        return
            except Exception: pass
        # holdings snapshot {code: qty} — used to distinguish external cashflow from trade-driven changes
        try:
            holdings_snap = {str(h.get("code","")).strip(): int(h.get("qty") or 0)
                             for h in (bp.get("_holdings") or []) if h.get("code")}
        except Exception:
            holdings_snap = {}
        entry = {"ts": now.strftime("%Y-%m-%d %H:%M:%S"),
                 "total_eval": float(bp.get("total_eval") or 0.0),
                 "cash": float(bp.get("cash") or 0.0),
                 "pnl_ratio": float(bp.get("pnl_ratio") or 0.0),
                 "src": source}
        if holdings_snap:
            entry["holdings"] = holdings_snap
        # 사장 지시 2026-05-21: 잔고와 함께 수집한 지수 현재값을 같이 저장 (벤치마크 일중 오버레이)
        try:
            if kospi and float(kospi) > 0:  entry["kospi"]  = float(kospi)
            if nasdaq and float(nasdaq) > 0: entry["nasdaq"] = float(nasdaq)
        except (TypeError, ValueError):
            pass
        # Detect external cashflow (deposits/withdrawals) — total_eval delta unexplained by holdings change.
        # Carry forward cumulative external flow so charts can show a "trade-only" equity curve.
        prev_flow = 0.0
        if data:
            try: prev_flow = float(data[-1].get("external_flow_cum", 0.0) or 0.0)
            except Exception: prev_flow = 0.0
        entry["external_flow_cum"] = prev_flow
        flow_delta = _detect_external_flow(data[-1] if data else None, entry)
        if flow_delta:
            entry["external_flow_cum"] = prev_flow + flow_delta
            entry["external_flow"] = flow_delta
            logger.info(f"[equity] 외부 현금흐름 감지: {flow_delta:+,.0f}원 (입출금) — 수익률 베이스라인 보정")
        data.append(entry)
        equity_path.parent.mkdir(parents=True, exist_ok=True)
        equity_path.write_text(json.dumps(data[-2000:], ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as _eq_e:
        # 동작 보존(여전히 raise 안 함) + 조용한 실패 표면화: equity 영속 실패는
        # 대시보드 P&L 을 말없이 고착시킨다.
        metrics.incr("equity_record_error")
        notifier.alert("WARN", "equity 기록 실패", str(_eq_e),
                       dedup_key="equity_record_error")

def _ts_to_kst(ts_str: str) -> Optional[datetime]:
    """Parse an ISO ('2026-05-13T17:13:57+09:00') or new-format ('2026-05-13 17:13:57') timestamp.
    Legacy ISO entries without tz are treated as UTC (the OCI server runs UTC, old format was naive UTC).
    New-format entries (space separator) are already KST (we strftime in KST). Returns KST-aware datetime."""
    s = (ts_str or "").strip()
    if not s:
        return None
    try:
        if "T" in s or "+" in s or "Z" in s:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(KST)
        dt = datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=KST)
    except Exception:
        return None

def is_market_session_now(dt: Optional[datetime] = None) -> bool:
    """지금(또는 dt)이 '실제 정규장 세션'인지 — 시간대 + 요일 + 휴장일까지 본다.
    is_trading_hours()/get_current_session()은 시간대만 보므로 주말 밤 US 시간대
    (토 22:30~일 05:00 KST = 실제 미국 토요일·휴장)를 장중으로 오인한다. 평가금액 기록·차트 표시는
    이 함수로 게이팅해 장외/주말/휴장 포인트가 쌓이거나 표시되지 않게 한다 (사장 지시 2026-05-24).
    KR 정규장 09:00-15:30(평일·비휴장) 또는 US 정규장 22:30-05:00(미 거래일)이면 True."""
    dt = dt or _now_kst()
    t = dt.hour * 60 + dt.minute
    if 9 * 60 <= t < 15 * 60 + 30:        # KR 정규장
        return not is_kr_holiday(dt)
    if t >= 22 * 60 + 30 or t < 5 * 60:   # US 정규장 (야간 wrap)
        return not is_us_holiday(dt)
    return False

def _equity_points(raw_equity, *, glitch_pct: float = 0.10):
    """정렬된 [(dt, adj_total, point)] — 입출금 보정(adj) 적용 + 결제 글리치 carry-forward.

    사장 지시 2026-05-22: 보유 종목 변동이 없는데 총평가가 비정상 급변(>glitch_pct)하면 KIS
    결제 과도기 글리치로 보고 직전 값을 유지(carry-forward)해, 누적수익·MDD·그래프에 가짜
    스파이크가 끼지 않게 한다. (보유가 실제로 바뀐 시점은 정상 변동이라 그대로 둔다.)"""
    enriched = []
    for p in (raw_equity or []):
        if not isinstance(p, dict) or not p.get("total_eval"):
            continue
        dt = _ts_to_kst(p.get("ts", ""))
        if not dt:
            continue
        try:
            ext = float(p.get("external_flow_cum", 0.0) or 0.0)
        except Exception:
            ext = 0.0
        try:
            adj = float(p["total_eval"]) - ext
        except Exception:
            continue
        enriched.append((dt, adj, p))
    enriched.sort(key=lambda x: x[0])
    out = []
    prev_total = None
    prev_hold = None
    n = len(enriched)
    for i, (dt, adj, p) in enumerate(enriched):
        hold = p.get("holdings")
        use = adj
        # 사장 지시 2026-05-24 (강화): 결제 글리치(전산오류) 배제 강화.
        # 실제 거래로 보유 수량이 '명시적으로' 바뀐 경우는 정상 변동이라 그대로 둔다.
        holdings_changed = (prev_hold is not None and hold is not None and hold != prev_hold)
        if (prev_total is not None and prev_total > 0
                and not holdings_changed
                and abs(adj - prev_total) / prev_total > glitch_pct):
            # (a) 보유 수량이 직전과 '동일함이 확인'되면 결제 글리치로 보고 즉시 직전값 유지(기존 동작).
            holds_same_known = (prev_hold is not None and hold is not None and hold == prev_hold)
            # (b) 보유 스냅샷이 미상(holdings 필드 없음)이면, 다음 포인트가 직전값으로 되돌아오는
            #     '일시 스파이크'일 때만 글리치로 본다 — 며칠에 걸친 실제 손익(되돌림 없음)은 보존.
            reverts = (i + 1 < n and abs(enriched[i + 1][1] - prev_total) / prev_total <= glitch_pct)
            if holds_same_known or reverts:
                use = prev_total  # 글리치 의심 — 직전 값 유지
        out.append((dt, use, {**p, "adj_total_eval": use}))
        prev_total = use
        if hold is not None:
            prev_hold = hold
    return out


def get_equity_series(equity_path, limit: int = 500, view: str = "realtime") -> list:
    """Return equity-curve points for the dashboard chart.
    - view='realtime' : KR/US 거래시간 포인트만, 5분 버킷의 마지막 값으로 다운샘플 (라벨은 'MM-DD HH:MM' KST)
    - view='daily'    : KST 일자별 마지막 포인트 (라벨 'YYYY-MM-DD')
    - view='monthly'  : KST 월별 마지막 포인트 (라벨 'YYYY-MM')
    `limit`은 최종 반환 포인트 수의 상한.
    Each point's `total_eval` is the RAW account value; `adj_total_eval` is the
    deposit/withdrawal-adjusted value (subtracts cumulative external cashflow so the
    chart shows trade-driven P&L only — per 사장 지시 2026-05-14).
    Phase 2 멀티테넌트: `equity_path` 로 요청 유저의 곡선을 읽는다."""
    equity_path = _Path(equity_path)
    try:
        if not equity_path.exists():
            return []
        raw = json.loads(equity_path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            return []
    except Exception:
        return []
    # 사장 지시 2026-05-22: 결제 글리치 carry-forward + 누적수익(cum_pnl, 0원 시작) 부여.
    cleaned = _equity_points(raw)
    if not cleaned:
        return []
    _baseline = cleaned[0][1]
    enriched = []
    for dt, adj, p in cleaned:
        try:
            p = {**p, "cum_pnl": adj - _baseline}
        except Exception:
            pass
        enriched.append((dt, p))
    if not enriched:
        return []
    out = []
    if view == "daily":
        bucket: Dict[str, tuple] = {}
        for dt, p in enriched:
            bucket[dt.strftime("%Y-%m-%d")] = (dt, p)
        for k in sorted(bucket):
            dt, p = bucket[k]
            out.append({**p, "label": k, "ts_kst": dt.strftime("%Y-%m-%d %H:%M")})
    elif view == "monthly":
        bucket: Dict[str, tuple] = {}
        for dt, p in enriched:
            bucket[dt.strftime("%Y-%m")] = (dt, p)
        for k in sorted(bucket):
            dt, p = bucket[k]
            out.append({**p, "label": k, "ts_kst": dt.strftime("%Y-%m-%d %H:%M")})
    else:  # realtime
        bucket: Dict[str, tuple] = {}
        for dt, p in enriched:
            if not is_market_session_now(dt):
                continue
            # 사장 지시 2026-05-21: 5분 체결 확인 폴링 주기에 맞춰 5분 버킷으로 다운샘플
            key = dt.strftime("%Y-%m-%d %H:") + f"{(dt.minute // 5) * 5:02d}"
            bucket[key] = (dt, p)
        for k in sorted(bucket):
            dt, p = bucket[k]
            label = dt.strftime("%m-%d %H:") + f"{(dt.minute // 5) * 5:02d}"
            out.append({**p, "label": label, "ts_kst": dt.strftime("%Y-%m-%d %H:%M")})
    return out[-max(1, int(limit)):]


def _trade_realized_stats(trades: Optional[list]) -> dict:
    """매도 거래의 FIFO/KIS 실현손익으로 승률·매도수·보유일 합계를 집계한다.
    performance_kpis(표시) 와 clear_trade_log(비우기 시 베이스라인 적립) 가 공유한다."""
    wins = total = 0
    hold_sum = 0.0; hold_n = 0
    for e in (trades or []):
        if str(e.get("side") or "").lower() != "sell":
            continue
        det = e.get("detail") or {}
        pnl = det.get("realized_pnl")
        if pnl is None:
            pnl = det.get("total_pnl")
        if pnl is None:
            continue
        total += 1
        if pnl > 0:
            wins += 1
        sell_dt = _ts_to_kst(e.get("ts", ""))
        for m in (det.get("matched") or []):
            bdt = _ts_to_kst(m.get("buy_ts", ""))
            if sell_dt and bdt:
                hold_sum += max(0.0, (sell_dt - bdt).total_seconds() / 86400.0); hold_n += 1
    return {"sell_count": total, "win_count": wins, "hold_days_sum": hold_sum, "hold_days_n": hold_n}


# 사장 지시 2026-05-27: 해외(US) 주식 거래비용 — 매수·매도 각 leg 0.3%. 국내(KR)는 미적용.
US_TRADE_COST_RATE = 0.003


def _net_realized_pnl(buy_price, sell_price, qty, is_us: bool):
    """체결 실현손익(거래비용 반영). 국내 0%, 해외 매수·매도 각 0.3%.
    net = (매도가-매수가)×수량 − (해외면) 0.003×(매수가+매도가)×수량.
    값이 비어있으면(None/0) None 반환(미상)."""
    if not (buy_price and sell_price and qty):
        return None
    gross = (sell_price - buy_price) * qty
    if not is_us:
        return gross
    cost = US_TRADE_COST_RATE * (buy_price + sell_price) * qty
    return gross - cost


def _realized_perf_buckets(trades: Optional[list], now: datetime, fx: Optional[float] = None) -> dict:
    """체결된 매도의 (비용반영) 실현손익을 누적/오늘/주/월로 집계 — 결정론적, 잔고스냅샷 비의존.
    각 매도 detail.realized_pnl(_enrich_trade_history 가 비용반영해 기록) 과 매입원금(cost_basis×qty)을
    버킷별로 합산해, 잔고 글리치에 흔들리지 않는 수익률(원/%)을 만든다 (사장 지시 2026-05-27).
    %는 pnl / 매입원금합 × 100. realized_pnl 이 없으면 total_pnl 폴백, 둘 다 없으면 건너뛴다.

    사장 지시 2026-05-30: 해외(USD) 실현손익·매입원금은 원/달러를 그대로 더하면 무의미하므로
    환율(fx)로 원화 환산해 합산한다. fx 미지정 시 5분 크롤 라이브 환율(get_usdkrw)로 채운다.
    detail.currency 가 'USD' 인 항목만 환산하고, 없으면(레거시/국내) 원화로 간주한다."""
    if fx is None:
        try:
            fx = get_usdkrw(USDKRW_FALLBACK)
        except Exception:
            fx = USDKRW_FALLBACK
    today0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week0 = today0 - timedelta(days=today0.weekday())
    month0 = today0.replace(day=1)
    # key → [pnl_sum, basis_sum]
    acc = {"cum": [0.0, 0.0], "today": [0.0, 0.0], "week": [0.0, 0.0], "month": [0.0, 0.0]}
    has = False
    for e in (trades or []):
        if str(e.get("side") or "").lower() != "sell":
            continue
        det = e.get("detail") or {}
        pnl = det.get("realized_pnl")
        if pnl is None:
            pnl = det.get("total_pnl")
        if pnl is None:
            continue
        try:
            qty = float(det.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0.0
        try:
            basis_per = float(det.get("cost_basis") or 0)
        except (TypeError, ValueError):
            basis_per = 0.0
        basis = basis_per * qty
        if basis <= 0:  # cost_basis 없으면 FIFO 매칭 매수금액으로 매입원금 추정
            basis = sum((m.get("buy_price") or 0) * (m.get("buy_qty") or 0)
                        for m in (det.get("matched") or []))
        # 사장 지시 2026-05-30: 해외(USD) 손익·원금을 원화로 환산 — KRW/USD 혼합 합산 방지.
        if str(det.get("currency") or "").upper() == "USD":
            rate = fx or USDKRW_FALLBACK
            pnl = pnl * rate
            basis = basis * rate
        sdt = _ts_to_kst(e.get("ts", ""))
        has = True
        acc["cum"][0] += pnl; acc["cum"][1] += basis
        for key, boundary in (("today", today0), ("week", week0), ("month", month0)):
            if sdt and sdt >= boundary:
                acc[key][0] += pnl; acc[key][1] += basis

    def _pct(pnl, basis):
        return (pnl / basis * 100.0) if basis else 0.0
    return {
        "cumulative_pnl": acc["cum"][0], "cumulative_pct": _pct(*acc["cum"]),
        "today_pnl": acc["today"][0], "today_pct": _pct(*acc["today"]),
        "week_pnl": acc["week"][0], "week_pct": _pct(*acc["week"]),
        "month_pnl": acc["month"][0], "month_pct": _pct(*acc["month"]),
        "_realized_has": has,
    }


def performance_kpis(equity_path=None, raw_equity: Optional[list] = None,
                     trades: Optional[list] = None, now: Optional[datetime] = None,
                     uid=None) -> dict:
    """수익률 탭 KPI 카드용 요약 (사장 지시 2026-05-21).

    equity_curve 의 입출금 보정값(adj total) 기준으로 누적·오늘·이번주·이번달 수익(원/%)
    과 최대낙폭(MDD)을, 거래 FIFO 실현손익 기준으로 승률·평균 보유일을 계산한다.
    `raw_equity`/`trades`/`now` 를 주입하면 디스크 없이 순수 계산 — 단위 테스트용.
    Phase 2 멀티테넌트: `equity_path`(유저별)를 주면 그 곡선을 읽는다."""
    now = now or datetime.now(KST)
    if raw_equity is None:
        try:
            ep = _Path(equity_path) if equity_path is not None else None
            raw_equity = (json.loads(ep.read_text(encoding="utf-8"))
                          if (ep is not None and ep.exists()) else [])
            if not isinstance(raw_equity, list):
                raw_equity = []
        except Exception:
            raw_equity = []

    # 평가금액 곡선: current(현재 자산가치)·MDD(자산 낙폭) 표시용으로만 쓴다.
    # 사장 지시 2026-05-22: 결제 글리치(보유 불변·총액 급변)를 carry-forward 해 가짜 낙폭/급등 배제.
    pts = [(dt, v) for dt, v, _p in _equity_points(raw_equity)]

    kpi: Dict[str, Any] = {"has_equity": False, "has_trades": False}
    if pts:
        cur = pts[-1][1]
        first = pts[0][1]
        peak = pts[0][1]
        mdd = 0.0
        for _, v in pts:
            if v > peak:
                peak = v
            if peak > 0:
                mdd = min(mdd, v / peak - 1.0)
        kpi.update({
            "has_equity": True, "current": cur, "start": first, "points": len(pts),
            "mdd_pct": mdd * 100.0,
        })

    if trades is None:
        try:
            trades = get_trade_history(limit=2000, uid=uid)
        except Exception:
            trades = []

    # 사장 지시 2026-05-27: 수익률(누적/오늘/주/월)은 잔고 스냅샷이 아닌 '체결 실현손익(비용반영)'
    # 합산 기반 — KIS 결제 글리치로 자산곡선이 튀어도 수익률이 흔들리지 않는다. (미실현 평가손익은
    # 보유 종목 목록에 별도 표시.) 거래비용: 국내 0%, 해외 매수·매도 각 0.3%.
    kpi.update({k: v for k, v in _realized_perf_buckets(trades, now).items() if k != "_realized_has"})
    live = _trade_realized_stats(trades)
    # 사장 지시 2026-05-24: '거래 내역 비우기' 후에도 승률·통계가 유지되도록, 비우기 때 적립한
    # 누적 베이스라인을 라이브 거래 통계에 더한다(평가금액 추이는 equity_curve 가 별도 보존).
    base = _read_perf_baseline()
    total = live["sell_count"] + int(base.get("sell_count", 0) or 0)
    wins = live["win_count"] + int(base.get("win_count", 0) or 0)
    hold_sum = live["hold_days_sum"] + float(base.get("hold_days_sum", 0.0) or 0.0)
    hold_n = live["hold_days_n"] + int(base.get("hold_days_n", 0) or 0)
    if total > 0:
        kpi.update({
            "has_trades": True, "sell_count": total, "win_count": wins,
            "win_rate_pct": wins / total * 100.0,
            "avg_hold_days": (hold_sum / hold_n) if hold_n else None,
        })
    return kpi

def _enrich_trade_history(events: list) -> list:
    """사장 피드백 2026-05-15 (5차): 거래 내역 상세보기용 enrich.
    각 trade event에 다음 추가:
    - est_price (추정 체결가 — cycle_store orders_planned reason의 ≈X원/$X 패턴 파싱)
    - est_currency ('KRW'/'USD')
    - cycle_id (소속 사이클 id)
    - pnl_pct_hint (매도 reason에 박힌 평가손익 %)
    - detail.matched (sell 시 FIFO 매칭된 매수 이력 + 실현 손익)
    체결가는 시장가라 reason text의 ≈X원이 1차 근사치. KIS 체결내역 API로 정확 가능."""
    if not events:
        return []
    try:
        from infra import cycle_store
        cycles = cycle_store.list_cycles(limit=300)
    except Exception:
        cycles = []
    # (ticker, side) → 사이클 후보 리스트
    order_lookup: Dict[tuple, list] = {}
    for c in cycles:
        op = c.get("orders_planned") or []
        if isinstance(op, str):
            try: op = json.loads(op)
            except Exception: op = []
        for o in (op or []):
            tk = str(o.get("ticker") or "").strip()
            sd = str(o.get("side") or "buy").strip().lower()
            reason = str(o.get("reason") or "")
            if not tk: continue
            est_kr = None; est_usd = None
            mr = re.search(r"≈\s*([\d,]+)\s*원", reason)
            if mr:
                try: est_kr = float(mr.group(1).replace(",", ""))
                except ValueError: pass
            mu = re.search(r"≈\s*\$\s*([\d.]+)", reason)
            if mu:
                try: est_usd = float(mu.group(1))
                except ValueError: pass
            pnl_pct_hint = None
            mp = re.search(r"평가손익\s*([+\-]?[\d.]+)\s*%", reason)
            if mp:
                try: pnl_pct_hint = float(mp.group(1))
                except ValueError: pass
            order_lookup.setdefault((tk, sd), []).append({
                "cycle_ts": c.get("started_at") or "", "ended_at": c.get("ended_at") or "",
                "cycle_id": c.get("id"), "reason": reason[:300],
                "est_kr": est_kr, "est_usd": est_usd, "pnl_pct_hint": pnl_pct_hint,
            })
    # ts 오름차순으로 FIFO 처리
    sorted_evs = sorted(events, key=lambda e: e.get("ts", ""))
    positions: Dict[str, list] = {}  # ticker → [{qty, price, ts, source}, ...]
    for e in sorted_evs:
        tk = (e.get("ticker") or "").strip()
        side = str(e.get("side") or "buy").strip().lower()
        try: qty = int(e.get("qty") or 0)
        except (TypeError, ValueError): qty = 0
        filled = str(e.get("filled", "")).lower() in ("true", "1", "yes")
        ts = e.get("ts", "") or ""
        is_us = not (_is_kr_code(tk))
        # 사장 피드백 2026-05-15 (6차): 실제 체결가 우선, 없으면 reason 텍스트 추정으로 폴백.
        actual_fill = e.get("fill_price")
        try: actual_fill = float(actual_fill) if actual_fill is not None else None
        except (TypeError, ValueError): actual_fill = None
        # 가장 가까운 (ts ≤ event ts) 사이클의 가격 후보 선택 (추정값)
        est_price = None; cycle_id = None; pnl_hint = None
        candidates = order_lookup.get((tk, side), [])
        best = None
        for cand in candidates:
            cts = cand.get("cycle_ts") or ""
            if cts <= ts and (best is None or cts > (best.get("cycle_ts") or "")):
                best = cand
        if best:
            est_price = best.get("est_usd") if is_us else best.get("est_kr")
            cycle_id = best.get("cycle_id"); pnl_hint = best.get("pnl_pct_hint")
        # effective_price: 실제 체결가가 있으면 그것, 없으면 추정
        effective_price = actual_fill if actual_fill is not None else est_price
        price_source = "actual" if actual_fill is not None else ("estimated" if est_price else "unknown")
        e["fill_price"] = actual_fill  # 실제 체결가 (있을 때)
        e["est_price"] = est_price     # 추정 체결가 (reason 텍스트)
        e["est_currency"] = "USD" if is_us else "KRW"
        e["cycle_id"] = cycle_id
        e["pnl_pct_hint"] = pnl_hint
        e["price_source"] = price_source
        if not filled:
            e["detail"] = {"unfilled": True, "note": "체결 미확인 — 호가 미체결 가능 (KIS 자동 취소)"}
            continue
        if side == "buy":
            if effective_price and qty > 0:
                positions.setdefault(tk, []).append({"qty": qty, "price": effective_price,
                                                       "ts": ts, "source": price_source})
            e["detail"] = {"buy_price": effective_price, "qty": qty, "currency": e["est_currency"],
                          "price_source": price_source, "avg_cost": e.get("avg_cost")}
        elif side == "sell":
            queue = positions.get(tk, [])
            remaining = qty
            matches = []
            # 사장 피드백 6차: 매도가 우선순위 — 실제 fill_price → est_price → buy_price × (1 + hint%) → buy_price
            while remaining > 0 and queue:
                head = queue[0]
                take = min(head["qty"], remaining)
                effective_sell = actual_fill if actual_fill is not None else est_price
                sell_inferred = (actual_fill is None) and (est_price is None)
                if effective_sell is None and pnl_hint is not None and head["price"]:
                    effective_sell = head["price"] * (1 + pnl_hint / 100.0)
                if effective_sell is None:
                    effective_sell = head["price"]
                pnl = 0.0; pnl_pct = 0.0
                if effective_sell and head["price"]:
                    # 사장 지시 2026-05-27: 거래비용 반영 실현손익 — 국내 0%, 해외 매수·매도 각 0.3%.
                    pnl = _net_realized_pnl(head["price"], effective_sell, take, is_us) or 0.0
                    pnl_pct = (pnl / (head["price"] * take) * 100) if head["price"] and take else 0.0
                matches.append({"buy_qty": take, "buy_price": head["price"], "buy_ts": head["ts"],
                                "buy_source": head.get("source", "unknown"),
                                "sell_price": effective_sell, "pnl": pnl, "pnl_pct": pnl_pct,
                                "sell_price_inferred": sell_inferred})
                head["qty"] -= take
                if head["qty"] <= 0: queue.pop(0)
                remaining -= take
            total_pnl = sum(m["pnl"] for m in matches)
            shown_sell = (actual_fill if actual_fill is not None
                          else (est_price if est_price else (matches[0]["sell_price"] if matches else None)))
            # 사장 지시 2026-05-19: 매도 거래에 박힌 KIS 실매입평균가(avg_cost)가 있으면
            # 그것을 실현손익의 권위 기준으로 삼는다 — 세전(총손익).
            # FIFO 재구성은 로그가 잘리면 어긋나 명목/실제 불일치의 근본 원인이었다.
            # avg_cost가 없는 과거 이벤트만 FIFO 폴백(cost_source로 구분).
            rec_avg = e.get("avg_cost")
            try:
                rec_avg = float(rec_avg) if rec_avg not in (None, "", 0, "0", 0.0) else None
            except (TypeError, ValueError):
                rec_avg = None
            eff_sell_auth = (actual_fill if actual_fill is not None
                             else (est_price if est_price else shown_sell))
            realized_pnl = realized_pct = None
            if rec_avg and rec_avg > 0 and eff_sell_auth:
                # 사장 지시 2026-05-27: KIS 실평단(매수가) 기준 실현손익에 거래비용 반영 — 국내 0%, 해외 매수·매도 각 0.3%.
                realized_pnl = _net_realized_pnl(rec_avg, eff_sell_auth, qty, is_us)
                realized_pct = (realized_pnl / (rec_avg * qty) * 100.0) if (realized_pnl is not None and rec_avg and qty) else None
                if realized_pnl is not None:
                    total_pnl = realized_pnl  # 표시 P&L을 권위값(KIS 평단 기준, 비용반영)으로 교체
            # 사장 지시 2026-05-22(R5): KIS 실평단(avg_cost)도 없고 매수 이력도 없으면(거래내역 초기화)
            # 손익을 0/추정으로 '부정확하게' 찍지 말고 '미상'으로 표기한다.
            _no_basis = (not (rec_avg and rec_avg > 0)) and (not matches)
            if _no_basis:
                total_pnl = None
            e["detail"] = {"matched": matches, "sell_price": shown_sell, "qty": qty,
                          "total_pnl": total_pnl, "unmatched_qty": remaining,
                          "currency": e["est_currency"], "price_source": price_source,
                          "cost_basis": rec_avg, "realized_pnl": realized_pnl,
                          "realized_pnl_pct": realized_pct,
                          "cost_source": ("kis_avg" if (rec_avg and rec_avg > 0)
                                          else ("none" if _no_basis else "fifo_reconstructed")),
                          "no_cost_basis": _no_basis,
                          "sell_price_inferred": (actual_fill is None and est_price is None)}
    return sorted_evs


def get_trade_history(limit: int = 500, uid=None) -> list:
    """All trade events (executed/failed), newest first, for the 거래 내역 list.
    사장 피드백 2026-05-15 (5차): 추정 가격 + FIFO 매칭 P&L detail 첨부.
    Phase 2 멀티테넌트: uid 별 로그만 반환한다."""
    evs = [e for e in _read_response_log(uid) if e.get("type") in ("trade_executed", "trade_failed")]
    enriched = _enrich_trade_history(evs[-max(1, int(limit)):])
    return list(reversed(enriched))

def clear_event_log(uid=None):
    """Wipe that uid's accumulated event/response log (the dashboard '초기화' button)."""
    p = _response_log_path(uid)
    if p is None:
        return
    try:
        p.write_text("[]", encoding="utf-8")
        log_response_event({"source": "system_event", "type": "status", "state": "IDLE",
                            "message": "로그 초기화됨 (사용자 요청)"}, uid=uid)
    except Exception as _e:
        logger.warning(f"이벤트 로그 초기화 실패(uid={uid}): {_e}")

def clear_trade_log(uid=None) -> int:
    """Wipe just the trade events from claude_response.json (keeps everything else) + clear in-memory trade log.
    Used by the 수익률 탭 '거래 내역 비우기' button. Returns the number of trade entries removed.

    사장 지시 2026-05-24: 비우기 해도 ① 승률·매도수·보유일 통계는 영속 베이스라인에 적립해 유지하고,
    ② 제거되는 거래 이벤트는 타임스탬프 백업으로 남긴다('기억 못해서 평가금액이 틀어지는' 문제 방지).
    누적 평가금액(누적수익)은 equity_curve.json 가 별도 보존하므로 비우기와 무관하게 정확하다."""
    removed = 0
    p = _response_log_path(uid)
    if p is None:
        return 0
    # ① 비우기 직전 실현손익 통계를 베이스라인에 적립 (승률이 0으로 초기화되지 않게)
    try:
        st = _trade_realized_stats(get_trade_history(limit=4000, uid=uid))
        if st["sell_count"] > 0:
            base = _read_perf_baseline()
            base["sell_count"] = int(base.get("sell_count", 0) or 0) + st["sell_count"]
            base["win_count"] = int(base.get("win_count", 0) or 0) + st["win_count"]
            base["hold_days_sum"] = float(base.get("hold_days_sum", 0.0) or 0.0) + st["hold_days_sum"]
            base["hold_days_n"] = int(base.get("hold_days_n", 0) or 0) + st["hold_days_n"]
            _write_perf_baseline(base)
    except Exception as _e:
        logger.warning(f"승률 베이스라인 적립 실패(무시): {_e}")
    try:
        data = _read_response_log(uid)
        trade_evs = [e for e in data if e.get("type") in ("trade_executed", "trade_failed")]
        kept = [e for e in data if e.get("type") not in ("trade_executed", "trade_failed")]
        removed = len(data) - len(kept)
        # ② 제거 전 백업 (안전망) — uid 별로 구분 저장.
        if trade_evs:
            try:
                _bdir = _Path(__file__).parent / "data" / "_reset_backup"
                _bdir.mkdir(parents=True, exist_ok=True)
                (_bdir / f"trades.uid{uid}.{datetime.now(KST):%Y%m%d-%H%M%S}.json").write_text(
                    json.dumps(trade_evs, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as _be:
                logger.warning(f"거래 내역 백업 실패(무시): {_be}")
        p.write_text(json.dumps(kept[-_RESPONSE_LOG_CAP:], ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as _e:
        logger.warning(f"거래 내역 초기화 실패(uid={uid}): {_e}")
    # Phase 2 멀티테넌트: 이 uid 의 스왐 인메모리 trade_log 만 비운다(이미 생성된 스왐만 —
    # lazy 프로퍼티 강제생성 안 함). 다른 유저의 인메모리 거래 상태는 건드리지 않는다.
    try:
        from infra.user_context import REGISTRY
        for _ctx in REGISTRY.all_contexts().values():
            if int(getattr(_ctx, "uid", -1)) != int(uid):
                continue
            sw = getattr(_ctx, "_swarm", None)
            if sw is not None:
                sw._trade_log.clear()
                sw._trades_executed = 0
    except Exception:
        pass
    log_response_event({"source": "system_event", "type": "status", "state": "IDLE",
                        "message": f"거래 내역 초기화됨 ({removed}건 제거 · 승률·통계는 유지)"}, uid=uid)
    return removed

_broadcast_callback = None
def set_broadcast_callback(cb):
    global _broadcast_callback; _broadcast_callback = cb
    # 운영자 알림을 대시보드로도 흘린다 (파일 싱크가 진실의 원천 — UI 는 best-effort).
    def _alert_bridge(entry: dict):
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_broadcast(entry))
        except RuntimeError:
            pass  # 실행 중인 이벤트 루프 없음 → 파일/로그 싱크로 충분
    try:
        notifier.set_broadcast_callback(_alert_bridge)
    except Exception:
        pass
async def _broadcast(msg, uid=None):
    """이벤트를 영속 로그에 적고 WS 콜백으로 흘린다.
    Phase 2 멀티테넌트: uid 가 주어지면(오케스트레이터 사이클 이벤트) 해당 유저 연결에만,
    uid 가 None 이면(시스템 알림 등) 전체 연결에 송신한다. 라우팅은 app.py 가 등록한
    콜백(_route)이 결정한다."""
    try:
        if isinstance(msg, dict): log_response_event({"source":"system_event", **msg}, uid=uid)
    except Exception: pass
    if _broadcast_callback:
        try: await _broadcast_callback(msg, uid)
        except: pass

_TICKER_SKIP = {"APPROVED","JSON","KOSPI","KOSDAQ","KPI","NYSE","NASD","NASDAQ","AMEX","ETF","RSI","MACD",
                "EPS","PER","PBR","PSR","ROE","ROA","ROIC","ESG","GDP","CPI","PPI","PCE","USD","KRW","JPY",
                "EUR","CEO","CFO","COO","AI","ML","HBM","DRAM","NAND","HQ","IPO","M&A","FOMC","ETN","REIT",
                "DJI","SPX","IXIC","SHS","NKY","WTI","SMA","EMA","BB","ADX","OBV"}

def _clean_codes(kr_raw, us_raw) -> List[str]:
    kr, seen = [], set()
    for c in kr_raw:
        c = c.strip()
        if re.fullmatch(r"\d{6}", c) and c not in seen:
            seen.add(c); kr.append(c)
    us = []
    for t in us_raw:
        t = t.strip().upper()
        if re.fullmatch(r"[A-Z]{1,5}", t) and t not in _TICKER_SKIP and t not in seen:
            seen.add(t); us.append(t)
    return kr[:10] + us[:8]

def _extract_stock_codes(text: str) -> List[str]:
    """Prefer the explicit '대상종목: ...' / '종목코드: ...' line the orchestrator is required to emit;
    fall back to scanning the whole text for 6-digit codes / US tickers (filtered)."""
    text = text or ""
    code6 = r"(?<!\d)\d{6}(?!\d)"
    m = re.search(r"(?:대상\s*종목|종목\s*코드|target\s*stocks?)\s*[:：]\s*(.+)", text, re.IGNORECASE)
    if m:
        seg = m.group(1).splitlines()[0]
        codes = _clean_codes(re.findall(code6, seg), re.findall(r"[A-Za-z]{1,5}", seg))
        if codes:
            return codes
    # fallback: whole-text scan (handles Korean particles glued to the code, e.g. "005930을")
    return _clean_codes(re.findall(code6, text), re.findall(r"\b[A-Z]{1,5}\b", text))

def _extract_codes_after(text: str, *labels: str) -> List[str]:
    """Codes on the line that starts with one of `labels` (e.g. '후보종목:' / '최종종목:' / '최종승인:').
    Returns [] when the label line isn't present (caller can distinguish 'no line' from 'empty list')."""
    text = text or ""
    code6 = r"(?<!\d)\d{6}(?!\d)"
    pat = r"(?:" + "|".join(re.escape(l) for l in labels) + r")\s*[:：]\s*(.+)"
    m = re.search(pat, text, re.IGNORECASE)
    if not m:
        return []
    seg = m.group(1).splitlines()[0]
    return _clean_codes(re.findall(code6, seg), re.findall(r"[A-Za-z]{1,5}", seg))

def _has_label(text: str, *labels: str) -> bool:
    return bool(re.search(r"(?:" + "|".join(re.escape(l) for l in labels) + r")\s*[:：]", text or "", re.IGNORECASE))


def _resolve_candidate_codes(allocation: str, *, session: Optional[str] = None,
                             resolver=None, name_check=None, limit: int = 5) -> List[str]:
    """'후보종목:' 라인을 파싱해 **실제 종목코드**로 해석한다 (사장 지시 2026-05-22).

    운용전략실장(LLM)은 종목명은 알아도 6자리 코드를 몰라 환각(123456 등)한다. 그래서
    `종목명(코드)`/`종목명`/`코드`/US티커가 섞여 들어온다. 규칙:
      - 6자리 코드가 유효(name_check 로 이름 조회 성공)하면 그대로 사용.
      - 코드가 무효/누락이면 **종목명을 resolver(이름→코드)로 검색**해 보정. 이름도 없으면 버린다.
      - 영문 토큰은 US 세션(또는 세션 불명)에서만 미국 티커로 본다. KR 세션의 'HPSP' 같은
        영문 종목명은 코드로 해석한다.
    resolver/name_check 미주입(테스트·오프라인) 시 보정 없이 코드만 신뢰한다."""
    m = re.search(r"(?:후보종목|대상종목|종목코드|target stocks)\s*[:：]\s*(.+)", allocation or "", re.IGNORECASE)
    if not m:
        return []
    seg = m.group(1).splitlines()[0]
    is_kr_session = session in ("KR_TRADING", "KR_PRE_MARKET", "KR_CLOSE_REVIEW")
    out: List[str] = []
    for tok in re.split(r"[,，;]", seg):
        tok = tok.strip().strip(".·").strip()
        if not tok:
            continue
        mm = re.match(r"(?P<name>.*?)\s*[\(（]\s*(?P<code>[A-Za-z0-9.]{1,6})\s*[\)）]\s*$", tok)
        name = mm.group("name").strip() if mm else ""
        inner = (mm.group("code").strip() if mm else tok)
        code = inner if re.fullmatch(r"\d{6}", inner) else ""
        # 영문 토큰 → US 티커 (단, KR 세션이면 영문 종목명일 수 있으니 코드 해석으로 넘김)
        if not code and re.fullmatch(r"[A-Za-z]{1,5}", inner) and not is_kr_session:
            t = inner.upper()
            if t not in out:
                out.append(t)
            if len(out) >= limit:
                break
            continue
        nm = name or ("" if code else tok)
        resolved = ""
        if code:
            if name_check is None or name_check(code):
                resolved = code  # 유효 코드(또는 검증 불가 환경)
        if not resolved and nm and resolver:
            resolved = resolver(nm) or ""
        if resolved and resolved not in out:
            out.append(resolved)
        if len(out) >= limit:
            break
    return out


# C (사장 지시 2026-05-28): 후보 해석이 0건인데 뉴스에 종목 신호가 있을 때, 뉴스분석 리포트의
# 괄호표기 종목(`종목명(MU)`/`종목명(005930)`)으로 후보를 보강한다 — '뉴스만 있고 후보 0'으로
# 사이클이 낭비되는 것 방지(로그 리뷰: uid2 cycle24 MU/NVDA 신호 무시). 보강 후보도 downstream
# 퀀트≥6·DART·리스크 게이트를 그대로 통과해야 매수되므로 과매수 위험은 낮다.
_NEWS_TICKER_STOPWORDS = {
    "AI", "ETF", "ETN", "CEO", "CFO", "USD", "KRW", "GDP", "FED", "CPI", "PPI",
    "IPO", "EPS", "PER", "PBR", "ROE", "AND", "OR", "US", "UK", "EU", "OK",
    "ESG", "API", "OPEC", "WTI", "FOMC", "ECB", "BOJ", "GPU", "CPU", "HBM",
}

def seed_candidates_from_news(news_report: str, session: Optional[str] = None,
                              limit: int = 5) -> List[str]:
    """뉴스분석 리포트의 괄호표기 종목에서 후보를 추출한다(세션 경계·중복·상한 적용).
    US 세션=영문 티커, KR 세션=6자리 코드. 흔한 비종목 약어(AI/ETF/FED 등)는 제외한다."""
    text = news_report or ""
    is_kr = session in ("KR_TRADING", "KR_PRE_MARKET", "KR_CLOSE_REVIEW")
    out: List[str] = []
    for m in re.finditer(r"[\(（]\s*([A-Za-z]{1,5}|\d{6})\s*[\)）]", text):
        tok = m.group(1)
        if re.fullmatch(r"\d{6}", tok):
            if is_kr and tok not in out:
                out.append(tok)
        else:
            t = tok.upper()
            if (not is_kr) and t not in _NEWS_TICKER_STOPWORDS and t not in out:
                out.append(t)
        if len(out) >= limit:
            break
    return out


def cycle_is_idle(sell_only: bool, holdings) -> bool:
    """D (사장 지시 2026-05-28): 뉴스0·미개장(sell_only)이고 보유 종목도 없으면 매수(뉴스게이트)·
    매도(보유없음) 둘 다 불가 → 매크로 리서치/LLM 호출을 생략해 비용을 아낀다. 보유가 있으면
    매도 평가가 필요하므로 절대 idle 로 보지 않는다(손절/익절 누락 방지)."""
    return bool(sell_only) and not (holdings or [])


def _affordable_one_share(price: float, cash: float, total: float) -> bool:
    """사장 결정 2026-05-16: '1주 예산'(총평가의 10%) 비율과 무관하게,
    1주 가격이 **가용 예수금** 이내면 최소 1주 매수를 허용한다.
    (소액 계좌에서 $85짜리 정상 종목이 비율 한도 때문에 무조건 제외되던 문제 해결)

    price/cash/total 은 **모두 같은 통화**로 전달된다 (KR=원, US=USD 환산).
    단일 종목 집중 위험은 이 함수가 아니라 리스크관리실장의 결정론 게이트
    (CONSERVATIVE_STOCK_RATIO·MIN_CASH_BUFFER·MAX_CYCLE_BUDGET_RATIO)에서 별도로 통제된다.

    Returns True ⇒ '1주는 살 수 있다'고 보고 주문 초안에 포함.

    ── 사장님 직접 정의 필요 (아래 TODO) ──
    설계 트레이드오프: 순수하게 `price <= cash`만 볼지, 슬리피지·수수료
    여유분(runtime 'MIN_CASH_BUFFER', 보통 1.10)을 곱한 `price * buffer <= cash`로
    볼지의 선택. 전자는 주문이 더 자주 시도되지만 KIS가 예수금 부족으로
    되돌릴 수 있고, 후자는 KIS 거절을 줄이지만 경계 종목을 더 자주 거른다.
    """
    import runtime as _rt
    if price <= 0 or cash <= 0:
        return False
    _buffer = float(_rt.get("MIN_CASH_BUFFER") or 1.10)
    # 기본값(예시 B — 보수적): 리스크관리실장의 예수금 게이트(notional×MIN_CASH_BUFFER ≤ cash)와
    # **동일 기준**을 써서, 주문 초안이 곧바로 리스크 단에서 반려되는 모순을 막는다.
    # 사장님이 더 공격적으로 가려면 아래 한 줄을 `return price <= cash` 로 바꾸십시오.
    return price * _buffer <= cash

_SELL_HOLD_WORDS = {"보유", "유지", "hold", "keep", "유보", "관망"}
_SELL_ALL_WORDS  = {"전량", "전부", "모두", "all", "full", "100%", "청산"}
_SELL_HALF_WORDS = {"절반", "반", "1/2", "half", "50%"}


def _assemble_sell_orders(holdings, sell_directives, *, enable_rebalance, take_profit_pct,
                          stop_loss_pct, trim_over_ratio, conservative_ratio, per_stock_cap, total,
                          sell_prices=None):
    """보유종목 → 매도 주문 리스트 + price_map. KR(6자리)·US(티커) 모두 처리.
    사후관리실장 매도결정(sell_directives)이 우선, 미언급 종목은 자동 익절/손절(안전망).
    편중축소(TRIM)는 KRW per_stock_cap 기준이라 KR에만 적용 — US(USD 평가액)와 통화를
    섞으면 안 됨(버그 2026-05-22). 반환 order dict 의 market 으로 실행부가 us_sell/kr_sell 라우팅."""
    sell_directives = sell_directives or {}
    orders = []
    price_map: dict = {}
    for h in holdings:
        code = str(h.get("code", "")).strip()
        qty = int(h.get("qty") or 0)
        if not code or qty < 1:
            continue
        is_kr = _is_kr_code(code)
        pkey = code if is_kr else code.upper()
        pnl = float(h.get("pnl_pct") or 0.0)
        cur = float(h.get("cur_price") or 0.0)
        price_map[pkey] = cur
        reason = None
        sell_qty = 0
        directive = sell_directives.get(code) or sell_directives.get(code.upper())
        if directive is not None:
            dl = str(directive).strip().lower()
            if dl in _SELL_HOLD_WORDS:
                continue
            elif dl in _SELL_ALL_WORDS:
                sell_qty = qty; reason = "사후관리실장 매도 판단 — 전량"
            elif dl in _SELL_HALF_WORDS:
                sell_qty = max(1, qty // 2); reason = "사후관리실장 매도 판단 — 절반"
            else:
                mnum = re.match(r"(\d+)", dl)
                if mnum:
                    sell_qty = max(1, min(int(mnum.group(1)), qty)); reason = f"사후관리실장 매도 판단 — {sell_qty}주"
                else:
                    continue  # 알 수 없는 지시 → 보유로 간주
        elif enable_rebalance:
            # 사후관리실장이 언급 안 한 종목 → 자동 익절/손절/편중축소 (안전망)
            if pnl >= take_profit_pct:
                reason = f"자동 익절 — 평가손익 {pnl:+.1f}% ≥ +{take_profit_pct:.0f}%"; sell_qty = qty
            elif pnl <= -stop_loss_pct:
                reason = f"자동 손절 — 평가손익 {pnl:+.1f}% ≤ -{stop_loss_pct:.0f}%"; sell_qty = qty
            elif is_kr and trim_over_ratio and per_stock_cap > 0 and cur > 0 and (cur * qty) > per_stock_cap:
                over = int(((cur * qty) - per_stock_cap) // cur) + 1
                sell_qty = max(1, min(over, qty))
                reason = f"편중 축소 — 비중 {cur*qty/total*100:.1f}% > {conservative_ratio*100:.0f}% 한도"
        if reason and sell_qty > 0:
            # 사장 지시 2026-05-22: 계량분석팀장이 '매도가'를 숫자로 제시하면 그 지정가로 매도.
            # (시장가/미지정·안전망 자동 익절손절은 시장가 유지 — 즉시 청산.)
            _sp = (sell_prices or {}).get(code) or (sell_prices or {}).get(pkey) or (sell_prices or {}).get(code.upper())
            _lim = _sp.get("limit_price") if (_sp and _sp.get("mode") == "limit") else None
            _od = {"ticker": pkey, "side": "sell", "qty": sell_qty,
                   "price_type": "limit" if _lim else "market",
                   "market": "KR" if is_kr else "US",
                   "reason": (f"{h.get('name', pkey)} {reason} (보유 {qty}주, 평가손익 {pnl:+.1f}%"
                              + (f", 매도지정가 {_lim:,.0f}" if _lim else "") + ")")}
            if _lim:
                _od["entry_mode"] = "limit"; _od["entry_limit"] = _lim
            orders.append(_od)
    return orders, price_map


def _codes_for_session(holdings, session) -> List[str]:
    """현재 세션 시장에 해당하는 보유 종목코드만 반환. US세션→US티커, KR세션→6자리, 장외→전체.
    계량분석·매도평가 대상이 세션 시장과 일치하도록 한다 — 과거 6자리(KR)만 골라 US 보유분이
    분석에서 통째 빠지던 버그(2026-05-22) 방지. [[arquant-kr-us-asymmetry-bugs]]"""
    out: List[str] = []
    for h in (holdings or []):
        c = str(h.get("code", "")).strip()
        if not c:
            continue
        is_kr = _is_kr_code(c)
        if session == "US_TRADING":
            if not is_kr:
                out.append(c)
        elif session in ("KR_TRADING", "KR_PRE_MARKET", "KR_CLOSE_REVIEW"):
            if is_kr:
                out.append(c)
        else:  # OFF_HOURS 등 — 양쪽 모두
            out.append(c)
    return out


def _looks_like_tool_call(text: str) -> bool:
    """계량분석팀장 등이 분석 대신 도구호출 JSON/코드를 뱉었는지 감지 (사장 피드백 2026-05-18).
    BaseAgent는 tool-exec 루프가 없으므로 이런 출력은 그대로 화면에 찍히는 버그였다."""
    if not text:
        return False
    t = text.strip()
    head = t[:400]
    # 1) 응답이 사실상 JSON/코드펜스로 시작 (분석 줄글이 아님)
    if t.startswith("{") or t.startswith("```"):
        return True
    # 2) tool-call 시그니처 — "api"/"params"/함수호출 표기 + 점수 줄 부재
    sig = ('"api"' in head or '"params"' in head or '"tool"' in head
           or "analyze_stock_technical(" in head or '"function"' in head)
    if sig and "퀀트점수" not in t:
        return True
    return False


def _strip_leading_section_marker(text: str, *markers: str) -> str:
    """LLM이 프롬프트의 `[후보 종목 선정]`/`[최종 매수 종목 결정]` 머리말을 그대로 따라 적어
    브로드캐스트 래퍼와 겹쳐 두 번 보이는 문제 제거 (사장 피드백 2026-05-18).
    선두에서 해당 마커로 시작하는 줄만 걷어낸다 (본문 중간 인용은 보존)."""
    if not text:
        return text
    s = text.lstrip()
    changed = True
    while changed:
        changed = False
        for mk in markers:
            if s.startswith(mk):
                nl = s.find("\n")
                s = (s[nl + 1:] if nl >= 0 else "").lstrip()
                changed = True
    return s


def _parse_sell_decisions(text: str) -> Dict[str, str]:
    """Parse the post-manager's '매도결정: 005930=전량, 000660=절반, AAPL=보유' line → {code: directive}."""
    out: Dict[str, str] = {}
    m = re.search(r"매도\s*결정\s*[:：]\s*(.+)", text or "", re.IGNORECASE)
    if not m:
        return out
    for part in re.split(r"[,，;]", m.group(1).splitlines()[0]):
        mm = re.match(r"\s*([0-9]{6}|[A-Za-z]{1,5})\s*=\s*([^\s,，;]+)", part)
        if mm:
            out[mm.group(1).upper()] = mm.group(2).strip()
    return out


# 사장 피드백 2026-05-15 (#4): 계량분석팀장의 `진입가: code=값` 한 줄을 파싱.
# 값 패턴: '시장가' | 숫자(지정가) | '관망 ±X%' | '관망 X원' | 빈문자열 (시장가)
def _parse_entry_directive(text: str, code: str) -> Dict[str, Any]:
    """Returns {"mode": "market"/"limit"/"watch", "limit_price": float|None, "watch_pct": float|None, "raw": str}."""
    if not text or not code:
        return {"mode": "market", "limit_price": None, "watch_pct": None, "raw": ""}
    # 종목 라인 찾기 (단일 종목 응답이므로 한 줄만 봐도 됨)
    m = re.search(rf"진입가\s*[:：][^\n]*\b{re.escape(code)}\s*=\s*([^\n,，;]+)", text, re.IGNORECASE)
    if not m:
        return {"mode": "market", "limit_price": None, "watch_pct": None, "raw": ""}
    raw = m.group(1).strip()
    s = raw.lower().replace(",", "")
    if not s or "시장가" in s or "market" in s or "now" in s or "즉시" in s:
        return {"mode": "market", "limit_price": None, "watch_pct": None, "raw": raw}
    # 관망 ±X% 패턴
    mm = re.match(r"(?:관망|wait|watch|대기)\s*([+\-]?[\d.]+)\s*%", s)
    if mm:
        try:
            pct = float(mm.group(1))
            # 음수 표시가 빠진 경우 '하락' 단어 확인
            if pct > 0 and any(k in s for k in ("하락", "drop", "down", "내려")): 
                pct = -pct
            # 관망 비율 상한: ±10% 이내로 제한 (극단적 값 방지)
            MAX_WATCH = 10.0
            if pct < -MAX_WATCH:
                pct = -MAX_WATCH
            elif pct > MAX_WATCH:
                pct = MAX_WATCH
            return {"mode": "watch", "limit_price": None, "watch_pct": pct, "raw": raw}
        except ValueError: pass
    # 단순 숫자 (지정가)
    mm = re.match(r"([+\-]?[\d.]+)", s)
    if mm:
        try:
            px = float(mm.group(1))
            if px > 0:
                return {"mode": "limit", "limit_price": px, "watch_pct": None, "raw": raw}
        except ValueError: pass
    # 인식 실패 → 시장가 폴백
    return {"mode": "market", "limit_price": None, "watch_pct": None, "raw": raw}


def _parse_sell_price(text: str, code: str) -> Dict[str, Any]:
    """계량분석팀장의 `매도가: code=값` 한 줄 파싱 (사장 지시 2026-05-22).

    값: '시장가' | 숫자(지정가) | 빈문자열(시장가). 매도는 관망 모드 없음 — 시장가 또는 지정가만.
    Returns {"mode": "market"/"limit", "limit_price": float|None, "raw": str}."""
    if not text or not code:
        return {"mode": "market", "limit_price": None, "raw": ""}
    m = re.search(rf"매도가\s*[:：][^\n]*\b{re.escape(code)}\s*=\s*([^\n,，;]+)", text, re.IGNORECASE)
    if not m:
        return {"mode": "market", "limit_price": None, "raw": ""}
    raw = m.group(1).strip()
    s = raw.lower().replace(",", "")
    if not s or "시장가" in s or "market" in s or "즉시" in s:
        return {"mode": "market", "limit_price": None, "raw": raw}
    mm = re.match(r"([+\-]?[\d.]+)", s)
    if mm:
        try:
            px = float(mm.group(1))
            if px > 0:
                return {"mode": "limit", "limit_price": px, "raw": raw}
        except ValueError:
            pass
    return {"mode": "market", "limit_price": None, "raw": raw}


def _quant_ctx_for(report: str, code: str, width: int = 200) -> str:
    """주문 사유에 첨부할 '그 종목'의 퀀트 분석 앞부분을 돌려준다.

    quant_report 는 종목별 섹션을 `\\n\\n---\\n\\n` 으로 이은 것이고, 각 섹션엔
    `퀀트점수: {code}=` / `진입가: {code}=` 가 들어 있다. 그 앵커로 섹션을 찾아
    인접 종목 텍스트 오염을 막는다.
    버그 2026-05-22: 옛 구현은 report.find(code) 후 [idx-100:idx+200] 윈도우를 ctx[:90]
    으로 잘라, idx-100 이 직전 종목 섹션의 꼬리를 끌어와 SK텔레콤(017670) 주문에
    지아이이노베이션(358570) 퀀트가 붙었다."""
    if not report or not code:
        return ""
    anchor = re.compile(rf"(?:퀀트점수|진입가)\s*[:：][^\n]*\b{re.escape(code)}\s*=", re.IGNORECASE)
    for sec in report.split("\n\n---\n\n"):
        if anchor.search(sec):
            body = sec.split("\n", 1)[1] if "\n" in sec else sec
            return body.replace("\n", " ").strip()[:width]
    # 섹션 매칭 실패 — 코드 첫 등장 위치에서 '앞으로'만(뒤로 끌어오지 않음)
    m = re.search(re.escape(code), report, re.IGNORECASE)
    if m:
        return report[m.start(): m.start() + width].replace("\n", " ").strip()
    return ""


def _affordable_buy_qty(price: float, *, per_order_budget: float, per_stock_cap: float,
                        cycle_remaining: float) -> int:
    """세 한도(주문당 예산·단일종목 한도·사이클 잔여예산)의 최소값 안에서 매수 수량을 산정.

    리스크관리실장 결정론 검증(guardrails._check_single_order)과 **같은 한도**를 사이징
    단계에서 선반영해, 종목을 골라놓고 비중·예산 초과로 반려되는 낭비를 막는다.
    버그 2026-05-22: 개장 사이클이 per_stock_cap 을 60%로 완화했으나 검증은 25%로 반려 →
    삼성전자 6주(58.6%)·대한항공 69주(59.9%)가 매번 반려, 매수 0건."""
    if price <= 0:
        return 0
    budget = min(per_order_budget, per_stock_cap, cycle_remaining)
    return int(budget // price) if budget > 0 else 0


# 체결 미확인 주문을 다시 확인하기까지의 대기 시간(초). 모듈 상수로 둬서 테스트가
# 5분 실대기 없이 재확인 로직을 검증할 수 있게 한다(seam).
_REVERIFY_DELAY_SEC = 300
# 사장 지시 2026-05-21: 미체결 주문을 5분마다 반복 폴링하는 최대 횟수 (5분 × 36 = 3시간).
# 보통은 해당 시장 마감(KIS 자동취소) 시 조기 종료되고, 이 상한은 안전 장치(루프 누수 방지)다.
_POLL_MAX_ATTEMPTS = 36


def _trade_event_type(ok: bool) -> str:
    """체결 이벤트 타입을 누적 카운트 기준(ok)과 일치시킨다.
    ok=True 면(체결 확인 또는 US 잠정 접수) trade_executed, 아니면 trade_failed.
    이전엔 filled 기준이라 US 접수만(ok=True, filled=False) 주문이 카운트엔 포함되면서도
    이벤트는 trade_failed 로 나가 거래내역이 불일치했다(2026-05-21 회귀)."""
    return "trade_executed" if ok else "trade_failed"


def _format_exec_for_report(exec_results: List[Dict]) -> str:
    """전략실장 최종요약(LLM) 입력용 실행요약 — 체결/접수대기/실패를 정직히 구분한다.
    접수만 되고 미체결인 주문(US 즉시확인 불가·KR 지정가 미도달)을 '실패'로 넣으면
    LLM이 사유를 지어내므로(유동성 부족·VWAP 괴리 등 환각), '접수·체결대기'로 표기한다.
    거부(accepted=False)만 '실패'."""
    if not exec_results:
        return "없음"
    parts = []
    for e in exec_results:
        tk = str(e.get("ticker") or "").strip()
        side = e.get("side") or "buy"
        qty = e.get("qty", 0)
        if e.get("filled"):
            status = "체결"
        elif e.get("accepted"):
            status = "접수·체결대기(미확인)"
        else:
            status = "실패(거부)"
        parts.append(f"{tk} {side} x{qty} → {status}")
    return "; ".join(parts)


def _format_order_disposition(risk_result) -> str:
    """리스크 검증 결과 → '주문 처리 결과' 결정론 요약 (운용전략실장 리포트 환각 방지).
    사장 검토 2026-05-29: 리포트가 리스크 반려된 매수를 '최종 매수 선정'으로 단정하는
    환각(선정≠체결 혼동)을 막기 위해, 승인/반려를 종목·방향·사유와 함께 명시 주입한다."""
    results = (risk_result or {}).get("results") or []
    if not results:
        return "주문 처리 결과: 검증된 주문 없음."
    appr, rej = [], []
    for r in results:
        tk = str(r.get("ticker") or "?").strip()
        side = "매수" if (r.get("side") or "buy") != "sell" else "매도"
        qty = r.get("qty", "?")
        if r.get("status") == "APPROVED":
            appr.append(f"{tk} {side} x{qty}")
        else:
            why = "; ".join(r.get("issues") or []) or "사유 미상"
            rej.append(f"{tk} {side} x{qty} — 반려: {why}")
    lines = ["승인: " + (", ".join(appr) if appr else "없음"),
             "반려: " + ("; ".join(rej) if rej else "없음")]
    return "주문 처리 결과 (리스크관리실장 결정론 검증):\n" + "\n".join(lines)


def cycle_health_warnings(exec_results: List[Dict]) -> List[str]:
    """사이클 실행 결과의 '진짜 이상'을 코드로 점검 — 거부·전건 미접수는 경고로 띄운다.
    접수-후-미체결(US 폴링 대기·KR 지정가 미도달)은 정상 대기라 경고하지 않는다(잡음 방지).
    체결률이 낮아도 ok:true 로 묻히던 문제(2026-05-27 진단)를 가시화하기 위함."""
    real = [e for e in (exec_results or []) if not e.get("watch")]
    if not real:
        return []
    warnings: List[str] = []
    rejected = [e for e in real if not e.get("accepted")]
    if rejected:
        tks = ", ".join(str(e.get("ticker") or "?") for e in rejected)
        warnings.append(f"주문 거부 {len(rejected)}건 ({tks}) — KIS 거부 사유 점검 필요")
    if not any(e.get("accepted") for e in real):
        warnings.append("이번 사이클 주문 전건 미접수(거부) — 실행 경로·자격증명·잔고 점검 필요")
    return warnings


async def _llm_is_standing_directive(message: str, response: str) -> bool:
    """사장 지시 2026-05-21: 사장 지시가 '앞으로 매 운용에 지속 적용할 상시 원칙'인지(STANDING)
    아니면 '일회성 질문·조회·단발 명령'인지(ONESHOT) 경량 LLM으로 판단. 실패 시 보수적으로 False.
    체크박스(수동 저장)를 대체하는 자동 판단기."""
    try:
        from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, MODEL_ASSIGNMENTS
        if not OPENROUTER_API_KEY:
            return False
        import aiohttp
        model = MODEL_ASSIGNMENTS.get("news_curator") or "deepseek/deepseek-v4-flash"
        sys_p = ("당신은 분류기입니다. 사장이 운용역에게 내린 지시가 '앞으로 매 운용에 지속 적용해야 할 "
                 "상시 원칙·정책'인지, 아니면 '일회성 질문·현황 조회·단발 명령'인지 판단하세요. "
                 "포트폴리오 비중·자산배분·매매규칙·익절/손절·금지/우선 종목·리스크 한도처럼 지속 적용할 "
                 "원칙이면 STANDING. 단순 질문·현재 현황 조회·1회성 실행 요청이면 ONESHOT. "
                 "오직 한 단어로만 답하세요: STANDING 또는 ONESHOT.")
        usr_p = f"[사장 지시]\n{message}\n\n[운용역 응답 요지]\n{(response or '')[:600]}"
        payload = {"model": model, "max_tokens": 8, "temperature": 0.0,
                   "messages": [{"role": "system", "content": sys_p}, {"role": "user", "content": usr_p}]}
        headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json",
                   "HTTP-Referer": "https://arquant.ai-ve.uk", "X-Title": "ArQuant-DirectiveClassifier"}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
            async with s.post(f"{OPENROUTER_BASE_URL}/chat/completions", json=payload, headers=headers) as r:
                if r.status != 200:
                    return False
                d = await r.json()
        reply = ((d.get("choices") or [{}])[0].get("message", {}) or {}).get("content", "") or ""
        return "STANDING" in reply.upper()
    except Exception as _e:
        logger.warning(f"상시지시 분류 실패(보수적 미저장): {_e}")
        return False


class _OpsRouterMixin:
    async def ceo_directive(self, message: str) -> str:
        # 사장 지시 2026-05-20: 운용지원실장은 ADMIN·일반 유저 모두 사용 가능
        # (프로필 한정 파라미터 조정만 — 코드 자가수정·산하 팀장 폐지).
        _auid, _admin = self._active_actor()
        # 선두 @<태그> 만 라우팅 태그로 인정한다(문장 중간 @멘션은 본문으로 취급).
        mention = re.match(r"\s*@(\S+)", message or "")
        if mention:
            name = mention.group(1)
            # 사장 지시 2026-05-20: 운용지원실장은 ADMIN·일반 유저 모두 사용 가능(프로필 한정
            # 파라미터 조정만). 산하 팀장 멘션 라우팅은 폐지(빈 매핑) — 일반 경로로 흐른다.
            agent = self._agents_map.get(name)
            if agent:
                # 운용지원실장: 자동 분류 후 적절한 팀장 role로 spawn
                if name == "운용지원실장":
                    # 운용지원실장은 ADMIN·일반 유저 모두 '프로필 한정 파라미터 튜닝'을 수행한다.
                    # (운용지원실장은 애초에 소스/서버를 건드릴 능력이 없으므로 별도 거부 게이트가 불필요.)
                    return await self._ops_support_execute(message)
                # 일반 에이전트는 대화 페르소나 — 설정/코드는 직접 못 바꿈.
                # 전략/예산 조정이 필요하면 운용지원실장 라인이 '이 프로필 전용' 파라미터로 반영.
                _guide = ("(참고: 당신은 시스템 설정·소스코드를 직접 변경할 수 없습니다. 이 지시가 전략/예산/매매규칙 "
                          "조정을 요구하면 의견·이유는 자유롭게 답하시되, '운용지원실장 라인을 통해 이 프로필 전용 "
                          "파라미터로 반영하겠다'라는 식으로 진행 의도를 밝히십시오. 단순 질의·분석이면 평소 역할대로 "
                          "답하고, 무성의하게 거절하지 마십시오.)")
                # 사장 지시 2026-05-19: 포지션 인지 에이전트(트레이딩팀장·사후관리실장)가
                # 실시간 잔고 없이 '보유 0주·이미 정리됨' 같은 환각을 답한 사례(한화시스템
                # 272210: 09:14 매수 1주 보유 중인데 09:39 "정리됨"이라 거짓 보고)를 차단 —
                # 멘션 시 실제 KIS 잔고를 컨텍스트로 주입한다.
                _live_ctx = None
                if name in ("트레이딩팀장", "사후관리실장"):
                    try:
                        _live_ctx = await self.broker.kr_balance()
                    except Exception as _be:
                        record_error(name, _be, context="ceo_directive 멘션 잔고 주입 실패", uid=self.uid)
                        _live_ctx = ("[국내 계좌잔고] 조회 실패 — 보유 수량을 단정하지 말고 "
                                     "'확인 불가'로 답하십시오.")
                resp = await agent.think(f"[🔴 사장 직접 지시] {message}\n\n{_guide}", context=_live_ctx)
                await self._emit({"type":"agent_msg","agent":name,"message":resp})
                # 자동 체이닝 — 운용지원실장(프로필 한정 파라미터 조정)으로 (ADMIN·일반 공통).
                if self._needs_ops_chain(resp):
                    await self._auto_chain_to_ops(message, source_agent=name, source_response=resp)
                asyncio.create_task(self._auto_persist_directive(message, resp))   # 체크박스 대체: 자동 판단 저장
                return resp
            # Task 8: 잘못된/알 수 없는 태그 → 하드 에러로 막지 말고 가장 적절한 에이전트(운용전략실장)에게
            # 핸드오프한다. 선두의 잘못된 @태그 토큰만 제거하고 본문을 운용전략실장에게 넘긴다.
            logger.info("ceo_directive: 알 수 없는 태그 '@%s' — 운용전략실장으로 핸드오프", name)
            _body = re.sub(r"^\s*@\S+\s*", "", message or "", count=1)
            message = _body if _body.strip() else (message or "")
        resp = await self.orchestrator.think(f"[🔴 사장 직접 지시] {message}")
        await self._emit({"type":"agent_msg","agent":"운용전략실장","message":resp})
        # 운용전략실장 응답도 자동 체이닝 — 운용지원실장(프로필 한정 조정)으로 (ADMIN·일반 공통).
        if self._needs_ops_chain(resp):
            await self._auto_chain_to_ops(message, source_agent="운용전략실장", source_response=resp)
        asyncio.create_task(self._auto_persist_directive(message, resp))   # 체크박스 대체: 자동 판단 저장
        return resp

    async def _auto_persist_directive(self, message: str, response: str) -> None:
        """사장 지시 2026-05-21: 체크박스 제거 — 지시가 '지속 적용 상시 원칙'인지 LLM이 판단해
        활성 계정의 standing_directive 로 자동 저장한다. 질문·일회성·에러응답은 저장하지 않는다."""
        try:
            _auid, _ = self._active_actor()
            if _auid is None:
                return
            if not (message or "").strip():
                return
            if (response or "").startswith("["):   # 에이전트 에러 응답이면 판단 보류
                return
            if not await _llm_is_standing_directive(message, response):
                return
            text = message.strip()
            if text.startswith("@"):               # 선두 @멘션 토큰 제거
                parts = text.split(None, 1)
                text = parts[1] if len(parts) > 1 else ""
            if not text:
                return
            from infra.standing_directives import append_directive
            if append_directive(_auid, text):
                await self._emit({"type": "agent_msg", "agent": "시스템",
                                  "message": "📌 위 지시를 '지속 운영 원칙'으로 판단해 상시 지시로 저장했습니다 "
                                             "— 프로필 › 지시사항 관리에서 확인·삭제할 수 있습니다."})
        except Exception as _e:
            logger.warning(f"지시 자동 저장 판단 실패: {_e}")

    @staticmethod
    def _needs_ops_chain(response_text: str) -> bool:
        """사장 지시 2026-05-14: 일반 에이전트 응답에서 '운용지원실장에게 위임' 신호 감지.
        과거에는 사장이 직접 다시 @운용지원실장 멘션해야 했지만 이제 시스템이 자동 체이닝한다.
        무한 루프 방지: 응답 자체가 워커 spawn 메시지(🛠 ... 별도 프로세스 워커)면 매칭 안 함."""
        if not response_text: return False
        # 워커 spawn 알림 자체엔 절대 매칭 금지
        if "별도 프로세스 워커" in response_text or "워커 spawn" in response_text:
            return False
        signals = (
            "@운용지원실장",
            "@투자관리팀장", "@경영관리팀장", "@재무관리팀장",
            "운용지원실장에게 지시", "운용지원실장에게 말",
            "운용지원실장에 지시", "운용지원실장에 말",
            "직접 반영할 수 없", "시스템 설정 변경", "시스템에 반영", "코드 수정", "코드 변경",
            "직접 변경할 수 없", "설정 변경이 필요",
        )
        return any(s in response_text for s in signals)

    async def _auto_chain_to_ops(self, original_message: str, source_agent: str, source_response: str):
        """사장 지시 2026-05-14: 자동 지명 호출 — 사장이 다시 멘션할 필요 없이 시스템이 직접 워커 spawn.
        사장 지시 2026-05-20: 산하 팀장 폐지 → 모든 지시는 운용지원실장(ops_support) 단일 처리."""
        # 사용자에게 체이닝 사실을 명확히 표시 (UI 로그에 노출)
        await self._emit({"type": "agent_msg", "agent": "시스템",
                          "message": f"🔁 자동 지명 호출: {source_agent} → 운용지원실장. "
                                     f"사장님 원지시를 그대로 전달합니다."})
        # 워커는 원래 사장 지시 메시지를 받아 동일하게 처리
        await self._ops_support_execute(original_message)

    def _active_actor(self) -> tuple:
        """This orchestrator's (uid, is_admin). Phase 2: identity comes from the owning
        UserContext, not a global active account."""
        return self.uid, self.is_admin

    def _spawn_ops_support_worker(self, cycle_id: Optional[int] = None, manual_directive: Optional[str] = None,
                                  role: str = "ops_support"):
        """Spawn the standalone 운용지원실장 worker — fire-and-forget.

        사장 지시 2026-05-20: 코드 자가수정·서버 재시작·산하 팀장 위임 폐지. 워커는 별도
        프로세스에서 진단 + **프로필 한정 전략 파라미터(param_overrides)** 조정·제안만
        수행한다. ADMIN·일반 유저 모두 spawn 되며 적용은 각자 프로필로 분리되고,
        안전성은 워커가 소스 코드·서버를 절대 건드리지 않음으로써 보장된다."""
        worker = _Path(__file__).parent / "infra" / "ops_support_worker.py"
        if not worker.exists():
            logger.warning(f"ops_support_worker.py 없음 — 스킵 ({worker})")
            return
        _auid, _admin = self._active_actor()
        # 운용지원실장 피드백 on/off 토글(프로필별) — 활성 계정 토글이 OFF면 spawn 안 함.
        try:
            import runtime as _rt
            if not _rt.ops_feedback_enabled(_auid):
                logger.info(f"운용지원 워커 spawn 스킵 — 피드백 토글 OFF "
                            f"(uid={_auid}, cycle_id={cycle_id})")
                return
        except Exception as _e:
            logger.warning(f"ops_feedback 토글 조회 실패 — 그대로 진행: {_e}")
        cmd = ["python3.11", str(worker), "--role", role]
        if cycle_id is not None:
            cmd += ["--cycle-id", str(int(cycle_id))]
        if manual_directive:
            cmd += ["--manual", manual_directive]
        if _auid is not None:
            cmd += ["--actor-user", str(int(_auid))]
        # 워커는 더 이상 actor-admin 으로 동작을 분기하지 않지만(코드 자가수정 제거),
        # 로깅·호환을 위해 실제 값 전달.
        cmd += ["--actor-admin", "1" if _admin else "0"]
        log_path = _Path(__file__).parent / "data" / "ops_support.spawn.log"
        try:
            f = open(log_path, "a", encoding="utf-8", buffering=1)
            f.write(f"\n=== {datetime.now(KST):%Y-%m-%d %H:%M:%S} spawn (role={role}, cycle_id={cycle_id}, manual={'Y' if manual_directive else 'N'}, actor_uid={_auid}, admin={_admin}) ===\n")
            subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT,
                             start_new_session=True, cwd=str(_Path(__file__).parent))
            logger.info(f"운용지원 워커 spawn: role={role}, cycle_id={cycle_id}, actor_uid={_auid}, admin={_admin}")
        except Exception as e:
            logger.warning(f"운용지원 워커 spawn 예외: {e}")

    async def _ops_support_execute(self, message: str, role: Optional[str] = None) -> str:
        """@운용지원실장 또는 @투자관리팀장/@경영관리팀장/@재무관리팀장 멘션 처리.
        사장 지시 2026-05-14:
         - 질의형 키워드(이력/히스토리/수정한/변경한/어떤 코드/list)가 보이면 → 즉시 이력 텍스트 응답
         - 그 외 일반 지시 → 키워드 분류 후 해당 role로 워커 spawn (자동 지명 호출).
         - role을 명시적으로 받으면 분류를 건너뛰고 그 role로 spawn (사장이 @팀장 직접 호출한 경우).
        운용지원실장 산하 팀장은 모두 동일 워커를 다른 페르소나(--role)로 실행한다 — 보안 가드는 공유."""
        query_keywords = ("이력", "히스토리", "수정한", "변경한", "어떤 코드", "고친", "고친게",
                          "수정 내역", "변경 내역", "한 게 있", "한게 있", "리스트", "list", "history")
        # 사장 지시 2026-05-20: 운용지원실장은 ADMIN·일반 유저 모두 사용 가능
        # (프로필 한정 파라미터 조정·진단만 — 코드 자가수정 없음). 산하 팀장 라인은 폐지됨.
        _auid, _admin = self._active_actor()

        if any(kw in message for kw in query_keywords):
            from infra import ops_history
            text = ops_history.summary_text(limit=20, only_with_changes=True)
            st = ops_history.stats()
            stats_line = (f"\n\n📊 전체 통계: 워커 실행 {st['runs']}회 / "
                          f"실제 조정 발생 {st['runs_with_changes']}회 / "
                          f"반영된 파라미터 {st['total_applied_changes']}건")
            full = text + stats_line
            await self._emit({"type": "agent_msg", "agent": "운용지원실장", "message": full})
            return full

        # 사장 피드백 2026-05-18: 운용지원실장 피드백 토글이 OFF면 새 지시는 받지 않는다
        # (과거 이력 조회는 위에서 이미 처리되므로 영향 없음 — 끄면 '안내'만).
        try:
            import runtime as _rt
            if not _rt.ops_feedback_enabled(_auid):
                msg = ("⏸ 이 계정의 운용지원실장 피드백이 현재 **꺼짐(OFF)** 상태입니다. "
                       "대시보드의 '운용지원 피드백' 토글을 켜면 지시·자동 사이클 분석이 재개됩니다.")
                await self._emit({"type": "agent_msg", "agent": "운용지원실장", "message": msg})
                return msg
        except Exception:
            pass

        # 팀장 폐지(2026-05-20) — 모든 지시는 운용지원실장 단일 역할로 처리.
        display = "운용지원실장"

        # 사장이 내린 지시 원문도 대시보드 로그에 남긴다.
        await self._emit({"type":"agent_msg","agent":"사장",
                          "message": f"🗣 [사장 → {display}] {message}"})
        self._spawn_ops_support_worker(cycle_id=None, manual_directive=message)
        msg = (f"🛠 {display}: 위 지시를 분석해 **이 프로필 전용** 전략 튜닝 파라미터로 조정안을 "
               f"제시합니다. 전략·예산·익절/손절 같은 '적용 가능 전략' 파라미터가 이 프로필에 "
               f"반영되어 다음 로그인 시 활성화됩니다. "
               f"('@운용지원실장 이력 보여줘'로 이 프로필 반영 내역 조회 가능)")
        await self._emit({"type":"agent_msg","agent":display,"message":msg})
        return msg



class _MarketCalendarMixin:
    async def _verify_market_open(self, session: str) -> Optional[bool]:
        """개장 5분 경과 후, KIS 실시세로 '오늘 실제 거래가 있었는지'를 1회 확인한다 (사장 지시 2026-05-24).
        휴일을 하드코딩 목록으로만 추정하지 말고, 실데이터로 한번 검증하라는 취지.
          KR: KOSPI(0001) 지수 일봉의 최신 봉 날짜가 '오늘'이면 개장.
          US: 유동성 큰 티커(AAPL) 일봉의 최신 봉 날짜가 'US 거래일'이면 개장.
        반환: True=거래중(개장 확정) / False=당일 데이터 없음(휴장 추정) / None=시기상조·확인불가(폴백).
        주의(거래 누락 방지): True(개장)만 당일 캐시한다. False/None 은 캐시하지 않아 다음 사이클에
        재확인 → KIS 당일 봉이 늦게 채워진 일시적 지연이 자기 교정된다."""
        now = _now_kst()
        is_kr = session in ("KR_TRADING", "KR_PRE_MARKET", "KR_CLOSE_REVIEW")
        is_us = session == "US_TRADING"
        if not (is_kr or is_us):
            return None
        # KR_PRE_MARKET 은 개장 전이라 당일 봉이 아직 없음 → 확인 보류(폴백).
        if session == "KR_PRE_MARKET":
            return None
        market = "KR" if is_kr else "US"
        # ── 개장 +5분 경과 판정 ──
        if is_kr:
            sh, sm = SCHEDULE["kr_trading"]["start"]
            past_open5 = (now.hour * 60 + now.minute) >= (sh * 60 + sm + 5)
            expect = now.strftime("%Y-%m-%d")
        else:
            # US 정규장 22:30 개장(야간) — 22:35 이후(당일) 또는 자정~05:00(이미 경과).
            past_open5 = (now.hour == 22 and now.minute >= 35) or (now.hour == 23) or (now.hour < 5)
            # US 거래일: KST 저녁(22~24시)=같은 날짜, 자정~새벽(00~05시)=전날.
            expect = now.strftime("%Y-%m-%d") if now.hour >= 22 else (now - timedelta(days=1)).strftime("%Y-%m-%d")
        if not past_open5:
            return None
        ck = f"{market}:{now.strftime('%Y-%m-%d')}"
        if self._mkt_open_verified.get(ck):
            return True
        try:
            if is_kr:
                rows = await self.broker.kr_index_daily("0001", days=5)
            else:
                rows = await self.broker.us_daily_chart("AAPL", days=5)
        except Exception as _e:
            logger.warning(f"[개장확인] {market} 실시세 확인 실패(폴백) — {_e}")
            return None
        if not rows:
            logger.info(f"[개장확인] {market} 실시세 빈 응답 — 확인 보류(폴백)")
            return None
        latest = str(rows[-1].get("date", ""))
        if latest == expect:
            self._mkt_open_verified[ck] = True
            logger.info(f"[개장확인] {market} {expect}: 개장 확정 (KIS 최신 봉={latest})")
            return True
        if latest and latest < expect:
            logger.info(f"[개장확인] {market} {expect}: 당일 봉 없음 → 휴장 추정 (KIS 최신 봉={latest})")
            return False
        logger.info(f"[개장확인] {market} {expect}: 판정 보류 (KIS 최신 봉={latest})")
        return None

    async def _market_closed_today(self, session: str) -> tuple[bool, str]:
        """이 세션의 시장이 오늘 휴장인지 판정. 우선순위(사장 지시 2026-05-24):
          1) 주말 → 무조건 휴장 (KIS 호출 안 함, 자명).
          2) 개장 5분 후 KIS 실시세 확인 → 개장이면 진행(하드코딩 휴장일보다 우선),
             명백히 당일 봉 없으면 휴장.
          3) 실확인 전/불가 → 하드코딩 휴장일 목록(폴백).
        반환: (휴장이면 True, 사유 문자열)."""
        now = _now_kst()
        is_kr = session in ("KR_TRADING", "KR_PRE_MARKET", "KR_CLOSE_REVIEW")
        is_us = session == "US_TRADING"
        if not (is_kr or is_us):
            return False, ""
        # 1) 주말 — 자명한 휴장
        if (is_kr and is_kr_weekend(now)) or (is_us and is_us_weekend(now)):
            return True, f"주말 — 사이클 스킵 ({now.strftime('%Y-%m-%d %a')})"
        # 2) 개장 5분 후 실시세 확인 (1순위 권위)
        verified = await self._verify_market_open(session)
        if verified is True:
            return False, ""
        if verified is False:
            mk = "KR" if is_kr else "US"
            return True, f"{mk} 실거래 확인 결과 오늘 휴장 (KIS 당일 봉 없음) — 사이클 스킵"
        # 3) 폴백 — 하드코딩 휴장일 목록
        if is_kr and is_kr_holiday(now):
            return True, f"KR 휴장일({now.strftime('%Y-%m-%d %a')}) — 사이클 스킵"
        if is_us and is_us_holiday(now):
            return True, "US 휴장일/주말 — 사이클 스킵"
        return False, ""

    def _market_closed_for(self, is_kr: bool) -> bool:
        """해당 종목 시장이 (지정가 미체결분이 더는 체결될 수 없는) 마감 상태인가.
        KR: 정규장·동시호가 시간대(프리장 포함)가 아니면 마감. US: US_TRADING 이 아니면 마감."""
        sess = get_current_session()
        if is_kr:
            return sess not in ("KR_PRE_MARKET", "KR_TRADING", "KR_CLOSE_REVIEW")
        return sess != "US_TRADING"

    async def _maybe_market_close_alert(self, prev_session: str, cur_session: str):
        """장 마감 전환 시 1회 — 당일·누적 수익률을 모바일 알림(type=market_close)으로 보고
        (사장 지시 2026-05-21, 모바일 알림 4종 중 ④). KR_TRADING→그외=한국장 마감,
        US_TRADING→그외=미국장 마감. 전환 순간에만 호출되므로 중복 발송이 없다."""
        kr_closed = prev_session == "KR_TRADING" and cur_session != "KR_TRADING"
        us_closed = prev_session == "US_TRADING" and cur_session != "US_TRADING"
        if not (kr_closed or us_closed):
            return
        # 사장 지시 2026-05-30(비운영일 알림 버그): get_current_session()은 시간대만 보므로 주말/휴장일에도
        # KR_TRADING/US_TRADING 으로 잡혀 가짜 '장 마감' 전환이 생긴다. 그 시장이 실제 거래일이었을
        # 때만 알림을 보낸다. 마감 직전 시각(−30분)을 기준으로 보면 US 자정 경계도 정확히 판정된다.
        ref = _now_kst() - timedelta(minutes=30)
        if kr_closed and is_kr_holiday(ref):
            return
        if us_closed and is_us_holiday(ref):
            return
        mkt = "한국" if kr_closed else "미국"
        try:
            # uid 누락 시 거래내역이 빈 배열로 로드돼 당일·누적이 항상 0% 였다(+0% 버그). uid 전달.
            k = performance_kpis(self.equity_path, uid=self.uid)
        except Exception:
            k = {}
        def _p(v): return f"{v:+.2f}%" if isinstance(v, (int, float)) else "-"
        def _w(v): return f"{v:+,.0f}원" if isinstance(v, (int, float)) else "-"
        await self._emit({"type": "market_close", "agent": "트레이딩팀장", "market": mkt,
            "message": (f"🔔 {mkt} 장 마감 — 당일 {_p(k.get('today_pct'))} ({_w(k.get('today_pnl'))}) · "
                        f"누적 {_p(k.get('cumulative_pct'))} ({_w(k.get('cumulative_pnl'))})"),
            "today_pct": k.get("today_pct"), "today_pnl": k.get("today_pnl"),
            "cumulative_pct": k.get("cumulative_pct"), "cumulative_pnl": k.get("cumulative_pnl"),
            "trades_total": self._trades_executed})



class _ExecutionMixin:
    async def _build_orders(self, target_codes: List[str], candidate_codes: List[str], quant_report: str, news_report: str,
                            holdings: List[Dict], sell_directives: Optional[Dict[str, str]] = None,
                            market_open: bool = False,
                            entry_dirs: Optional[Dict[str, Dict[str, Any]]] = None,
                            sell_prices: Optional[Dict[str, Dict[str, Any]]] = None):
        """Assemble OrderDraft JSON in Python (no LLM):
          1) SELL from current holdings — if 사후관리실장 gave a `sell_directives` map ({code: '전량'|'절반'|'보유'|'N주'})
             it is authoritative for the holdings it addresses; holdings it didn't mention fall back to the auto rules
             (take profit ≥TAKE_PROFIT_PCT / stop loss ≤-STOP_LOSS_PCT / trim over CONSERVATIVE_STOCK_RATIO).
          2) BUY targets (2패스 최종종목), sized to available cash: qty = floor( min(cash·PER_ORDER_BUDGET_RATIO,
             total·CONSERVATIVE_STOCK_RATIO) / price ), capped by MAX_ORDER_QTY (if >0). Skip names already held.
          3) If no target is affordable, fall back to the cheapest liquid name in whatever market is tradeable now.
        US targets get a conservative qty=1 (needs USD cash). Returns (order_obj, price_map, buying_power)."""
        sell_directives = sell_directives or {}
        # live strategy params
        PER_ORDER_BUDGET_RATIO = runtime.get("PER_ORDER_BUDGET_RATIO", uid=self.uid); CONSERVATIVE_STOCK_RATIO = runtime.get("CONSERVATIVE_STOCK_RATIO", uid=self.uid)
        PER_ORDER_BUDGET_OVERSHOOT = float(runtime.get("PER_ORDER_BUDGET_OVERSHOOT", uid=self.uid) or 1.20)
        MAX_ORDER_QTY = runtime.get("MAX_ORDER_QTY", uid=self.uid); MAX_TRADES_PER_CYCLE = runtime.get("MAX_TRADES_PER_CYCLE", uid=self.uid)
        ENABLE_SELL_REBALANCE = runtime.get("ENABLE_SELL_REBALANCE", uid=self.uid); TAKE_PROFIT_PCT = runtime.get("TAKE_PROFIT_PCT", uid=self.uid)
        STOP_LOSS_PCT = runtime.get("STOP_LOSS_PCT", uid=self.uid); TRIM_OVER_RATIO = runtime.get("TRIM_OVER_RATIO", uid=self.uid)
        ENABLE_CHEAP_FALLBACK = runtime.get("ENABLE_CHEAP_FALLBACK", uid=self.uid); ALLOW_US_STOCKS = runtime.get("ALLOW_US_STOCKS", uid=self.uid)
        ALLOW_DERIVATIVES = runtime.get("ALLOW_DERIVATIVES", uid=self.uid)
        MAX_CYCLE_BUDGET_RATIO = float(runtime.get("MAX_CYCLE_BUDGET_RATIO", uid=self.uid) or 0.4)  # 리스크검증과 동일한 사이클 예산 한도
        report = (quant_report or "") + "\n" + (news_report or "")
        snap = await self.broker.kr_account_snapshot()
        bp = snap["buying_power"]; holdings = holdings or snap.get("holdings") or []
        # 사장 지시 2026-05-21: 자산곡선은 KR+US 통합 총평가로 기록한다(주문 사이징은 KR 기준 bp 유지).
        # 사장 지시 2026-05-24: 실제 정규장 세션(요일·휴장 반영)에만 평가금액 추이를 기록한다(장외/주말/휴장 제외).
        if is_market_session_now():
            try:
                _pf = await self.broker.portfolio_holdings()
                record_equity(self.equity_path, _pf.get("buying_power") or bp, "cycle", holdings=_pf.get("holdings") or holdings)
            except Exception:
                record_equity(self.equity_path, bp, "cycle", holdings=holdings)
        cash = float(bp.get("cash", 0.0) or 0.0)
        total = float(bp.get("total_eval", 0.0) or 0.0) or cash
        # 사장 지시 2026-05-14: 1주 예산은 **총평가액** 기준 (실제 spend는 cash로 캡).
        target_per_order = total * PER_ORDER_BUDGET_RATIO if total > 0 else 0.0
        # 사장 지시 2026-05-14: 개장 사이클은 한도 해제 — 100건 뉴스 정보를 적극 활용해 큰 포지션 가능
        if market_open:
            target_per_order = total * min(1.0, PER_ORDER_BUDGET_RATIO * 5.0)  # 5배 확대 (균형형이면 50%까지)
        per_order_budget = min(target_per_order, cash) if cash > 0 else 0.0
        # 단일 종목 비중 한도는 리스크관리실장 검증(total*CONSERVATIVE_STOCK_RATIO)과 동일하게 유지한다.
        # 버그 2026-05-22: 개장 사이클에서만 이 한도를 60%로 풀어 큰 주문을 만들었으나, 검증부는
        # 완화를 몰라 25%로 반려 → 고가주가 매번 반려됐다. 사이징을 검증과 일치시켜 통과시킨다.
        per_stock_cap = total * CONSERVATIVE_STOCK_RATIO if total > 0 else 0.0
        cycle_budget = cash * MAX_CYCLE_BUDGET_RATIO if cash > 0 else 0.0
        spent_krw = 0.0  # 이번 사이클 매수 누적(원화 환산) — 검증부 사이클 예산 한도를 사이징에 선반영
        held_codes = {str(h.get("code","")).strip() for h in holdings}
        # 사장 지시 2026-05-22: 사이클 예산을 '매수 대상 종목 수'로 균등 배분해 분산 매수한다.
        # (한 종목이 예산을 독식해 다음 종목이 '사이클 누적 매수예산 초과'로 반려되던 케이스 방지 —
        #  QUBT 49주 승인 후 IBM 반려처럼.)
        _n_buy = max(1, len({str(c).strip() for c in (target_codes or [])
                             if str(c).strip() and str(c).strip() not in held_codes}))
        per_name_budget = (cycle_budget / _n_buy) if cycle_budget > 0 else float("inf")
        orders: List[Dict] = []
        price_map: Dict[str, float] = {}
        notes: List[str] = []

        # ── 1) SELL — 사후관리실장 매도결정 우선, 미언급 종목은 자동 규칙 (KR·US 모두) ──
        if ENABLE_SELL_REBALANCE or sell_directives:
            _sell_orders, _sell_px = _assemble_sell_orders(
                holdings, sell_directives, enable_rebalance=ENABLE_SELL_REBALANCE,
                take_profit_pct=TAKE_PROFIT_PCT, stop_loss_pct=STOP_LOSS_PCT,
                trim_over_ratio=TRIM_OVER_RATIO, conservative_ratio=CONSERVATIVE_STOCK_RATIO,
                per_stock_cap=per_stock_cap, total=total, sell_prices=sell_prices)
            orders.extend(_sell_orders)
            price_map.update(_sell_px)

        # ── 2) BUY targets ──────────────────────────────────────────────
        affordable_buy_found = False
        for code in (target_codes or [])[:8]:
            code = str(code).strip()
            if not code:
                continue
            is_kr = _is_kr_code(code)
            if not is_kr and not ALLOW_US_STOCKS:
                notes.append(f"{code}: 해외주식 비활성(ALLOW_US_STOCKS=False) → 제외"); continue
            ctx = _quant_ctx_for(report, code)
            if is_kr:
                if code in held_codes:
                    notes.append(f"{code}: 이미 보유 → 신규 매수 생략(분산)"); continue
                price = await self.broker.kr_last_price(code)
                await asyncio.sleep(0.25)  # ease KIS TPS
                price_map[code] = price
                if price <= 0 or per_order_budget <= 0:
                    notes.append(f"{code}: 사이즈 산정 불가(가격={price:,.0f}원, 총평가={total:,.0f}원·예수금={cash:,.0f}원) → 제외"); continue
                qty = _affordable_buy_qty(
                    price, per_order_budget=min(per_order_budget, per_name_budget),
                    per_stock_cap=(per_stock_cap if per_stock_cap > 0 else float("inf")),
                    cycle_remaining=max(0.0, cycle_budget - spent_krw))
                if MAX_ORDER_QTY and MAX_ORDER_QTY > 0:
                    qty = min(qty, MAX_ORDER_QTY)
                if qty < 1:
                    # 사장 결정 2026-05-16: 비율 예산과 무관하게 1주 가격이 예수금 이내면 1주 매수 허용.
                    if _affordable_one_share(price, cash, total) and not (MAX_ORDER_QTY and 0 < MAX_ORDER_QTY < 1):
                        qty = 1
                        notes.append(f"{code}: 1주 {price:,.0f}원 — 예수금 {cash:,.0f}원 이내 → 1주 매수 (비율 예산 무관)")
                    else:
                        notes.append(f"{code}: 1주가 {price:,.0f}원 — 예수금 {cash:,.0f}원으로 매수 불가 → 제외"); continue
                affordable_buy_found = True
                spent_krw += qty * price
                # 사장 피드백 2026-05-15 (#4): 계량분석팀장이 지정한 진입가 directive 첨부 (시장가 default)
                _ed = (entry_dirs or {}).get(code, {"mode": "market", "limit_price": None, "watch_pct": None, "raw": ""})
                orders.append({"ticker": code, "side": "buy", "qty": qty, "price_type": "market", "market": "KR",
                               "entry_mode": _ed.get("mode"), "entry_limit": _ed.get("limit_price"),
                               "entry_watch_pct": _ed.get("watch_pct"), "entry_raw": _ed.get("raw"),
                               "reason": f"운용전략실장 지정 · {qty}주(≈{qty*price:,.0f}원, 총평가 {PER_ORDER_BUDGET_RATIO*100:.0f}%·종목 {CONSERVATIVE_STOCK_RATIO*100:.0f}% 한도 내) · 진입가:{_ed.get('raw') or '시장가'}{' [⚠ 관망 모드는 미구현 — 시장가 즉시 매수]' if _ed.get('mode') == 'watch' else ''} · 퀀트: {ctx[:90]}"})
            else:
                # US stock: 사장 피드백 2026-05-15 (#14): SOUN $8인데 1주만 산 버그 — 예산 내에서 가능한 만큼 매수.
                # 환율 1,500원/$ 가정으로 KRW 예산을 USD로 환산 후 정수 주수 산정.
                tk = code.upper()
                us_px = await self.broker.us_last_price(tk)
                price_map[tk] = us_px
                await asyncio.sleep(0.25)
                if us_px <= 0:
                    notes.append(f"{tk}: 해외 시세 조회 실패(거래소 미확인) → 제외"); continue
                if us_px < 0.01:
                    notes.append(f"{tk}: 가격 ${us_px:.4f} — KIS 온라인 주문 최소단위($0.01) 미만 → 주문불가, 제외"); continue
                _krw_per_usd = get_usdkrw(USDKRW_FALLBACK)  # 사장 지시 2026-05-22: 5분 크롤 라이브 환율(폴백)
                _budget_usd = per_order_budget / _krw_per_usd  # 표시용(주문당 예산)
                qty_us = _affordable_buy_qty(
                    us_px, per_order_budget=min(per_order_budget, per_name_budget) / _krw_per_usd,
                    per_stock_cap=((per_stock_cap / _krw_per_usd) if per_stock_cap > 0 else float("inf")),
                    cycle_remaining=max(0.0, cycle_budget - spent_krw) / _krw_per_usd)
                if MAX_ORDER_QTY and MAX_ORDER_QTY > 0:
                    qty_us = min(qty_us, MAX_ORDER_QTY)
                if qty_us < 1:
                    # 사장 결정 2026-05-16: 예수금(USD 환산) 기준 1주 허용 — 비율 예산 무관.
                    _cash_usd = cash / _krw_per_usd
                    _total_usd = total / _krw_per_usd
                    if _affordable_one_share(us_px, _cash_usd, _total_usd):
                        qty_us = 1
                        notes.append(f"{tk}: 1주 ${us_px:.2f} — 예수금 ${_cash_usd:,.2f} 이내 → 1주 매수 (비율 예산 무관)")
                    else:
                        notes.append(f"{tk}: 1주 ${us_px:.2f} — 예수금 ${_cash_usd:,.2f}으로 매수 불가 → 제외"); continue
                est_krw = us_px * qty_us * _krw_per_usd
                affordable_buy_found = True
                spent_krw += est_krw
                _ed = (entry_dirs or {}).get(tk, {"mode": "market", "limit_price": None, "watch_pct": None, "raw": ""})
                orders.append({"ticker": tk, "side": "buy", "qty": qty_us, "price_type": "market", "market": "US",
                               "entry_mode": _ed.get("mode"), "entry_limit": _ed.get("limit_price"),
                               "entry_watch_pct": _ed.get("watch_pct"), "entry_raw": _ed.get("raw"),
                               "reason": f"운용전략실장 지정 해외종목 · {qty_us}주(≈${us_px*qty_us:,.2f} / ≈{est_krw:,.0f}원, 예산 ${_budget_usd:.2f}) · 진입가:{_ed.get('raw') or '시장가'}{' [⚠ 관망 모드는 미구현 — 시장가 즉시 매수]' if _ed.get('mode') == 'watch' else ''} · 퀀트: {ctx[:80]}"})

        # ── 3) 대체 후보 — 최종 지정 종목이 예산 초과/시세불가라 못 샀을 때 (요청: '뜬금없는' 폴백 금지) ──
        #      순서: ① 운용전략실장 1차 후보 5개 중 아직 안 산 종목 중 예산 내 최저가  →
        #            ② (그래도 없으면) KR: 거래량 상위에서 레버리지/인버스/저가 제외 후 예산 내 최저가 / US: 미국 유니버스 최저가 1주
        from config import CHEAP_FALLBACK_US_TICKERS, CHEAP_FALLBACK_EXCLUDE_KEYWORDS, CHEAP_FALLBACK_MIN_PRICE
        _sess = get_current_session()
        cap = min(per_order_budget, per_stock_cap or per_order_budget) if per_order_budget > 0 else 0.0
        target_set = {str(c).strip() for c in (target_codes or [])}

        if ENABLE_CHEAP_FALLBACK and not affordable_buy_found and cap > 0:
            # ① 1차 후보 5개 중에서 (KR 종목 우선) 예산 내 최저가
            best = None  # (code, price, market, name)
            for c in (candidate_codes or []):
                c = str(c).strip()
                if not c or c in target_set or c in held_codes:
                    continue
                if _is_kr_code(c) and _sess in ("KR_TRADING", "KR_PRE_MARKET"):
                    px = price_map.get(c)
                    if px is None:
                        try: px = await self.broker.kr_last_price(c); await asyncio.sleep(0.2)
                        except Exception: px = 0.0
                        price_map[c] = px
                    if 0 < px <= cap and (best is None or px < best[1]):
                        best = (c, px, "KR", c)
                elif not (_is_kr_code(c)) and _sess == "US_TRADING":
                    tk = c.upper(); px = price_map.get(tk)
                    if px is None:
                        try: px = await self.broker.us_last_price(tk); await asyncio.sleep(0.2)
                        except Exception: px = 0.0
                        price_map[tk] = px
                    if px and 0.01 <= px <= cap and (best is None or px < best[1]):
                        best = (tk, px, "US", tk)
            if best:
                c, px, mkt, nm = best
                q = max(1, int(cap // px)) if mkt == "KR" else 1
                if MAX_ORDER_QTY and MAX_ORDER_QTY > 0: q = min(q, MAX_ORDER_QTY)
                orders.append({"ticker": c, "side": "buy", "qty": q, "price_type": "market", "market": mkt,
                               "reason": f"대체 후보(운용전략실장 1차 후보 중 예산 내 최저가) {nm} {q}주(≈{q*px:,.0f}{'원' if mkt=='KR' else 'USD'}) — 최종 지정 종목이 예산 초과"})
                notes.append(f"대체 후보 채택(후보군 내): {nm} {px:,.2f}")
                affordable_buy_found = True

        # ② 후보군에도 적격이 없으면 — 시장별 안전 폴백
        if ENABLE_CHEAP_FALLBACK and not affordable_buy_found and _sess in ("KR_TRADING", "KR_PRE_MARKET") and cap > 0:
            try:
                vlist = await self.broker.kr_volume_rank_list()
                def _ok(v):
                    nm = str(v.get("name", "") or "")
                    if any(kw in nm for kw in CHEAP_FALLBACK_EXCLUDE_KEYWORDS):  # 레버리지/인버스/선물 ETF·ETN 제외
                        return False
                    return v["code"] not in held_codes and CHEAP_FALLBACK_MIN_PRICE <= float(v.get("price", 0) or 0) <= cap
                cand = sorted([v for v in vlist if _ok(v)], key=lambda v: v["price"])
                if cand:
                    v = cand[0]; q = max(1, int(cap // v["price"]))
                    if MAX_ORDER_QTY and MAX_ORDER_QTY > 0: q = min(q, MAX_ORDER_QTY)
                    price_map[v["code"]] = v["price"]
                    orders.append({"ticker": v["code"], "side": "buy", "qty": q, "price_type": "market", "market": "KR",
                                   "reason": f"대체 후보(거래량 상위·레버리지/인버스 제외, 예산 내 최저가) {v.get('name',v['code'])} {q}주(≈{q*v['price']:,.0f}원) — 최종 지정 종목이 예산 초과"})
                    notes.append(f"대체 후보 채택(KR 거래상위): {v.get('name','')}({v['code']}) {v['price']:,.0f}원")
                    affordable_buy_found = True
                else:
                    notes.append("대체 후보 없음(KR) — 예산 내 적격 종목 없음 → 이번 사이클 신규 매수 생략")
            except Exception as e:
                notes.append(f"대체 후보 탐색 실패(KR): {e}")
        elif ENABLE_CHEAP_FALLBACK and not affordable_buy_found and _sess == "US_TRADING":
            try:
                us_cands = [str(c).upper() for c in ((candidate_codes or []) + (target_codes or [])) if not (_is_kr_code(c))]
                universe, seen = [], set()
                for t in (us_cands + list(CHEAP_FALLBACK_US_TICKERS)):
                    t = t.strip().upper()
                    if t and t not in seen and t not in held_codes:
                        seen.add(t); universe.append(t)
                priced = []
                for t in universe[:16]:
                    px = price_map.get(t)
                    if px is None:
                        px = await self.broker.us_last_price(t); await asyncio.sleep(0.2)
                        price_map[t] = px
                    if px and px >= 0.01:
                        priced.append((t, px))
                if priced:
                    t, px = sorted(priced, key=lambda x: x[1])[0]
                    orders.append({"ticker": t, "side": "buy", "qty": 1, "price_type": "market", "market": "US",
                                   "reason": f"대체 후보(미국 정규장 · 후보군+유니버스 내 최저가) {t} 1주 ≈${px:,.2f} — 최종 지정 종목이 예산 초과/시세불가(USD 필요)"})
                    notes.append(f"대체 후보 채택(US): {t} ${px:,.2f}")
                    affordable_buy_found = True
                else:
                    notes.append("대체 후보 없음(US) — 후보군 시세 조회 실패 → 신규 매수 생략")
            except Exception as e:
                notes.append(f"대체 후보 탐색 실패(US): {e}")
        elif ENABLE_CHEAP_FALLBACK and not affordable_buy_found:
            notes.append(f"대체 후보 생략 — 현재 장외시간({_sess})이라 체결 가능한 시장 없음")

        # ── 4) Session-aware market validation (item 11) ──────────────────────
        # KR orders should only go through during KR trading hours; US during US hours.
        # This prevents errors like "장운영일자가 주문일과 상이합니다".
        session = get_current_session()
        filtered_orders = []
        for o in orders:
            mkt = o.get("market", "KR")
            if mkt == "KR" and session not in ("KR_TRADING", "KR_PRE_MARKET"):
                notes.append(f"{o['ticker']}: 현재 KR 장외시간({session}) → KR 주문 제외 (장운영일자 불일치 방지)")
                continue
            if mkt == "US" and session != "US_TRADING":
                notes.append(f"{o['ticker']}: 현재 US 장외시간({session}) → US 주문 제외")
                continue
            filtered_orders.append(o)
        orders = filtered_orders

        # sells first (risk-reducing), then buys; clamp to MAX_TRADES_PER_CYCLE
        orders.sort(key=lambda o: 0 if o["side"] == "sell" else 1)
        if not ALLOW_DERIVATIVES:
            orders = [o for o in orders if o.get("market") not in ("BOND", "FUTURES")]
        return {"orders": orders, "sizing_notes": notes}, price_map, bp

    async def _dart_risk_review(self, buy_orders: List[Dict], per_dart: Dict[str, str]) -> tuple:
        """리스크관리실장 2차 재심 — 매수 주문 종목의 DART 공시를 읽고 리스크성 공시가 있으면 반려.

        사장 지시 2026-05-14: 더 이상 자체적으로 broadcast/log 하지 않음 — 호출처(`_run_analysis_cycle`)에서
        1차 결과와 묶어 단일 메시지로 출력. Returns (vetoed_set, llm_response_text). Fail-open."""
        relevant = {}
        for r in (buy_orders or []):
            tk = str(r.get("ticker", "")).strip()
            d = per_dart.get(tk) or per_dart.get(tk.upper())
            if d:
                relevant[tk] = d
        if not relevant:
            return set(), "DART 재심: 관련 공시 없음 — 1차 결과 유지"
        tickers = [str(r.get("ticker", "")).strip() for r in buy_orders]
        digest = "\n\n".join(f"[{k}]\n{(v or '')[:2500]}" for k, v in relevant.items())
        # ITEM2b: QUERY_FAILED 종목 목록 추출 — 해당 종목은 특별 주의 지시
        _qf_tickers = []
        for r in (buy_orders or []):
            tk_qf = str(r.get("ticker", "")).strip()
            d_qf = per_dart.get(tk_qf) or per_dart.get(tk_qf.upper()) or ""
            if "시스템 리스크" in d_qf or "QUERY_FAILED" in d_qf:
                _qf_tickers.append(tk_qf)
        _qf_warning = ""
        if _qf_tickers:
            _qf_warning = (
                f"\n\n⚠️ [DART 조회 실패 종목 — 시스템 리스크 주의]: {', '.join(_qf_tickers)}\n"
                f"위 종목은 API 오류·키 없음·네트워크 장애로 DART 데이터를 가져오지 못했습니다. "
                f"'재무 미확보'로 처리하되, 조회 실패 자체를 **시스템 리스크 경고**로 간주하십시오. "
                f"다른 명백한 승인 근거가 없으면 보수적으로 반려를 검토하십시오.\n"
            )
        try:
            resp = await self.risk_guard.think(
                f"1차 결정론 검증을 통과한 매수 주문 종목: {', '.join(tickers)}\n\n"
                f"각 종목 — 최근 ~90일 DART 공시 + **가장 최근 가용 분기/반기/연간 요약재무**:\n{digest}"
                f"{_qf_warning}\n\n"
                f"① 공시 리스크 신호(관리종목/거래정지/상장폐지심사/횡령·배임/회계감리/불성실공시/감자/대규모 유상증자/주요 소송·제재 등) "
                f"② 재무 적신호(자본잠식·부채비율 과다·연속 적자·매출 급감) 를 종합해, 해당하는 종목은 반려하십시오. "
                f"⚠️ '부채>자산' 판정은 **재무상태표 검증 통과(✅ 재무상태표 검증: ...)** 표기가 있을 때만 하십시오. "
                f"'⚠️ 재무상태표 내적 불일치' 또는 '시스템 리스크' 경고가 있는 종목의 재무 수치를 근거로 부채>자산·자본잠식을 단정하지 마십시오. "
                f"명백한 악재가 없으면 승인 유지(공시·재무 정보 없음만으로는 반려 금지). "
                f"마지막 줄에 반드시 `최종승인: 코드, 코드` (유지할 종목만; 전부 반려면 `최종승인: 없음`).")
            if not _has_label(resp, "최종승인"):
                return set(), f"[DART 재심 응답]\n{resp}\n\n⚠ '최종승인' 라인 없음 — 1차 결과 유지(fail-open)"
            kept_upper = {c.upper() for c in _extract_codes_after(resp, "최종승인")}
            vetoed = {tk.upper() for tk in relevant if tk.upper() not in kept_upper}
            return vetoed, resp
        except Exception as e:
            logger.warning(f"DART 재심 실패: {e}")
            return set(), f"DART 재심 예외 — 1차 결과 유지: {e}"

    async def _entry_watch_task(self, ticker: str, qty: int, market: str,
                                 watch_pct: float, baseline_holdings: List[Dict],
                                 reason: str = ""):
        """사장 피드백 2026-05-15 (#4): 분봉 진입 타이밍 모니터.
        - 1분 주기로 현재가 폴링, baseline 대비 watch_pct 도달 시 시장가 매수.
        - 최대 3시간(180분) 대기 후 타임아웃 → 시장가 매수 (계량분석팀장 의도대로 일단 진입).
        - 도중에 해당 시장이 마감되면 매수 취소(주문 안 냄)."""
        start = time.time()
        max_wait = 3 * 3600  # 3시간
        poll_interval = 60   # 1분
        is_kr = (market == "KR")
        try:
            initial_px = await (self.broker.kr_last_price(ticker) if is_kr else self.broker.us_last_price(ticker))
            if not initial_px or initial_px <= 0:
                await self._emit({"type":"trade_failed",
                    "message": f"⏰ {ticker} 대기 매수 취소 — 초기가 조회 실패"})
                return
            target_px = initial_px * (1 + watch_pct / 100.0)
            triggered = False
            elapsed_min = 0
            while time.time() - start < max_wait:
                if self._stop_event.is_set():
                    await self._emit({"type":"trade_failed",
                        "message": f"⏰ {ticker} 대기 매수 취소 — 사용자 중지"})
                    return
                # 시장 마감 체크
                sess = get_current_session()
                if (is_kr and sess not in ("KR_TRADING", "KR_PRE_MARKET")) or (not is_kr and sess != "US_TRADING"):
                    await self._emit({"type":"trade_failed",
                        "message": f"⏰ {ticker} 대기 매수 취소 — 장 마감 ({sess})"})
                    return
                cur = await (self.broker.kr_last_price(ticker) if is_kr else self.broker.us_last_price(ticker))
                if cur and cur > 0:
                    move_pct = (cur / initial_px - 1) * 100
                    # 조건 충족 검사 (음수 pct = 하락 트리거, 양수 pct = 돌파 트리거)
                    if (watch_pct < 0 and cur <= target_px) or (watch_pct > 0 and cur >= target_px):
                        triggered = True
                        break
                    if elapsed_min % 5 == 0:  # 5분마다 진행상황 broadcast
                        await self._emit({"type":"agent_msg","agent":"트레이딩팀장",
                            "message": f"⏱ {ticker} 분봉 모니터 ({elapsed_min}분 경과): 현재가 {cur:,.2f} / 목표 {target_px:,.2f} ({move_pct:+.2f}%)"})
                await asyncio.sleep(poll_interval)
                elapsed_min += 1
            # 시장가 매수 발동 (트리거 or 타임아웃)
            od = OrderDraft(ticker=ticker, side="buy", qty=qty, price_type="market",
                            market=market, reason=f"분봉 대기 매수 ({'트리거' if triggered else '3시간 타임아웃'}) — {reason[:80]}",
                            approved=True)
            # 시장 마감 직전 다시 한 번 체크 (KIS가 거부할 수 있으므로)
            sess = get_current_session()
            if (is_kr and sess not in ("KR_TRADING", "KR_PRE_MARKET")) or (not is_kr and sess != "US_TRADING"):
                await self._emit({"type":"trade_failed",
                    "message": f"⏰ {ticker} 대기 매수 취소 — 매수 시점에 장 마감"})
                return
            # 주문 직전 보유 스냅샷 — 즉시 체결 확인 + (미확인 시) 폴링 baseline (KR+US)
            try: _pre_kr = await self.broker.kr_holdings()
            except Exception: _pre_kr = list(baseline_holdings or [])
            _pre_us = []
            if not is_kr:
                try: _pre_us = await self.broker._overseas_holdings()
                except Exception: _pre_us = []
            res = await self.broker.place_order(od)
            badge = "⏱ 분봉 진입 매수 발동" if triggered else "⌛ 3시간 타임아웃 매수"
            accepted = all(bad not in res for bad in ("실패", "에러", "거부", "예외", "REJECT"))
            # 즉시 체결 확인 (KR 2초 후 보유 재조회 / US 는 즉시 확인 불가 → 폴링)
            filled = False; fill_note = ""; fill_price = None; avg_cost = None
            if accepted and is_kr:
                try:
                    await asyncio.sleep(2.0)
                    self.broker._acct_snap = None
                    after = await self.broker.kr_holdings()
                    before_qty = next((h["qty"] for h in _pre_kr if h.get("code") == ticker), 0) or 0
                    after_qty = next((h["qty"] for h in after if h.get("code") == ticker), 0) or 0
                    if after_qty > before_qty:
                        filled = True; fill_note = f"보유 {before_qty}→{after_qty}주"
                        # 사장 지시 2026-05-27: 매수가·수량 항상 기록 — 평단 변화로 체결가 역산.
                        before_avg = next((h.get("avg_price") for h in _pre_kr if h.get("code") == ticker), 0) or 0
                        after_avg = next((h.get("avg_price") for h in after if h.get("code") == ticker), 0) or 0
                        bq = after_qty - before_qty
                        if bq > 0 and after_avg and after_qty:
                            fill_price = (after_avg * after_qty - before_avg * before_qty) / bq
                        avg_cost = after_avg or before_avg or None
                except Exception as _e:
                    fill_note = f"체결확인 실패({_e})"
            # 사장 지시 2026-05-21: 즉시 체결 확인된 경우에만 +1. 접수만(미확인)이면 폴링이 확정 시 +1.
            if filled:
                self._trades_executed += 1
                self._trade_log.append({"ts": _now_kst_iso(), "ticker": ticker, "side": "buy",
                                        "qty": qty, "result": res, "filled": True, "ok": True, "watch": True,
                                        "fill_price": fill_price, "avg_cost": avg_cost,
                                        "fill_currency": "KRW"})
            # 모바일 알림 ①: 체결 신청(접수)
            if accepted:
                await self._emit({"type": "order_submitted", "agent": "트레이딩팀장",
                    "message": f"📨 {ticker} 매수 {qty}주 주문 접수 — 체결 확인 중",
                    "ticker": ticker, "side": "buy", "qty": qty})
            # 모바일 알림 ②: 체결 완료(즉시) / 또는 실패
            if filled or not accepted:
                await self._emit({"type": _trade_event_type(filled),
                    "message": f"{badge} — {ticker} {qty}주: {res}" + (f" | {fill_note}" if fill_note else ""),
                    "ticker": ticker, "side": "buy", "qty": qty, "filled": filled,
                    "trades_total": self._trades_executed})
            elif accepted:  # 접수만(미확인) → 5분마다 반복 폴링으로 확정 시 카운트
                asyncio.create_task(self._poll_fills_until_confirmed(
                    [{"ticker": ticker, "side": "buy", "qty": qty}],
                    list(_pre_kr) + list(_pre_us)))
            metrics.incr("orders_filled" if filled else "orders_unfilled",
                         market="KR" if is_kr else "US")
        except Exception as e:
            logger.error(f"_entry_watch_task({ticker}) 예외: {e}")
            await self._emit({"type":"trade_failed",
                "message": f"⚠ {ticker} 분봉 모니터 예외 — {e}"})
            metrics.incr("entry_watch_error", ticker=ticker)
            notifier.alert("CRITICAL", "분봉 진입 모니터 예외",
                           f"{ticker} {qty}주 진입 감시 중단 — {e}",
                           dedup_key=f"entry_watch_error:{ticker}")

    async def _poll_fills_until_confirmed(self, pending: List[Dict], baseline_holdings: List[Dict], cyc=None):
        """접수됐지만 체결 미확인인 주문을 5분마다 '반복' 폴링한다 (사장 지시 2026-05-21).

        - pending: [{ticker, side, qty, ...}] — 실행부에서 즉시 체결이 확인되지 않은 주문들.
          (즉시 체결된 주문은 실행부에서 이미 +1 했으므로 여기 오지 않는다.)
        - baseline_holdings: 주문 직전 holdings 스냅샷 (qty 비교 기준; US false-positive 방지).

        매 주기마다 보유 변동을 재확인한다:
          • 체결 확인  → 그때 누적 카운트 +1, trade_log 기록, 통신로그 '체결 확인됨'
                         (type=trade_executed → 모바일 '체결 완료' 알림). 그 주문은 목록에서 제거.
          • 아직 미체결 → 채팅 메시지도, 카운트도 올리지 않고 '조용히' 다음 주기 재시도.
          • 해당 시장 마감 → 그 주문의 폴링을 조용히 종료 (KIS가 미체결 지정가를 자동 취소).
        루프 누수 방지를 위해 _POLL_MAX_ATTEMPTS 회 후엔 무조건 종료한다."""
        remaining = [dict(e) for e in (pending or [])]
        attempts = 0
        try:
            while remaining and attempts < _POLL_MAX_ATTEMPTS and not self._stop_event.is_set():
                await asyncio.sleep(_REVERIFY_DELAY_SEC)  # 5분 (테스트는 0으로 단축)
                attempts += 1
                try:
                    self.broker._acct_snap = None  # force fresh read
                    after_kr = await self.broker.kr_holdings()
                    try:
                        after_us = await self.broker._overseas_holdings()
                    except Exception:
                        after_us = []
                except Exception as e:
                    logger.warning(f"_poll_fills 보유조회 실패(다음 주기 재시도): {e}")
                    continue
                still: List[Dict] = []
                for e in remaining:
                    tk = str(e.get("ticker", "")).strip()
                    side = e.get("side", "buy")
                    qty = e.get("qty", 0)
                    is_kr_tk = _is_kr_code(tk)
                    after = after_kr if is_kr_tk else after_us
                    before_qty = next((h["qty"] for h in (baseline_holdings or []) if h.get("code") == tk), 0)
                    after_qty = next((h["qty"] for h in after if h.get("code") == tk), 0)
                    truly_filled = (side == "buy" and after_qty > before_qty) or \
                                   (side == "sell" and after_qty < before_qty)
                    if truly_filled:
                        # 사장 지시 2026-05-27: 폴링 확정 체결도 매수가/매도가·수량을 '항상' 기록한다.
                        # 보유 변동(평단·현재가)으로 체결가·매수원가(avg_cost)를 역산 — 실현손익 비용계산의 근거.
                        before_avg = next((h.get("avg_price") for h in (baseline_holdings or []) if h.get("code") == tk), 0) or 0
                        after_h = next((h for h in after if h.get("code") == tk), None)
                        after_avg = (after_h or {}).get("avg_price") or 0
                        fill_price = None; avg_cost = None
                        if side == "buy":
                            bq = after_qty - before_qty
                            if bq > 0 and after_avg and after_qty:
                                # (after_avg×after_qty − before_avg×before_qty) = fill_price × 매수수량
                                fill_price = (after_avg * after_qty - before_avg * before_qty) / bq
                            avg_cost = after_avg or before_avg or None
                        else:  # sell — 시장가 체결가 ≈ 확정시점 현재가, 매수원가 = 매도 직전 평단
                            base_cur = next((h.get("cur_price") for h in (baseline_holdings or []) if h.get("code") == tk), 0) or 0
                            fill_price = ((after_h or {}).get("cur_price") or base_cur) or None
                            avg_cost = before_avg or None
                        # 사장 지시 2026-05-30(KR/US 비대칭): US는 결제 과도기/장중에 보유 평단·현재가가
                        # 0/누락으로 와 체결가·평단이 None 으로 찍혀 실현손익이 추정가(부정확)로 날조됐다.
                        # 결손 시 라이브 호가(us_last_price)로 체결가를 확보하고, 매수 평단 미상이면
                        # 체결가로 근사한다 (KR 은 평단 역산이 신뢰 가능하므로 손대지 않는다).
                        if not is_kr_tk and not fill_price:
                            try:
                                _lp = await self.broker.us_last_price(tk)
                                fill_price = float(_lp) if _lp and _lp > 0 else fill_price
                            except Exception:
                                pass
                        if not is_kr_tk and not avg_cost and side == "buy":
                            avg_cost = fill_price
                        self._trades_executed += 1
                        self._trade_log.append({"ts": _now_kst_iso(), "ticker": tk, "side": side,
                                                "qty": qty, "filled": True, "ok": True,
                                                "fill_note": "5분 폴링 후 체결 확인",
                                                "fill_price": fill_price, "avg_cost": avg_cost,
                                                "fill_currency": ("KRW" if is_kr_tk else "USD")})
                        await self._emit({"type": "trade_executed", "agent": "트레이딩팀장",
                            "message": (f"✅ {tk} {('매수' if side == 'buy' else '매도')} {qty}주 체결 확인됨 — "
                                        f"보유 {before_qty}→{after_qty}주 (누적 체결 {self._trades_executed}건)"),
                            "ticker": tk, "side": side, "qty": qty, "filled": True,
                            "trades_total": self._trades_executed})
                        # 사장 지시 2026-05-29(KR/US 비대칭 버그 수정): US 비동기 매수도 체결 확정 시점에
                        # 펀드기획팀장 thesis 를 기록한다. 기존엔 동기 실행부 `if filled:` 에서만 기록돼
                        # US(폴링 확정)는 영원히 누락 → 매도 직전 상기시킬 thesis 0건이었다.
                        # (폴링은 이미 느린 백그라운드 루프이므로 await 해도 핫패스에 영향 없음.)
                        if side == "buy" and cyc is not None:
                            _buy_rec = {"ticker": tk, "side": "buy", "ok": True,
                                        "fill_price": fill_price, "avg_cost": avg_cost,
                                        "fill_currency": ("KRW" if is_kr_tk else "USD")}
                            try:
                                await self._record_buy_thesis(_buy_rec, cyc)
                            except Exception as _the:
                                logger.warning(f"[펀드기획] 폴링 체결 thesis 기록 실패 {tk}: {_the}")
                    elif not self._market_closed_for(is_kr_tk):
                        still.append(e)  # 장중 미체결 → 조용히 다음 주기 재확인
                    # else: 시장 마감 → 미체결 확정, 조용히 폐기 (메시지·카운트 없음)
                remaining = still
        except Exception as e:
            logger.warning(f"_poll_fills_until_confirmed 예외: {e}")
            # 폴링 실패 = 체결 카운트가 누락될 수 있음 → 운영자에게 표면화.
            metrics.incr("poll_fills_error")
            notifier.alert("WARN", "체결 폴링 예외",
                           f"미체결 주문 반복 확인 중단 — {e}",
                           dedup_key="poll_fills_error")



class ArquantOrchestrator(_OpsRouterMixin, _MarketCalendarMixin, _ExecutionMixin):
    def __init__(self, ctx):
        self.ctx = ctx
        self.uid = ctx.uid
        self.is_admin = ctx.is_admin
        from infra import user_paths
        self.equity_path = user_paths.equity_path(ctx.uid)
        self.trade_log_path = user_paths.trade_log_path(ctx.uid)
        _inj = {"uid": ctx.uid,
                "openrouter_key": ctx.creds.get("openrouter_key"),
                "openrouter_base_url": None}
        self.orchestrator = BaseAgent(name="운용전략실장", role="chief_orchestrator", model_key="chief_orchestrator", injection=_inj,
            system_prompt="""당신은 ArQuant v1.0의 운용전략실장입니다. 의사결정은 **2단계(2패스)**로 진행됩니다.

## 데이터 소스
1. **글로벌 지수**: KOSPI, KOSDAQ, S&P500, NASDAQ, 다우, 상해, 니케이, 환율, WTI
2. **뉴스**: 네이버 금융 실시간 뉴스
3. **DART 공시**: 최근 공시 자동 수집
4. **퀀트 데이터**: 3년치 일봉 + 수급 + KIS 분봉

## [후보 종목 선정]
- 전략리서치팀장의 매크로 보고와 검증된 지수만 근거로, 분석할 **후보 종목 정확히 5개**를 고릅니다.
- ⚠️ **대형주·메가캡에 치우치지 마십시오.** 시가총액 상·중·소형, 서로 다른 업종/테마를 골고루 섞으십시오.
  (예: 한 종목은 대형 반도체, 한 종목은 중형 소재, 한 종목은 소형 성장주, 한 종목은 금융/유틸 ...)
  같은 업종 3개 이상, '삼성전자·SK하이닉스만' 같은 구성은 금지.
- 이미 보유 중인 종목은 가급적 제외하고 새 후보를 우선합니다.
- 응답 **마지막 줄**에 반드시 이 형식으로만(다른 텍스트 없이):
  `후보종목: 삼성전자(005930), SK하이닉스(000660), 에코프로비엠(247540), 클래시스(214150), AAPL`
  ← **종목명(코드)** 형식, 미국은 티커. 정확히 5개. 코드가 불확실하면 종목명만 정확히 적으면 시스템이 코드를 채웁니다 — 코드를 지어내지 마십시오.

## [최종 매수 종목 결정]
- 계량분석팀장의 정량 평가와 뉴스분석팀장의 감성 분석을 받아, 후보 5개 중에서 **실제 매수할 종목을 좁힙니다.**
- 매수 개수는 프롬프트에 주어진 '최대 매수 개수 N'(전략 프리셋: 보수형 1 / 균형형 2 / 공격형 3)을 넘기지 마십시오.
- 퀀트 점수가 6점 미만이거나 뉴스 감성이 부정적인 종목은 제외합니다. 마땅한 게 없으면 N개보다 적게 골라도 됩니다.
- 응답 **마지막 줄**에 반드시 이 형식으로만:
  `최종종목: 005930, AAPL`  ← 후보 목록에 있던 코드만, N개 이하. 없으면 `최종종목: 없음`

## 공통
- 표에 없는 수치는 추정·생성하지 않습니다. 사장님(@사장)의 직접 지시는 최우선입니다.""")
        self.macro_analyst = create_macro_analyst(injection=_inj)
        self.quant_analyst = create_quant_analyst(injection=_inj)
        self.news_analyst = create_news_analyst(injection=_inj)
        self.trader = create_trader(injection=_inj)
        self.risk_guard = create_risk_guard(injection=_inj)
        # 사장 피드백 2026-05-18: 수탁자책임실장(policy_filter) 폐지 → 역할은 risk_guard 통합
        self.post_manager = create_post_manager(injection=_inj)
        # 사장 지시 2026-05-28(우선순위 3 단독): 사후관리실장 산하 펀드기획팀장 — 매수 시점 thesis 박고 매도 직전 상기.
        from agents.specialists import create_fund_planner
        self.fund_planner = create_fund_planner(injection=_inj)
        self.ops_support = create_ops_support(injection=_inj)
        # 뉴스 헤드라인 사전 선별기 — 40건 초과 시 굵직한 40건만 추리는 경량 페르소나 (대시보드 @멘션은 안 받음)
        # 표시 이름은 사장 지시(2026-05-14)에 따라 '전략리서치팀장'으로 통일 (macro_analyst와 페르소나 공유 — model_key/role은 별도 유지).
        self.news_curator = BaseAgent(
            name="전략리서치팀장", role="news_curator", model_key="news_curator", injection=_inj,
            system_prompt=("당신은 ArQuant '전략리서치팀장'의 뉴스 큐레이션 페르소나입니다. 다수의 증권 속보 헤드라인 중 시장·종목 분석에 가장 가치 있는 것만 골라내는 게 이 단계의 역할입니다.\n"
                "선정 기준: ① 실적/M&A/규제/소송/증자·감자/관리종목·거래정지/실적 가이던스/대규모 계약 등 실질 이벤트 우선, "
                "② 단순 시황 요약·일반 사설·반복 속보·재배포는 후순위, ③ 같은 사건 중복 보도는 1건만.\n"
                "응답은 오직 한 줄 — `선정: 1, 4, 7, 12, ...` (1-base 인덱스 콤마 구분). 다른 설명/주석 절대 금지."))
        self.broker = ctx.broker
        self.news_monitor = get_monitor()
        self.current_state = SwarmState.IDLE
        self.cycle_log: Optional[SwarmCycleLog] = None
        self.validation_attempts = 0
        self._cycle_history: List[Dict] = []
        self._stop_event = asyncio.Event()
        # Pending news split by target market (KR/US). 'BOTH' articles are mirrored into both lists
        # so the right session uses them on its cycle without losing globally-relevant news.
        self._pending_news_kr: List[Dict] = []
        self._pending_news_us: List[Dict] = []
        self._last_cycle_at: float = 0.0   # epoch of last analysis cycle (for the hourly periodic trigger)
        self._last_session: Optional[str] = None   # previous loop iteration's session (for market-open detection)
        self._last_status_state: Optional[str] = None  # last broadcast status state (suppress 1-min OFF_HOURS spam)
        # 사장 지시 2026-05-24: 개장 5분 후 KIS 실시세로 '오늘 개장 확정'을 1회 확인한 결과 캐시.
        # 키 "KR:YYYY-MM-DD"/"US:YYYY-MM-DD" → True(개장 확정)만 적재(이후 호출 생략).
        # 휴장/확인불가는 적재하지 않아 다음 사이클에 재확인(데이터 지연 자기 교정).
        self._mkt_open_verified: Dict[str, bool] = {}
        self._trades_executed = 0
        self._trade_log: List[Dict] = []
        self._agents_map = {
            "운용전략실장": self.orchestrator, "전략리서치팀장": self.macro_analyst,
            "계량분석팀장": self.quant_analyst, "뉴스분석팀장": self.news_analyst,
            "트레이딩팀장": self.trader, "리스크관리실장": self.risk_guard,
            "사후관리실장": self.post_manager, "운용지원실장": self.ops_support,
            "펀드기획실장": self.fund_planner,
        }
        # 사장 지시 2026-05-20: 산하 팀장(investment/operations/finance) 및 코드 자가수정 폐지.
        # 운용지원실장 단일 역할만 남으며, 팀장 멘션 라우팅은 빈 매핑으로 비활성화한다.
        self._ops_team_leaders: Dict[str, str] = {}

    async def _emit(self, msg):
        """이 오케스트레이터(=이 유저)의 사이클 이벤트를 그 유저 WS 연결에만 송신한다.
        Phase 2 멀티테넌트: 다른 유저 대시보드로 이벤트가 새지 않게 uid 로 라우팅한다."""
        await _broadcast(msg, uid=self.uid)

    async def _research_macro_themes(self, session: str, force: bool = False,
                                     news_digest: str = "", index_digest: str = "") -> str:
        """alibaba/tongyi-deepresearch가 검색·합성을 통합 수행 (search 단계).
        사장 피드백 2026-05-16: **뉴스분석팀장이 짚은 포인트 + 실제 검증 지수**를 검색 쿼리에
        주입해, 정적 질문이 아니라 '오늘 실제로 움직인 것'을 출발점으로 심층 검색하게 한다.
        세션 캐시(30분)지만 뉴스/지수 컨텍스트가 바뀌면 캐시를 무시하고 새로 검색.
        실패 시 빈 문자열 — fail-open (최종 작성은 deepseek/deepseek-v4-flash = macro_analyst)."""
        cache_key = "KR" if session in ("KR_TRADING","KR_PRE_MARKET","KR_CLOSE_REVIEW") else ("US" if session == "US_TRADING" else "OFF")
        # 컨텍스트 시그니처 — 뉴스/지수가 실질적으로 바뀌면 캐시 무효화
        _ctx_sig = str(hash((cache_key, (news_digest or "")[:1500], (index_digest or "")[:600])))
        cached = _research_cache.get(cache_key)
        if (not force and cached and (time.time() - cached.get("ts", 0)) < MACRO_CACHE_TTL_SEC
                and cached.get("value") and cached.get("sig") == _ctx_sig):
            return cached["value"]
        # 세션별 종합 리서치 관점
        if cache_key == "KR":
            _focus = ("1. 외국인 수급 동향 (최근 매도/매수 흐름, 시점, 펀더멘털 변화 vs 리밸런싱 해석)\n"
                      "2. 한국은행 금리 정책 전망 (다음 금통위, 시장 컨센서스, 분기 인하/인상 기대)\n"
                      "3. 코스피·코스닥 투자심리 (밸류에이션 부담·기대, 섹터 로테이션)\n"
                      "4. 원/달러 환율 영향 (수출·수입 비중, 자금 흐름에 미치는 효과)")
        elif cache_key == "US":
            _focus = ("1. 연준(Fed) 통화정책 방향 (FOMC dovish/hawkish 스탠스, 시장 기대치)\n"
                      "2. 미국 노동·물가 지표가 시사하는 매크로 (CPI·고용·임금 trend)\n"
                      "3. S&P/Nasdaq 투자심리·포지셔닝 (섹터 로테이션, 헤지펀드·소매 흐름)\n"
                      "4. 美 국채 금리·달러 강세가 주식 시장에 주는 영향")
        else:  # OFF_HOURS — 글로벌 시각
            _focus = ("1. 글로벌 주식시장 투자심리 (위험선호/회피 사이클)\n"
                      "2. 미중 무역·관세 관계 동향 (협상 진전, 갈등 격화 가능성)\n"
                      "3. 지정학적 리스크 (중동·우크라이나·대만 등 공급망 영향)\n"
                      "4. 원자재·유가 수급 (OPEC 회의, 미국 셰일, 수요 전망)")
        _ctx_block = ""
        if (index_digest or "").strip():
            _ctx_block += f"\n[금일 검증된 지수 움직임 — 실제 수치]\n{index_digest.strip()[:800]}\n"
        if (news_digest or "").strip():
            _ctx_block += f"\n[뉴스분석팀장이 이번 사이클에 짚은 포인트]\n{news_digest.strip()[:1800]}\n"
        query = (
            f"현재 {'한국' if cache_key=='KR' else '미국' if cache_key=='US' else '글로벌'} 증시 시황을 "
            f"종합·심층 분석해 주세요. 다음 4가지 관점:\n{_focus}\n"
            f"{_ctx_block}\n"
            "⚠️ 위 '실제 지수 움직임'과 '뉴스분석팀장이 짚은 포인트'를 **출발점**으로 삼아, "
            "관련된 최신 정책·수급·심리·지정학 동향을 구체적으로 검색해 분석하십시오. "
            "각 관점은 (1) 무엇이 (2) 왜 (3) 어떤 경로로 시장에 영향을 주는지 3~5문장으로 상세히, "
            "가능하면 날짜·기관·발언 주체를 명시. 가격 수치는 부차적 — 정책·심리·구조 해설 위주.")
        result = await deep_research(query, max_tokens=8000, timeout_sec=180)
        if not result:
            return ""  # fail-open
        _research_cache[cache_key] = {"ts": time.time(), "value": result, "sig": _ctx_sig}
        return result

    async def _collect_index_data(self):
        """글로벌 지수 워치리스트 크롤링 (blocking → run_in_executor). Returns the structured dict."""
        self.current_state = SwarmState.DATA_COLLECTION
        await self._emit({"type":"status","state":"DATA_COLLECTION","message":"글로벌 지수 수집 중"})
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, get_index_data)

    async def _collect_company_data(self, codes: List[str]) -> str:
        """전략가가 반환한 종목의 일봉 + 수급 + KIS 분봉 수집.

        Fallback chain (사장 피드백 2026-05-18 — KIS 우선·'데이터 부족' 해소):
          KR 일봉: ① KIS API 페이지네이션(`kr_daily_chart_deep`, ~2년) 먼저 →
                   ② KIS 0행이면 네이버 금융 크롤(`crawl_company_full`) 폴백
          KR 수급: 네이버 크롤(KIS 미제공) — 항상 보강 시도
          US: KIS 시세 + 일봉 (primary) → 실패 시 메시지에 명시"""
        if not codes:
            return "[종목 데이터] 분석 대상 종목 없음"
        await self._emit({"type":"status","state":"DATA_COLLECTION","message":f"종목 {len(codes)}개 데이터 수집 중"})
        loop = asyncio.get_event_loop()
        summaries = []
        empty_us_codes = set()
        for code in codes[:8]:  # cap to avoid rate limits (was 5 — bumped for 6 후보 + 보유 종목)
            if _is_kr_code(code):
                # ── KR 일봉: KIS API 먼저 (페이지네이션으로 ~2년) ──
                kis_rows = 0
                try:
                    kis_rows = len(await self.broker.kr_daily_chart_deep(code, years=2))
                except Exception as e:
                    summaries.append(f"  ⚠ [{code}] KIS 일봉 조회 예외: {e}")
                if kis_rows > 0:
                    csv_daily = _csv_row_count(_Path("data") / f"daily_{code}.csv")
                    summaries.append(f"[{code}] 일봉: KIS +{kis_rows}행 / 누적 {csv_daily}행")
                else:
                    # 사장님 지시 2026-05-19: KIS 신규 0행이면 누적 CSV가 있어도(=stale 가능성)
                    # 네이버 금융(KRX Sim 로직 fetch_stock_daily)으로 최신 봉 top-up — stale 데이터로
                    # 분석하던 문제 차단. (기존엔 csv_daily>0이면 폴백을 아예 안 했음.)
                    _n = 0
                    try:
                        from tools.market_data import fetch_stock_daily as _fsd
                        _ndf = await loop.run_in_executor(None, _fsd, code, 2)
                        _n = len(_ndf) if _ndf is not None else 0
                    except Exception as _e:
                        summaries.append(f"  ⚠ [{code}] 네이버 일봉 폴백 예외: {_e}")
                    csv_daily = _csv_row_count(_Path("data") / f"daily_{code}.csv")
                    summaries.append(f"  🔁 [{code}] KIS 0행 → 네이버 폴백 +{_n}행 / 누적 {csv_daily}행")
                # ── KR 수급(기관·외인): KIS 미제공 → 네이버 크롤로 항상 보강 ──
                try:
                    inv_df = await loop.run_in_executor(None, fetch_investor_data, code)
                    inv_total = _csv_row_count(_Path("data") / f"investor_{code}.csv")
                    summaries.append(f"  [{code}] 수급: 신규 +{len(inv_df) if inv_df is not None else 0} / 누적 {inv_total}행")
                except Exception as e:
                    summaries.append(f"  ⚠ [{code}] 수급 크롤 실패: {e}")
                # KR: KIS 분봉 (장중이면)
                if is_trading_hours():
                    try:
                        minute = await self.broker.kr_minute_chart(code)
                        summaries.append(f"  [{code}] KIS 분봉 {len(minute)}건 수집")
                    except Exception as e:
                        summaries.append(f"  ⚠ [{code}] KIS 분봉 실패: {e}")
            else:
                # US: KIS API로 시세 + 일봉 (사장 지시 2026-05-14 — 미국도 historical 데이터 필요)
                try:
                    price = await self.broker.us_price(code)
                    summaries.append(price)
                except Exception as e:
                    summaries.append(f"[US시세] {code} | ⚠ 조회 예외: {e}")
                try:
                    us_rows = await self.broker.us_daily_chart(code, days=100)
                    if us_rows:
                        summaries.append(f"  📈 [{code}] KIS 미국 일봉 +{len(us_rows)}행 누적")
                    else:
                        summaries.append(f"  ⚠️ [{code}] KIS US 일봉 수집 실패 (0행)")
                        empty_us_codes.add(code)
                except Exception as e:
                    summaries.append(f"  ⚠ [{code}] 미국 일봉 수집 실패: {e}")
            await asyncio.sleep(0.5)

        # Build formatted quant data — KR과 US 종목 모두 (load_daily_csv가 자동 분기)
        quant_parts = []
        for code in codes[:8]:
            quant_parts.append(format_quant_data_for_agent(code))
            if code in empty_us_codes:
                quant_parts.append(f"⚠️ [{code}] KIS US 일봉 수집 실패 (0행)")
        return "\n".join(summaries + quant_parts)

    async def _prefilter_news(self, articles: List[Dict], limit: Optional[int] = None) -> List[Dict]:
        """헤드라인이 너무 많으면 굵직한 `limit` 건만 추려낸다.
        사장 피드백 2026-05-15 (#22): LLM 응답 파싱이 자주 실패하던 이유로 결정론적 키워드 스코어링으로 전환.
        뉴스는 이미 crawl 시점에 KR/US/BOTH로 분류되어 있고 MATERIAL_NEWS_KEYWORDS도 있으니 LLM 호출 없이 정렬 가능."""
        from config import MATERIAL_NEWS_KEYWORDS
        effective_limit = int(limit) if limit and int(limit) > 0 else NEWS_PREFILTER_LIMIT
        if not articles or len(articles) <= max(NEWS_PREFILTER_TRIGGER, effective_limit):
            return articles or []

        def _score(a: Dict) -> int:
            text = (a.get("title", "") or "") + " " + (a.get("summary", "") or "")
            s = 0
            for kw in MATERIAL_NEWS_KEYWORDS:
                if kw in text:
                    s += 2
            # 종목 코드/티커 직접 언급 가중치
            if re.search(r"(?<!\d)\d{6}(?!\d)", text):
                s += 3
            if re.search(r"\b[A-Z]{2,5}\b", text):
                s += 1
            # 너무 짧거나 광고성 헤드라인 페널티
            if len(a.get("title", "") or "") < 12:
                s -= 1
            return s

        # 원래 순서를 보존하면서 점수 내림차순 정렬 → 같은 점수면 최신순 (articles는 최신 last가 일반적이므로 reversed)
        scored = sorted(enumerate(articles), key=lambda kv: (-_score(kv[1]), -kv[0]))
        picked = [a for _, a in scored[:effective_limit]]
        # 원래 시간 순서로 다시 정렬해 뉴스 분석가가 흐름을 잡기 쉽게
        picked.sort(key=lambda a: articles.index(a))
        await self._emit({"type": "agent_msg", "agent": "전략리서치팀장",
            "message": f"🗂️ 누적 헤드라인 {len(articles)}건 → 결정론적 점수로 굵직한 **{len(picked)}건** 선별 (LLM 미호출, 키워드+종목코드 가중치)"})
        return picked

    async def _collect_per_stock_dart(self, codes: List[str], days: int = 90, limit: int = 8):
        """Per-stock 최근 공시(~90일) + **가장 최근** 분기/반기/연간 요약재무.
        사장 피드백 2026-05-18: 공시 윈도우를 14→90일로 넓혀 관리종목·증자·소송 등을 놓치지 않음.
        ITEM2a: DartResult.state 를 사람-읽기 텍스트로 접두사 표기 — 리스크관리실장이
                QUERY_FAILED(시스템 리스크 주의)와 NO_DISCLOSURE(특이사항 없음)를 명확히 구분.
        Returns (dart_dict, name_map). Best-effort — skips a code on any error."""
        out: Dict[str, str] = {}; names: Dict[str, str] = {}
        loop = asyncio.get_event_loop()
        for code in [c for c in (codes or []) if _is_kr_code(c)][:limit]:
            try:
                nm = await loop.run_in_executor(None, get_stock_name, code)
                if nm:
                    names[code] = nm
                    # 최근 공시 (기존 동작) — DartResult 반환
                    dd_result = await search_disclosures(corp_name=nm, bgn_de=(datetime.now() - timedelta(days=days)).strftime("%Y%m%d"))
                    # 사장 피드백 #7: 직전연도 요약재무상태표 + 손익계산서 추가 — DartResult 반환
                    fin_result = await get_financial_summary_by_stock_code(code)

                    # QUERY_FAILED 이면 리스크관리실장이 알 수 있도록 명시적 경고 접두사 삽입
                    dd_text = dd_result.risk_text() if dd_result.failed else str(dd_result)
                    fin_text = fin_result.risk_text() if fin_result.failed else str(fin_result)

                    sections = [f"{nm}({code})", dd_text]
                    if fin_text:
                        sections.append("")
                        sections.append(fin_text)
                    out[code] = "\n".join(sections)
            except Exception as _e:
                logger.warning(f"per-stock DART {code} 실패: {_e}")
        return out, names

    # markets whose *open* should force an immediate cycle off the accumulated news
    _MARKET_OPEN_SESSIONS = ("KR_PRE_MARKET", "US_TRADING")
    _LIVE_SESSIONS = ("KR_PRE_MARKET", "KR_TRADING", "US_TRADING")

    async def _set_status(self, state: str, message: str, *, force: bool = False):
        """Broadcast a status update only when the state actually changes (or force=True).
        Prevents the every-60s OFF_HOURS spam in the dashboard log."""
        if not force and state == self._last_status_state:
            return
        self._last_status_state = state
        try:
            self.current_state = SwarmState(state)
        except ValueError:
            pass
        await self._emit({"type": "status", "state": state, "message": message})

    async def _equity_poller(self):
        """Poll account balance every 5 min during market hours so equity_curve has dense points
        between analysis cycles. 사장 지시 2026-05-24: 장 운영시간(KR/US 정규장) 외에는 기록하지 않는다
        — 장외 평가금액 추이는 의미가 없어 포인트를 남기지 않는다."""
        while not self._stop_event.is_set():
            try:
                # 사장 지시 2026-05-24: 장 운영시간(KR/US 정규장) 외에는 평가금액 추이를 기록하지 않는다
                # — 장외엔 잔고 폴링·기록 모두 생략(불필요한 KIS 호출도 절약).
                # 사장 지시 2026-05-21: 자산곡선을 KR+US 통합 총평가로 — portfolio_holdings 가
                # 국내 nass_amt 에 해외주식 원화환산 평가를 더한 총평가를 돌려준다.
                snap = await self.broker.portfolio_holdings() if is_market_session_now() else None
                bp = (snap or {}).get("buying_power") or {}
                if snap is not None and bp.get("ok"):
                    # 사장 지시 2026-05-21: 잔고 확인과 동시에 KOSPI·NASDAQ(QQQ) 현재값을 수집 →
                    # equity 포인트와 같은 타임스탬프에 저장(벤치마크 일중 오버레이, 검증된 현재가 API만 사용).
                    kospi = nasdaq = None
                    try: kospi = await self.broker.kr_index_now("0001") or None
                    except Exception: kospi = None
                    try:
                        _q = await self.broker.us_last_price("QQQ")
                        nasdaq = _q if (_q and _q > 0) else None
                    except Exception: nasdaq = None
                    record_equity(self.equity_path, bp, "poll", holdings=snap.get("holdings") or [],
                                  kospi=kospi, nasdaq=nasdaq)
            except Exception as e:
                logger.warning(f"[equity_poller] {e}")
            # 5분 슬립 — 60초 게이트와 결합해 잔고가 자주 안 흔들리는 한 가벼움
            for _ in range(300):
                if self._stop_event.is_set(): break
                await asyncio.sleep(1)

    async def _weekly_review_scheduler(self):
        """매 시간마다 토요일 06시 이후인지 체크 → 트리거 (사장 지시 2026-05-14).
        marker 파일로 중복 실행 방지 — 한 주에 한 번만 운용지원실장 호출."""
        from infra import weekly_review
        while not self._stop_event.is_set():
            try:
                _wk_msg = weekly_review.trigger_if_due(uid=self.uid, is_admin=self.is_admin)
                if _wk_msg:
                    await self._emit({"type": "agent_msg", "agent": "시스템",
                        "message": _wk_msg if isinstance(_wk_msg, str) else
                        "📅 [주간 피드백 루프] 지난 7일 운영을 점검했습니다."})
            except Exception as e:
                logger.warning(f"[weekly_review_scheduler] {e}")
            # 1시간마다 체크
            for _ in range(3600):
                if self._stop_event.is_set(): break
                await asyncio.sleep(1)

    async def start_continuous(self, user_directive: Optional[str] = None):
        self._stop_event.clear(); self.news_monitor.is_running = True
        self._last_cycle_at = time.time()        # hourly periodic trigger fires 1h after start, not immediately
        self._last_session = get_current_session()
        self._last_status_state = None
        first_run_pending = True                  # ▶ 실행 직후 1회는 누적 뉴스로 즉시 사이클 (장중일 때)
        # Equity poller — keeps the dashboard 수익률 chart populated even outside cycles
        try: asyncio.create_task(self._equity_poller())
        except Exception: pass
        # 사장 지시 2026-05-14: 주간 피드백 스케줄러 — 토요일 KST에 운용지원실장 자동 호출
        try: asyncio.create_task(self._weekly_review_scheduler())
        except Exception: pass
        if self._last_session == "OFF_HOURS":
            await self._set_status("OFF_HOURS", f"장외 ({_now_kst().strftime('%H:%M')} KST) — 뉴스 수집만, 다음 개장 시 사이클", force=True)
        else:
            await self._set_status("MONITORING", "연속 감시 시작 — 즉시 1회 + 이후 1시간마다 + 장 개장 시 사이클", force=True)
        while not self._stop_event.is_set():
            try:
                session = get_current_session()
                # 사장 지시 2026-05-21: 장 마감 전환 시 당일·누적 수익률 모바일 알림 (전환 순간 1회)
                try:
                    await self._maybe_market_close_alert(self._last_session, session)
                except Exception as _mce:
                    logger.warning(f"market_close 알림 실패: {_mce}")
                # status badge: only emit on transition (no 1-minute 장외 spam)
                if session == "OFF_HOURS":
                    await self._set_status("OFF_HOURS", f"장외 ({_now_kst().strftime('%H:%M')} KST)")
                else:
                    await self._set_status("MONITORING", "감시 중")

                # crawl + accumulate news (dedup near-identical headlines); log only on real new items.
                # Split by market: KR articles → _pending_news_kr, US → _pending_news_us,
                # BOTH (global / uncertain) → mirrored into both so neither session loses it.
                new_articles = self.news_monitor.crawl_once()
                # 사장 피드백 2026-05-15 (4차): 키워드 분류 부정확 → tencent/hy3-preview LLM 배치 재분류.
                # 새 헤드라인만 모아 한 번에 LLM 호출, 결과로 article["market"] 갱신.
                if new_articles:
                    try:
                        from tools.news_monitor import llm_classify_articles
                        _link2mkt = await llm_classify_articles(new_articles)
                        if _link2mkt:
                            for a in new_articles:
                                _mk = _link2mkt.get(a.get("link"))
                                if _mk in ("KR", "US", "BOTH"):
                                    a["market"] = _mk
                            # in-memory history도 동일하게 동기화 (대시보드 뉴스 탭 표시 일관성)
                            try:
                                self.news_monitor.reclassify_in_history(_link2mkt)
                            except Exception: pass
                            logger.info(f"📰 LLM 재분류 완료 — {len(_link2mkt)}건 (model=tencent/hy3-preview)")
                    except Exception as _ce:
                        logger.warning(f"📰 LLM 분류 실패 → 키워드 분류 유지: {_ce}")
                if new_articles:
                    existing_kr = [a.get("title", "") for a in self._pending_news_kr]
                    existing_us = [a.get("title", "") for a in self._pending_news_us]
                    added_kr = []; added_us = []
                    for a in new_articles:
                        title = a.get("title", "")
                        mk = (a.get("market") or "BOTH").upper()
                        if mk in ("KR", "BOTH") and not _is_dup_title(title, existing_kr):
                            self._pending_news_kr.append(a); existing_kr.append(title); added_kr.append(a)
                        if mk in ("US", "BOTH") and not _is_dup_title(title, existing_us):
                            self._pending_news_us.append(a); existing_us.append(title); added_us.append(a)
                    if added_kr or added_us:
                        # union count of unique titles added this round (BOTH counted once)
                        seen_titles = set()
                        unique_added = []
                        for a in (added_kr + added_us):
                            t = a.get("title", "")
                            if t and t not in seen_titles:
                                seen_titles.add(t); unique_added.append(a)
                        await self._emit({"type": "news", "count": len(unique_added), "ts": self.news_monitor.last_crawl_time,
                            "message": (f"📰 +{len(unique_added)}건 (KR 누적 {len(self._pending_news_kr)} / "
                                        f"US 누적 {len(self._pending_news_us)}) | 크롤 {self.news_monitor.last_crawl_time or ''}"),
                            "articles": [{"title": a.get("title", ""), "market": a.get("market", "BOTH"),
                                          "link": a.get("link", "")} for a in unique_added[:5]]})

                # ── cycle trigger: (a) ▶ 실행 직후 첫 회, (b) a market just opened, or
                #                  (c) ≥ PERIODIC_CYCLE_SEC since last cycle — 모두 '장중일 때'만 ──
                first_run = first_run_pending
                market_open = (session in self._MARKET_OPEN_SESSIONS) and (self._last_session not in self._LIVE_SESSIONS)
                periodic_due = (time.time() - self._last_cycle_at) >= PERIODIC_CYCLE_SEC

                # ── 사장 지시 2026-05-14/2026-05-24: 사이클 사전 게이트 ──
                # (1) 휴장 — 주말은 자명 스킵, 그 외엔 개장 5분 후 KIS 실시세로 '오늘 개장'을 1회 확인
                #     (하드코딩 휴장일 목록은 폴백). → _market_closed_today
                # (2) cash 부족 — 가용 예수금이 최소 매매 단위(현실적 최저가 1주 ~5000원)도 안 되면 스킵
                # (3) 너무 잦은 사이클 방지 — 직전 사이클이 5분 이내라면 스킵 (트리거 중복 가드)
                skip_reason = None
                if first_run or market_open or periodic_due:
                    if (time.time() - self._last_cycle_at) < 300 and not first_run:
                        skip_reason = f"직전 사이클이 {int((time.time()-self._last_cycle_at)/60)}분 전 — 트리거 중복 스킵"
                    else:
                        _closed, _closed_reason = await self._market_closed_today(session)
                        if _closed:
                            skip_reason = _closed_reason
                        else:
                            # cash 가용성 체크 — 한 번의 KIS 호출. 조회 실패(ok=False)는
                            # '돈 없음'이 아니라 '데이터 미상'이므로 스킵하지 않고 진행한다.
                            try:
                                _snap = await self.broker.kr_account_snapshot()
                                skip_reason = _low_cash_skip_reason(_snap)
                            except Exception:
                                pass  # 잔고 조회 실패는 진행 (broker가 사이클 안에서 재시도)
                if skip_reason and (first_run or market_open or periodic_due):
                    await self._set_status("MONITORING", f"⏭ 사이클 사전 게이트: {skip_reason}", force=True)
                    self._last_cycle_at = time.time()  # 트리거 재무장 방지 — 다음 1시간 후 재시도
                    first_run_pending = False
                    self._last_session = session
                    for _ in range(admin_config.news_crawl_interval(NEWS_CHECK_INTERVAL)):
                        if self._stop_event.is_set(): break
                        await asyncio.sleep(1)
                    continue

                if (first_run or market_open or periodic_due) and (is_trading_hours() or is_pre_market()):
                    first_run_pending = False
                    # Pick the news pool that matches the active session:
                    # US_TRADING → US news (KR news 누적 유지); KR_PRE_MARKET/KR_TRADING → KR news (US 누적 유지).
                    if session == "US_TRADING":
                        pool, pool_label = self._pending_news_us, "US"
                        accumulated_other = len(self._pending_news_kr)
                    else:
                        pool, pool_label = self._pending_news_kr, "KR"
                        accumulated_other = len(self._pending_news_us)
                    if first_run:
                        await self._set_status("MONITORING",
                            f"▶ 실행 — {pool_label} 뉴스 {len(pool)}건 기반 첫 사이클 (반대편 누적 {accumulated_other})", force=True)
                    elif market_open:
                        await self._set_status("MONITORING",
                            f"📈 {session} 개장 — {pool_label} 뉴스 {len(pool)}건 기반 사이클 (반대편 누적 {accumulated_other})", force=True)
                    else:
                        await self._set_status("MONITORING", f"⏱️ 1시간 정기 사이클 — {pool_label} 뉴스 {len(pool)}건", force=True)
                    cycle_news = list(pool); pool.clear()
                    with metrics.timer("analysis_cycle", session=str(session),
                                       market_open=bool(market_open)):
                        await self._run_analysis_cycle(cycle_news, user_directive,
                                                       session, market_open=bool(market_open))
                    self._last_cycle_at = time.time()
                    # 쿨다운 없음 — 사이클 끝나면 곧장 감시 상태로 복귀 (배지 고착 방지를 위해 명시적 브로드캐스트)
                    if not self._stop_event.is_set():
                        session = get_current_session()
                        if session == "OFF_HOURS":
                            await self._set_status("OFF_HOURS", f"장외 ({_now_kst().strftime('%H:%M')} KST) — 감시 재개", force=True)
                        else:
                            await self._set_status("MONITORING", "사이클 완료 — 감시 재개 (다음 사이클 1시간 뒤)", force=True)

                self._last_session = session
                for _ in range(admin_config.news_crawl_interval(NEWS_CHECK_INTERVAL)):
                    if self._stop_event.is_set(): break
                    await asyncio.sleep(1)
            except Exception as e:
                self.current_state = SwarmState.ERROR; logger.error(f"루프 오류: {e}")
                await self._emit({"type": "error", "message": str(e)})
                # 감시 루프 크래시는 '거래 중'인 줄 알고 멈추는 최악의 조용한 실패.
                metrics.incr("engine_loop_error")
                notifier.alert("CRITICAL", "감시 루프 오류 — 30초 후 자동 재시도",
                               str(e), dedup_key="engine_loop_error")
                await asyncio.sleep(30)
        self.current_state = SwarmState.STOPPED; self.news_monitor.is_running = False
        await self._emit({"type": "status", "state": "STOPPED", "message": "감시 중지됨"})

    async def _session_holdings(self, session: str) -> List[Dict]:
        """매도 평가용 보유종목. US 세션엔 해외 보유분을 합쳐야 미국 포지션이 사후관리실장
        매도 평가에 들어온다. (버그 2026-05-22: kr_holdings()만 써서 US_TRADING 사이클에
        미국 보유분이 통째로 누락 → 항상 "보유 종목 없음 — 분석 생략"으로 빠졌다.)
        _overseas_holdings()는 내부에서 예외를 삼키고 []를 반환하므로 KR 보유분은 보존된다."""
        holdings = await self.broker.kr_holdings()
        if session == "US_TRADING":
            holdings = holdings + await self.broker._overseas_holdings()
        return holdings

    async def _run_analysis_cycle(self, news_articles, user_directive, session, market_open: bool = False):
        # 리팩터링 2026-05-27: 거대 단일 메서드를 단계별 헬퍼(_cyc_stage_*)로 분리.
        # 본문은 최상위 early-return 없는 선형 시퀀스이므로, 단계 간 공유 지역변수를
        # `cyc`(SimpleNamespace)에 담아 순서대로 호출만 한다 — 로직·순서·동작 1바이트 불변.
        cyc = types.SimpleNamespace()
        cyc.news_articles = news_articles
        cyc.user_directive = user_directive
        cyc.session = session
        cyc.market_open = market_open
        self.cycle_log = SwarmCycleLog(); self.validation_attempts = 0
        # 누적 헤드라인이 NEWS_PREFILTER_TRIGGER(기본 40)을 넘으면 큐레이터로 굵직한 N건만 선별.
        # 개장(market_open) 사이클은 N을 100으로 상향 — 누적된 종일치 뉴스를 폭넓게 흡수.
        prefilter_limit = 100 if market_open else None  # None → config 기본(NEWS_PREFILTER_LIMIT=40)
        cyc.news_articles = await self._prefilter_news(cyc.news_articles, limit=prefilter_limit)
        cyc.formatted_news = self.news_monitor.format_articles_for_agent(cyc.news_articles)
        # ITEM6: 활성 계정의 상시 지시사항 로드 — 운용전략실장 프롬프트에 주입 (계정 격리)
        _active_uid, _ = self._active_actor()
        try:
            from infra.standing_directives import build_orchestrator_directive_block
            cyc.standing_directive_block = build_orchestrator_directive_block(_active_uid)
        except Exception as _sde:
            logger.warning("상시지시 로드 실패(uid=%s): %s — 사이클 계속", _active_uid, _sde)
            cyc.standing_directive_block = ""
        try:
            await self._cyc_stage_collect(cyc)
            await self._cyc_stage_news_macro(cyc)
            await self._cyc_stage_select(cyc)
            await self._cyc_stage_data_quant(cyc)
            await self._cyc_stage_finalize_sell(cyc)
            await self._cyc_stage_build_orders(cyc)
            await self._cyc_stage_risk(cyc)
            await self._cyc_stage_execute(cyc)
            await self._cyc_stage_report(cyc)
        except Exception as e:
            self.current_state = SwarmState.ERROR; logger.error(f"사이클 오류: {e}")
            if self.cycle_log: self.cycle_log.log("ERROR","시스템",str(e)); self._cycle_history.append(self.cycle_log.to_dict())
            try:
                cycle_store.record_cycle({
                    "uid": self.uid,
                    "started_at": self.cycle_log.started_at if self.cycle_log else _now_kst().strftime("%Y-%m-%d %H:%M:%S"),
                    "ended_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
                    "session": session, "market_open": market_open,
                    "news_count": len(cyc.news_articles) if cyc.news_articles else 0,
                    "error": str(e)[:2000],
                })
            except Exception: pass

    async def _cyc_stage_collect(self, cyc):
            session = cyc.session
            # [1] GLOBAL INDEX DATA (validated numbers only — no garbage)
            index_data = await self._collect_index_data()
            index_report = crawl_index_snapshot(index_data)
            index_facts = format_indices_for_macro(index_data)
            # 사장 피드백 2026-05-15 (4차): 글로벌 지수 수집 결과는 전략리서치팀장 이름으로 표기 (이름만, 결과는 그대로 수집된 데이터).
            self.cycle_log.log("DATA", "전략리서치팀장", index_report)
            await self._emit({"type":"agent_msg","agent":"전략리서치팀장","message":f"📈 지수 수집 완료\n{index_report}"})

            # current holdings — used both to diversify the orchestrator's picks and for fill confirmation
            holdings = []
            try:
                holdings = await self._session_holdings(session)
            except Exception as _he:
                # 클린코드 2026-05-19: 침묵 삼킴 제거 — 빈 holdings로 진행하면 보유 종목
                # 회피·체결확인이 불가하므로 최소한 원인을 남긴다.
                record_error("_collect_company_data", _he, context="세션 보유종목 조회 실패 → 빈 목록 진행", uid=self.uid)
            holdings_str = ("; ".join(f"{h['name']}({h['code']}) {h['qty']}주 손익 {h['pnl_pct']:+.1f}%" for h in holdings) or "없음")

            # [2] DART — 사장 피드백 2026-05-15 (#20): DART는 국내 종목만 있으니 KR 장 시간에만 30분 간격으로 가동.
            # US_TRADING/OFF_HOURS 사이클은 DART 호출 자체를 생략.
            _is_kr_session = session in ("KR_TRADING", "KR_PRE_MARKET", "KR_CLOSE_REVIEW")
            _dart_ttl = 30 * 60 if _is_kr_session else DART_CACHE_TTL_SEC  # KR 세션은 30분, 그 외는 캐시 유지
            if not _is_kr_session:
                dart_report = "[DART] US/장외 세션 — 국내 공시 조회 생략"
                # 사장 피드백 2026-05-16: 장외 DART 생략은 정상 동작이므로 대시보드에 알리지 않음 (내부 처리만).
            elif (time.time() - _dart_cache["ts"]) < _dart_ttl and _dart_cache["value"]:
                dart_report = _dart_cache["value"]
                await self._emit({"type":"agent_msg","agent":"시스템","message":f"♻️ DART 공시 캐시 재사용 ({int((time.time()-_dart_cache['ts'])/60)}분 전) — API 호출 생략"})
            else:
                _dr = await search_disclosures(bgn_de=(datetime.now()-timedelta(days=3)).strftime("%Y%m%d"))
                # ITEM2a: QUERY_FAILED는 시스템 리스크 경고, NO_DISCLOSURE는 정상으로 구분
                dart_report = _dr.risk_text() if _dr.failed else str(_dr)
                # QUERY_FAILED 응답은 캐시하지 않음 (다음 사이클에서 재시도)
                if not _dr.failed:
                    _dart_cache.update(ts=time.time(), value=dart_report)
            self.cycle_log.log("DATA", "시스템", dart_report)
            cyc.index_report = index_report
            cyc.index_facts = index_facts
            cyc.holdings = holdings
            cyc.holdings_str = holdings_str
            cyc.dart_report = dart_report

    async def _cyc_stage_news_macro(self, cyc):
            news_articles = cyc.news_articles
            market_open = cyc.market_open
            session = cyc.session
            formatted_news = cyc.formatted_news
            index_facts = cyc.index_facts
            dart_report = cyc.dart_report
            # [3] NEWS ANALYSIS first — macro now reflects the analyzed news (사장 지시 2026-05-14).
            # 개장 사이클은 뉴스 100건이 들어오므로 macro/orchestrator가 모두 그것을 흡수해야 함.
            # 사장 지시 2026-05-22: 신규 뉴스 0건(정기 사이클)이면 뉴스분석팀장 호출 자체를 생략하고
            # 보유 종목 매도 평가(계량분석)만 진행한다 — 뉴스가 없는데 뉴스분석 LLM을 부르는 건 무의미.
            _sell_only = (not news_articles) and (not market_open)
            self.current_state = SwarmState.NEWS_ANALYSIS
            await self._emit({"type":"status","state":"NEWS_ANALYSIS","message":"뉴스 분석 (증권 속보)"})
            _session_market_label = "미국 장" if session == "US_TRADING" else "국내 장"
            if _sell_only:
                news_report = "[뉴스분석팀장] 신규 뉴스 없음 — 뉴스 분석 생략. 이번 사이클은 보유 종목 매도 평가(계량분석)만 진행합니다."
                self.cycle_log.log("NEWS", "뉴스분석팀장", news_report)
                await self._emit({"type":"agent_msg","agent":"뉴스분석팀장",
                    "message":"🔕 신규 뉴스 없음 — 뉴스 분석 생략, 보유 종목 매도 평가(계량분석)만 진행합니다."})
            else:
                news_report = await self.news_analyst.think(
                    f"네이버 금융 '증권 속보' 크롤링 결과입니다 (이번 사이클 누적 {len(news_articles)}건, **{_session_market_label}** 대상):\n{formatted_news}\n\n"
                    f"이 뉴스들을 분석해 다음을 정리하십시오:\n"
                    f"① 직접 언급되거나 직접 영향을 받는 **종목/업종**과 각각의 호재·악재·이벤트 — 가능하면 종목명(또는 6자리 코드)을 함께 적되, "
                    f"뉴스에 실제로 나온 것만 쓰고 모르는 코드는 지어내지 마십시오.\n"
                    f"② 시장 전반 분위기·주목 테마 (1~3줄).\n"
                    f"③ 매크로(금리/환율/원자재/지정학) 시사점 — 전략리서치팀장 매크로 분석에 영감을 줄 수 있는 포인트 1~3개.\n"
                    f"이 분석은 전략리서치팀장 매크로 분석 및 운용전략실장 종목 선정에 최우선으로 반영됩니다.")
                self.cycle_log.log("NEWS", "뉴스분석팀장", news_report)
                await self._emit({"type":"agent_msg","agent":"뉴스분석팀장","message":news_report})

            # [4] MACRO ANALYSIS — cached up to MACRO_CACHE_TTL_SEC. 개장 사이클(market_open)에선 캐시 무시
            # (방금 분석한 뉴스/지수를 반드시 흡수해야 하므로 새로 호출).
            self.current_state = SwarmState.MACRO_ANALYSIS
            await self._emit({"type":"status","state":"MACRO_ANALYSIS","message":"매크로 분석"})
            _idle = cycle_is_idle(_sell_only, getattr(cyc, "holdings", None))
            _use_macro_cache = (not market_open) and (time.time() - _macro_cache["ts"]) < MACRO_CACHE_TTL_SEC and _macro_cache["value"]
            if _idle:
                # D (사장 지시 2026-05-28): 보유0 + 신규뉴스0 = 매수·매도 둘 다 불가 → 매크로 리서치/LLM 생략(비용 절감).
                # (아래 macro_report 후처리 else 분기가 cycle_log+emit 를 담당 — 캐시 재사용 분기와 동형.)
                macro_report = "[전략리서치팀장] 보유·신규뉴스 없음 — 매수·매도 대상이 없어 분석을 생략합니다(LLM 비용 절감)."
                metrics.incr("cycle_idle_skip")
            elif _use_macro_cache:
                macro_report = _macro_cache["value"]
                await self._emit({"type":"agent_msg","agent":"시스템","message":f"♻️ 매크로 분석 캐시 재사용 ({int((time.time()-_macro_cache['ts'])/60)}분 전 생성) — 전략리서치팀장 LLM 호출 생략"})
            else:
                _cache_hint = ("⚡ 개장 사이클 — 캐시 무시, 누적 뉴스 100건 분석 결과를 반영합니다.\n" if market_open else "")
                # 사장 피드백 2026-05-15 (8차): alibaba/tongyi-deepresearch로 매크로 종합 리서치 (Tavily 대체).
                # 30분 캐시 + 세션별 종합 쿼리. 실패해도 매크로 분석은 진행 (fail-open).
                _macro_research = ""
                try:
                    # 사장 피드백 2026-05-16: 리서치 진행/완료 알림은 내부 단계라 대시보드 미표시.
                    # (최종 매크로 환경 요약만 전략리서치팀장 메시지로 노출됨)
                    _macro_research = await self._research_macro_themes(
                        session, force=market_open,
                        news_digest=news_report, index_digest=index_facts)
                except Exception as _te:
                    logger.warning(f"매크로 리서치 실패: {_te}")
                # 사장 피드백 2026-05-15 (#18): 직전 사이클의 자산 배분 권고를 명시적으로 첨부.
                # cycle_store에서 가장 최근 매크로 리포트를 가져와 핵심(자산 배분 권고 라인)만 추출.
                _prev_macro_hint = ""
                try:
                    _recent_cycles = cycle_store.list_cycles(limit=3)
                    for _rc in _recent_cycles:
                        _prev = (_rc.get("macro_report") or "").strip()
                        if _prev and len(_prev) > 40:
                            # 자산 배분 권고 줄만 추출 (핵심 비중 %)
                            _alloc_lines = []
                            for _ln in _prev.splitlines():
                                if ("주식" in _ln or "채권" in _ln or "현금" in _ln) and "%" in _ln:
                                    _alloc_lines.append(_ln.strip())
                                if len(_alloc_lines) >= 5:
                                    break
                            if _alloc_lines:
                                _prev_macro_hint = ("\n[직전 사이클 자산 배분 권고 — 사장 피드백 #18: 이 권고와 다르게 가려면 'X% → Y%'로 변경 폭 명시]\n"
                                                    + "\n".join(_alloc_lines[:5]) + "\n")
                                break
                except Exception as _pme:
                    logger.warning(f"직전 매크로 권고 로드 실패: {_pme}")
                _research_section = (
                    f"\n[매크로 종합 리서치 — alibaba/tongyi-deepresearch, 세션 {session}]\n"
                    f"⚠️ 아래는 **시황 해설·정책 전망·투자심리·수급 분석**용 합성 정보입니다. "
                    f"**가격·지수 수치는 절대 인용하지 마십시오** — 가격은 위 '검증된 글로벌 지수'(네이버 크롤)만 신뢰합니다.\n\n"
                    f"{_macro_research}\n"
                    if _macro_research else "")
                macro_report = await self.macro_analyst.think(
                    f"{_cache_hint}{index_facts}\n{_prev_macro_hint}"
                    f"{_research_section}\n"
                    f"뉴스분석팀장 뉴스 분석 (감성·이벤트 정리, 본 사이클 {len(news_articles)}건 기반):\n{news_report}\n\n"
                    f"최신 공시:\n{dart_report}\n\n세션: {session}.\n"
                    f"위 정보를 종합하여 매크로 분석과 자산배분 가이드라인을 제시하십시오. **가격 데이터 우선순위**:\n"
                    f"  1순위 (수치): '검증된 글로벌 지수' (네이버 크롤) — 모든 가격·% 인용은 여기서만\n"
                    f"  2순위 (해설): 매크로 리서치 (alibaba)의 시황·정책·수급·심리 분석 — 가격은 인용 금지, 해설만 활용\n"
                    f"  3순위 (이벤트): 뉴스분석팀장 분석 — 감성·이벤트 흐름\n"
                    f"⚠️ 매크로 리서치 결과에 가격·지수 수치가 있어도 **출처를 알 수 없으므로 인용 금지**. "
                    f"가격이 필요하면 위 검증된 지수 표에서만 가져오십시오.\n"
                    f"⚠️ 리서치 답변에서 *해설·정책 방향·투자심리·수급 흐름·지정학적 영향*은 적극 활용·인용 OK.\n"
                    f"⚠️ 뉴스에서 짚은 매크로 시사점(금리/환율/원자재/지정학)을 반드시 매크로 결론에 통합하십시오.\n"
                    f"⚠️ 직전 권고와 다른 비중을 권고할 때는 반드시 정확한 변경 폭을 표기하십시오.\n"
                    f"⚠️ 사장 피드백 2026-05-16: **상세히** 작성하십시오 — 매크로 환경 요약은 핵심 동인별로 "
                    f"(무엇이/왜/시장 영향 경로) 풀어쓰고, 자산 배분 권고는 주식·채권·현금 각각에 대해 "
                    f"근거 1~2줄씩, 핵심 리스크는 3개 이상 + 각 리스크의 트리거/모니터링 포인트를 함께 적으십시오. "
                    f"단, 가격·지수 수치 인용 규칙(검증 지수만)은 그대로 지키고 표/마크다운 강조는 쓰지 마십시오.\n"
                    f"표에 없는 수치는 추정·생성 금지.")
                # 버그 수정 2026-05-19: 빈/실패(예: OpenRouter 401) 응답이 캐시되면
                # MACRO_CACHE_TTL(30분) 동안 모든 사이클이 그 빈 값을 '캐시 재사용'해
                # 전략리서치팀장이 계속 '매크로 분석 실패'만 반복했다.
                # → 유효 응답(비어있지 않고 40자 이상)일 때만 캐시한다.
                if (macro_report or "").strip() and len((macro_report or "").strip()) >= 40:
                    _macro_cache.update(ts=time.time(), value=macro_report)
            # 사장 피드백 2026-05-16 (OPS#21): 매크로 LLM이 빈/너무 짧은 응답을 줄 때
            # 전략리서치팀장 칸이 '아무 말 없이' 비어 운용전략실장이 참조할 게 없던 문제.
            # 빈 응답은 명확히 표시하고, 운용전략실장에는 '매크로 분석 불가' 신호를 전달.
            if not (macro_report or "").strip() or len((macro_report or "").strip()) < 40:
                macro_report = "[전략리서치팀장] ⚠️ 매크로 분석 실패(LLM 빈 응답) — 이번 사이클은 검증 지수·뉴스만으로 판단합니다."
                self.cycle_log.log("MACRO", "전략리서치팀장", macro_report)
                await self._emit({"type":"agent_msg","agent":"전략리서치팀장","message":macro_report})
            else:
                self.cycle_log.log("MACRO", "전략리서치팀장", macro_report)
                await self._emit({"type":"agent_msg","agent":"전략리서치팀장","message":macro_report})

            # 1주 매수 예산 힌트 — 사장 지시 2026-05-14: **총평가액** 기준 (실제 spend는 예수금으로 캡됨)
            _budget_hint = ""
            try:
                _bp0 = (await self.broker.kr_account_snapshot()).get("buying_power", {}) or {}
                _cash0 = float(_bp0.get("cash", 0.0) or 0.0)
                _total0 = float(_bp0.get("total_eval", 0.0) or 0.0) or _cash0
                _por = float(runtime.get("PER_ORDER_BUDGET_RATIO", uid=self.uid) or 0.10)
                _pob = min(_total0 * _por, _cash0) if _cash0 > 0 else 0.0
                if _total0 > 0:
                    _budget_hint = (f"\n참고 — 현재 총평가 {_total0:,.0f}원 / 예수금 {_cash0:,.0f}원, "
                        f"1종목당 1주 매수 예산은 약 {_pob:,.0f}원(총평가의 {_por*100:.0f}%, 예수금으로 캡)입니다. "
                        f"1주 가격이 이 예산을 크게 초과하는 초고가 종목은 사실상 매수가 어려우니 후보에서 빼거나 신중히 고르십시오.")
            except Exception:
                pass
            cyc._sell_only = _sell_only
            cyc.news_report = news_report
            cyc.macro_report = macro_report
            cyc._budget_hint = _budget_hint

    async def _cyc_stage_select(self, cyc):
            session = cyc.session
            market_open = cyc.market_open
            news_articles = cyc.news_articles
            user_directive = cyc.user_directive
            _standing_directive_block = cyc.standing_directive_block
            _sell_only = cyc._sell_only
            macro_report = cyc.macro_report
            news_report = cyc.news_report
            index_facts = cyc.index_facts
            holdings = cyc.holdings
            holdings_str = cyc.holdings_str
            _budget_hint = cyc._budget_hint
            # ── PASS 1: 운용전략실장 → 후보 5종목 (뉴스 우선 + 시총·업종 분산) ────────
            # Session-aware: 사장 지시(2026-05-14) — US 정규장엔 **반드시 미국 티커만**, KR 세션엔 **반드시 국내 코드만**.
            # (반대편 시장 뉴스는 다른 풀에 누적되어 다음 세션에 사용됨.)
            if session == "US_TRADING":
                session_hint = ("⚠️ 현재 세션은 **미국 정규장(US_TRADING)** — 지금 체결 가능한 시장은 미국뿐입니다. "
                    "후보 5개는 **반드시 미국 상장 티커만**, 대형/중형/소형·서로 다른 섹터를 골고루 (메가캡만 금지). "
                    "**국내 6자리 코드는 절대 금지** — 국내 종목은 한국 장 시간에 분석합니다.")
            elif session in ("KR_TRADING", "KR_PRE_MARKET"):
                session_hint = ("현재 세션은 **한국 장(또는 장 시작 전)** — 후보 5개는 **반드시 국내 6자리 종목코드만**, "
                    "대형/중형/소형주·서로 다른 업종을 골고루 (삼성전자·SK하이닉스 같은 메가캡 편중 금지). "
                    "**해외 티커는 절대 금지** — 미국 종목은 미국 장 시간에 분석합니다.")
            else:
                session_hint = ("현재 장외시간 — 다음 개장(한국/미국) 기준으로 후보 5개 (국내 6자리 / 미국 티커, 시총·업종 골고루).")
            if _sell_only:
                # 사장 피드백 2026-05-16: 신규 뉴스 0건 → 신규 매수 후보 선정 자체를 건너뜀.
                allocation = "후보종목: 없음"
                await self._emit({"type":"agent_msg","agent":"운용전략실장",
                    "message":"[후보 종목 선정] 신규 뉴스 없음 — 신규 매수 후보 선정 생략, 보유 종목 매도 평가만 진행합니다."})
            else:
                allocation = await self.orchestrator.think(
                    f"[후보 종목 선정]\n전략리서치팀 매크로 보고:\n{macro_report}\n\n"
                    f"뉴스분석팀장 뉴스 분석 (증권 속보 기반):\n{news_report}\n\n"
                    f"검증된 지수:\n{index_facts}\n\n현재 보유 종목: {holdings_str}\n현재 세션: {session}\n"
                    f"{('사장 지시: '+user_directive if user_directive else '')}\n"
                    f"{_budget_hint}\n\n"
                    f"{session_hint}\n"
                    + (f"\n{_standing_directive_block}\n" if _standing_directive_block else "")
                    + f"분석할 후보 종목 **정확히 5개**를 고르십시오.\n"
                    f"⚠️ **최우선**: 위 뉴스분석팀장이 짚은 종목/업종을 먼저 후보에 넣으십시오. (뉴스 원문은 제공되지 않으니 뉴스분석팀장 분석만 참고) "
                    f"뉴스 기반 적격 종목이 5개에 못 미칠 때만 매크로 판단으로 나머지를 채우십시오.\n"
                    f"⚠️ 각 종목은 반드시 **`종목명(종목코드)`** 형식으로 적으십시오 (예: `삼성전자(005930)`). "
                    f"코드가 확실치 않으면 **종목명만** 정확히 적으면 됩니다 — 시스템이 정확한 코드를 채웁니다. "
                    f"코드를 임의로 지어내지 마십시오(가짜 코드는 종목 자체가 누락됩니다). 미국 종목은 티커(예: AAPL).\n"
                    f"그 외에는 대형주에 치우치지 말고 시가총액·업종을 분산하고, 위 1주 예산 안에서 매수 가능한(또는 근접한) 종목을 우선하며, 이미 보유한 종목은 가급적 제외하십시오.\n"
                    f"⚠️ 응답 마지막 줄은 반드시 `후보종목: 종목명(코드), ...` (5개, 다른 텍스트 없이).")
                allocation = _strip_leading_section_marker(allocation, "[후보 종목 선정]", "[최종 매수 종목 결정]")
                self.cycle_log.log("MACRO", "운용전략실장", allocation)
                await self._emit({"type":"agent_msg","agent":"운용전략실장","message":f"[후보 종목 선정]\n{allocation}"})
            # 사장 지시 2026-05-22: 운용전략실장의 후보 코드 환각을 이름→코드 검색으로 보정.
            candidate_codes = _resolve_candidate_codes(
                allocation, session=session, resolver=resolve_kr_stock_code, name_check=get_stock_name
            ) or _extract_stock_codes(allocation)
            # Hard filter — enforce session/market boundary on candidates (LLM이 가끔 어김).
            # US 세션엔 KR 6자리 코드 제외, KR 세션엔 US 티커 제외. 장외엔 양쪽 모두 허용.
            if session == "US_TRADING":
                candidate_codes = [c for c in candidate_codes if not (_is_kr_code(c))]
            elif session in ("KR_TRADING", "KR_PRE_MARKET"):
                candidate_codes = [c for c in candidate_codes if _is_kr_code(c)]
            candidate_codes = candidate_codes[:5]
            # C (사장 지시 2026-05-28): 후보 해석이 0건인데 뉴스 신호가 있으면 뉴스 괄호표기 종목으로 보강 —
            # '뉴스만 있고 후보 0'으로 사이클이 낭비되는 것 방지(uid2 cycle24 MU/NVDA 무시 사례).
            # 보강 후보도 downstream 퀀트≥6·DART·리스크 게이트를 그대로 통과해야 매수된다.
            if not candidate_codes and not _sell_only:
                _seeded = seed_candidates_from_news(getattr(cyc, "news_report", ""), session)
                if _seeded:
                    candidate_codes = _seeded[:5]
                    metrics.incr("cycle_candidate_seeded_from_news")
                    await self._emit({"type": "agent_msg", "agent": "운용전략실장",
                        "message": f"⚠️ 후보 해석 0건 — 뉴스 신호 종목으로 자동 보강: {', '.join(candidate_codes)} "
                                   f"(퀀트·리스크 게이트 통과 시에만 매수)"})
                else:
                    metrics.incr("cycle_no_candidate")

            # ── 사장 지시 2026-05-14: 후보 사전 필터 — 데이터 수집/퀀트 평가 전에 예산 초과 종목 제거 ──
            # 1주 가격이 예산의 1.5배를 넘으면 어차피 못 사니까 LLM 비용 낭비 차단.
            try:
                _bp_snap = (await self.broker.kr_account_snapshot()).get("buying_power", {}) or {}
                _cash_pre = float(_bp_snap.get("cash", 0.0) or 0.0)
                _total_pre = float(_bp_snap.get("total_eval", 0.0) or 0.0) or _cash_pre
                _krw_usd_pre = get_usdkrw(USDKRW_FALLBACK)  # 사장 지시 2026-05-22: 5분 크롤 라이브 환율(폴백)
                # 사장 결정 2026-05-16: 사전 필터도 최종 게이트와 **동일한 '예수금 기준 1주' 규칙**을 사용.
                # → 비싼 종목을 계량분석 전에 일관되게 제외(또는 통과)해, "왜 퀀트 후에야 빠지지?" 혼선 제거.
                if _cash_pre > 0:
                    kept, dropped = [], []
                    for c in candidate_codes:
                        try:
                            if _is_kr_code(c):
                                px = await self.broker.kr_last_price(c)
                                await asyncio.sleep(0.1)
                                if px <= 0 or _affordable_one_share(px, _cash_pre, _total_pre):
                                    kept.append(c)  # 가격 조회 실패는 일단 통과
                                else:
                                    dropped.append(f"{c}({px:,.0f}원)")
                            else:
                                px = await self.broker.us_last_price(c.upper())
                                await asyncio.sleep(0.1)
                                if px <= 0 or _affordable_one_share(px, _cash_pre / _krw_usd_pre, _total_pre / _krw_usd_pre):
                                    kept.append(c)
                                else:
                                    dropped.append(f"{c.upper()}(${px:,.2f})")
                        except Exception:
                            kept.append(c)
                    if dropped:
                        # 사장 피드백 2026-05-15 (4차): 사전 필터 결과를 운용전략실장 후보 선정 메시지의 연장으로 통합.
                        await self._emit({"type":"agent_msg","agent":"운용전략실장",
                            "message": (f"후보 사전 필터 — 1주 가격이 예수금({_cash_pre:,.0f}원)으로 매수 불가라 제외: {', '.join(dropped)}\n"
                                        f"최종 후보 종목: {', '.join(kept) or '없음'}")})
                        candidate_codes = kept
            except Exception as _e:
                logger.warning(f"후보 사전 필터 실패: {_e}")

            # 분석 대상 = 후보 ∪ 보유(KR) — 보유분은 사후관리실장의 매도 판단을 위해 함께 분석
            held_session = _codes_for_session(holdings, session)
            analysis_codes, _seen = [], set()
            for c in (candidate_codes + held_session):
                if c and c not in _seen:
                    _seen.add(c); analysis_codes.append(c)
            analysis_codes = analysis_codes[:8]
            cyc.candidate_codes = candidate_codes
            cyc.held_session = held_session
            cyc.analysis_codes = analysis_codes

    async def _cyc_stage_data_quant(self, cyc):
            session = cyc.session
            market_open = cyc.market_open
            news_report = cyc.news_report
            candidate_codes = cyc.candidate_codes
            held_session = cyc.held_session
            analysis_codes = cyc.analysis_codes
            # [4] DATA — 3yr daily/supply + 분봉 for analysis_codes, + per-stock DART (+ 종목명 맵)
            company_data = ""; per_dart: Dict[str, str] = {}; name_map: Dict[str, str] = {}
            if analysis_codes:
                await self._emit({"type":"status","state":"DATA_COLLECTION","message":f"종목 {len(analysis_codes)}개 데이터 수집"})
                company_data = await self._collect_company_data(analysis_codes)
                per_dart, name_map = await self._collect_per_stock_dart(analysis_codes, days=90, limit=8)
                if per_dart:
                    company_data += "\n\n[종목별 최근 DART 공시]\n" + "\n".join(f"[{k}]\n{v}" for k, v in per_dart.items())
                # 사장 피드백 2026-05-15 (4차): 데이터 수집 완료는 계량분석팀장 이름으로 (실제 분석 주체).
                self.cycle_log.log("DATA", "계량분석팀장", f"분석 종목: {analysis_codes}")
                await self._emit({"type":"agent_msg","agent":"계량분석팀장","message":f"📊 데이터 수집 완료 — 후보 {len(candidate_codes)}개 / 보유 {len(held_session)}개 (공시 {len(per_dart)}건)"})

                # ── 데이터 없는 종목 자동 제외 가드 (사장 지시 2026-05-14) ──
                # 데이터 수집 후 일봉 누적 0행인 종목은 분석/매수 대상에서 제외 — LLM이 빈 데이터로
                # 0점 평가하는 패턴 차단. 보유 종목은 매도 판단 위해 유지.
                from tools.market_data import load_daily_csv as _load_daily
                held_set = set(held_session)
                no_data_codes = []
                for c in list(candidate_codes):
                    if c in held_set:
                        continue
                    df = _load_daily(c)
                    if df is None or len(df) == 0:
                        no_data_codes.append(c)
                if no_data_codes:
                    _kept = [c for c in candidate_codes if c not in no_data_codes]
                    # 사장 피드백 2026-05-15 (#23, 4차): 상장폐지 의심 종목·일봉 누락 안내를 계량분석팀장 이름으로 통합 1줄로.
                    _delisted_suspects = []
                    for _c in no_data_codes:
                        if _is_kr_code(_c):
                            try:
                                _px = await self.broker.kr_last_price(_c)
                                if _px <= 0:
                                    _delisted_suspects.append(_c)
                            except Exception:
                                _delisted_suspects.append(_c)
                            await asyncio.sleep(0.1)
                    _msg_lines = [f"🚫 일봉 데이터 없는 종목 평가 제외 (LLM 계량평가 생략): {', '.join(no_data_codes)} → 후보 {len(candidate_codes)} → {len(_kept)}개"]
                    if _delisted_suspects:
                        _msg_lines.append(f"⚠️ 상장폐지/거래정지 의심: {', '.join(_delisted_suspects)} (다음 사이클 후보 선정 시 사전 확인 필요)")
                    await self._emit({"type":"agent_msg","agent":"계량분석팀장",
                        "message": "\n".join(_msg_lines)})
                    candidate_codes = _kept
                    # 버그+정책 수정 2026-05-19 (사장님 지시: "데이터 없으면 평가 자체를 안 함"):
                    # 기존엔 candidate_codes만 필터링하고 퀀트 루프는 analysis_codes를
                    # 순회해 데이터 없는 종목(예: BAC)이 그대로 LLM 평가·자의적 채점됐다.
                    # analysis_codes에서도 제거해 LLM 호출 자체를 생략한다.
                    # (보유 종목은 위 가드에서 no_data_codes에 넣지 않으므로 매도 판단은 유지)
                    analysis_codes = [c for c in analysis_codes if c not in no_data_codes]

            def _nm(c: str) -> str:
                c = str(c).strip()
                return f"{c}({name_map[c]})" if c in name_map else c

            # [5] Volume rank
            vol_rank = ""
            try:
                vol_rank = await self.broker.kr_volume_rank()
            except Exception: pass

            # [6] QUANT — 사장 피드백 2026-05-15 (#23): 종목당 1회 호출 → 별개 메시지로 출력.
            # 직전엔 후보+보유 전체를 한 번에 평가해 응답이 토큰 한계에 걸려 글자가 깨지는 일이 잦았음.
            # 시장 분리(#4): US 세션엔 KR 보유종목 분석 생략, KR 세션엔 US 보유종목 분석 생략.
            self.current_state = SwarmState.QUANT_ANALYSIS
            await self._emit({"type":"status","state":"QUANT_ANALYSIS","message":"퀀트 분석 (종목별 평가)"})
            # 후보·보유 종목 라인 — Pass 2 orchestrator 프롬프트에서 사용 (사장 피드백 2026-05-15 — 누락 복구)
            _cand_line = ", ".join(_nm(c) for c in candidate_codes) or "(없음)"
            _held_line = ", ".join(_nm(c) for c in held_session) or "(없음)"
            _quant_codes = list(analysis_codes or [])
            # 세션별 시장 필터 — 반대편 시장 종목은 평가에서 제외
            # 사장 피드백 2026-05-16: 미국장 세션엔 KR 종목을 보유분 포함 **완전 제외**.
            # (KR 주문은 어차피 KR 세션에만 체결되므로 매도 판단도 KR 세션에서 수행)
            if session == "US_TRADING":
                _quant_codes = [c for c in _quant_codes if not (_is_kr_code(c))]
            elif session in ("KR_TRADING", "KR_PRE_MARKET"):
                _quant_codes = [c for c in _quant_codes if _is_kr_code(c)]
            _quant_scores: Dict[str, int] = {}
            _quant_sections: List[str] = []
            _entry_dirs: Dict[str, Dict[str, Any]] = {}  # 사장 피드백 #4: 진입가 directive 저장
            _sell_prices: Dict[str, Dict[str, Any]] = {}  # 사장 지시 2026-05-22: 보유종목 매도가 directive
            for _qcode in _quant_codes:
                _qname = _nm(_qcode)
                _is_held = _qcode in held_session
                _qrole = "보유" if _is_held else "후보"
                # 버그 수정 2026-05-19 (치명적): 마커가 '\n[{code}]'였는데 실제 퀀트 블록은
                # format_quant_data_for_agent가 '[{code} 퀀트 데이터]'로 출력 → 마커가 요약줄
                # '[{code}] 일봉: KIS +N행'에 매칭돼 행수만 전달, 실데이터(OHLCV/지표/수급)는
                # 누락 → 계량분석팀장이 매번 '데이터 부족'으로 블라인드 평가(실매매까지 발생).
                # 실제 출력 헤더 '[{code} 퀀트 데이터]'를 직접 타깃팅한다.
                _stock_section_marker = f"[{_qcode} 퀀트 데이터]"
                _stock_data = ""
                _idx = company_data.find(_stock_section_marker)
                if _idx >= 0:
                    _end = company_data.find("\n[", _idx + len(_stock_section_marker))
                    _stock_data = company_data[_idx:_end] if _end > _idx else company_data[_idx:_idx + 4000]
                _per_dart_str = per_dart.get(_qcode, "")
                # 자동 재시도: 실패/도구호출JSON 응답 시 최대 2회 retry (사장 피드백 2026-05-18)
                _quant_resp = ""
                _base_prompt = (
                    f"[종목 단독 평가 — {_qrole}] {_qname}\n"
                    f"현재 세션: {session} / 최대 매수 종목 수 (참고): {runtime.get('MAX_TRADES_PER_CYCLE', uid=self.uid)}\n\n"
                    f"뉴스분석팀장 분석 발췌:\n{news_report[:1200]}\n\n"
                    f"종목 데이터:\n{_stock_data or '(데이터 부족)'}\n\n"
                    f"최근 DART 공시:\n{_per_dart_str or '(공시 없음)'}\n\n"
                    f"위 데이터를 **여러 계량 기법으로 평가**하고, 매수 적합도 0~10점 + 한 줄 코멘트로 응답하십시오. "
                    f"마지막 줄은 반드시 `퀀트점수: {_qcode}=점수` (1종목 1점수)"
                    + (f". 보유 종목이므로 매수 분석이 아니라 **매도 분석**을 하고 `매도가: {_qcode}=시장가|숫자` 줄도 제시하십시오."
                       if _is_held else f". 후보 종목이면 `진입가: {_qcode}=시장가|숫자` 줄도 제시 가능."))
                for _attempt in range(3):
                    try:
                        self.quant_analyst.reset_history()  # 종목 간 컨텍스트 누수 방지
                        _prompt = _base_prompt
                        if _attempt > 0 and _looks_like_tool_call(_quant_resp):
                            _prompt = ("⚠️ 직전 응답이 도구호출 JSON/코드였습니다. 도구를 호출하지 마십시오 — "
                                       "데이터는 아래에 이미 전부 주어져 있습니다. JSON·코드펜스·함수호출 표기 없이 "
                                       "한국어 분석 줄글로만 답하십시오.\n\n" + _base_prompt)
                        _quant_resp = await self.quant_analyst.think(_prompt)
                        if (_quant_resp and "에러" not in _quant_resp[:80]
                                and "퀀트점수" in _quant_resp and not _looks_like_tool_call(_quant_resp)):
                            break
                    except Exception as _e:
                        _quant_resp = f"[계량분석팀장 에러] {_e}"
                    if _attempt < 2:
                        await asyncio.sleep(1.0)
                # 최종 방어: 그래도 도구호출 JSON이면 원문을 화면에 노출하지 않고 데이터부족으로 강등.
                if _looks_like_tool_call(_quant_resp):
                    logger.warning(f"[계량분석팀장] {_qcode} 도구호출JSON 3회 — 데이터부족 처리")
                    _quant_resp = (f"{_qname}\n- 분석 실패: 모델이 분석 대신 도구호출을 반복 출력해 "
                                   f"해당 종목은 데이터 부족으로 처리합니다 (매수 후보에서 제외 권고).\n"
                                   f"퀀트점수: {_qcode}=0")
                await self._emit({"type":"agent_msg","agent":"계량분석팀장",
                    "message": f"📊 [{_qrole}] {_qname}\n\n{_quant_resp}"})
                # 점수 추출
                _ms = re.search(rf"퀀트점수\s*[:：][^\n]*\b{re.escape(_qcode)}\s*=\s*(\d+)", _quant_resp or "")
                if _ms:
                    try:
                        _quant_scores[_qcode] = int(_ms.group(1))
                    except ValueError: pass
                # 사장 피드백 2026-05-15 (#4): 진입가 directive 파싱 (선택)
                _entry = _parse_entry_directive(_quant_resp, _qcode)
                if _entry["mode"] != "market":
                    _entry_dirs[_qcode] = _entry
                # 사장 지시 2026-05-22: 보유 종목은 매도가(지정가) directive 파싱 — 트레이딩팀장이 그 가격으로 매도.
                if _is_held:
                    _sp = _parse_sell_price(_quant_resp, _qcode)
                    if _sp["mode"] == "limit":
                        _sell_prices[_qcode] = _sp
                _quant_sections.append(f"[{_qrole}] {_qname}\n{_quant_resp}")
                await asyncio.sleep(0.3)
            # 통합 리포트 (이후 운용전략실장·사후관리실장 입력용)
            quant_report = "\n\n---\n\n".join(_quant_sections) if _quant_sections else "[퀀트 평가 데이터 없음]"
            if _quant_scores:
                quant_report += "\n\n퀀트점수: " + ", ".join(f"{k}={v}" for k, v in _quant_scores.items())
            if _entry_dirs:
                quant_report += "\n\n진입가 지시: " + "; ".join(
                    f"{k}={v.get('raw') or v.get('mode')}" for k, v in _entry_dirs.items())
            if _sell_prices:
                quant_report += "\n\n매도가 지시: " + "; ".join(
                    f"{k}={v.get('raw') or v.get('mode')}" for k, v in _sell_prices.items())
            self.cycle_log.log("QUANT", "계량분석팀장", quant_report)
            cyc.candidate_codes = candidate_codes
            cyc.quant_report = quant_report
            cyc.per_dart = per_dart
            cyc.name_map = name_map
            cyc._entry_dirs = _entry_dirs
            cyc._sell_prices = _sell_prices
            cyc._cand_line = _cand_line

    async def _cyc_stage_finalize_sell(self, cyc):
            session = cyc.session
            market_open = cyc.market_open
            _sell_only = cyc._sell_only
            _standing_directive_block = cyc.standing_directive_block
            candidate_codes = cyc.candidate_codes
            _cand_line = cyc._cand_line
            macro_report = cyc.macro_report
            quant_report = cyc.quant_report
            news_report = cyc.news_report
            holdings = cyc.holdings
            holdings_str = cyc.holdings_str
            _budget_hint = cyc._budget_hint
            # ── PASS 2: 운용전략실장 → 최종 매수 종목 (전략 프리셋에 따라 1~N개) ──────
            _N = int(runtime.get("MAX_TRADES_PER_CYCLE", uid=self.uid) or 2)
            # 사장 지시 2026-05-14: 개장 사이클은 뉴스 100건 정보를 최대 활용 — 매매 건수 한도 확대
            if market_open:
                _N = min(8, max(_N, _N * 3))  # 3배 또는 8개 중 작은 값
            # _max_trades_runtime은 실행 단계에서도 동일한 한도를 쓰도록 별도 저장
            _exec_N = _N
            if _sell_only or not candidate_codes:
                # 사장 피드백 2026-05-16: 신규 뉴스 없음(또는 후보 0) → 신규 매수 결정 자체를 생략.
                final_view = "최종종목: 없음"
                if _sell_only:
                    await self._emit({"type":"agent_msg","agent":"운용전략실장",
                        "message":"[최종 매수 종목 결정] 신규 매수 보류 (신규 뉴스 없는 사이클) — 보유 종목 매도 평가로 진행."})
                picked = []
            else:
                final_view = await self.orchestrator.think(
                    f"[최종 매수 종목 결정]\n1차 후보: {_cand_line}\n최대 매수 개수 N = {_N} (전략 프리셋)\n현재 세션: {session}\n"
                    f"{_budget_hint}\n\n"
                    + (f"{_standing_directive_block}\n\n" if _standing_directive_block else "")
                    + f"전략리서치팀장 자산 배분 권고(전략리서치팀장의 매크로 판단 — 참고하여 반영):\n{macro_report[:600]}\n\n"
                    f"계량분석팀장 평가:\n{quant_report}\n\n뉴스분석팀장 평가:\n{news_report}\n\n"
                    f"후보 {len(candidate_codes)}개 중에서 실제로 매수할 종목을 **N개 이하**로 좁히십시오. "
                    f"퀀트 점수가 낮거나 뉴스 감성이 부정적이거나 1주 예산을 크게 넘는 종목은 빼십시오. 마땅한 게 없으면 더 적게 골라도, 0개여도 됩니다.\n"
                    f"⚠️ **전략리서치팀장**의 주식 비중 권고가 확대면 N개 풀로, 축소면 N개보다 적게(또는 0개), 유지면 N의 절반~80% 수준 매수를 고려하십시오. "
                    f"이는 전략리서치팀장의 매크로 판단이며 사장님 지시가 아닙니다 — 매수 사유를 쓸 때 '사장님 지시/피드백에 따라 비중 축소' 같은 표현을 쓰지 말고, "
                    f"'전략리서치팀장 매크로 권고에 따라'로 정확히 출처를 밝히십시오. (사장님이 직접 비중 지시를 내린 게 아니면 사장님을 근거로 인용 금지)\n"
                    f"⚠️ 응답 마지막 줄은 반드시 `최종종목: ...` (후보 목록에 있던 코드만, N개 이하; 없으면 `최종종목: 없음`).")
                final_view = _strip_leading_section_marker(final_view, "[최종 매수 종목 결정]", "[후보 종목 선정]")
                self.cycle_log.log("DRAFT", "운용전략실장", final_view)
                await self._emit({"type":"agent_msg","agent":"운용전략실장","message":f"[최종 매수 종목 결정]\n{final_view}"})
                picked = _extract_codes_after(final_view, "최종종목", "대상종목", "종목코드", "target stocks")
            cand_set = set(candidate_codes)
            target_codes = ([c for c in picked if c in cand_set] if cand_set else picked)[:_N]
            # Enforce session boundary on final picks too (defence-in-depth)
            if session == "US_TRADING":
                target_codes = [c for c in target_codes if not (_is_kr_code(c))]
            elif session in ("KR_TRADING", "KR_PRE_MARKET"):
                target_codes = [c for c in target_codes if _is_kr_code(c)]

            # ── 사후관리실장: 보유 종목 매도 판단 ─────────────────────────────────
            # 사장 피드백 2026-05-15 (#5): 현재 세션 시장의 보유 종목이 없으면 분석 자체를 시작하지 않음.
            # KR 보유 종목이 있는데 US 세션이면 → 매도 판단 보류, 그 반대도 동일.
            _holdings_this_market = []
            for _h in (holdings or []):
                _hc = str(_h.get("code", "")).strip()
                _is_kr_holding = _is_kr_code(_hc)
                if session == "US_TRADING" and not _is_kr_holding:
                    _holdings_this_market.append(_h)
                elif session in ("KR_TRADING", "KR_PRE_MARKET", "KR_CLOSE_REVIEW") and _is_kr_holding:
                    _holdings_this_market.append(_h)
                elif session == "OFF_HOURS":
                    _holdings_this_market.append(_h)  # 장외엔 양쪽 모두 분석 가능
            sell_directives: Dict[str, str] = {}
            if _holdings_this_market:
                await self._emit({"type":"status","state":"QUANT_ANALYSIS","message":"사후관리 — 보유 종목 매도 판단"})
                _orig_holdings = holdings  # 매도 결정 후 모든 종목에 다시 사용
                holdings = _holdings_this_market  # 사후관리실장 입력은 이 시장 종목만
                # 보유기간 정보(holdings_history 기반) — 사후관리실장이 단기/중장기 판단할 때 사용
                period_lines = []
                for h in holdings:
                    hp = cycle_store.get_holding_period(h.get("code",""))
                    if hp and hp.get("days_held") is not None:
                        period_lines.append(f"  - {h.get('name',h.get('code'))}({h.get('code')}): 보유 {hp['days_held']:.1f}일 (관찰 시작 {hp['first_seen']})")
                holding_period_str = "\n".join(period_lines) if period_lines else "  (보유기간 데이터 없음 — 신규 관찰)"
                # 사장 피드백 2026-05-15 (#24): 데이트레이딩 회피 룰은 전략 프리셋에서 토글.
                # ALLOW_DAY_TRADING=True(기본)면 보유기간 가이드 자체를 안 보냄 — 사후관리실장이 자유롭게 판단.
                _allow_day = bool(runtime.get("ALLOW_DAY_TRADING", uid=self.uid))
                _min_hold = float(runtime.get("MIN_HOLDING_DAYS_FOR_SELL", uid=self.uid) or 0.0)
                if _allow_day:
                    _hold_guide = "보유기간은 참고만 — **데이트레이딩 허용(전략 설정)**, 신호가 명확하면 단기 매도 OK."
                else:
                    _hold_guide = (f"**보유기간 가중** — {_min_hold}일 미만은 데이트레이딩 회피, "
                                   f"7일 이상은 트렌드 점검.")
                # 사장 지시 2026-05-24: 사후관리실장 프롬프트에 현재 세션을 명시한다.
                # (버그 2026-05-23 04:47: 세션 정보가 없어 LLM이 'KR 장중이라 미국 시장 닫힘'으로
                #  환각 → US_TRADING 인데도 QUBT/UUP/DAL 매도 신호를 '매매 불가'로 무시하고 보유.)
                # 아래 보유 종목은 이미 현재 세션에서 거래 가능한 것만 필터링돼 들어온다.
                _pm_session_hint = _post_manager_session_hint(session)
                # 사장 지시 2026-05-28(우선순위 3): 펀드기획팀장 thesis 상기 — 사후관리실장이 매도 판단 직전
                # 진입 시점의 목표가/손절가/계획 보유기간을 보고 무계획 단타를 막는다.
                _thesis_reminder = ""
                try:
                    from infra import position_thesis
                    from agents.specialists import format_thesis_reminder
                    _theses = position_thesis.get_all(self.uid)
                    if _theses:
                        _thesis_reminder = format_thesis_reminder(_theses, holdings, _now_kst_iso())
                        if _thesis_reminder:
                            await self._emit({"type": "agent_msg", "agent": "펀드기획실장",
                                              "message": _thesis_reminder})
                except Exception as _te:
                    logger.warning(f"[펀드기획] thesis 상기 실패: {_te}")
                _pm_prompt = (
                    f"{_pm_session_hint}\n\n"
                    + (f"{_thesis_reminder}\n\n" if _thesis_reminder else "")
                    + f"전략리서치팀장 매크로 보고:\n{macro_report}\n\n현재 보유 종목: {holdings_str}\n\n"
                    f"보유기간 (Arquant 자체 관찰):\n{holding_period_str}\n\n"
                    f"계량분석팀장 평가:\n{quant_report}\n\n뉴스분석팀장 평가:\n{news_report}\n\n"
                    f"위 매크로 → 퀀트 → 뉴스 → 평가손익 순으로 가중해 보유 종목별로 매도/보유를 결정하십시오. "
                    f"{_hold_guide} 종목별 사유에 '시장이 닫혀 매매 불가' 같은 세션 추측을 쓰지 마십시오 (위 세션 안내가 사실). "
                    + ("펀드기획팀장 thesis 가 위에 명시된 종목은 진입 사유·목표가·손절가·계획 보유기간을 우선 참고하십시오. "
                       "목표 미달·손절 미터치·계획 기간 미경과인데 매도하려면 명확한 신호 변화 이유를 사유에 적으십시오.\n"
                       if _thesis_reminder else "")
                    + f"마지막 줄은 반드시 `매도결정: 코드=전량/절반/보유, ...` (보유 종목 전체).")
                # 사장 지시 2026-05-22: 빈 응답/에러 시 재시도 (계량분석팀장과 동일 패턴) —
                # 직전 사이클에서 사후관리실장이 'API 응답 비어있음'으로 매도 평가가 통째 빠진 버그.
                pm_view = ""
                for _pm_attempt in range(3):
                    try:
                        pm_view = await self.post_manager.think(_pm_prompt)
                    except Exception as _pe:
                        pm_view = f"[사후관리실장 에러] {_pe}"
                    if pm_view and pm_view.strip() and "에러" not in pm_view[:80] and "응답 비어" not in pm_view:
                        break
                    if _pm_attempt < 2:
                        await asyncio.sleep(1.0)
                self.cycle_log.log("RISK", "사후관리실장", pm_view)
                await self._emit({"type":"agent_msg","agent":"사후관리실장","message":pm_view})
                sell_directives = _parse_sell_decisions(pm_view)
                holdings = _orig_holdings  # 후속 처리(주문 조립·이력 기록)는 전체 보유 종목 사용
                # 사장 지시 2026-05-29: 펀드기획팀장 → 펀드기획실장 승격 — thesis 거부권.
                # 계획기간 미경과 + 소폭이익 + 손절·목표 미해당 매도결정을 '보유'로 결정론적 오버라이드해
                # 무계획 단타를 차단(손절·손실·목표·계획기간 경과 매도는 비차단). 투명 로깅.
                try:
                    from infra import position_thesis as _pt
                    from agents.specialists import apply_thesis_veto
                    _veto_theses = _pt.get_all(self.uid)
                    if _veto_theses and sell_directives:
                        sell_directives, _veto_msgs = apply_thesis_veto(
                            _veto_theses, _orig_holdings, sell_directives, _now_kst_iso(),
                            enabled=bool(runtime.get("THESIS_VETO_ENABLED", uid=self.uid)),
                            allow_day_trading=bool(runtime.get("ALLOW_DAY_TRADING", uid=self.uid)))
                        for _vm in _veto_msgs:
                            self.cycle_log.log("RISK", "펀드기획실장", _vm)
                            await self._emit({"type": "agent_msg", "agent": "펀드기획실장", "message": _vm})
                except Exception as _ve:
                    logger.warning(f"[펀드기획] 거부권 적용 실패(매도결정 원안 유지): {_ve}")
                # 사장 피드백 2026-05-15 (#5, #13): 반대편 시장 보유 종목은 자동 '보유' 처리.
                # 사후관리실장이 분석을 안 했으므로 _build_orders의 자동 익절/손절 룰이 잘못 매도하는 것 방지.
                _existing_lower = {k.lower() for k in sell_directives.keys()}
                for _h in (_orig_holdings or []):
                    _hc = str(_h.get("code", "")).strip()
                    if not _hc or _hc.lower() in _existing_lower:
                        continue
                    _is_kr_h = _is_kr_code(_hc)
                    _opposite = ((session == "US_TRADING" and _is_kr_h) or
                                 (session in ("KR_TRADING", "KR_PRE_MARKET", "KR_CLOSE_REVIEW") and not _is_kr_h))
                    if _opposite:
                        sell_directives[_hc] = "보유"
            elif holdings:
                # 반대편 시장 보유만 있는 상황 — 사장 피드백 2026-05-15 (#5, #13): 분석 생략 + 전체 자동 '보유'.
                _other_mkt = "US" if session in ("KR_TRADING", "KR_PRE_MARKET", "KR_CLOSE_REVIEW") else "KR"
                await self._emit({"type":"agent_msg","agent":"사후관리실장",
                    "message": f"📭 현재 세션({session})의 보유 종목 없음 — 분석 생략 ({_other_mkt} 보유분은 해당 시장 사이클에서 처리)."})
                for _h in (holdings or []):
                    _hc = str(_h.get("code", "")).strip()
                    if _hc:
                        sell_directives[_hc] = "보유"
            else:
                await self._emit({"type":"agent_msg","agent":"사후관리실장","message":"보유 종목 없음 — 매도 판단 불필요"})
            cyc.target_codes = target_codes
            cyc.sell_directives = sell_directives
            cyc.holdings = holdings
            cyc._exec_N = _exec_N

    async def _cyc_stage_build_orders(self, cyc):
            market_open = cyc.market_open
            target_codes = cyc.target_codes
            candidate_codes = cyc.candidate_codes
            quant_report = cyc.quant_report
            news_report = cyc.news_report
            holdings = cyc.holdings
            sell_directives = cyc.sell_directives
            _entry_dirs = cyc._entry_dirs
            _sell_prices = cyc._sell_prices
            # [7] ORDER DRAFT — assembled in Python: buys = 2패스 최종종목, sells = 사후관리실장 매도결정.
            #     The trader LLM is only invoked as a *fallback* when Python has nothing to act on.
            self.current_state = SwarmState.ORDER_DRAFTING
            await self._emit({"type":"status","state":"ORDER_DRAFTING","message":"주문 초안 (잔고 비율 기반 사이징)"})
            order_obj, price_map, buying_power = await self._build_orders(
                target_codes, candidate_codes, quant_report, news_report, holdings, sell_directives=sell_directives,
                market_open=market_open, entry_dirs=_entry_dirs, sell_prices=_sell_prices)
            order_draft = json.dumps(order_obj, ensure_ascii=False, indent=2)
            # 사장 피드백 2026-05-15 (3차): 트레이더 호출은 EXECUTION 이후로 이동.
            # 여기서는 시스템 상태 메시지로 "조립 완료, 리스크 검증 이동" 만 알린다.
            if order_obj["orders"]:
                _drafting_msg = f"주문 {len(order_obj['orders'])}건 조립 완료 → 리스크 검증으로 이관"
            else:
                _drafting_msg = "ℹ️ 잔고/한도 조건상 신규 주문 없음. " + ("; ".join(order_obj.get("sizing_notes") or []))[:240]
            await self._emit({"type":"status","state":"ORDER_DRAFTING","message": _drafting_msg})
            self.cycle_log.log("DRAFT", "트레이딩팀장", order_draft)
            cyc.order_obj = order_obj
            cyc.price_map = price_map
            cyc.buying_power = buying_power
            cyc.order_draft = order_draft

    async def _cyc_stage_risk(self, cyc):
            per_dart = cyc.per_dart
            buying_power = cyc.buying_power
            price_map = cyc.price_map
            order_draft = cyc.order_draft
            # [8] RISK — 사장 지시 2026-05-14: 1차(결정론) + 2차(DART 공시) 통합 실행 후 **한 번에** 출력
            self.current_state = SwarmState.RISK_VALIDATION
            await self._emit({"type":"status","state":"RISK_VALIDATION","message":"리스크 검증 (결정론 + DART)"})
            self.validation_attempts = 1
            risk_result = validate_order_draft(order_draft, "", buying_power=buying_power, price_map=price_map, uid=self.uid)
            risk_approved = bool(risk_result["approved"])
            approved_orders = [r for r in risk_result["results"] if r["status"] == "APPROVED"]
            # 2차 DART 재심 — 매도(리스크 감소)는 통과, 매수만 공시 기반 재확인
            dart_resp_text = ""
            dart_vetoed: set = set()
            _buys = [r for r in approved_orders if (r.get("side") or "buy") != "sell"]
            if _buys:
                dart_vetoed, dart_resp_text = await self._dart_risk_review(_buys, per_dart)
                if dart_vetoed:
                    approved_orders = [r for r in approved_orders
                                       if not ((r.get("side") or "buy") != "sell" and str(r.get("ticker","")).upper() in dart_vetoed)]
                    risk_approved = bool(approved_orders)
            # 통합 메시지 — 1차 결과 + DART 재심을 한 번에
            unified_lines = ["🧮 [리스크관리실장 — 통합 검증 보고]\n",
                             "▶ 1차 결정론 검증 (편중도·MDD·예수금·예산·수량):", risk_result["report"]]
            if _buys:
                unified_lines.append("\n▶ 2차 DART 공시 재심 (매수 한정):")
                unified_lines.append(dart_resp_text or "(DART 응답 없음)")
                if dart_vetoed:
                    unified_lines.append(f"\n⚠ DART 재심 반려: {', '.join(sorted(dart_vetoed))}")
            else:
                unified_lines.append("\n▶ 2차 DART 공시 재심: 매수 주문 없음 → 생략")
            unified_lines.append(f"\n▶ 최종 결과: {'✅ 승인' if risk_approved else '❌ 미승인'} ({len(approved_orders)}건)")
            unified_msg = "\n".join(unified_lines)
            self.cycle_log.log("RISK", "리스크관리실장", unified_msg)
            await self._emit({"type":"agent_msg","agent":"리스크관리실장","message":unified_msg})
            cyc.risk_result = risk_result
            cyc.risk_approved = risk_approved
            cyc.approved_orders = approved_orders

    async def _cyc_stage_execute(self, cyc):
            session = cyc.session
            market_open = cyc.market_open
            _exec_N = cyc._exec_N
            holdings = cyc.holdings
            order_obj = cyc.order_obj
            order_draft = cyc.order_draft
            sell_directives = cyc.sell_directives
            name_map = cyc.name_map
            risk_result = cyc.risk_result
            risk_approved = cyc.risk_approved
            approved_orders = cyc.approved_orders
            # [9] EXECUTION — place KIS orders: sells(사후관리) 우선, 매수는 전략 N개까지. qty from sizing.
            self.current_state = SwarmState.EXECUTION
            exec_results: List[Dict] = []
            # 사장 지시 2026-05-14: 개장 사이클은 _exec_N(Pass2에서 확대됨)을 사용
            _max_trades = _exec_N if market_open else runtime.get("MAX_TRADES_PER_CYCLE", uid=self.uid); _max_qty = runtime.get("MAX_ORDER_QTY", uid=self.uid)
            if risk_approved and LIVE_TRADING and approved_orders:
                _ap_sells = [r for r in approved_orders if (r.get("side") or "buy") == "sell"]
                _ap_buys  = [r for r in approved_orders if (r.get("side") or "buy") != "sell"]
                _exec_list = _ap_sells + _ap_buys[:int(_max_trades or 2)]
                # 사장 지시 2026-05-21: US 체결 재확인용 '주문 직전' 해외 보유 스냅샷.
                # cycle 의 holdings 는 kr_holdings() 라 US 가 없어, 이미 보유 중인 US 종목의
                # before_qty 가 0으로 잡혀 false-positive(미체결을 체결로 오판)가 날 수 있다.
                # US 주문이 있을 때만 1회 조회해 _reverify_fills baseline 에 합친다.
                _us_baseline: List[Dict] = []
                def _is_us_tk(_t: str) -> bool:
                    _t = (_t or "").strip()
                    return not (_is_kr_code(_t))
                if any(_is_us_tk(r.get("ticker")) for r in _exec_list):
                    try:
                        _us_baseline = await self.broker._overseas_holdings()
                    except Exception:
                        _us_baseline = []
                for r in _exec_list:
                    tk = str(r.get("ticker") or "").strip()
                    qty = max(1, int(r.get("qty") or 1))
                    if _max_qty and _max_qty > 0:
                        qty = min(qty, _max_qty)
                    is_kr = _is_kr_code(tk)
                    # 사장 피드백 2026-05-15 (#4): _build_orders에서 첨부한 entry_mode로 분기.
                    _emode = r.get("entry_mode") or "market"
                    _elimit = r.get("entry_limit")
                    _ewatch_pct = r.get("entry_watch_pct")
                    _side = r.get("side") or "buy"
                    # 사장 지시 2026-05-22: 매도는 계량분석팀장이 '매도가'를 숫자로 제시한 경우 그 지정가로,
                    # 아니면 시장가(즉시 청산). watch 모드는 매도에 적용하지 않는다.
                    if _side == "sell":
                        _emode = "limit" if (_emode == "limit" and _elimit and float(_elimit) > 0) else "market"
                    # 대기(watch) 모드 — 백그라운드 태스크 spawn, 즉시 체결 대기 카운트 X
                    if _emode == "watch" and _ewatch_pct is not None:
                        asyncio.create_task(self._entry_watch_task(
                            ticker=tk, qty=qty, market=("KR" if is_kr else "US"),
                            watch_pct=float(_ewatch_pct), baseline_holdings=list(holdings or []),
                            reason=r.get("reason", "")))
                        await self._emit({"type":"agent_msg","agent":"트레이딩팀장",
                            "message": f"⏱ {tk} {qty}주 분봉 모니터링 시작 — {_ewatch_pct:+.1f}% 도달 시 시장가 매수 (최대 3시간, 장 마감 시 취소)"})
                        # exec_results에는 'pending watch'로 기록 (체결 카운트 X)
                        exec_results.append({"ticker": tk, "side": _side, "qty": qty,
                                             "result": f"watch task spawned ({_ewatch_pct:+.1f}%)",
                                             "accepted": False, "filled": False,
                                             "fill_note": "분봉 대기 중", "ok": False, "watch": True})
                        continue
                    # 지정가(limit) 모드 — KIS limit 주문 (장 마감 시 KIS가 자동 취소)
                    _price_for_kis = 0
                    if _emode == "limit" and _elimit and _elimit > 0:
                        _price_for_kis = float(_elimit) if not is_kr else int(_elimit)
                    od = OrderDraft(ticker=tk, side=_side, qty=qty,
                                    price_type=("limit" if _emode == "limit" and _price_for_kis else "market"),
                                    limit_price=(float(_price_for_kis) if _price_for_kis else None),
                                    market="KR" if is_kr else "US",
                                    reason="Arquant 자동매매 — 보수적 리스크 승인", approved=True)
                    # 사장 피드백 2026-05-15 (6차): 추정 체결가 → 실제 체결가 기록.
                    # 주문 직전 스냅샷 — before_qty/before_avg (매수 시 평균단가 차이로 정확한 체결가 역산용)
                    # 매도는 avg_price가 안 변하므로 주문 직전 cur_price를 시장가 매도 체결가로 사용 (KIS 시장가 = 직전체결가 근사).
                    before_qty = next((h.get("qty") for h in (holdings or []) if h.get("code") == tk), 0) or 0
                    before_avg = next((h.get("avg_price") for h in (holdings or []) if h.get("code") == tk), 0) or 0
                    sell_ref_price = 0.0
                    if _side == "sell":
                        if is_kr:
                            try: sell_ref_price = await self.broker.kr_last_price(tk)
                            except Exception: sell_ref_price = 0.0
                        else:
                            try: sell_ref_price = await self.broker.us_last_price(tk)
                            except Exception: sell_ref_price = 0.0
                    # KIS throttles orders aggressively (초당 거래건수) — pace + one retry.
                    res = ""
                    for attempt in range(3):
                        await asyncio.sleep(1.5 if attempt == 0 else 2.5)
                        try:
                            res = await self.broker.place_order(od)
                        except Exception as e:
                            res = f"[주문 예외] {e}"
                        if not any(x in res for x in ("초당", "거래건수", "EGW", "유량", "TPS")):
                            break
                    accepted = all(bad not in res for bad in ("실패", "에러", "거부", "예외", "REJECT", "초당", "거래건수"))
                    # Fill confirmation — "주문 전송 완료" only means *accepted*, not *filled*. Re-read holdings.
                    filled = False; fill_note = ""; fill_price: Optional[float] = None
                    after_avg = 0.0  # KR 체결 확인 시 갱신 — 매수 후 블렌딩 평단
                    if accepted and is_kr:
                        try:
                            await asyncio.sleep(2.0)
                            self.broker._acct_snap = None  # force fresh read post-order
                            after = await self.broker.kr_holdings()
                            after_qty = next((h.get("qty") for h in after if h.get("code") == tk), 0) or 0
                            after_avg = next((h.get("avg_price") for h in after if h.get("code") == tk), 0) or 0
                            if od.side == "buy" and after_qty > before_qty:
                                filled = True; fill_note = f"보유 {before_qty}→{after_qty}주 확인"
                                # 사장 피드백 6차: 매수 체결가를 평균단가 차이로 정확히 역산.
                                # (after_avg × after_qty) - (before_avg × before_qty) = fill_price × buy_qty
                                buy_qty_actual = after_qty - before_qty
                                if buy_qty_actual > 0:
                                    fill_price = (after_avg * after_qty - before_avg * before_qty) / buy_qty_actual
                                    fill_note += f" · 체결가 {fill_price:,.0f}원"
                            elif od.side == "sell" and after_qty < before_qty:
                                filled = True; fill_note = f"보유 {before_qty}→{after_qty}주 확인"
                                # 사장 피드백 6차: 매도는 직전 last_price를 시장가 체결가로 사용.
                                if sell_ref_price > 0:
                                    fill_price = float(sell_ref_price)
                                    fill_note += f" · 체결가 ≈{fill_price:,.0f}원 (직전 호가)"
                            else:
                                fill_note = f"체결 미확인(보유 {after_qty}주) — 접수만 완료, 호가 미체결 가능"
                        except Exception as _e:
                            fill_note = f"체결확인 조회 실패({_e})"
                    elif accepted and not is_kr:
                        # US: holdings 즉시 확인 어려움. 시장가 = 직전 last_price 사용.
                        if _side == "buy":
                            try: fill_price = await self.broker.us_last_price(tk)
                            except Exception: fill_price = None
                        else:
                            fill_price = sell_ref_price if sell_ref_price > 0 else None
                    # 사장 지시 2026-05-21: 누적 실매매 체결 카운트는 '즉시 체결이 확인된' 경우에만 +1.
                    # 접수(accepted)만으론 올리지 않는다 — 미확인 주문은 _poll_fills_until_confirmed 가
                    # 5분마다 반복 확인해, 실제 보유 변동이 잡히는 그 시점에 비로소 +1 한다(차감 로직 없음).
                    is_us = not is_kr
                    ok = filled  # 즉시 체결 확인된 주문만 카운트 (KR 2초 후 보유 확인 / US 는 즉시 확인 불가 → 폴링)
                    # 사장 지시 2026-05-19: 매수·매도 시점의 KIS 실매입평균가(pchs_avg_pric)를
                    # 거래에 직접 박는다. 매도는 주문 직전 평단(before_avg)=청산 로트의 실제
                    # 매수평단, 매수는 체결 후 블렌딩 평단(after_avg).
                    avg_cost = (float(before_avg or 0.0) if od.side == "sell"
                                else float(after_avg or before_avg or 0.0))
                    rec = {"ticker": tk, "side": od.side, "qty": qty, "result": res,
                           "accepted": accepted, "filled": filled, "fill_note": fill_note, "ok": ok,
                           "fill_price": fill_price, "fill_currency": ("USD" if is_us else "KRW"),
                           "avg_cost": avg_cost}
                    exec_results.append(rec)
                    if ok:
                        self._trades_executed += 1
                        self._trade_log.append({"ts": _now_kst_iso(), **rec})
                        # 사장 지시 2026-05-28(우선순위 3): 매수 체결 직후 펀드기획팀장이 진입 thesis 작성·영속.
                        # fire-and-forget — LLM 호출이라 사이클 다음 단계를 막지 않게 background task.
                        if od.side == "buy":
                            asyncio.create_task(self._record_buy_thesis(rec, cyc))
                    # 모바일 알림 ①: 체결 신청(주문 접수 성공) — 즉시 체결이든 미확인이든 '접수'된 순간 1회.
                    if accepted:
                        await self._emit({"type": "order_submitted", "agent": "트레이딩팀장",
                            "message": f"📨 {tk} {('매수' if od.side == 'buy' else '매도')} {qty}주 주문 접수 — 체결 확인 중",
                            "ticker": tk, "side": od.side, "qty": qty})
                    # 모바일 알림 ②: 체결 완료(즉시 확인) / 또는 주문 실패. 미확인(접수만)은 폴링이 추후 처리.
                    if filled or not accepted:
                        badge = "✅ 실매매 체결확인" if filled else "⚠ 주문 실패"
                        await self._emit({"type": _trade_event_type(filled),
                            "message": f"{badge} — {res}" + (f" | {fill_note}" if fill_note else ""),
                            "ticker": tk, "side": od.side, "qty": qty, "filled": filled,
                            "fill_price": fill_price, "fill_currency": ("USD" if is_us else "KRW"),
                            "avg_cost": avg_cost,
                            "trades_total": self._trades_executed})
                    self.cycle_log.log("EXEC", "시스템", f"{tk} {od.side} x{qty} → {res} | {fill_note}")
                # 사장 피드백 2026-05-15 (#15): 체결과 접수 분리 표기
                _filled_cnt = sum(1 for e in exec_results if e.get("filled"))
                _accepted_only = sum(1 for e in exec_results if e.get("accepted") and not e.get("filled"))
                _exec_msg = f"실행 완료 — 이번 사이클 체결 {_filled_cnt}건"
                if _accepted_only:
                    _exec_msg += f" (+ 접수만 {_accepted_only}건, 5분 후 재확인)"
                _exec_msg += f" / 누적 체결 {self._trades_executed}건"
                await self._emit({"type":"execution_ready", "message": _exec_msg, "draft": order_draft})
                # ── 사장 지시 2026-05-21: 미체결 주문 5분마다 '반복' 체결 확인 백그라운드 태스크 spawn ──
                # 접수만 되고 즉시 체결이 확인 안 된 주문은 그 시점부터 5분마다 보유 변동을 재확인하다
                # 체결이 잡히면 그때 누적 카운트 +1 + '체결 확인됨' 보고. 그때까지는 조용히(메시지 없음).
                _unconfirmed = [e for e in exec_results if e.get("accepted") and not e.get("filled")]
                if _unconfirmed:
                    asyncio.create_task(self._poll_fills_until_confirmed(_unconfirmed, list(holdings or []) + _us_baseline, cyc=cyc))
            elif risk_approved and not LIVE_TRADING:
                await self._emit({"type":"execution_ready","message":"✅ 리스크 승인 (LIVE_TRADING=False — 실주문 생략)","draft":order_draft})
            else:
                await self._emit({"type":"execution_skipped","message":"❌ 리스크 미승인 — 실행 없음"})

            # [9.5] 트레이딩팀장 체결 보고 — 결정론적 템플릿 (사장 피드백 2026-05-18)
            # 더 이상 LLM을 호출하지 않는다: 보고 내용이 전부 사실(종목·수량·체결여부·체결가·사유)
            # 이므로 고정 양식으로 조립하면 일관·정확·무비용. (체결 vs 접수 혼동도 구조적으로 차단.)
            try:
                def _nm(c: str) -> str:
                    c = str(c).strip()
                    return f"{c}({name_map[c]})" if c in name_map else c
                def _disp(tk: str) -> str:
                    try: return _nm(tk)            # name_map 있으면 코드(종목명)
                    except Exception: return tk
                def _price_str(e: Dict) -> str:
                    fp = e.get("fill_price")
                    if not fp or fp <= 0:
                        return ""
                    return f" · 체결가 {fp:,.0f}원" if (e.get("fill_currency") != "USD") else f" · 체결가 ${fp:,.2f}"
                # 사유 맵 — 매수: order_obj, 매도: 사후관리실장 directive
                _reason_by_tk: Dict[str, str] = {}
                for o in (order_obj.get("orders") or []):
                    _otk = str(o.get("ticker") or "").strip()
                    if _otk:
                        _reason_by_tk[_otk] = (o.get("reason") or "").strip()
                _HOLD = {"보유", "유지", "hold", "keep", "유보", "관망"}
                _sell_dir = {str(k).strip(): str(v).strip() for k, v in (sell_directives or {}).items()
                             if str(v).strip().lower() not in _HOLD}

                _buy_rows, _sell_rows = [], []
                for e in (exec_results or []):
                    _tk = str(e.get("ticker") or "").strip()
                    _qty = e.get("qty", 0)
                    _is_sell = (e.get("side") or "buy") == "sell"
                    if e.get("watch"):
                        _badge = "⏳ 분봉 대기"
                        _note = e.get("fill_note") or f"{e.get('result','')[:80]}"
                    elif e.get("filled"):
                        _badge = "✅ 체결확인"
                        _note = (e.get("fill_note") or "").strip()
                    elif e.get("accepted"):
                        _badge = "📨 접수(체결 확인 중)"
                        _note = "호가 미체결 가능 — 5분마다 보유 변동 반복 확인(체결 시 누적 +1)"
                    else:
                        _badge = "⚠️ 실패"
                        _note = (e.get("result") or "")[:100]
                    _reason = _reason_by_tk.get(_tk) or (
                        f"사후관리실장 판단 — {_sell_dir.get(_tk, '')}" if _is_sell else "")
                    _line = f"- {_badge} {_disp(_tk)} {_qty}주{_price_str(e)}"
                    if _note:
                        _line += f"\n   └ {_note}"
                    if _reason:
                        _line += f"\n   └ 사유: {_reason[:180]}"
                    (_sell_rows if _is_sell else _buy_rows).append(_line)

                _filled_cnt2 = sum(1 for e in (exec_results or []) if e.get("filled"))
                _accepted_only2 = sum(1 for e in (exec_results or []) if e.get("accepted") and not e.get("filled"))
                _watch_cnt = sum(1 for e in (exec_results or []) if e.get("watch"))

                _parts = ["🧾 [트레이딩팀장 — 사이클 체결 보고]"]
                if not exec_results:
                    _why = "; ".join(order_obj.get("sizing_notes") or []) or (
                        "리스크 미승인" if not risk_approved else "잔고·한도 조건상 신규/청산 주문 없음")
                    _parts.append(f"\n▶ 이번 사이클 신규 체결 없음 — {_why[:240]}")
                else:
                    if _buy_rows:
                        _parts.append("\n▶ 매수\n" + "\n".join(_buy_rows))
                    if _sell_rows:
                        _parts.append("\n▶ 매도\n" + "\n".join(_sell_rows))
                _tail = f"\n▶ 집계 — 이번 사이클 체결 {_filled_cnt2}건"
                _extra = []
                if _accepted_only2: _extra.append(f"접수 {_accepted_only2}건")
                if _watch_cnt:     _extra.append(f"분봉대기 {_watch_cnt}건")
                if _extra:         _tail += f" (+ {' · '.join(_extra)})"
                _tail += f" / 누적 체결 {self._trades_executed}건"
                _parts.append(_tail)
                _trader_msg = "\n".join(_parts)
                await self._emit({"type":"agent_msg","agent":"트레이딩팀장", "message": _trader_msg})
                self.cycle_log.log("REPORT", "트레이딩팀장", _trader_msg)
            except Exception as _te:
                logger.warning(f"트레이더 체결 보고 조립 실패: {_te}")

            # [9.7] 사이클 자기검증 — 체결률·거부를 ok:true 로 묻지 말고 지표·경고로 가시화 (2026-05-27 진단).
            try:
                _real = [e for e in (exec_results or []) if not e.get("watch")]
                _filled = sum(1 for e in _real if e.get("filled"))
                _acc = sum(1 for e in _real if e.get("accepted"))
                metrics.gauge("cycle_orders", float(len(_real)), uid=str(self.uid), session=str(session))
                metrics.gauge("cycle_filled", float(_filled), uid=str(self.uid), session=str(session))
                metrics.gauge("cycle_accepted", float(_acc), uid=str(self.uid), session=str(session))
                _health = cycle_health_warnings(exec_results)
                if _health:
                    metrics.incr("cycle_health_warning", by=float(len(_health)), uid=str(self.uid))
                    _wmsg = "⚠ 사이클 자기검증 경고:\n" + "\n".join(f"  • {w}" for w in _health)
                    await self._emit({"type": "agent_msg", "agent": "리스크관리실장", "message": _wmsg})
                    self.cycle_log.log("HEALTH", "시스템", _wmsg)
                    notifier.alert("WARN", "사이클 자기검증 경고", "; ".join(_health),
                                   dedup_key=f"cycle_health_{self.uid}")
            except Exception as _he:
                logger.warning(f"사이클 자기검증 실패(무시): {_he}")
            cyc.exec_results = exec_results

    async def _record_buy_thesis(self, rec: Dict, cyc) -> None:
        """매수 체결 직후 펀드기획팀장이 진입 thesis(목표가/손절가/계획 보유기간/사유)를 작성·영속.
        fire-and-forget — 실패해도 매매 흐름엔 영향 없음.
        사장 지시 2026-05-28(우선순위 3): 무계획 단타 매매 차단."""
        code = str(rec.get("ticker", "")).strip()
        if not code or rec.get("side") != "buy" or not rec.get("ok"):
            return
        try:
            from agents.specialists import parse_fund_plan
            from infra import position_thesis
            # 중복가드(2026-05-29): 동기 실행부와 폴링 확정 경로가 같은 종목을 중복 기록하지 않도록,
            # 이미 thesis 가 있으면 LLM 호출·재기록을 건너뛴다.
            if position_thesis.get(self.uid, code):
                return
            quant_brief = (getattr(cyc, "quant_report", "") or "")[:1200]
            news_brief = (getattr(cyc, "news_report", "") or "")[:500]
            order_obj = getattr(cyc, "order_obj", None) or {}
            buy_reason = ""
            for o in (order_obj.get("orders") or []):
                if str(o.get("ticker", "")).strip() == code and o.get("side") == "buy":
                    buy_reason = (o.get("reason") or "")[:300]
                    break
            fill_price = float(rec.get("fill_price") or rec.get("avg_cost") or 0.0)
            ccy = rec.get("fill_currency") or "KRW"
            prompt = (f"[plan 모드]\n종목: {code}\n체결가: {fill_price:,.2f} {ccy}\n"
                      f"매수 사유(운용전략실장): {buy_reason}\n\n"
                      f"계량팀 리포트 발췌:\n{quant_brief}\n\n뉴스 요약 발췌:\n{news_brief}\n\n"
                      f"이 매수의 thesis 를 4줄(목표가/손절가/계획 보유기간/진입 사유 요약)로만 응답하십시오.")
            text = await self.fund_planner.think(prompt)
            parsed = parse_fund_plan(text)
            thesis = {
                "entry_ts": _now_kst_iso(),
                "entry_price": fill_price,
                "fill_currency": ccy,
                "target_price": parsed.get("target_price"),
                "stop_price": parsed.get("stop_price"),
                "planned_hold_hours": parsed.get("planned_hold_hours"),
                "entry_reason": parsed.get("entry_reason") or buy_reason[:200],
                "source_agent": "펀드기획실장",
            }
            position_thesis.record(self.uid, code, thesis)
            await self._emit({"type": "agent_msg", "agent": "펀드기획실장",
                "message": (f"📌 [진입 thesis] {code} 체결가 {fill_price:,.2f} {ccy}\n"
                            f"목표가 {parsed.get('target_price') or '?'} | 손절가 {parsed.get('stop_price') or '?'} | "
                            f"계획 보유 {parsed.get('planned_hold_hours') or '?'}h\n"
                            f"진입 사유: {parsed.get('entry_reason') or buy_reason[:150]}")})
        except Exception as e:
            logger.warning(f"[펀드기획] thesis 기록 실패 {code}: {e}")

    def _sync_thesis_with_current_holdings(self, holdings) -> None:
        """전량 매도된 종목의 thesis 제거 (보유 0주가 된 코드 자동 정리)."""
        try:
            from infra import position_thesis
            codes = [str(h.get("code", "")).strip() for h in (holdings or []) if int(h.get("qty") or 0) > 0]
            removed = position_thesis.sync_with_holdings(self.uid, codes)
            if removed:
                logger.info(f"[펀드기획] 전량 매도로 thesis 제거: {removed}")
        except Exception as e:
            logger.warning(f"[펀드기획] thesis 동기화 실패: {e}")

    async def _cyc_stage_report(self, cyc):
            session = cyc.session
            market_open = cyc.market_open
            news_articles = cyc.news_articles
            news_report = cyc.news_report
            index_report = cyc.index_report
            macro_report = cyc.macro_report
            quant_report = cyc.quant_report
            candidate_codes = cyc.candidate_codes
            target_codes = cyc.target_codes
            sell_directives = cyc.sell_directives
            holdings = cyc.holdings
            order_obj = cyc.order_obj
            buying_power = cyc.buying_power
            risk_result = cyc.risk_result
            risk_approved = cyc.risk_approved
            approved_orders = cyc.approved_orders
            exec_results = cyc.exec_results
            # [10] REPORT
            self.current_state = SwarmState.REPORT
            exec_summary = _format_exec_for_report(exec_results)
            _disposition = _format_order_disposition(risk_result)
            report = await self.orchestrator.think(
                f"사이클 완료.\n지수: {index_report[:200]}\n매크로: {macro_report[:200]}\n"
                f"퀀트: {quant_report[:200]}\n리스크: {'승인' if risk_approved else '미승인'} ({len(approved_orders)}건)\n"
                f"{_disposition}\n"
                f"실매매: {exec_summary} / 누적 체결 {self._trades_executed}건\n"
                f"주의: '접수·체결대기(미확인)'는 실패가 아니라 아직 체결 확인 전 상태다"
                f"(US는 폴링으로 추후 확정, KR 지정가는 호가 미도달). 체결 실패·미체결 사유를 추측하거나 지어내지 말 것 — "
                f"위 실매매 상태 그대로만 서술한다.\n"
                f"**중요**: 위 '주문 처리 결과'에서 반려된 종목, 그리고 아직 미체결인 종목을 "
                f"'매수/매도 완료'·'최종 매수 선정'으로 서술하지 말 것. '후보 선정'과 '리스크 반려'와 '실제 체결'을 "
                f"명확히 구분해 사실대로만 보고한다.\n"
                f"3~5줄로 결과 요약과 다음 사이클 유의사항만.")
            self.cycle_log.final_report = report
            # 사장 지시 2026-05-28(우선순위 3): 사이클 종료 시 thesis 와 현재 보유 동기화 — 전량 매도된 종목 thesis 자동 제거.
            try:
                _sync_snap = await self.broker.kr_account_snapshot()
                _all_holdings = list(_sync_snap.get("holdings") or [])
                # US 보유까지 보장 위해 best-effort 합산(실패 시 KR 만으로 동기화)
                try:
                    _us = await self.broker._overseas_holdings()
                    _all_holdings += list(_us or [])
                except Exception:
                    pass
                self._sync_thesis_with_current_holdings(_all_holdings)
            except Exception as _se:
                logger.warning(f"[펀드기획] 사이클 종료 thesis 동기화 스킵: {_se}")
            await self._emit({"type":"cycle_complete","report":report,"trades_total":self._trades_executed})
            self._cycle_history.append(self.cycle_log.to_dict())

            # ── Persist cycle to SQLite (사장 지시 2026-05-14 — 백테스트/장기 분석용) ──
            new_cycle_id = None
            try:
                new_cycle_id = cycle_store.record_cycle({
                    "uid": self.uid,
                    "started_at": self.cycle_log.started_at,
                    "ended_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
                    "session": session, "market_open": market_open,
                    "news_count": len(news_articles),
                    "candidate_codes": candidate_codes, "target_codes": target_codes,
                    "sell_directives": sell_directives,
                    "orders_planned": order_obj.get("orders", []),
                    "orders_executed": exec_results,
                    "risk_approved": risk_approved,
                    "risk_report": risk_result.get("report", "")[:8000] if isinstance(risk_result, dict) else "",
                    "macro_report": (macro_report or "")[:8000],
                    "quant_report": (quant_report or "")[:8000],
                    "news_report":  (news_report  or "")[:8000],
                    "final_report": (report or "")[:4000],
                    "bp_cash": float(buying_power.get("cash") or 0.0),
                    "bp_total_eval": float(buying_power.get("total_eval") or 0.0),
                    "bp_pnl_ratio": float(buying_power.get("pnl_ratio") or 0.0),
                })
                # holdings_history 갱신 — 보유기간 P&L 추적용
                try:
                    for h in (holdings or []):
                        cycle_store.upsert_holding_seen(h.get("code",""), int(h.get("qty") or 0), float(h.get("avg_price") or 0.0))
                    cycle_store.reconcile_holdings([h.get("code","") for h in (holdings or [])])
                except Exception: pass
                # 사장 지시 2026-05-14: 뉴스 분류 정확도 학습 로그 — 사이클당 1행
                try:
                    _clf = news_classifier_log.classify_articles(news_articles)
                    _cmkt = "US" if session == "US_TRADING" else ("KR" if session in ("KR_TRADING","KR_PRE_MARKET") else "OFF")
                    _cand_kr = sum(1 for c in (candidate_codes or []) if _is_kr_code(c))
                    _cand_us = sum(1 for c in (candidate_codes or []) if not (_is_kr_code(c)))
                    _bought_kr = sum(1 for o in (exec_results or [])
                                     if o.get("ok") and o.get("side") == "buy" and _is_kr_code(o.get("ticker","")))
                    _bought_us = sum(1 for o in (exec_results or [])
                                     if o.get("ok") and o.get("side") == "buy" and not (_is_kr_code(o.get("ticker",""))))
                    news_classifier_log.record({
                        "session": session, "cycle_id": new_cycle_id,
                        "kr_count": _clf["KR"], "us_count": _clf["US"], "both_count": _clf["BOTH"],
                        "cycle_market": _cmkt,
                        "candidates_kr": _cand_kr, "candidates_us": _cand_us,
                        "bought_kr": _bought_kr, "bought_us": _bought_us,
                        "notes": f"market_open={market_open}",
                    })
                except Exception as _e: logger.warning(f"news_classifier_log 기록 실패: {_e}")
            except Exception as _e:
                logger.warning(f"cycle_store 기록 실패: {_e}")

            # ── 운용지원실장 워커 spawn (사장 피드백 2026-05-18):
            #   더 이상 파이썬이 키워드로 도메인을 추측하지 않는다. 항상 운용지원실장(ops_support)
            #   1개만 spawn → 워커가 스스로 진단(Phase A) 후 ① 고칠 게 없으면 팀장 미호출·실장 선
            #   종료, ② 고칠 게 있으면 담당 팀장에게 구체 수정 지시를 위임(자식 프로세스 spawn).
            #   진단/위임/결과 메시지는 모두 워커가 [OPS#cycle] 마커로 직접 대시보드에 기록한다. ──
            if new_cycle_id:
                try:
                    self._spawn_ops_support_worker(new_cycle_id, role="ops_support")
                    await self._emit({"type":"agent_msg","agent":"운용지원실장",
                        "message": (f"🛠 [OPS#{new_cycle_id}] 사이클 #{new_cycle_id} 점검 시작 — "
                                    f"운용지원실장이 직전 사이클을 분석해 조정 가능한 전략 파라미터를 점검합니다. "
                                    f"바꿀 게 없으면 그대로 두고, 필요한 항목만 조정합니다.")})
                except Exception as _e:
                    logger.warning(f"ops_support 워커 spawn 실패: {_e}")

    def stop(self): self._stop_event.set()
    def get_status(self):
        _next_cycle_sec = max(0, int(PERIODIC_CYCLE_SEC - (time.time() - self._last_cycle_at))) if self._last_cycle_at else None
        return {"current_state":self.current_state.value,"session":get_current_session(),
            "time_kst":_now_kst().strftime("%H:%M:%S"),"is_trading":is_market_session_now(),
            "validation_attempts":self.validation_attempts,"cycle_history_count":len(self._cycle_history),
            "pending_news":len(self._pending_news_kr) + len(self._pending_news_us),
            "pending_news_kr":len(self._pending_news_kr),"pending_news_us":len(self._pending_news_us),
            "next_cycle_sec":_next_cycle_sec,
            "trades_executed":self._trades_executed,"trade_log":self._trade_log[-5:],
            "live_trading":LIVE_TRADING,"strategy":runtime.active(uid=self.uid),
            "news_monitor":self.news_monitor.get_status(),
            "watchlist":list(INDEX_WATCHLIST.keys()),
            "schedule":{k:v["desc"] for k,v in SCHEDULE.items()}}
    def get_history(self): return self._cycle_history

# Phase 2 멀티테넌트: 전역 단일 스왐(get_swarm/_swarm) 폐지.
# 스왐은 유저별 UserContext.swarm(lazy ArquantOrchestrator(ctx))로 생성되며,
# 라이프사이클(start/stop)은 server/app.py 의 _start_uid/_stop_uid 가 관리한다.
def get_swarm():
    raise RuntimeError(
        "get_swarm() is retired in Phase 2 — use UserContext.swarm (per-uid). "
        "server/app.py routes by request.state.user_id → REGISTRY.get_or_create(uid).swarm.")
