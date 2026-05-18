"""
Arquant v1.0 - Continuous Market Surveillance & Trading Orchestrator
Watchlist: 10 global indices (KOSPI, KOSDAQ, S&P500, NASDAQ, etc.)
When strategist returns target stocks → 3yr daily + supply crawl + minute chart
"""
import json, re, asyncio, logging, time, difflib, subprocess
from enum import Enum
from typing import Optional, Dict, List, Any
from datetime import datetime, timezone, timedelta

from agents.base_agent import BaseAgent
from agents.specialists import (create_macro_analyst, create_quant_analyst, create_news_analyst,
                                create_trader, create_post_manager, create_ops_support)
from agents.guardrails import create_risk_guard, validate_order_draft
from infra.kis_broker import get_broker, OrderDraft
from infra import cycle_store, news_classifier_log, notifier, metrics
from tools.news_monitor import get_monitor
from tools.dart_disclosure import search_disclosures, get_financial_summary_by_stock_code
# 사장 피드백 2026-05-15 (8차): Tavily → alibaba/tongyi-deepresearch로 전환
from tools.global_search import deep_research
from tools.market_data import (
    crawl_index_snapshot, crawl_company_full, format_quant_data_for_agent, INDEX_WATCHLIST,
    get_index_data, format_indices_for_macro, get_stock_name, fetch_investor_data, _csv_row_count
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
COOLDOWN_AFTER_CYCLE = 0     # 사이클 후 쿨다운 폐지 (어차피 다음 사이클은 1시간 뒤)


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

def is_kr_holiday(d: Optional[datetime] = None) -> bool:
    """오늘이 한국 휴장일이면 True. 주말은 별도 체크가 아니라 SCHEDULE에서 09:00-15:30이 평일이라
    가정함 — 그러나 휴장일+평일 케이스(공휴일)는 여기서 차단해야 사이클이 안 돈다."""
    d = d or _now_kst()
    if d.weekday() >= 5:  # 토(5)·일(6)
        return True
    return d.strftime("%Y-%m-%d") in KR_MARKET_HOLIDAYS

def is_us_holiday(d: Optional[datetime] = None) -> bool:
    """미국 시장 휴장일. NYSE 기준 고정 휴일만 — 음력·이동식은 ops_support가 추후 보완 가능.
    KST 기준 22:30~05:00이 US 정규장이므로 KST의 토/일 야간엔 휴장 (= 미 동부 금/토 낮)."""
    d = d or _now_kst()
    # US 정규장은 KST 22:30~05:00. 미 동부 시간으로는 09:30~16:00.
    # KST 토 새벽 02:00 = 미 동부 금 12:00 (US 장중). KST 일 새벽 = 미 동부 토 = 휴장.
    # 단순화: KST 일·월요일 새벽 시간대(00~05)는 미국이 토/일 오후 → 휴장
    if d.hour < 5 and d.weekday() in (6, 0):  # KST 일/월 새벽
        return True
    if d.hour >= 22 and d.weekday() == 4:  # KST 금 밤 = 미 동부 금 → 정상 (마지막 거래일)
        return False
    if d.hour >= 22 and d.weekday() in (5, 6):  # KST 토/일 밤 = 미 동부 토/일 → 휴장
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

# ─── Real-time event/response log (claude_response.json) ────────────────────
# Accumulates *forever* (per user request) until the dashboard "초기화" button calls
# clear_event_log(). Soft-capped so the file can't grow without bound.
from pathlib import Path as _Path
_RESPONSE_LOG = _Path(__file__).parent / "claude_response.json"
_RESPONSE_LOG_CAP = 4000  # keep at most this many entries on disk
_DISPLAY_EVENT_TYPES = {"status","news","trigger","agent_msg","cycle_complete",
                        "execution_ready","execution_skipped","trade_executed","trade_failed","error"}

def _read_response_log() -> list:
    try:
        if _RESPONSE_LOG.exists():
            d = json.loads(_RESPONSE_LOG.read_text(encoding="utf-8"))
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


def log_response_event(entry: dict):
    """Append an event/response record to claude_response.json (full text, no truncation)."""
    try:
        data = _read_response_log()
        data.append({"ts": _now_kst_iso(), **entry})
        if len(data) > _RESPONSE_LOG_CAP:
            data = data[-_RESPONSE_LOG_CAP:]
        _RESPONSE_LOG.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as _e:
        logger.warning(f"claude_response.json 기록 실패: {_e}")

def get_recent_events(limit: int = 500) -> list:
    """UI replay: the display-relevant events (newest-last), so a page reload restores
    the trade/agent log without re-running anything."""
    evs = [e for e in _read_response_log()
           if e.get("source") == "system_event" and e.get("type") in _DISPLAY_EVENT_TYPES]
    return evs[-max(1, int(limit)):]

# ─── Equity curve (for the 수익률 tab) ──────────────────────────────────────
_EQUITY_LOG = _Path(__file__).parent / "data" / "equity_curve.json"

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
    """Return the inferred external cashflow (deposit +, withdrawal -) between two equity snapshots.
    Heuristic: if holdings map is identical (same codes & same qty) and total_eval delta is large
    AND mostly comes from cash (not stock price moves), treat the cash component as a deposit/withdrawal.
    Returns 0.0 when not detectable / not significant (< 50,000원).
    Conservative — we'd rather miss a deposit than wrongly mask a real P&L move."""
    if not prev:
        return 0.0
    try:
        prev_h = prev.get("holdings") or {}
        curr_h = curr.get("holdings") or {}
    except Exception:
        return 0.0
    # need both snapshots to have holdings recorded (older entries don't)
    if not prev_h or not curr_h:
        return 0.0
    # holdings must be identical (same codes & quantities) — any trade-driven change disqualifies
    if set(prev_h.items()) != set(curr_h.items()):
        return 0.0
    try:
        d_total = float(curr.get("total_eval", 0.0)) - float(prev.get("total_eval", 0.0))
        d_cash = float(curr.get("cash", 0.0)) - float(prev.get("cash", 0.0))
    except Exception:
        return 0.0
    # threshold: small drift is just price/fx — don't tag as flow
    THRESHOLD = 50_000.0  # KRW
    if abs(d_cash) < THRESHOLD:
        return 0.0
    # cash jump must explain most of the total_eval move (else it's mostly price action)
    if abs(d_total) < THRESHOLD * 0.5:
        return 0.0
    if abs(d_total - d_cash) > max(abs(d_cash) * 0.20, 20_000.0):
        return 0.0  # significant stock-value swing too — not a clean deposit
    return d_cash

def record_equity(bp: dict, source: str = "poll", holdings: Optional[List[Dict]] = None):
    """Append a {ts,total_eval,cash,pnl_ratio,holdings,external_flow_cum} point — at most one per 60s.
    Caps at 2000 points. `holdings` (optional list of {code,qty}) is used to detect external cashflow
    (deposits/withdrawals) vs trade-driven changes — see _detect_external_flow()."""
    if holdings is not None:
        try:
            bp = {**bp, "_holdings": holdings}
        except Exception: pass
    try:
        if not bp or not bp.get("ok"):
            return
        data = []
        if _EQUITY_LOG.exists():
            try: data = json.loads(_EQUITY_LOG.read_text(encoding="utf-8"))
            except Exception: data = []
        if not isinstance(data, list): data = []
        now = datetime.now()
        if data:
            try:
                last = _parse_ts_any(data[-1]["ts"])
                if last and (now - last.replace(tzinfo=None) if last.tzinfo else now - last).total_seconds() < 60:
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
        _EQUITY_LOG.write_text(json.dumps(data[-2000:], ensure_ascii=False, indent=2), encoding="utf-8")
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

def _is_trading_kst(dt_kst: datetime) -> bool:
    """Is `dt_kst` inside KR regular hours (09:00-15:30 KST) or US regular hours (22:30-05:00 KST)?"""
    h, m = dt_kst.hour, dt_kst.minute; t = h * 60 + m
    for name in ("kr_trading", "us_trading"):
        s = SCHEDULE[name]; st = s["start"][0] * 60 + s["start"][1]; en = s["end"][0] * 60 + s["end"][1]
        if (st <= t < en) if st <= en else (t >= st or t < en):
            return True
    return False

def get_equity_series(limit: int = 500, view: str = "realtime") -> list:
    """Return equity-curve points for the dashboard chart.
    - view='realtime' : KR/US 거래시간 포인트만, 10분 버킷의 마지막 값으로 다운샘플 (라벨은 'MM-DD HH:M0' KST)
    - view='daily'    : KST 일자별 마지막 포인트 (라벨 'YYYY-MM-DD')
    - view='monthly'  : KST 월별 마지막 포인트 (라벨 'YYYY-MM')
    `limit`은 최종 반환 포인트 수의 상한.
    Each point's `total_eval` is the RAW account value; `adj_total_eval` is the
    deposit/withdrawal-adjusted value (subtracts cumulative external cashflow so the
    chart shows trade-driven P&L only — per 사장 지시 2026-05-14)."""
    try:
        if not _EQUITY_LOG.exists():
            return []
        raw = json.loads(_EQUITY_LOG.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            return []
    except Exception:
        return []
    enriched = []
    for p in raw:
        if not isinstance(p, dict) or not p.get("total_eval"):
            continue
        dt = _ts_to_kst(p.get("ts", ""))
        if not dt:
            continue
        # adjusted total = total_eval − cumulative external cashflow (deposits/withdrawals removed)
        try:
            ext = float(p.get("external_flow_cum", 0.0) or 0.0)
        except Exception:
            ext = 0.0
        try:
            p = {**p, "adj_total_eval": float(p["total_eval"]) - ext}
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
            if not _is_trading_kst(dt):
                continue
            key = dt.strftime("%Y-%m-%d %H:") + f"{(dt.minute // 10) * 10:02d}"
            bucket[key] = (dt, p)
        for k in sorted(bucket):
            dt, p = bucket[k]
            label = dt.strftime("%m-%d %H:") + f"{(dt.minute // 10) * 10:02d}"
            out.append({**p, "label": label, "ts_kst": dt.strftime("%Y-%m-%d %H:%M")})
    return out[-max(1, int(limit)):]

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
        is_us = not (tk.isdigit() and len(tk) == 6)
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
                          "price_source": price_source}
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
                    pnl = (effective_sell - head["price"]) * take
                    pnl_pct = (effective_sell / head["price"] - 1) * 100
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
            e["detail"] = {"matched": matches, "sell_price": shown_sell, "qty": qty,
                          "total_pnl": total_pnl, "unmatched_qty": remaining,
                          "currency": e["est_currency"], "price_source": price_source,
                          "sell_price_inferred": (actual_fill is None and est_price is None)}
    return sorted_evs


def get_trade_history(limit: int = 500) -> list:
    """All trade events (executed/failed), newest first, for the 거래 내역 list.
    사장 피드백 2026-05-15 (5차): 추정 가격 + FIFO 매칭 P&L detail 첨부."""
    evs = [e for e in _read_response_log() if e.get("type") in ("trade_executed", "trade_failed")]
    enriched = _enrich_trade_history(evs[-max(1, int(limit)):])
    return list(reversed(enriched))

def clear_event_log():
    """Wipe the accumulated event/response log (the dashboard '초기화' button)."""
    try:
        _RESPONSE_LOG.write_text("[]", encoding="utf-8")
        log_response_event({"source": "system_event", "type": "status", "state": "IDLE",
                            "message": "로그 초기화됨 (사용자 요청)"})
    except Exception as _e:
        logger.warning(f"claude_response.json 초기화 실패: {_e}")

def clear_trade_log() -> int:
    """Wipe just the trade events from claude_response.json (keeps everything else) + clear in-memory trade log.
    Used by the 수익률 탭 '거래 내역 비우기' button. Returns the number of trade entries removed."""
    removed = 0
    try:
        data = _read_response_log()
        kept = [e for e in data if e.get("type") not in ("trade_executed", "trade_failed")]
        removed = len(data) - len(kept)
        _RESPONSE_LOG.write_text(json.dumps(kept[-_RESPONSE_LOG_CAP:], ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as _e:
        logger.warning(f"거래 내역 초기화 실패: {_e}")
    try:
        sw = _swarm
        if sw is not None:
            sw._trade_log.clear()
            sw._trades_executed = 0
    except Exception:
        pass
    log_response_event({"source": "system_event", "type": "status", "state": "IDLE",
                        "message": f"거래 내역 초기화됨 ({removed}건 제거)"})
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
async def _broadcast(msg):
    try:
        if isinstance(msg, dict): log_response_event({"source":"system_event", **msg})
    except Exception: pass
    if _broadcast_callback:
        try: await _broadcast_callback(msg)
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

class ArquantOrchestrator:
    def __init__(self):
        self.orchestrator = BaseAgent(name="운용전략실장", role="chief_orchestrator", model_key="chief_orchestrator",
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
  `후보종목: 005930, 000660, 012345, 067890, AAPL`  ← 국내 6자리 숫자 / 미국 티커, 정확히 5개

## [최종 매수 종목 결정]
- 계량분석팀장의 정량 평가와 뉴스분석팀장의 감성 분석을 받아, 후보 5개 중에서 **실제 매수할 종목을 좁힙니다.**
- 매수 개수는 프롬프트에 주어진 '최대 매수 개수 N'(전략 프리셋: 보수형 1 / 균형형 2 / 공격형 3)을 넘기지 마십시오.
- 퀀트 점수가 6점 미만이거나 뉴스 감성이 부정적인 종목은 제외합니다. 마땅한 게 없으면 N개보다 적게 골라도 됩니다.
- 응답 **마지막 줄**에 반드시 이 형식으로만:
  `최종종목: 005930, AAPL`  ← 후보 목록에 있던 코드만, N개 이하. 없으면 `최종종목: 없음`

## 공통
- 표에 없는 수치는 추정·생성하지 않습니다. 사장님(@사장)의 직접 지시는 최우선입니다.""")
        self.macro_analyst = create_macro_analyst()
        self.quant_analyst = create_quant_analyst()
        self.news_analyst = create_news_analyst()
        self.trader = create_trader()
        self.risk_guard = create_risk_guard()
        # 사장 피드백 2026-05-18: 수탁자책임실장(policy_filter) 폐지 → 역할은 risk_guard 통합
        self.post_manager = create_post_manager()
        self.ops_support = create_ops_support()
        # 뉴스 헤드라인 사전 선별기 — 40건 초과 시 굵직한 40건만 추리는 경량 페르소나 (대시보드 @멘션은 안 받음)
        # 표시 이름은 사장 지시(2026-05-14)에 따라 '전략리서치팀장'으로 통일 (macro_analyst와 페르소나 공유 — model_key/role은 별도 유지).
        self.news_curator = BaseAgent(
            name="전략리서치팀장", role="news_curator", model_key="news_curator",
            system_prompt=("당신은 ArQuant '전략리서치팀장'의 뉴스 큐레이션 페르소나입니다. 다수의 증권 속보 헤드라인 중 시장·종목 분석에 가장 가치 있는 것만 골라내는 게 이 단계의 역할입니다.\n"
                "선정 기준: ① 실적/M&A/규제/소송/증자·감자/관리종목·거래정지/실적 가이던스/대규모 계약 등 실질 이벤트 우선, "
                "② 단순 시황 요약·일반 사설·반복 속보·재배포는 후순위, ③ 같은 사건 중복 보도는 1건만.\n"
                "응답은 오직 한 줄 — `선정: 1, 4, 7, 12, ...` (1-base 인덱스 콤마 구분). 다른 설명/주석 절대 금지."))
        self.broker = get_broker()
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
        self._trades_executed = 0
        self._trade_log: List[Dict] = []
        self._agents_map = {
            "운용전략실장": self.orchestrator, "전략리서치팀장": self.macro_analyst,
            "계량분석팀장": self.quant_analyst, "뉴스분석팀장": self.news_analyst,
            "트레이딩팀장": self.trader, "리스크관리실장": self.risk_guard,
            "사후관리실장": self.post_manager, "운용지원실장": self.ops_support,
        }
        # 사장 지시 2026-05-14: 운용지원실장 산하 팀장 — 실제 객체는 없고 멘션 → role 매핑만 한다.
        # @투자관리팀장/@경영관리팀장/@재무관리팀장 호출은 ops_support_worker를 해당 role로 spawn.
        self._ops_team_leaders = {
            "투자관리팀장": "investment",
            "경영관리팀장": "operations",
            "재무관리팀장": "finance",
        }

    async def ceo_directive(self, message: str) -> str:
        # 사장 피드백 2026-05-18: 운용지원실장·산하 팀장은 ADMIN(hh09080) 전용 —
        # 비관리자에겐 '존재하지 않는 에이전트'로 취급(보이지도·불리지도 않음).
        _auid, _admin = self._active_actor()
        _ops_names = set(self._ops_team_leaders) | {"운용지원실장"}
        mention = re.search(r"@(\S+)", message)
        if mention:
            name = mention.group(1)
            if (name in _ops_names) and not _admin:
                visible = [n for n in self._agents_map if n not in _ops_names]
                logger.info(f"운용지원 계열 멘션 거부(비관리자 uid={_auid}): @{name}")
                return f"에이전트 '{name}' 없음. 가능: {', '.join(visible)}"
            # 사장 지시 2026-05-14: 운용지원실장 산하 팀장(@투자관리팀장 등) — 동일 워커를 해당 role로 spawn
            if name in self._ops_team_leaders:
                return await self._ops_support_execute(message, role=self._ops_team_leaders[name])
            agent = self._agents_map.get(name)
            if agent:
                # 운용지원실장: 자동 분류 후 적절한 팀장 role로 spawn
                if name == "운용지원실장":
                    return await self._ops_support_execute(message)
                # 일반 에이전트는 대화 페르소나 — 설정/코드는 못 바꿈.
                # ADMIN 계정에서만 운용지원실장 라인을 언급/안내 (비관리자에겐 그 라인 자체가 비공개).
                if _admin:
                    _guide = ("(참고: 당신은 시스템 설정·소스코드를 직접 변경할 수 없습니다. 만약 이 지시가 설정/매매규칙/코드 변경을 요구하는 것이라면 "
                              "의견·이유는 자유롭게 답하시되, '시스템에 적용하려면 운용지원실장 라인을 자동 체이닝하겠다'라는 식으로 진행 의도를 명확히 표시하십시오. "
                              "단순 질의·분석 요청이면 평소 역할대로 답하십시오. '절대 규칙' 같은 말로 무성의하게 거절하지 마십시오.)")
                else:
                    _guide = ("(참고: 당신은 시스템 설정·소스코드를 직접 변경할 수 없습니다. 이 지시가 설정/매매규칙/코드 변경을 요구하면 "
                              "의견·이유는 자유롭게 답하되, 이 계정에서는 시스템 반영이 불가능함을 간단히 알리십시오. "
                              "단순 질의·분석 요청이면 평소 역할대로 답하십시오. 무성의하게 거절하지 마십시오.)")
                resp = await agent.think(f"[🔴 사장 직접 지시] {message}\n\n{_guide}")
                await _broadcast({"type":"agent_msg","agent":name,"message":resp})
                # 자동 체이닝은 ADMIN 계정에서만 — 비관리자는 운용지원실장 라인 자체가 비활성
                if _admin and self._needs_ops_chain(resp):
                    await self._auto_chain_to_ops(message, source_agent=name, source_response=resp)
                return resp
            _avail = [n for n in self._agents_map if (_admin or n not in _ops_names)]
            if _admin:
                _avail = _avail + list(self._ops_team_leaders.keys())
            return f"에이전트 '{name}' 없음. 가능: {', '.join(_avail)}"
        resp = await self.orchestrator.think(f"[🔴 사장 직접 지시] {message}")
        await _broadcast({"type":"agent_msg","agent":"운용전략실장","message":resp})
        # 사장 지시 2026-05-14: 운용전략실장도 자동 체이닝 — 단 ADMIN 계정에서만
        if _admin and self._needs_ops_chain(resp):
            await self._auto_chain_to_ops(message, source_agent="운용전략실장", source_response=resp)
        return resp

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
        분류된 role로 운용지원실장 라인을 호출하고, 사용자에게 체이닝이 일어났음을 알린다."""
        role = self._classify_ops_role(original_message)
        ROLE_DISPLAY = {"ops_support": "운용지원실장", "investment": "투자관리팀장",
                        "operations": "경영관리팀장", "finance": "재무관리팀장"}
        display = ROLE_DISPLAY.get(role, "운용지원실장")
        # 사용자에게 체이닝 사실을 명확히 표시 (UI 로그에 노출)
        await _broadcast({"type": "agent_msg", "agent": "시스템",
                          "message": f"🔁 자동 지명 호출: {source_agent} → {display} (role={role}). "
                                     f"사장님 원지시를 그대로 전달합니다."})
        # 워커는 원래 사장 지시 메시지를 받아 동일하게 처리
        await self._ops_support_execute(original_message, role=role)

    @staticmethod
    def _active_actor() -> tuple:
        """활성(봇을 장악한) 계정의 (user_id, is_admin).

        사장 피드백 2026-05-18: 운용지원실장·산하 팀장(코드수정·서버재시작)은
        ADMIN(hh09080) 전용. 조회 실패 시 **default-deny**(비관리자)로 떨어뜨려
        사고로라도 비관리자 계정에서 전체 소스가 바뀌지 않게 한다."""
        try:
            from infra import credentials as _creds
            _act = _creds.current()
            return _act.get("user_id"), bool(_act.get("is_admin"))
        except Exception as _e:
            logger.warning(f"활성 계정 조회 실패 — 비관리자로 처리: {_e}")
            return None, False

    def _spawn_ops_support_worker(self, cycle_id: Optional[int] = None, manual_directive: Optional[str] = None,
                                  role: str = "ops_support"):
        """Spawn the standalone 운용지원실장 (or sub-leader) worker — fire-and-forget.

        The worker runs in a *separate Python process* so it can edit code without
        racing the running interpreter (which has main_swarm.py already loaded).
        After applying changes, it may trigger start_server.sh, which leaves a
        RESUME_ON_BOOT marker so the new server auto-resumes the watch loop.

        사장 지시 2026-05-14: role 인자로 페르소나 선택 — ops_support(기본) / investment / operations / finance."""
        worker = _Path(__file__).parent / "infra" / "ops_support_worker.py"
        if not worker.exists():
            logger.warning(f"ops_support_worker.py 없음 — 스킵 ({worker})")
            return
        # 사장 피드백 2026-05-18: 운용지원실장·산하 팀장은 ADMIN(hh09080) 전용 기능.
        # 비관리자 활성 계정에서는 워커를 **아예 spawn 하지 않는다** — 코드 수정·서버
        # 재시작·프로필 샌드박스까지 전부 차단(원천 봉쇄). 모든 진입점(@멘션 / 자동
        # 체이닝 / 사이클 후 자동)이 이 한 곳을 통과하므로 여기가 마스터 게이트다.
        _auid, _admin = self._active_actor()
        if not _admin:
            logger.info(f"운용지원 워커 spawn 거부 — 비관리자 계정(uid={_auid}) "
                        f"role={role}, cycle_id={cycle_id} · ADMIN(hh09080) 전용")
            return
        # 사장 피드백 2026-05-18: 운용지원실장 피드백 on/off 토글 — OFF면 spawn 안 함.
        try:
            import runtime as _rt
            if not _rt.ops_feedback_enabled():
                logger.info(f"운용지원 워커 spawn 스킵 — 피드백 토글 OFF "
                            f"(role={role}, cycle_id={cycle_id})")
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
        cmd += ["--actor-admin", "1"]  # 위 게이트에서 ADMIN 확인 완료
        log_path = _Path(__file__).parent / "data" / "ops_support.spawn.log"
        try:
            f = open(log_path, "a", encoding="utf-8", buffering=1)
            f.write(f"\n=== {datetime.now(KST):%Y-%m-%d %H:%M:%S} spawn (role={role}, cycle_id={cycle_id}, manual={'Y' if manual_directive else 'N'}, actor_uid={_auid}, admin={_admin}) ===\n")
            subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT,
                             start_new_session=True, cwd=str(_Path(__file__).parent))
            logger.info(f"운용지원 워커 spawn: role={role}, cycle_id={cycle_id}, actor_uid={_auid}, admin={_admin}")
        except Exception as e:
            logger.warning(f"운용지원 워커 spawn 예외: {e}")

    @staticmethod
    def _classify_ops_role(directive: str) -> str:
        """사장 지시 2026-05-14: 자유 텍스트 지시를 팀장 역할로 분류 (키워드 기반).
        매칭이 없으면 'ops_support'(통합 책임자)로 fallback."""
        d = (directive or "").lower()
        # 재무관리팀장 — 자산/리스크/예산
        finance_kw = ("예산", "비율", "한도", "리스크", "손절", "익절", "stop_loss", "take_profit",
                      "수익률", "p&l", "pnl", "환율", "평가액", "현금", "입출금", "max_daily")
        # 투자관리팀장 — 전략/매매 정책/종목
        invest_kw = ("전략", "프리셋", "preset", "rsi", "macd", "adx", "vwap", "지표", "후보",
                     "종목", "비중", "보유", "매수", "매도", "사이징", "size", "ticker",
                     "anthropic", "tsla", "nvda", "aapl", "msft", "googl", "amzn",
                     "etf", "테마", "섹터", "퀀트", "분석", "신호")
        # 경영관리팀장 — 인프라/UX/모니터링
        ops_kw    = ("대시보드", "ui", "화면", "탭", "버튼", "표시", "출력", "로그", "logging",
                     "엔드포인트", "endpoint", "api", "서버", "모니터", "monitor", "차트", "차트색",
                     "주간 리뷰", "weekly", "cycle_store", "ops_history", "news_classifier",
                     "재시작", "restart", "프로세스", "subprocess", "supervisor")
        # 우선순위: investment > finance > operations.
        # 이유: 사용자 지시는 보통 종목·전략 맥락에서 예산 비율을 함께 언급한다
        # (예: "Anthropic 종목 예산 10% 비중 보유" — '예산' 키워드가 있어도 본질은 종목 정책).
        # 순수 예산·리스크 한도 변경은 investment 키워드가 없으므로 자연히 finance로 떨어진다.
        if any(k in d for k in invest_kw):  return "investment"
        if any(k in d for k in finance_kw): return "finance"
        if any(k in d for k in ops_kw):     return "operations"
        return "ops_support"

    async def _ops_support_execute(self, message: str, role: Optional[str] = None) -> str:
        """@운용지원실장 또는 @투자관리팀장/@경영관리팀장/@재무관리팀장 멘션 처리.
        사장 지시 2026-05-14:
         - 질의형 키워드(이력/히스토리/수정한/변경한/어떤 코드/list)가 보이면 → 즉시 이력 텍스트 응답
         - 그 외 일반 지시 → 키워드 분류 후 해당 role로 워커 spawn (자동 지명 호출).
         - role을 명시적으로 받으면 분류를 건너뛰고 그 role로 spawn (사장이 @팀장 직접 호출한 경우).
        운용지원실장 산하 팀장은 모두 동일 워커를 다른 페르소나(--role)로 실행한다 — 보안 가드는 공유."""
        query_keywords = ("이력", "히스토리", "수정한", "변경한", "어떤 코드", "고친", "고친게",
                          "수정 내역", "변경 내역", "한 게 있", "한게 있", "리스트", "list", "history")
        # 사장 피드백 2026-05-18: 운용지원실장·산하 팀장은 ADMIN(hh09080) 전용.
        # 비관리자 계정에서는 이 라인을 **완전 비활성**으로 취급 — 에이전트로서
        # 어떤 응답·브로드캐스트도 하지 않고, 호출자에게만 사용 불가를 알린다.
        _auid, _admin = self._active_actor()
        if not _admin:
            logger.info(f"운용지원실장 호출 거부 — 비관리자(uid={_auid})")
            return ("⚠️ 운용지원실장·투자관리팀장·경영관리팀장·재무관리팀장은 "
                    "ADMIN(hh09080) 전용 기능이며 이 계정에서는 비활성화되어 있습니다.")

        if any(kw in message for kw in query_keywords):
            from infra import ops_history
            text = ops_history.summary_text(limit=20, only_with_changes=True)
            st = ops_history.stats()
            stats_line = (f"\n\n📊 전체 통계: 워커 실행 {st['runs']}회 / "
                          f"실제 변경 발생 {st['runs_with_changes']}회 / "
                          f"서버 재시작 {st['runs_restarted']}회 / "
                          f"적용된 변경 {st['total_applied_changes']}건 / "
                          f"거부된 변경 {st['total_rejected_changes']}건")
            full = text + stats_line
            await _broadcast({"type": "agent_msg", "agent": "운용지원실장", "message": full})
            return full

        # 사장 피드백 2026-05-18: 운용지원실장 피드백 토글이 OFF면 새 지시는 받지 않는다
        # (과거 이력 조회는 위에서 이미 처리되므로 영향 없음 — 끄면 '안내'만).
        try:
            import runtime as _rt
            if not _rt.ops_feedback_enabled():
                msg = ("⏸ 운용지원실장 피드백이 현재 **꺼짐(OFF)** 상태입니다. "
                       "대시보드의 '운용지원 피드백' 토글을 켜면 지시·자동 사이클 분석이 재개됩니다.")
                await _broadcast({"type": "agent_msg", "agent": "운용지원실장", "message": msg})
                return msg
        except Exception:
            pass

        # role이 명시되지 않았으면 키워드 분류 — 매칭 없으면 ops_support로 떨어짐
        if role is None:
            role = self._classify_ops_role(message)
        ROLE_DISPLAY = {"ops_support": "운용지원실장", "investment": "투자관리팀장",
                        "operations": "경영관리팀장", "finance": "재무관리팀장"}
        display = ROLE_DISPLAY.get(role, "운용지원실장")

        # 사장 피드백 2026-05-16: 사장이 팀장에게 직접 내린 지시 원문도 대시보드 로그에 남긴다
        # (지금까지는 워커 spawn 알림만 보여서 '무슨 지시였는지' 추적 불가했음).
        await _broadcast({"type":"agent_msg","agent":"사장",
                          "message": f"🗣 [사장 → {display}] {message}"})
        self._spawn_ops_support_worker(cycle_id=None, manual_directive=message, role=role)
        if _admin:
            msg = (f"🛠 {display}: 위 지시를 받아 별도 워커가 분석·코드수정·재시작을 수행합니다 (role={role}). "
                   f"결과는 같은 로그에 곧 표시됩니다. "
                   f"('@운용지원실장 수정한 코드 이력 보여줘'로 누적 변경 사항 조회 가능)")
        else:
            msg = (f"🛠 {display}: 위 지시를 분석합니다 (role={role}). "
                   f"⚠️ 이 계정은 ADMIN이 아니므로 공유 소스 코드·서버 재시작은 변경되지 않습니다 "
                   f"(전체 유저 영향 방지). 전략·예산·익절/손절 같은 튜닝 파라미터만 "
                   f"**이 프로필 전용**으로 반영되며, 다음 로그인/계정 적용 시 활성화됩니다. "
                   f"소스 구조 변경이 필요하면 ADMIN(hh09080)에게 요청하세요. "
                   f"('@운용지원실장 이력 보여줘'로 이 프로필 반영 내역 조회 가능)")
        await _broadcast({"type":"agent_msg","agent":display,"message":msg})
        return msg

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
        await _broadcast({"type":"status","state":"DATA_COLLECTION","message":"글로벌 지수 수집 중"})
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
        await _broadcast({"type":"status","state":"DATA_COLLECTION","message":f"종목 {len(codes)}개 데이터 수집 중"})
        loop = asyncio.get_event_loop()
        summaries = []
        for code in codes[:8]:  # cap to avoid rate limits (was 5 — bumped for 6 후보 + 보유 종목)
            if code.isdigit() and len(code) == 6:
                # ── KR 일봉: KIS API 먼저 (페이지네이션으로 ~2년) ──
                kis_rows = 0
                try:
                    kis_rows = len(await self.broker.kr_daily_chart_deep(code, years=2))
                except Exception as e:
                    summaries.append(f"  ⚠ [{code}] KIS 일봉 조회 예외: {e}")
                csv_daily = _csv_row_count(_Path("data") / f"daily_{code}.csv")
                if kis_rows > 0 or csv_daily > 0:
                    summaries.append(f"[{code}] 일봉: KIS +{kis_rows}행 / 누적 {csv_daily}행")
                else:
                    # ── KIS 0행 → 네이버 금융 크롤 폴백 ──
                    s = await loop.run_in_executor(None, crawl_company_full, code)
                    summaries.append(f"  🔁 [{code}] KIS 일봉 0행 → 네이버 폴백\n{s}")
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
                        summaries.append(f"  ⚠ [{code}] 미국 일봉 0행 — KIS API 응답 비어있음")
                except Exception as e:
                    summaries.append(f"  ⚠ [{code}] 미국 일봉 수집 실패: {e}")
            await asyncio.sleep(0.5)

        # Build formatted quant data — KR과 US 종목 모두 (load_daily_csv가 자동 분기)
        quant_parts = []
        for code in codes[:8]:
            quant_parts.append(format_quant_data_for_agent(code))
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
        await _broadcast({"type": "agent_msg", "agent": "전략리서치팀장",
            "message": f"🗂️ 누적 헤드라인 {len(articles)}건 → 결정론적 점수로 굵직한 **{len(picked)}건** 선별 (LLM 미호출, 키워드+종목코드 가중치)"})
        return picked

    async def _collect_per_stock_dart(self, codes: List[str], days: int = 90, limit: int = 8):
        """Per-stock 최근 공시(~90일) + **가장 최근** 분기/반기/연간 요약재무.
        사장 피드백 2026-05-18: 공시 윈도우를 14→90일로 넓혀 관리종목·증자·소송 등을 놓치지 않음.
        Returns (dart_dict, name_map). Best-effort — skips a code on any error."""
        out: Dict[str, str] = {}; names: Dict[str, str] = {}
        loop = asyncio.get_event_loop()
        for code in [c for c in (codes or []) if c.isdigit() and len(c) == 6][:limit]:
            try:
                nm = await loop.run_in_executor(None, get_stock_name, code)
                if nm:
                    names[code] = nm
                    # 최근 공시 (기존 동작)
                    dd = await search_disclosures(corp_name=nm, bgn_de=(datetime.now() - timedelta(days=days)).strftime("%Y%m%d"))
                    # 사장 피드백 #7: 직전연도 요약재무상태표 + 손익계산서 추가
                    fin = await get_financial_summary_by_stock_code(code)
                    sections = [f"{nm}({code})", dd]
                    if fin:
                        sections.append("")
                        sections.append(fin)
                    out[code] = "\n".join(sections)
            except Exception as _e:
                logger.warning(f"per-stock DART {code} 실패: {_e}")
        return out, names

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
        try:
            resp = await self.risk_guard.think(
                f"1차 결정론 검증을 통과한 매수 주문 종목: {', '.join(tickers)}\n\n"
                f"각 종목 — 최근 ~90일 DART 공시 + **가장 최근 가용 분기/반기/연간 요약재무**:\n{digest}\n\n"
                f"① 공시 리스크 신호(관리종목/거래정지/상장폐지심사/횡령·배임/회계감리/불성실공시/감자/대규모 유상증자/주요 소송·제재 등) "
                f"② 재무 적신호(자본잠식·부채>자산·연속 적자·매출 급감) 를 종합해, 해당하는 종목은 반려하십시오. "
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

    async def _build_orders(self, target_codes: List[str], candidate_codes: List[str], quant_report: str, news_report: str,
                            holdings: List[Dict], sell_directives: Optional[Dict[str, str]] = None,
                            market_open: bool = False,
                            entry_dirs: Optional[Dict[str, Dict[str, Any]]] = None):
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
        PER_ORDER_BUDGET_RATIO = runtime.get("PER_ORDER_BUDGET_RATIO"); CONSERVATIVE_STOCK_RATIO = runtime.get("CONSERVATIVE_STOCK_RATIO")
        PER_ORDER_BUDGET_OVERSHOOT = float(runtime.get("PER_ORDER_BUDGET_OVERSHOOT") or 1.20)
        MAX_ORDER_QTY = runtime.get("MAX_ORDER_QTY"); MAX_TRADES_PER_CYCLE = runtime.get("MAX_TRADES_PER_CYCLE")
        ENABLE_SELL_REBALANCE = runtime.get("ENABLE_SELL_REBALANCE"); TAKE_PROFIT_PCT = runtime.get("TAKE_PROFIT_PCT")
        STOP_LOSS_PCT = runtime.get("STOP_LOSS_PCT"); TRIM_OVER_RATIO = runtime.get("TRIM_OVER_RATIO")
        ENABLE_CHEAP_FALLBACK = runtime.get("ENABLE_CHEAP_FALLBACK"); ALLOW_US_STOCKS = runtime.get("ALLOW_US_STOCKS")
        ALLOW_DERIVATIVES = runtime.get("ALLOW_DERIVATIVES")
        report = (quant_report or "") + "\n" + (news_report or "")
        snap = await self.broker.kr_account_snapshot()
        bp = snap["buying_power"]; holdings = holdings or snap.get("holdings") or []
        record_equity(bp, "cycle", holdings=holdings)
        cash = float(bp.get("cash", 0.0) or 0.0)
        total = float(bp.get("total_eval", 0.0) or 0.0) or cash
        # 사장 지시 2026-05-14: 1주 예산은 **총평가액** 기준 (실제 spend는 cash로 캡).
        target_per_order = total * PER_ORDER_BUDGET_RATIO if total > 0 else 0.0
        # 사장 지시 2026-05-14: 개장 사이클은 한도 해제 — 100건 뉴스 정보를 적극 활용해 큰 포지션 가능
        if market_open:
            target_per_order = total * min(1.0, PER_ORDER_BUDGET_RATIO * 5.0)  # 5배 확대 (균형형이면 50%까지)
        per_order_budget = min(target_per_order, cash) if cash > 0 else 0.0
        per_stock_cap = total * CONSERVATIVE_STOCK_RATIO if total > 0 else 0.0
        if market_open:
            # 개장 한도 해제 — 단일 종목 비중 한도도 같이 풀어줘야 큰 사이즈가 통과
            per_stock_cap = total * min(0.6, CONSERVATIVE_STOCK_RATIO * 3.0)
        held_codes = {str(h.get("code","")).strip() for h in holdings}
        orders: List[Dict] = []
        price_map: Dict[str, float] = {}
        notes: List[str] = []

        # ── 1) SELL — 사후관리실장 매도결정 우선, 미언급 종목은 자동 규칙 ───────────────
        _HOLD_WORDS = {"보유", "유지", "hold", "keep", "유보", "관망"}
        _ALL_WORDS  = {"전량", "전부", "모두", "all", "full", "100%", "청산"}
        _HALF_WORDS = {"절반", "반", "1/2", "half", "50%"}
        if ENABLE_SELL_REBALANCE or sell_directives:
            for h in holdings:
                code = str(h.get("code","")).strip(); qty = int(h.get("qty") or 0)
                if not (code.isdigit() and len(code) == 6) or qty < 1:
                    continue
                pnl = float(h.get("pnl_pct") or 0.0); cur = float(h.get("cur_price") or 0.0)
                price_map[code] = cur
                reason = None; sell_qty = 0
                directive = sell_directives.get(code) or sell_directives.get(code.upper())
                if directive is not None:
                    dl = str(directive).strip().lower()
                    if dl in _HOLD_WORDS:
                        continue
                    elif dl in _ALL_WORDS:
                        sell_qty = qty; reason = "사후관리실장 매도 판단 — 전량"
                    elif dl in _HALF_WORDS:
                        sell_qty = max(1, qty // 2); reason = "사후관리실장 매도 판단 — 절반"
                    else:
                        mnum = re.match(r"(\d+)", dl)
                        if mnum:
                            sell_qty = max(1, min(int(mnum.group(1)), qty)); reason = f"사후관리실장 매도 판단 — {sell_qty}주"
                        else:
                            continue  # 알 수 없는 지시 → 보유로 간주
                elif ENABLE_SELL_REBALANCE:
                    # 사후관리실장이 언급 안 한 종목 → 자동 익절/손절/편중축소 (안전망)
                    if pnl >= TAKE_PROFIT_PCT:
                        reason = f"자동 익절 — 평가손익 {pnl:+.1f}% ≥ +{TAKE_PROFIT_PCT:.0f}%"; sell_qty = qty
                    elif pnl <= -STOP_LOSS_PCT:
                        reason = f"자동 손절 — 평가손익 {pnl:+.1f}% ≤ -{STOP_LOSS_PCT:.0f}%"; sell_qty = qty
                    elif TRIM_OVER_RATIO and per_stock_cap > 0 and cur > 0 and (cur * qty) > per_stock_cap:
                        over = int(((cur * qty) - per_stock_cap) // cur) + 1
                        sell_qty = max(1, min(over, qty)); reason = f"편중 축소 — 비중 {cur*qty/total*100:.1f}% > {CONSERVATIVE_STOCK_RATIO*100:.0f}% 한도"
                if reason and sell_qty > 0:
                    orders.append({"ticker": code, "side": "sell", "qty": sell_qty, "price_type": "market",
                                   "market": "KR", "reason": f"{h.get('name',code)} {reason} (보유 {qty}주, 평가손익 {pnl:+.1f}%)"})

        # ── 2) BUY targets ──────────────────────────────────────────────
        affordable_buy_found = False
        for code in (target_codes or [])[:8]:
            code = str(code).strip()
            if not code:
                continue
            is_kr = code.isdigit() and len(code) == 6
            if not is_kr and not ALLOW_US_STOCKS:
                notes.append(f"{code}: 해외주식 비활성(ALLOW_US_STOCKS=False) → 제외"); continue
            ctx = ""
            idx = report.find(code)
            if idx >= 0:
                ctx = report[max(0, idx - 100): idx + 200].replace("\n", " ").strip()
            if is_kr:
                if code in held_codes:
                    notes.append(f"{code}: 이미 보유 → 신규 매수 생략(분산)"); continue
                price = await self.broker.kr_last_price(code)
                await asyncio.sleep(0.25)  # ease KIS TPS
                price_map[code] = price
                if price <= 0 or per_order_budget <= 0:
                    notes.append(f"{code}: 사이즈 산정 불가(가격={price:,.0f}원, 총평가={total:,.0f}원·예수금={cash:,.0f}원) → 제외"); continue
                budget = min(per_order_budget, per_stock_cap) if per_stock_cap > 0 else per_order_budget
                qty = int(budget // price)
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
                _krw_per_usd = 1500.0  # rough; KIS 통합증거금 환산. 정확한 환율은 KIS API에서.
                _budget_usd = (min(per_order_budget, per_stock_cap) if per_stock_cap > 0 else per_order_budget) / _krw_per_usd
                qty_us = int(_budget_usd // us_px) if _budget_usd > 0 else 0
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
                if c.isdigit() and len(c) == 6 and _sess in ("KR_TRADING", "KR_PRE_MARKET"):
                    px = price_map.get(c)
                    if px is None:
                        try: px = await self.broker.kr_last_price(c); await asyncio.sleep(0.2)
                        except Exception: px = 0.0
                        price_map[c] = px
                    if 0 < px <= cap and (best is None or px < best[1]):
                        best = (c, px, "KR", c)
                elif not (c.isdigit() and len(c) == 6) and _sess == "US_TRADING":
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
                us_cands = [str(c).upper() for c in ((candidate_codes or []) + (target_codes or [])) if not (str(c).isdigit() and len(str(c)) == 6)]
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
        await _broadcast({"type": "status", "state": state, "message": message})

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
                await _broadcast({"type":"trade_failed",
                    "message": f"⏰ {ticker} 대기 매수 취소 — 초기가 조회 실패"})
                return
            target_px = initial_px * (1 + watch_pct / 100.0)
            triggered = False
            elapsed_min = 0
            while time.time() - start < max_wait:
                if self._stop_event.is_set():
                    await _broadcast({"type":"trade_failed",
                        "message": f"⏰ {ticker} 대기 매수 취소 — 사용자 중지"})
                    return
                # 시장 마감 체크
                sess = get_current_session()
                if (is_kr and sess not in ("KR_TRADING", "KR_PRE_MARKET")) or (not is_kr and sess != "US_TRADING"):
                    await _broadcast({"type":"trade_failed",
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
                        await _broadcast({"type":"agent_msg","agent":"트레이딩팀장",
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
                await _broadcast({"type":"trade_failed",
                    "message": f"⏰ {ticker} 대기 매수 취소 — 매수 시점에 장 마감"})
                return
            res = await self.broker.place_order(od)
            badge = "⏱ 분봉 진입 매수 발동" if triggered else "⌛ 3시간 타임아웃 매수"
            accepted = all(bad not in res for bad in ("실패", "에러", "거부", "예외", "REJECT"))
            # 체결 확인 (KR)
            filled = False; fill_note = ""
            if accepted and is_kr:
                try:
                    await asyncio.sleep(2.0)
                    self.broker._acct_snap = None
                    after = await self.broker.kr_holdings()
                    before_qty = next((h["qty"] for h in (baseline_holdings or []) if h["code"] == ticker), 0)
                    after_qty = next((h["qty"] for h in after if h["code"] == ticker), 0)
                    if after_qty > before_qty:
                        filled = True; fill_note = f"보유 {before_qty}→{after_qty}주"
                except Exception as _e:
                    fill_note = f"체결확인 실패({_e})"
            if filled or (not is_kr and accepted):
                self._trades_executed += 1
                self._trade_log.append({"ts": _now_kst_iso(), "ticker": ticker, "side": "buy",
                                        "qty": qty, "result": res, "filled": filled, "ok": True, "watch": True})
            await _broadcast({"type": "trade_executed" if filled else "trade_failed",
                "message": f"{badge} — {ticker} {qty}주: {res}" + (f" | {fill_note}" if fill_note else ""),
                "ticker": ticker, "side": "buy", "qty": qty, "filled": filled,
                "trades_total": self._trades_executed})
            metrics.incr("orders_filled" if filled else "orders_unfilled",
                         market="KR" if is_kr else "US")
        except Exception as e:
            logger.error(f"_entry_watch_task({ticker}) 예외: {e}")
            await _broadcast({"type":"trade_failed",
                "message": f"⚠ {ticker} 분봉 모니터 예외 — {e}"})
            metrics.incr("entry_watch_error", ticker=ticker)
            notifier.alert("CRITICAL", "분봉 진입 모니터 예외",
                           f"{ticker} {qty}주 진입 감시 중단 — {e}",
                           dedup_key=f"entry_watch_error:{ticker}")

    async def _reverify_fills(self, pending: List[Dict], baseline_holdings: List[Dict]):
        """체결 미확인 주문을 5분 후 다시 확인 (사장 지시 2026-05-14).
        - pending: [{ticker, side, qty, accepted, filled=False, ...}] 형식의 미확인 주문 목록
        - baseline_holdings: 실행 직전 holdings 스냅샷 (qty 비교 기준)
        체결 안 됐다고 판단되면 trades_executed 카운트 차감 + 보정 메시지 broadcast.
        호가 미체결 주문은 KIS 측에서 장 마감 시 자동 취소되므로 별도 cancel API 호출은 불필요."""
        try:
            await asyncio.sleep(300)  # 5분 대기
            self.broker._acct_snap = None  # force fresh read
            after = await self.broker.kr_holdings()
            adjustments = []
            for e in pending:
                tk = str(e.get("ticker","")).strip()
                side = e.get("side","buy")
                if not (tk.isdigit() and len(tk) == 6):
                    continue  # KR만 보강 (US는 별도 API 필요)
                before_qty = next((h["qty"] for h in (baseline_holdings or []) if h["code"] == tk), 0)
                after_qty = next((h["qty"] for h in after if h["code"] == tk), 0)
                truly_filled = (side == "buy" and after_qty > before_qty) or \
                               (side == "sell" and after_qty < before_qty)
                if truly_filled:
                    adjustments.append(f"✓ {tk} {side} — 5분 후 체결 확인 (보유 {before_qty}→{after_qty})")
                else:
                    # 미체결 확정 — 누적 카운트 차감
                    self._trades_executed = max(0, self._trades_executed - 1)
                    # 사이클 trade_log에서 해당 항목도 제거 (가장 최근 매칭 1개)
                    for i in range(len(self._trade_log) - 1, -1, -1):
                        t = self._trade_log[i]
                        if t.get("ticker") == tk and t.get("side") == side:
                            self._trade_log.pop(i); break
                    adjustments.append(f"✗ {tk} {side} — 5분 경과 미체결 → 누적 차감 (현재 {self._trades_executed}건)")
            if adjustments:
                # 사장 피드백 2026-05-16: 체결 재확인은 시스템이 아니라 트레이딩팀장이 보고.
                await _broadcast({"type": "agent_msg", "agent": "트레이딩팀장",
                    "message": "🕐 5분 후 체결 재확인 결과\n" + "\n".join(adjustments)})
        except Exception as e:
            logger.warning(f"_reverify_fills 예외: {e}")
            # 체결 재확인 실패 = 체결 카운트가 부정확할 수 있음 → 즉시 표면화.
            metrics.incr("reverify_fills_error")
            notifier.alert("CRITICAL", "체결 재확인 실패",
                           f"trades_executed 카운트가 부정확할 수 있음 — {e}",
                           dedup_key="reverify_fills_error")

    async def _equity_poller(self):
        """Poll account balance every 5 min so equity_curve has data points outside analysis cycles.
        Without this the chart would show 9-hour gaps overnight (관측된 버그 2026-05-14)."""
        while not self._stop_event.is_set():
            try:
                snap = await self.broker.kr_account_snapshot()
                bp = snap.get("buying_power") or {}
                if bp.get("ok"):
                    record_equity(bp, "poll", holdings=snap.get("holdings") or [])
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
                if weekly_review.trigger_if_due():
                    await _broadcast({"type": "agent_msg", "agent": "시스템",
                        "message": "📅 [주간 피드백 루프] 토요일 KST — 지난 7일 통계를 운용지원실장에 전달했습니다. 결과는 data/weekly_review.log + data/ops_support.log 참고."})
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
                        await _broadcast({"type": "news", "count": len(unique_added), "ts": self.news_monitor.last_crawl_time,
                            "message": (f"📰 +{len(unique_added)}건 (KR 누적 {len(self._pending_news_kr)} / "
                                        f"US 누적 {len(self._pending_news_us)}) | 크롤 {self.news_monitor.last_crawl_time or ''}"),
                            "articles": [a["title"] for a in unique_added[:5]]})

                # ── cycle trigger: (a) ▶ 실행 직후 첫 회, (b) a market just opened, or
                #                  (c) ≥ PERIODIC_CYCLE_SEC since last cycle — 모두 '장중일 때'만 ──
                first_run = first_run_pending
                market_open = (session in self._MARKET_OPEN_SESSIONS) and (self._last_session not in self._LIVE_SESSIONS)
                periodic_due = (time.time() - self._last_cycle_at) >= PERIODIC_CYCLE_SEC

                # ── 사장 지시 2026-05-14: 사이클 사전 게이트 ──
                # (1) 휴장일 — KR 세션이면 KR 휴장, US 세션이면 US 휴장 체크 → 사이클 스킵
                # (2) cash 부족 — 가용 예수금이 최소 매매 단위(현실적 최저가 1주 ~5000원)도 안 되면 스킵
                # (3) 너무 잦은 사이클 방지 — 직전 사이클이 5분 이내라면 스킵 (트리거 중복 가드)
                skip_reason = None
                if first_run or market_open or periodic_due:
                    if (time.time() - self._last_cycle_at) < 300 and not first_run:
                        skip_reason = f"직전 사이클이 {int((time.time()-self._last_cycle_at)/60)}분 전 — 트리거 중복 스킵"
                    elif session in ("KR_PRE_MARKET", "KR_TRADING") and is_kr_holiday():
                        skip_reason = f"KR 휴장일({_now_kst().strftime('%Y-%m-%d %a')}) — 사이클 스킵"
                    elif session == "US_TRADING" and is_us_holiday():
                        skip_reason = f"US 휴장일/주말 — 사이클 스킵"
                    else:
                        # cash 가용성 체크 — 한 번의 KIS 호출
                        try:
                            _snap = await self.broker.kr_account_snapshot()
                            _cash = float((_snap.get("buying_power") or {}).get("cash", 0.0) or 0.0)
                            if _cash < 5000:  # 최저가 종목 1주도 못 살 정도
                                skip_reason = f"가용 예수금 부족 ({_cash:,.0f}원 < 5,000원) — 분석 비용만 듦, 스킵"
                        except Exception:
                            pass  # 잔고 조회 실패는 진행 (broker가 사이클 안에서 재시도)
                if skip_reason and (first_run or market_open or periodic_due):
                    await self._set_status("MONITORING", f"⏭ 사이클 사전 게이트: {skip_reason}", force=True)
                    self._last_cycle_at = time.time()  # 트리거 재무장 방지 — 다음 1시간 후 재시도
                    first_run_pending = False
                    self._last_session = session
                    for _ in range(NEWS_CHECK_INTERVAL):
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
                for _ in range(NEWS_CHECK_INTERVAL):
                    if self._stop_event.is_set(): break
                    await asyncio.sleep(1)
            except Exception as e:
                self.current_state = SwarmState.ERROR; logger.error(f"루프 오류: {e}")
                await _broadcast({"type": "error", "message": str(e)})
                # 감시 루프 크래시는 '거래 중'인 줄 알고 멈추는 최악의 조용한 실패.
                metrics.incr("engine_loop_error")
                notifier.alert("CRITICAL", "감시 루프 오류 — 30초 후 자동 재시도",
                               str(e), dedup_key="engine_loop_error")
                await asyncio.sleep(30)
        self.current_state = SwarmState.STOPPED; self.news_monitor.is_running = False
        await _broadcast({"type": "status", "state": "STOPPED", "message": "감시 중지됨"})

    async def _run_analysis_cycle(self, news_articles, user_directive, session, market_open: bool = False):
        self.cycle_log = SwarmCycleLog(); self.validation_attempts = 0
        # 누적 헤드라인이 NEWS_PREFILTER_TRIGGER(기본 40)을 넘으면 큐레이터로 굵직한 N건만 선별.
        # 개장(market_open) 사이클은 N을 100으로 상향 — 누적된 종일치 뉴스를 폭넓게 흡수.
        prefilter_limit = 100 if market_open else None  # None → config 기본(NEWS_PREFILTER_LIMIT=40)
        news_articles = await self._prefilter_news(news_articles, limit=prefilter_limit)
        formatted_news = self.news_monitor.format_articles_for_agent(news_articles)
        try:
            # [1] GLOBAL INDEX DATA (validated numbers only — no garbage)
            index_data = await self._collect_index_data()
            index_report = crawl_index_snapshot(index_data)
            index_facts = format_indices_for_macro(index_data)
            # 사장 피드백 2026-05-15 (4차): 글로벌 지수 수집 결과는 전략리서치팀장 이름으로 표기 (이름만, 결과는 그대로 수집된 데이터).
            self.cycle_log.log("DATA", "전략리서치팀장", index_report)
            await _broadcast({"type":"agent_msg","agent":"전략리서치팀장","message":f"📈 지수 수집 완료\n{index_report}"})

            # current holdings — used both to diversify the orchestrator's picks and for fill confirmation
            holdings = []
            try:
                holdings = await self.broker.kr_holdings()
            except Exception: pass
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
                await _broadcast({"type":"agent_msg","agent":"시스템","message":f"♻️ DART 공시 캐시 재사용 ({int((time.time()-_dart_cache['ts'])/60)}분 전) — API 호출 생략"})
            else:
                dart_report = await search_disclosures(bgn_de=(datetime.now()-timedelta(days=3)).strftime("%Y%m%d"))
                _dart_cache.update(ts=time.time(), value=dart_report)
            self.cycle_log.log("DATA", "시스템", dart_report)

            # [3] NEWS ANALYSIS first — macro now reflects the analyzed news (사장 지시 2026-05-14).
            # 개장 사이클은 뉴스 100건이 들어오므로 macro/orchestrator가 모두 그것을 흡수해야 함.
            self.current_state = SwarmState.NEWS_ANALYSIS
            await _broadcast({"type":"status","state":"NEWS_ANALYSIS","message":"뉴스 분석 (증권 속보)"})
            _session_market_label = "미국 장" if session == "US_TRADING" else "국내 장"
            news_report = await self.news_analyst.think(
                f"네이버 금융 '증권 속보' 크롤링 결과입니다 (이번 사이클 누적 {len(news_articles)}건, **{_session_market_label}** 대상):\n{formatted_news}\n\n"
                f"이 뉴스들을 분석해 다음을 정리하십시오:\n"
                f"① 직접 언급되거나 직접 영향을 받는 **종목/업종**과 각각의 호재·악재·이벤트 — 가능하면 종목명(또는 6자리 코드)을 함께 적되, "
                f"뉴스에 실제로 나온 것만 쓰고 모르는 코드는 지어내지 마십시오.\n"
                f"② 시장 전반 분위기·주목 테마 (1~3줄).\n"
                f"③ 매크로(금리/환율/원자재/지정학) 시사점 — 전략리서치팀장 매크로 분석에 영감을 줄 수 있는 포인트 1~3개.\n"
                f"이 분석은 전략리서치팀장 매크로 분석 및 운용전략실장 종목 선정에 최우선으로 반영됩니다.")
            self.cycle_log.log("NEWS", "뉴스분석팀장", news_report)
            await _broadcast({"type":"agent_msg","agent":"뉴스분석팀장","message":news_report})

            # 사장 피드백 2026-05-16: 1시간 정기 사이클에 신규 뉴스가 0건이면 신규 매수 파이프라인
            # (후보 선정·데이터 수집·2패스)을 건너뛰고, 보유 종목 매도 평가(계량분석)만 수행한다.
            _sell_only = (not news_articles) and (not market_open)
            if _sell_only:
                await _broadcast({"type":"agent_msg","agent":"뉴스분석팀장",
                    "message":"🔕 신규 뉴스 없음 — 이번 사이클은 신규 매수 보류, 보유 종목 매도 평가(계량분석)만 진행합니다."})

            # [4] MACRO ANALYSIS — cached up to MACRO_CACHE_TTL_SEC. 개장 사이클(market_open)에선 캐시 무시
            # (방금 분석한 뉴스/지수를 반드시 흡수해야 하므로 새로 호출).
            self.current_state = SwarmState.MACRO_ANALYSIS
            await _broadcast({"type":"status","state":"MACRO_ANALYSIS","message":"매크로 분석"})
            _use_macro_cache = (not market_open) and (time.time() - _macro_cache["ts"]) < MACRO_CACHE_TTL_SEC and _macro_cache["value"]
            if _use_macro_cache:
                macro_report = _macro_cache["value"]
                await _broadcast({"type":"agent_msg","agent":"시스템","message":f"♻️ 매크로 분석 캐시 재사용 ({int((time.time()-_macro_cache['ts'])/60)}분 전 생성) — 전략리서치팀장 LLM 호출 생략"})
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
                _macro_cache.update(ts=time.time(), value=macro_report)
            # 사장 피드백 2026-05-16 (OPS#21): 매크로 LLM이 빈/너무 짧은 응답을 줄 때
            # 전략리서치팀장 칸이 '아무 말 없이' 비어 운용전략실장이 참조할 게 없던 문제.
            # 빈 응답은 명확히 표시하고, 운용전략실장에는 '매크로 분석 불가' 신호를 전달.
            if not (macro_report or "").strip() or len((macro_report or "").strip()) < 40:
                macro_report = "[전략리서치팀장] ⚠️ 매크로 분석 실패(LLM 빈 응답) — 이번 사이클은 검증 지수·뉴스만으로 판단합니다."
                self.cycle_log.log("MACRO", "전략리서치팀장", macro_report)
                await _broadcast({"type":"agent_msg","agent":"전략리서치팀장","message":macro_report})
            else:
                self.cycle_log.log("MACRO", "전략리서치팀장", macro_report)
                await _broadcast({"type":"agent_msg","agent":"전략리서치팀장","message":macro_report})

            # 1주 매수 예산 힌트 — 사장 지시 2026-05-14: **총평가액** 기준 (실제 spend는 예수금으로 캡됨)
            _budget_hint = ""
            try:
                _bp0 = (await self.broker.kr_account_snapshot()).get("buying_power", {}) or {}
                _cash0 = float(_bp0.get("cash", 0.0) or 0.0)
                _total0 = float(_bp0.get("total_eval", 0.0) or 0.0) or _cash0
                _por = float(runtime.get("PER_ORDER_BUDGET_RATIO") or 0.10)
                _pob = min(_total0 * _por, _cash0) if _cash0 > 0 else 0.0
                if _total0 > 0:
                    _budget_hint = (f"\n참고 — 현재 총평가 {_total0:,.0f}원 / 예수금 {_cash0:,.0f}원, "
                        f"1종목당 1주 매수 예산은 약 {_pob:,.0f}원(총평가의 {_por*100:.0f}%, 예수금으로 캡)입니다. "
                        f"1주 가격이 이 예산을 크게 초과하는 초고가 종목은 사실상 매수가 어려우니 후보에서 빼거나 신중히 고르십시오.")
            except Exception:
                pass

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
                await _broadcast({"type":"agent_msg","agent":"운용전략실장",
                    "message":"[후보 종목 선정] 신규 뉴스 없음 — 신규 매수 후보 선정 생략, 보유 종목 매도 평가만 진행합니다."})
            else:
                allocation = await self.orchestrator.think(
                    f"[후보 종목 선정]\n전략리서치팀 매크로 보고:\n{macro_report}\n\n"
                    f"뉴스분석팀장 뉴스 분석 (증권 속보 기반):\n{news_report}\n\n"
                    f"검증된 지수:\n{index_facts}\n\n현재 보유 종목: {holdings_str}\n현재 세션: {session}\n"
                    f"{('사장 지시: '+user_directive if user_directive else '')}\n"
                    f"{_budget_hint}\n\n"
                    f"{session_hint}\n"
                    f"분석할 후보 종목 **정확히 5개**를 고르십시오.\n"
                    f"⚠️ **최우선**: 위 뉴스분석팀장이 짚은 종목/업종을 먼저 후보에 넣으십시오. (뉴스 원문은 제공되지 않으니 뉴스분석팀장 분석만 참고) "
                    f"뉴스 기반 적격 종목이 5개에 못 미칠 때만 매크로 판단으로 나머지를 채우십시오.\n"
                    f"종목명만 보고 종목코드를 임의 생성하지 말고 확실히 아는 코드만 쓰십시오. "
                    f"그 외에는 대형주에 치우치지 말고 시가총액·업종을 분산하고, 위 1주 예산 안에서 매수 가능한(또는 근접한) 종목을 우선하며, 이미 보유한 종목은 가급적 제외하십시오.\n"
                    f"⚠️ 응답 마지막 줄은 반드시 `후보종목: ...` (5개, 다른 텍스트 없이).")
                allocation = _strip_leading_section_marker(allocation, "[후보 종목 선정]", "[최종 매수 종목 결정]")
                self.cycle_log.log("MACRO", "운용전략실장", allocation)
                await _broadcast({"type":"agent_msg","agent":"운용전략실장","message":f"[후보 종목 선정]\n{allocation}"})
            candidate_codes = _extract_codes_after(allocation, "후보종목", "대상종목", "종목코드", "target stocks") or _extract_stock_codes(allocation)
            # Hard filter — enforce session/market boundary on candidates (LLM이 가끔 어김).
            # US 세션엔 KR 6자리 코드 제외, KR 세션엔 US 티커 제외. 장외엔 양쪽 모두 허용.
            if session == "US_TRADING":
                candidate_codes = [c for c in candidate_codes if not (c.isdigit() and len(c) == 6)]
            elif session in ("KR_TRADING", "KR_PRE_MARKET"):
                candidate_codes = [c for c in candidate_codes if c.isdigit() and len(c) == 6]
            candidate_codes = candidate_codes[:5]

            # ── 사장 지시 2026-05-14: 후보 사전 필터 — 데이터 수집/퀀트 평가 전에 예산 초과 종목 제거 ──
            # 1주 가격이 예산의 1.5배를 넘으면 어차피 못 사니까 LLM 비용 낭비 차단.
            try:
                _bp_snap = (await self.broker.kr_account_snapshot()).get("buying_power", {}) or {}
                _cash_pre = float(_bp_snap.get("cash", 0.0) or 0.0)
                _total_pre = float(_bp_snap.get("total_eval", 0.0) or 0.0) or _cash_pre
                _krw_usd_pre = 1500.0
                # 사장 결정 2026-05-16: 사전 필터도 최종 게이트와 **동일한 '예수금 기준 1주' 규칙**을 사용.
                # → 비싼 종목을 계량분석 전에 일관되게 제외(또는 통과)해, "왜 퀀트 후에야 빠지지?" 혼선 제거.
                if _cash_pre > 0:
                    kept, dropped = [], []
                    for c in candidate_codes:
                        try:
                            if c.isdigit() and len(c) == 6:
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
                        await _broadcast({"type":"agent_msg","agent":"운용전략실장",
                            "message": (f"후보 사전 필터 — 1주 가격이 예수금({_cash_pre:,.0f}원)으로 매수 불가라 제외: {', '.join(dropped)}\n"
                                        f"최종 후보 종목: {', '.join(kept) or '없음'}")})
                        candidate_codes = kept
            except Exception as _e:
                logger.warning(f"후보 사전 필터 실패: {_e}")

            # 분석 대상 = 후보 ∪ 보유(KR) — 보유분은 사후관리실장의 매도 판단을 위해 함께 분석
            held_kr = [c for c in (str(h.get("code","")).strip() for h in (holdings or [])) if c.isdigit() and len(c) == 6]
            analysis_codes, _seen = [], set()
            for c in (candidate_codes + held_kr):
                if c and c not in _seen:
                    _seen.add(c); analysis_codes.append(c)
            analysis_codes = analysis_codes[:8]

            # [4] DATA — 3yr daily/supply + 분봉 for analysis_codes, + per-stock DART (+ 종목명 맵)
            company_data = ""; per_dart: Dict[str, str] = {}; name_map: Dict[str, str] = {}
            if analysis_codes:
                await _broadcast({"type":"status","state":"DATA_COLLECTION","message":f"종목 {len(analysis_codes)}개 데이터 수집"})
                company_data = await self._collect_company_data(analysis_codes)
                per_dart, name_map = await self._collect_per_stock_dart(analysis_codes, days=90, limit=8)
                if per_dart:
                    company_data += "\n\n[종목별 최근 DART 공시]\n" + "\n".join(f"[{k}]\n{v}" for k, v in per_dart.items())
                # 사장 피드백 2026-05-15 (4차): 데이터 수집 완료는 계량분석팀장 이름으로 (실제 분석 주체).
                self.cycle_log.log("DATA", "계량분석팀장", f"분석 종목: {analysis_codes}")
                await _broadcast({"type":"agent_msg","agent":"계량분석팀장","message":f"📊 데이터 수집 완료 — 후보 {len(candidate_codes)}개 / 보유 {len(held_kr)}개 (공시 {len(per_dart)}건)"})

                # ── 데이터 없는 종목 자동 제외 가드 (사장 지시 2026-05-14) ──
                # 데이터 수집 후 일봉 누적 0행인 종목은 분석/매수 대상에서 제외 — LLM이 빈 데이터로
                # 0점 평가하는 패턴 차단. 보유 종목은 매도 판단 위해 유지.
                from tools.market_data import load_daily_csv as _load_daily
                held_set = set(held_kr)
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
                        if _c.isdigit() and len(_c) == 6:
                            try:
                                _px = await self.broker.kr_last_price(_c)
                                if _px <= 0:
                                    _delisted_suspects.append(_c)
                            except Exception:
                                _delisted_suspects.append(_c)
                            await asyncio.sleep(0.1)
                    _msg_lines = [f"🚫 일봉 데이터 없는 종목 자동 제외: {', '.join(no_data_codes)} → 후보 {len(candidate_codes)} → {len(_kept)}개"]
                    if _delisted_suspects:
                        _msg_lines.append(f"⚠️ 상장폐지/거래정지 의심: {', '.join(_delisted_suspects)} (다음 사이클 후보 선정 시 사전 확인 필요)")
                    await _broadcast({"type":"agent_msg","agent":"계량분석팀장",
                        "message": "\n".join(_msg_lines)})
                    candidate_codes = _kept

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
            await _broadcast({"type":"status","state":"QUANT_ANALYSIS","message":"퀀트 분석 (종목별 평가)"})
            # 후보·보유 종목 라인 — Pass 2 orchestrator 프롬프트에서 사용 (사장 피드백 2026-05-15 — 누락 복구)
            _cand_line = ", ".join(_nm(c) for c in candidate_codes) or "(없음)"
            _held_line = ", ".join(_nm(c) for c in held_kr) or "(없음)"
            _quant_codes = list(analysis_codes or [])
            # 세션별 시장 필터 — 반대편 시장 종목은 평가에서 제외
            # 사장 피드백 2026-05-16: 미국장 세션엔 KR 종목을 보유분 포함 **완전 제외**.
            # (KR 주문은 어차피 KR 세션에만 체결되므로 매도 판단도 KR 세션에서 수행)
            if session == "US_TRADING":
                _quant_codes = [c for c in _quant_codes if not (c.isdigit() and len(c) == 6)]
            elif session in ("KR_TRADING", "KR_PRE_MARKET"):
                _quant_codes = [c for c in _quant_codes if c.isdigit() and len(c) == 6]
            _quant_scores: Dict[str, int] = {}
            _quant_sections: List[str] = []
            _entry_dirs: Dict[str, Dict[str, Any]] = {}  # 사장 피드백 #4: 진입가 directive 저장
            for _qcode in _quant_codes:
                _qname = _nm(_qcode)
                _is_held = _qcode in held_kr
                _qrole = "보유" if _is_held else "후보"
                _stock_section_marker = f"\n[{_qcode}]"
                _stock_data = ""
                _idx = company_data.find(_stock_section_marker)
                if _idx >= 0:
                    _end = company_data.find("\n[", _idx + 1)
                    _stock_data = company_data[_idx:_end] if _end > _idx else company_data[_idx:_idx + 4000]
                _per_dart_str = per_dart.get(_qcode, "")
                # 자동 재시도: 실패/도구호출JSON 응답 시 최대 2회 retry (사장 피드백 2026-05-18)
                _quant_resp = ""
                _base_prompt = (
                    f"[종목 단독 평가 — {_qrole}] {_qname}\n"
                    f"현재 세션: {session} / 최대 매수 종목 수 (참고): {runtime.get('MAX_TRADES_PER_CYCLE')}\n\n"
                    f"뉴스분석팀장 분석 발췌:\n{news_report[:1200]}\n\n"
                    f"종목 데이터:\n{_stock_data or '(데이터 부족)'}\n\n"
                    f"최근 DART 공시:\n{_per_dart_str or '(공시 없음)'}\n\n"
                    f"위 데이터를 **여러 계량 기법으로 평가**하고, 매수 적합도 0~10점 + 한 줄 코멘트로 응답하십시오. "
                    f"마지막 줄은 반드시 `퀀트점수: {_qcode}=점수` (1종목 1점수)")
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
                await _broadcast({"type":"agent_msg","agent":"계량분석팀장",
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
                _quant_sections.append(f"[{_qrole}] {_qname}\n{_quant_resp}")
                await asyncio.sleep(0.3)
            # 통합 리포트 (이후 운용전략실장·사후관리실장 입력용)
            quant_report = "\n\n---\n\n".join(_quant_sections) if _quant_sections else "[퀀트 평가 데이터 없음]"
            if _quant_scores:
                quant_report += "\n\n퀀트점수: " + ", ".join(f"{k}={v}" for k, v in _quant_scores.items())
            if _entry_dirs:
                quant_report += "\n\n진입가 지시: " + "; ".join(
                    f"{k}={v.get('raw') or v.get('mode')}" for k, v in _entry_dirs.items())
            self.cycle_log.log("QUANT", "계량분석팀장", quant_report)

            # ── PASS 2: 운용전략실장 → 최종 매수 종목 (전략 프리셋에 따라 1~N개) ──────
            _N = int(runtime.get("MAX_TRADES_PER_CYCLE") or 2)
            # 사장 지시 2026-05-14: 개장 사이클은 뉴스 100건 정보를 최대 활용 — 매매 건수 한도 확대
            if market_open:
                _N = min(8, max(_N, _N * 3))  # 3배 또는 8개 중 작은 값
            # _max_trades_runtime은 실행 단계에서도 동일한 한도를 쓰도록 별도 저장
            _exec_N = _N
            if _sell_only or not candidate_codes:
                # 사장 피드백 2026-05-16: 신규 뉴스 없음(또는 후보 0) → 신규 매수 결정 자체를 생략.
                final_view = "최종종목: 없음"
                if _sell_only:
                    await _broadcast({"type":"agent_msg","agent":"운용전략실장",
                        "message":"[최종 매수 종목 결정] 신규 매수 보류 (신규 뉴스 없는 사이클) — 보유 종목 매도 평가로 진행."})
                picked = []
            else:
                final_view = await self.orchestrator.think(
                    f"[최종 매수 종목 결정]\n1차 후보: {_cand_line}\n최대 매수 개수 N = {_N} (전략 프리셋)\n현재 세션: {session}\n"
                    f"{_budget_hint}\n\n"
                    f"전략리서치팀장 자산 배분 권고(전략리서치팀장의 매크로 판단 — 참고하여 반영):\n{macro_report[:600]}\n\n"
                    f"계량분석팀장 평가:\n{quant_report}\n\n뉴스분석팀장 평가:\n{news_report}\n\n"
                    f"후보 {len(candidate_codes)}개 중에서 실제로 매수할 종목을 **N개 이하**로 좁히십시오. "
                    f"퀀트 점수가 낮거나 뉴스 감성이 부정적이거나 1주 예산을 크게 넘는 종목은 빼십시오. 마땅한 게 없으면 더 적게 골라도, 0개여도 됩니다.\n"
                    f"⚠️ **전략리서치팀장**의 주식 비중 권고가 확대면 N개 풀로, 축소면 N개보다 적게(또는 0개), 유지면 N의 절반~80% 수준 매수를 고려하십시오. "
                    f"이는 전략리서치팀장의 매크로 판단이며 사장님 지시가 아닙니다 — 매수 사유를 쓸 때 '사장님 지시/피드백에 따라 비중 축소' 같은 표현을 쓰지 말고, "
                    f"'전략리서치팀장 매크로 권고에 따라'로 정확히 출처를 밝히십시오. (사장님이 직접 비중 지시를 내린 게 아니면 사장님을 근거로 인용 금지)\n"
                    f"⚠️ 응답 마지막 줄은 반드시 `최종종목: ...` (후보 목록에 있던 코드만, N개 이하; 없으면 `최종종목: 없음`).")
                final_view = _strip_leading_section_marker(final_view, "[최종 매수 종목 결정]", "[후보 종목 선정]")
                self.cycle_log.log("DRAFT", "운용전략실장", final_view)
                await _broadcast({"type":"agent_msg","agent":"운용전략실장","message":f"[최종 매수 종목 결정]\n{final_view}"})
                picked = _extract_codes_after(final_view, "최종종목", "대상종목", "종목코드", "target stocks")
            cand_set = set(candidate_codes)
            target_codes = ([c for c in picked if c in cand_set] if cand_set else picked)[:_N]
            # Enforce session boundary on final picks too (defence-in-depth)
            if session == "US_TRADING":
                target_codes = [c for c in target_codes if not (c.isdigit() and len(c) == 6)]
            elif session in ("KR_TRADING", "KR_PRE_MARKET"):
                target_codes = [c for c in target_codes if c.isdigit() and len(c) == 6]

            # ── 사후관리실장: 보유 종목 매도 판단 ─────────────────────────────────
            # 사장 피드백 2026-05-15 (#5): 현재 세션 시장의 보유 종목이 없으면 분석 자체를 시작하지 않음.
            # KR 보유 종목이 있는데 US 세션이면 → 매도 판단 보류, 그 반대도 동일.
            _holdings_this_market = []
            for _h in (holdings or []):
                _hc = str(_h.get("code", "")).strip()
                _is_kr_holding = _hc.isdigit() and len(_hc) == 6
                if session == "US_TRADING" and not _is_kr_holding:
                    _holdings_this_market.append(_h)
                elif session in ("KR_TRADING", "KR_PRE_MARKET", "KR_CLOSE_REVIEW") and _is_kr_holding:
                    _holdings_this_market.append(_h)
                elif session == "OFF_HOURS":
                    _holdings_this_market.append(_h)  # 장외엔 양쪽 모두 분석 가능
            sell_directives: Dict[str, str] = {}
            if _holdings_this_market:
                await _broadcast({"type":"status","state":"QUANT_ANALYSIS","message":"사후관리 — 보유 종목 매도 판단"})
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
                _allow_day = bool(runtime.get("ALLOW_DAY_TRADING"))
                _min_hold = float(runtime.get("MIN_HOLDING_DAYS_FOR_SELL") or 0.0)
                if _allow_day:
                    _hold_guide = "보유기간은 참고만 — **데이트레이딩 허용(전략 설정)**, 신호가 명확하면 단기 매도 OK."
                else:
                    _hold_guide = (f"**보유기간 가중** — {_min_hold}일 미만은 데이트레이딩 회피, "
                                   f"7일 이상은 트렌드 점검.")
                pm_view = await self.post_manager.think(
                    f"전략리서치팀장 매크로 보고:\n{macro_report}\n\n현재 보유 종목: {holdings_str}\n\n"
                    f"보유기간 (Arquant 자체 관찰):\n{holding_period_str}\n\n"
                    f"계량분석팀장 평가:\n{quant_report}\n\n뉴스분석팀장 평가:\n{news_report}\n\n"
                    f"위 매크로 → 퀀트 → 뉴스 → 평가손익 순으로 가중해 보유 종목별로 매도/보유를 결정하십시오. "
                    f"{_hold_guide} "
                    f"마지막 줄은 반드시 `매도결정: 코드=전량/절반/보유, ...` (보유 종목 전체).")
                self.cycle_log.log("RISK", "사후관리실장", pm_view)
                await _broadcast({"type":"agent_msg","agent":"사후관리실장","message":pm_view})
                sell_directives = _parse_sell_decisions(pm_view)
                holdings = _orig_holdings  # 후속 처리(주문 조립·이력 기록)는 전체 보유 종목 사용
                # 사장 피드백 2026-05-15 (#5, #13): 반대편 시장 보유 종목은 자동 '보유' 처리.
                # 사후관리실장이 분석을 안 했으므로 _build_orders의 자동 익절/손절 룰이 잘못 매도하는 것 방지.
                _existing_lower = {k.lower() for k in sell_directives.keys()}
                for _h in (_orig_holdings or []):
                    _hc = str(_h.get("code", "")).strip()
                    if not _hc or _hc.lower() in _existing_lower:
                        continue
                    _is_kr_h = _hc.isdigit() and len(_hc) == 6
                    _opposite = ((session == "US_TRADING" and _is_kr_h) or
                                 (session in ("KR_TRADING", "KR_PRE_MARKET", "KR_CLOSE_REVIEW") and not _is_kr_h))
                    if _opposite:
                        sell_directives[_hc] = "보유"
            elif holdings:
                # 반대편 시장 보유만 있는 상황 — 사장 피드백 2026-05-15 (#5, #13): 분석 생략 + 전체 자동 '보유'.
                _other_mkt = "US" if session in ("KR_TRADING", "KR_PRE_MARKET", "KR_CLOSE_REVIEW") else "KR"
                await _broadcast({"type":"agent_msg","agent":"사후관리실장",
                    "message": f"📭 현재 세션({session})의 보유 종목 없음 — 분석 생략 ({_other_mkt} 보유분은 해당 시장 사이클에서 처리)."})
                for _h in (holdings or []):
                    _hc = str(_h.get("code", "")).strip()
                    if _hc:
                        sell_directives[_hc] = "보유"
            else:
                await _broadcast({"type":"agent_msg","agent":"사후관리실장","message":"보유 종목 없음 — 매도 판단 불필요"})

            # [7] ORDER DRAFT — assembled in Python: buys = 2패스 최종종목, sells = 사후관리실장 매도결정.
            #     The trader LLM is only invoked as a *fallback* when Python has nothing to act on.
            self.current_state = SwarmState.ORDER_DRAFTING
            await _broadcast({"type":"status","state":"ORDER_DRAFTING","message":"주문 초안 (잔고 비율 기반 사이징)"})
            order_obj, price_map, buying_power = await self._build_orders(
                target_codes, candidate_codes, quant_report, news_report, holdings, sell_directives=sell_directives,
                market_open=market_open, entry_dirs=_entry_dirs)
            order_draft = json.dumps(order_obj, ensure_ascii=False, indent=2)
            # 사장 피드백 2026-05-15 (3차): 트레이더 호출은 EXECUTION 이후로 이동.
            # 여기서는 시스템 상태 메시지로 "조립 완료, 리스크 검증 이동" 만 알린다.
            if order_obj["orders"]:
                _drafting_msg = f"주문 {len(order_obj['orders'])}건 조립 완료 → 리스크 검증으로 이관"
            else:
                _drafting_msg = "ℹ️ 잔고/한도 조건상 신규 주문 없음. " + ("; ".join(order_obj.get("sizing_notes") or []))[:240]
            await _broadcast({"type":"status","state":"ORDER_DRAFTING","message": _drafting_msg})
            self.cycle_log.log("DRAFT", "트레이딩팀장", order_draft)

            # [8] RISK — 사장 지시 2026-05-14: 1차(결정론) + 2차(DART 공시) 통합 실행 후 **한 번에** 출력
            self.current_state = SwarmState.RISK_VALIDATION
            await _broadcast({"type":"status","state":"RISK_VALIDATION","message":"리스크 검증 (결정론 + DART)"})
            self.validation_attempts = 1
            risk_result = validate_order_draft(order_draft, "", buying_power=buying_power, price_map=price_map)
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
            await _broadcast({"type":"agent_msg","agent":"리스크관리실장","message":unified_msg})

            # [9] EXECUTION — place KIS orders: sells(사후관리) 우선, 매수는 전략 N개까지. qty from sizing.
            self.current_state = SwarmState.EXECUTION
            exec_results: List[Dict] = []
            # 사장 지시 2026-05-14: 개장 사이클은 _exec_N(Pass2에서 확대됨)을 사용
            _max_trades = _exec_N if market_open else runtime.get("MAX_TRADES_PER_CYCLE"); _max_qty = runtime.get("MAX_ORDER_QTY")
            if risk_approved and LIVE_TRADING and approved_orders:
                _ap_sells = [r for r in approved_orders if (r.get("side") or "buy") == "sell"]
                _ap_buys  = [r for r in approved_orders if (r.get("side") or "buy") != "sell"]
                _exec_list = _ap_sells + _ap_buys[:int(_max_trades or 2)]
                for r in _exec_list:
                    tk = str(r.get("ticker") or "").strip()
                    qty = max(1, int(r.get("qty") or 1))
                    if _max_qty and _max_qty > 0:
                        qty = min(qty, _max_qty)
                    is_kr = tk.isdigit() and len(tk) == 6
                    # 사장 피드백 2026-05-15 (#4): _build_orders에서 첨부한 entry_mode로 분기.
                    _emode = r.get("entry_mode") or "market"
                    _elimit = r.get("entry_limit")
                    _ewatch_pct = r.get("entry_watch_pct")
                    _side = r.get("side") or "buy"
                    # 매도는 항상 시장가 (사후관리 결정에 따른 즉시 청산).
                    if _side == "sell":
                        _emode = "market"
                    # 대기(watch) 모드 — 백그라운드 태스크 spawn, 즉시 체결 대기 카운트 X
                    if _emode == "watch" and _ewatch_pct is not None:
                        asyncio.create_task(self._entry_watch_task(
                            ticker=tk, qty=qty, market=("KR" if is_kr else "US"),
                            watch_pct=float(_ewatch_pct), baseline_holdings=list(holdings or []),
                            reason=r.get("reason", "")))
                        await _broadcast({"type":"agent_msg","agent":"트레이딩팀장",
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
                    # 사장 피드백 2026-05-15 (#8, #15): "체결"은 holdings 변동이 확인됐을 때만 카운트.
                    # 접수(accepted=True)만으로는 누적 실매매 카운트를 올리지 않는다 — 호가 미체결 가능성 있음.
                    # 미국 종목은 KIS holdings 즉시 확인이 어렵기 때문에 일단 'accepted'로 잠정 카운트 후,
                    # 5분 후 _reverify_fills가 실제 보유 변동 없으면 차감한다.
                    is_us = not is_kr
                    ok = filled or (is_us and accepted)  # KR: 체결 확인 필수 / US: 접수=잠정 체결
                    rec = {"ticker": tk, "side": od.side, "qty": qty, "result": res,
                           "accepted": accepted, "filled": filled, "fill_note": fill_note, "ok": ok,
                           "fill_price": fill_price, "fill_currency": ("USD" if is_us else "KRW")}
                    exec_results.append(rec)
                    if ok:
                        self._trades_executed += 1
                        self._trade_log.append({"ts": _now_kst_iso(), **rec})
                    badge = "✅ 실매매 체결확인" if filled else ("📨 주문 접수(체결 미확인 — 5분 후 재확인)" if accepted else "⚠ 주문 실패")
                    await _broadcast({"type": "trade_executed" if filled else "trade_failed",
                        "message": f"{badge} — {res}" + (f" | {fill_note}" if fill_note else ""),
                        "ticker": tk, "side": od.side, "qty": qty, "filled": filled,
                        "fill_price": fill_price, "fill_currency": ("USD" if is_us else "KRW"),
                        "trades_total": self._trades_executed})
                    self.cycle_log.log("EXEC", "시스템", f"{tk} {od.side} x{qty} → {res} | {fill_note}")
                # 사장 피드백 2026-05-15 (#15): 체결과 접수 분리 표기
                _filled_cnt = sum(1 for e in exec_results if e.get("filled"))
                _accepted_only = sum(1 for e in exec_results if e.get("accepted") and not e.get("filled"))
                _exec_msg = f"실행 완료 — 이번 사이클 체결 {_filled_cnt}건"
                if _accepted_only:
                    _exec_msg += f" (+ 접수만 {_accepted_only}건, 5분 후 재확인)"
                _exec_msg += f" / 누적 체결 {self._trades_executed}건"
                await _broadcast({"type":"execution_ready", "message": _exec_msg, "draft": order_draft})
                # ── 사장 지시 2026-05-14: 5분 후 미체결 재확인 백그라운드 태스크 spawn ──
                # 즉시 holdings 조회는 체결을 못 잡을 수 있으므로 5분 더 기다린 후 재검증.
                # accepted=True/filled=False인 주문이 실제로 안 채워졌으면 trades_executed 카운트 차감.
                _unconfirmed = [e for e in exec_results if e.get("accepted") and not e.get("filled")]
                if _unconfirmed:
                    asyncio.create_task(self._reverify_fills(_unconfirmed, list(holdings or [])))
            elif risk_approved and not LIVE_TRADING:
                await _broadcast({"type":"execution_ready","message":"✅ 리스크 승인 (LIVE_TRADING=False — 실주문 생략)","draft":order_draft})
            else:
                await _broadcast({"type":"execution_skipped","message":"❌ 리스크 미승인 — 실행 없음"})

            # [9.5] 트레이딩팀장 체결 보고 — 결정론적 템플릿 (사장 피드백 2026-05-18)
            # 더 이상 LLM을 호출하지 않는다: 보고 내용이 전부 사실(종목·수량·체결여부·체결가·사유)
            # 이므로 고정 양식으로 조립하면 일관·정확·무비용. (체결 vs 접수 혼동도 구조적으로 차단.)
            try:
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
                        _badge = "📨 접수(5분 후 재확인)"
                        _note = "호가 미체결 가능 — 5분 후 보유 변동 재확인"
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
                await _broadcast({"type":"agent_msg","agent":"트레이딩팀장", "message": _trader_msg})
                self.cycle_log.log("REPORT", "트레이딩팀장", _trader_msg)
            except Exception as _te:
                logger.warning(f"트레이더 체결 보고 조립 실패: {_te}")

            # [10] REPORT
            self.current_state = SwarmState.REPORT
            exec_summary = "; ".join(f"{e['ticker']} {e['side']} x{e['qty']} → {'OK' if e['ok'] else '실패'}" for e in exec_results) or "없음"
            report = await self.orchestrator.think(
                f"사이클 완료.\n지수: {index_report[:200]}\n매크로: {macro_report[:200]}\n"
                f"퀀트: {quant_report[:200]}\n리스크: {'승인' if risk_approved else '미승인'} ({len(approved_orders)}건)\n"
                f"실매매: {exec_summary} / 누적 체결 {self._trades_executed}건\n"
                f"3~5줄로 결과 요약과 다음 사이클 유의사항만.")
            self.cycle_log.final_report = report
            await _broadcast({"type":"cycle_complete","report":report,"trades_total":self._trades_executed})
            self._cycle_history.append(self.cycle_log.to_dict())

            # ── Persist cycle to SQLite (사장 지시 2026-05-14 — 백테스트/장기 분석용) ──
            new_cycle_id = None
            try:
                new_cycle_id = cycle_store.record_cycle({
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
                    _cand_kr = sum(1 for c in (candidate_codes or []) if str(c).isdigit() and len(str(c)) == 6)
                    _cand_us = sum(1 for c in (candidate_codes or []) if not (str(c).isdigit() and len(str(c)) == 6))
                    _bought_kr = sum(1 for o in (exec_results or [])
                                     if o.get("ok") and o.get("side") == "buy" and str(o.get("ticker","")).isdigit() and len(str(o.get("ticker",""))) == 6)
                    _bought_us = sum(1 for o in (exec_results or [])
                                     if o.get("ok") and o.get("side") == "buy" and not (str(o.get("ticker","")).isdigit() and len(str(o.get("ticker",""))) == 6))
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
                    await _broadcast({"type":"agent_msg","agent":"운용지원실장",
                        "message": (f"🛠 [OPS#{new_cycle_id}] 사이클 #{new_cycle_id} 점검 시작 — "
                                    f"운용지원실장이 데이터 부족·출력 깨짐 등 버그를 직접 진단합니다. "
                                    f"고칠 게 없으면 실장 선에서 종료, 있으면 담당 팀장에게 구체 수정만 위임합니다.")})
                except Exception as _e:
                    logger.warning(f"ops_support 워커 spawn 실패: {_e}")

        except Exception as e:
            self.current_state = SwarmState.ERROR; logger.error(f"사이클 오류: {e}")
            if self.cycle_log: self.cycle_log.log("ERROR","시스템",str(e)); self._cycle_history.append(self.cycle_log.to_dict())
            try:
                cycle_store.record_cycle({
                    "started_at": self.cycle_log.started_at if self.cycle_log else _now_kst().strftime("%Y-%m-%d %H:%M:%S"),
                    "ended_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
                    "session": session, "market_open": market_open,
                    "news_count": len(news_articles) if news_articles else 0,
                    "error": str(e)[:2000],
                })
            except Exception: pass

    def stop(self): self._stop_event.set()
    def get_status(self):
        _next_cycle_sec = max(0, int(PERIODIC_CYCLE_SEC - (time.time() - self._last_cycle_at))) if self._last_cycle_at else None
        return {"current_state":self.current_state.value,"session":get_current_session(),
            "time_kst":_now_kst().strftime("%H:%M:%S"),"is_trading":is_trading_hours(),
            "validation_attempts":self.validation_attempts,"cycle_history_count":len(self._cycle_history),
            "pending_news":len(self._pending_news_kr) + len(self._pending_news_us),
            "pending_news_kr":len(self._pending_news_kr),"pending_news_us":len(self._pending_news_us),
            "next_cycle_sec":_next_cycle_sec,
            "trades_executed":self._trades_executed,"trade_log":self._trade_log[-5:],
            "live_trading":LIVE_TRADING,"strategy":runtime.active(),
            "news_monitor":self.news_monitor.get_status(),
            "watchlist":list(INDEX_WATCHLIST.keys()),
            "schedule":{k:v["desc"] for k,v in SCHEDULE.items()}}
    def get_history(self): return self._cycle_history

_swarm: Optional[ArquantOrchestrator] = None
def get_swarm():
    global _swarm
    if _swarm is None: _swarm = ArquantOrchestrator()
    return _swarm
