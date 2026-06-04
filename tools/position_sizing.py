"""리스크기반 포지션 사이징 — 순수 함수 (사장 지시 2026-06-04 ②).
종목별 사이징 가중치 w∈(0,∞), Σw=종목수. budget_i = (cycle_budget/n) * w[code_i].
점수↑(우호)·변동성↓(안전)일수록 큰 비중. equal 모드/strength=0 이면 전원 1.0(기존 균등분배).
결측 점수/σ 는 중립(1.0 요인) 처리. 하드 한도(per_stock_cap)는 호출부에서 별도 적용."""
from typing import Dict, List


def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def compute_sizing_weights(codes: List[str], scores: Dict[str, float], sigmas: Dict[str, float],
                           *, mode: str = "risk_weighted", strength: float = 0.5,
                           max_tilt: float = 2.0) -> Dict[str, float]:
    codes = [str(c).strip() for c in (codes or []) if str(c).strip()]
    if not codes:
        return {}
    if mode != "risk_weighted":
        return {c: 1.0 for c in codes}
    strength = max(0.0, min(1.0, float(strength)))
    max_tilt = max(1.0, float(max_tilt))

    # 점수 요인: score/5 → 5점=1.0, 10점=2.0, 0점=0(하한 0.2로 막아 0 방지)
    score_factor = {}
    for c in codes:
        s = scores.get(c)
        score_factor[c] = max(0.2, float(s) / 5.0) if s is not None else 1.0
    # 변동성 요인: median_sigma / sigma → 저변동 종목이 >1 (역변동성). σ 결측/<=0 → 1.0
    valid_sig = [float(sigmas[c]) for c in codes if sigmas.get(c) and float(sigmas[c]) > 0]
    med = sorted(valid_sig)[len(valid_sig) // 2] if valid_sig else 0.0
    vol_factor = {}
    for c in codes:
        sg = sigmas.get(c)
        vol_factor[c] = (med / float(sg)) if (sg and float(sg) > 0 and med > 0) else 1.0

    raw = {c: score_factor[c] * vol_factor[c] for c in codes}
    m = _mean([raw[c] for c in codes]) or 1.0
    # 균등(1.0)과 정규화 raw 사이 strength 보간 → 클램프 → 합=n 재정규화
    tilt = {}
    for c in codes:
        norm = raw[c] / m
        t = 1.0 + strength * (norm - 1.0)
        tilt[c] = max(1.0 / max_tilt, min(max_tilt, t))
    tm = _mean([tilt[c] for c in codes]) or 1.0
    return {c: tilt[c] / tm for c in codes}      # Σw = n
