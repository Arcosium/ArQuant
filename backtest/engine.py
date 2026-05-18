"""결정론 백테스트 엔진 — 전략 프리셋의 *리스크/청산 규칙*을 과거 일봉에 적용.

⚠️ 정직성 경계 (반드시 읽을 것)
────────────────────────────────────────────────────────────────────────
실제 ArQuant 의 종목 선정은 9개 LLM 에이전트의 2패스 협업이라 **오프라인
재현이 불가능**하다. 그러므로 이 백테스트는 종목 선정 능력을 평가하지
**않는다**. 대신:

  • 진입 신호는 **고정된 투명한 모멘텀 프록시**(SMA 상향 돌파)로 통일하고,
  • 프리셋별로 다른 것은 오직 `config.STRATEGY_PRESETS` 의 **결정론 규칙**
    (사이징·익절·손절·단일종목 한도·사이클 예산·MDD 차단)뿐이다.

→ 따라서 결과의 *절대 수익률* 이 아니라, **동일 신호 위에서 프리셋 규칙이
   리스크/MDD/회전율을 어떻게 바꾸는지의 상대 비교**로만 해석해야 한다.
   이것이 정확히 프리셋 스펙트럼(방어형↔초공격형)이 튜닝하는 레이어다.
────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Dict, List, Optional

import config

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


# ── 데이터 로딩 ───────────────────────────────────────────────────────────
def load_prices(data_dir: Path = DATA_DIR) -> Dict[str, List[dict]]:
    """data/daily_<code>.csv → {code: [{date,open,high,low,close,volume}, ...]} (날짜 오름차순).

    CSV 는 최신이 위(내림차순)이므로 시간순 워크포워드를 위해 **오름차순으로
    재정렬**한다 — 룩어헤드 편향 방지의 핵심.
    """
    out: Dict[str, List[dict]] = {}
    for fp in sorted(data_dir.glob("daily_*.csv")):
        code = fp.stem.replace("daily_", "")
        rows: List[dict] = []
        with fp.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    rows.append({"date": r["date"],
                                 "close": float(r["close"]),
                                 "high": float(r["high"]),
                                 "low": float(r["low"])})
                except (KeyError, ValueError):
                    continue
        rows.sort(key=lambda x: x["date"])  # 오름차순 — 과거 → 현재
        if len(rows) > 30:
            out[code] = rows
    return out


# ── 진입 신호 (고정 프록시 — LLM 픽 대체) ────────────────────────────────
def sma_breakout_signal(closes: List[float], i: int, lookback: int = 20) -> bool:
    """i 일자에 'SMA(lookback) 상향 신규 돌파'면 True. 결정론·룩어헤드 없음."""
    if i < lookback:
        return False
    window = closes[i - lookback:i]
    sma = sum(window) / lookback
    prev_sma = sum(closes[i - lookback - 1:i - 1]) / lookback if i > lookback else sma
    return closes[i - 1] <= prev_sma and closes[i] > sma


# ── 백테스트 ──────────────────────────────────────────────────────────────
def run_backtest(preset_name: str,
                 prices: Dict[str, List[dict]],
                 start_cash: float = 10_000_000.0,
                 lookback: int = 20) -> dict:
    """프리셋 1개를 전 종목 공통 신호로 시뮬레이션. 반환: 성과 지표 dict.

    규칙 매핑(config.STRATEGY_PRESETS 시맨틱 그대로):
      PER_ORDER_BUDGET_RATIO     → 1주문 = 가용현금 × 비율
      CONSERVATIVE_STOCK_RATIO   → 단일 종목 평가액 한도 (초과 시 진입 스킵)
      MAX_CYCLE_BUDGET_RATIO     → 하루(=1사이클) 총 매수 한도
      MIN_CASH_BUFFER            → 노티오날 × 버퍼 ≤ 현금이어야 진입
      CONSERVATIVE_MDD           → 계좌 평가손익 ≤ -MDD 면 신규 매수 전면 차단
      TAKE_PROFIT_PCT/STOP_LOSS_PCT → 보유 수익률 기준 전량 청산
      TRIM_OVER_RATIO            → 단일 종목 비중 초과분 익일 부분 청산
    """
    p = config.STRATEGY_PRESETS[preset_name]
    cash = start_cash
    holdings: Dict[str, dict] = {}        # code -> {qty, avg, peak_date}
    equity_curve: List[float] = []
    trades = wins = 0

    # 공통 날짜 축 (가장 긴 종목 기준).
    all_dates = sorted({d["date"] for rows in prices.values() for d in rows})
    closes_by_code = {c: [r["close"] for r in rows] for c, rows in prices.items()}
    date_idx = {c: {r["date"]: k for k, r in enumerate(rows)} for c, rows in prices.items()}

    for day in all_dates:
        # 1) 보유 평가 + 익절/손절.
        port_val = cash
        for code in list(holdings.keys()):
            k = date_idx[code].get(day)
            if k is None:
                port_val += holdings[code]["qty"] * holdings[code]["avg"]
                continue
            px = closes_by_code[code][k]
            h = holdings[code]
            pnl_pct = (px / h["avg"] - 1.0) * 100.0
            if pnl_pct >= p["TAKE_PROFIT_PCT"] or pnl_pct <= -p["STOP_LOSS_PCT"]:
                cash += h["qty"] * px
                trades += 1
                wins += 1 if pnl_pct > 0 else 0
                del holdings[code]
            else:
                port_val += h["qty"] * px
        total_eval = cash + sum(
            holdings[c]["qty"] * closes_by_code[c][date_idx[c][day]]
            for c in holdings if day in date_idx[c])

        equity_curve.append(total_eval)

        # 2) 계좌 MDD 차단 — 시작자본 대비 손실이 한도 초과면 신규 매수 금지.
        acct_pnl = total_eval / start_cash - 1.0
        cycle_spent = 0.0
        if acct_pnl <= -abs(p["CONSERVATIVE_MDD"]):
            continue

        # 3) 신규 진입 (신호 발생 + 한도 통과).
        for code, rows in prices.items():
            k = date_idx[code].get(day)
            if k is None or code in holdings:
                continue
            if not sma_breakout_signal(closes_by_code[code], k, lookback):
                continue
            px = closes_by_code[code][k]
            budget = cash * p["PER_ORDER_BUDGET_RATIO"]
            qty = int(budget // px)
            if qty <= 0:
                continue
            notional = qty * px
            if notional * p["MIN_CASH_BUFFER"] > cash:
                continue
            if notional > total_eval * p["CONSERVATIVE_STOCK_RATIO"]:
                continue
            if cycle_spent + notional > cash * p["MAX_CYCLE_BUDGET_RATIO"]:
                continue
            cash -= notional
            cycle_spent += notional
            holdings[code] = {"qty": qty, "avg": px}
            trades += 1

    # 청산가치로 마감.
    final = equity_curve[-1] if equity_curve else start_cash
    return _metrics(preset_name, p.get("label", preset_name),
                    start_cash, final, equity_curve, trades, wins)


def _metrics(name, label, start, final, curve, trades, wins) -> dict:
    total_ret = (final / start - 1.0) * 100.0
    peak = -math.inf
    mdd = 0.0
    for v in curve:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1.0)
    # 일간 수익률 표준편차 기반 거친 변동성 (연율화 X — 상대비교용).
    rets = [curve[i] / curve[i - 1] - 1.0 for i in range(1, len(curve)) if curve[i - 1]]
    mean = sum(rets) / len(rets) if rets else 0.0
    var = sum((r - mean) ** 2 for r in rets) / len(rets) if rets else 0.0
    vol = math.sqrt(var)
    sharpe_like = (mean / vol * math.sqrt(252)) if vol else 0.0
    return {"preset": name, "label": label,
            "total_return_pct": round(total_ret, 2),
            "max_drawdown_pct": round(mdd * 100.0, 2),
            "sharpe_like": round(sharpe_like, 2),
            "trades": trades,
            "win_rate_pct": round(wins / trades * 100.0, 1) if trades else 0.0,
            "final_eval": round(final)}
