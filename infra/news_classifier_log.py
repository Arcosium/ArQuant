"""
News market-classification learning log (사장 지시 2026-05-14).

Each cycle appends one row tracking:
  - 분류 분포 (KR/US/BOTH 헤드라인 카운트)
  - 실제 사이클이 매매 시도한 시장
  - 후보 선정 결과 (어느 시장의 종목을 선택했는가)

운용지원실장이 주간 피드백 루프에서 이 로그를 분석해
NaverFinanceMonitor.classify_market의 키워드 가중치 조정을 제안할 수 있다.
"""
from __future__ import annotations
import sqlite3, threading, logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional

logger = logging.getLogger("NEWS_CLF")
KST = timezone(timedelta(hours=9))
DB_PATH = Path(__file__).parent.parent / "data" / "news_classifier.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS classifications (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    session       TEXT,
    cycle_id      INTEGER,
    kr_count      INTEGER DEFAULT 0,
    us_count      INTEGER DEFAULT 0,
    both_count    INTEGER DEFAULT 0,
    cycle_market  TEXT,        -- 'KR' / 'US' / 'OFF' (사이클이 어느 시장을 매매하려 했는가)
    candidates_kr INTEGER DEFAULT 0,
    candidates_us INTEGER DEFAULT 0,
    bought_kr     INTEGER DEFAULT 0,
    bought_us     INTEGER DEFAULT 0,
    notes         TEXT
);
CREATE INDEX IF NOT EXISTS idx_clf_ts ON classifications(ts);
CREATE INDEX IF NOT EXISTS idx_clf_session ON classifications(session);
"""

_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None

def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, isolation_level=None)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(_SCHEMA)
    return _conn

def _now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

def record(meta: Dict) -> Optional[int]:
    """Persist one cycle's classification-vs-execution stats.
    Caller passes a flat dict matching the schema; missing fields → NULL/0."""
    cols = ("ts","session","cycle_id","kr_count","us_count","both_count",
            "cycle_market","candidates_kr","candidates_us","bought_kr","bought_us","notes")
    vals = []
    for c in cols:
        v = meta.get(c)
        if c == "ts" and not v:
            v = _now_kst()
        vals.append(v)
    try:
        with _lock:
            cur = _get_conn().execute(
                f"INSERT INTO classifications ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
                vals)
            return cur.lastrowid
    except Exception as e:
        logger.warning(f"news_classifier_log.record 실패: {e}")
        return None

def classify_articles(articles: List[Dict]) -> Dict[str, int]:
    """Count {KR, US, BOTH} buckets from a list of article dicts (with 'market' field)."""
    out = {"KR": 0, "US": 0, "BOTH": 0}
    for a in (articles or []):
        m = (a.get("market") or "BOTH").upper()
        if m in out: out[m] += 1
        else: out["BOTH"] += 1
    return out

def recent_stats(days: int = 7) -> Dict:
    """Returns aggregate stats for the last N days — used by the weekly review."""
    cutoff = (datetime.now(KST) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        with _lock:
            row = _get_conn().execute(
                "SELECT SUM(kr_count) k, SUM(us_count) u, SUM(both_count) b, "
                "SUM(candidates_kr) ckr, SUM(candidates_us) cus, "
                "SUM(bought_kr) bkr, SUM(bought_us) bus, COUNT(*) n "
                "FROM classifications WHERE ts >= ?", (cutoff,)).fetchone()
        if not row:
            return {}
        return {"days": days,
                "cycles": row["n"] or 0,
                "headlines_kr": row["k"] or 0,
                "headlines_us": row["u"] or 0,
                "headlines_both": row["b"] or 0,
                "candidates_kr": row["ckr"] or 0,
                "candidates_us": row["cus"] or 0,
                "bought_kr": row["bkr"] or 0,
                "bought_us": row["bus"] or 0}
    except Exception as e:
        logger.warning(f"recent_stats 실패: {e}")
        return {}
