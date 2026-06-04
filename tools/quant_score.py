"""결정론 점수 엔진 — 순수 함수 (사장 지시 2026-06-04: LLM 일관성 문제 → 점수는 무조건 Python).
spec: docs/superpowers/specs/2026-06-04-deterministic-score-engine-design.md

두 계층 모두 스케일 안정 정규화 `norm(w,s)=5+5·Σ(w·s)/Σ|w|` 를 쓴다 — 가중치 크기 무관,
부호·비율만 의미, 결과 0~10 고정. IO·LLM 없음(전부 순수 함수).
"""
from typing import Dict, Optional, Tuple, Any


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return lo if x < lo else (hi if x > hi else x)


def _norm(weights: Dict[str, float], sigs: Dict[str, float]) -> float:
    """존재하는 신호(weights∩sigs)만 가중합 → 0~10. Σ|w|=0 또는 교집합 없음 → 중립 5.0."""
    num = 0.0
    den = 0.0
    for k, s in sigs.items():
        w = weights.get(k)
        if w is None:
            continue
        num += float(w) * float(s)
        den += abs(float(w))
    if den <= 0:
        return 5.0
    return _clamp(5.0 + 5.0 * num / den, 0.0, 10.0)


def indicator_signals(ind: Dict[str, Any]) -> Dict[str, float]:
    """지표 raw dict → 신호 s∈[-1,+1] (+1=매수 우호). 원천 값이 없는 지표는 결과에서 생략."""
    s: Dict[str, float] = {}
    if ind.get("rsi14") is not None:
        s["rsi"] = _clamp((50.0 - float(ind["rsi14"])) / 30.0)
    if ind.get("macd_hist_pct") is not None:
        s["macd"] = _clamp(float(ind["macd_hist_pct"]) / 2.0)
    if ind.get("adx") is not None:
        mag = _clamp((float(ind["adx"]) - 20.0) / 20.0, 0.0, 1.0)
        d = 1.0 if float(ind.get("adx_dir", 1) or 1) >= 0 else -1.0
        s["adx"] = mag * d
    if ind.get("vwap_dev") is not None:
        s["vwap"] = _clamp(-float(ind["vwap_dev"]) / 15.0)
    if ind.get("sigma20") is not None:
        s["vol"] = _clamp((40.0 - float(ind["sigma20"])) / 40.0)
    if ind.get("mom_1m") is not None or ind.get("mom_3m") is not None:
        m1 = float(ind.get("mom_1m") or 0.0)
        m3 = float(ind.get("mom_3m") or 0.0)
        s["mom"] = _clamp((0.4 * m1 + 0.6 * m3) / 20.0)
    if ind.get("cmf") is not None:
        s["cmf"] = _clamp(float(ind["cmf"]))          # CMF 는 이미 -1..1
    if ind.get("flow") is not None:
        s["flow"] = _clamp(float(ind["flow"]))        # 사전 정규화 수급 신호
    if ind.get("high52_prox") is not None:
        s["high52"] = _clamp((float(ind["high52_prox"]) - 0.5) / 0.5)
    return s


def compute_indicator_score(ind: Dict[str, Any], weights: Dict[str, float]) -> Tuple[float, Dict[str, float]]:
    """지표 신호 가중합 → (0~10, breakdown). weights 는 신호명(rsi/macd/.../high52) 키."""
    sigs = indicator_signals(ind)
    score = _norm(weights, sigs)
    breakdown = {k: float(weights.get(k, 0.0)) * v for k, v in sigs.items()}
    return score, breakdown


def news_score(sentiment: Optional[float]) -> Optional[float]:
    """뉴스 감성(-1..1) → 0~10. None(파싱 실패) → None(차원 제외 폴백)."""
    if sentiment is None:
        return None
    return _clamp(5.0 + 5.0 * _clamp(float(sentiment)), 0.0, 10.0)


def macro_score(stock_pct: Optional[float]) -> Optional[float]:
    """매크로 권고 주식%(0~100) → 0~10 (15%=중립 5점). None → None."""
    if stock_pct is None:
        return None
    return _clamp(5.0 + 5.0 * _clamp((float(stock_pct) - 15.0) / 25.0), 0.0, 10.0)


def combine_dimensions(scores: Dict[str, Optional[float]], dw: Dict[str, float]) -> float:
    """차원 점수(QUANT/NEWS/MACRO, 각 0~10 또는 None)를 차원 가중치 dw로 합성 → 0~10.
    None 차원은 제외, 전부 None 이면 중립 5.0. DW 음수면 그 차원 반전."""
    centered = {k: (float(v) - 5.0) / 5.0 for k, v in scores.items() if v is not None}
    return _norm(dw, centered)
