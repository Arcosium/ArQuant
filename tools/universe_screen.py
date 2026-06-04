"""유니버스 스크리닝 — 순수 함수 (사장 지시 2026-06-04 ③).
후보(아이디어 풀)에서 레버리지/인버스·저가·거래대금 미달 종목을 사전 배제한다.
임계 0/False = 해당 기준 비활성. price/turnover 결측 종목은 평가불가 → 보존(드롭 금지).
*최종 주문은 거르지 않는다* — 후보 풀에만 적용해 주문 스킵을 피한다. 거른 내역은 사유 동반."""
from typing import Dict, List, Tuple

LEVERAGE_KEYWORDS = ("레버리지", "인버스", "곱버스", "2X", "3X", "ETN", "LEVERAGE", "INVERSE")


def _is_leveraged(name: str) -> bool:
    u = (name or "").upper()
    return any(k.upper() in u for k in LEVERAGE_KEYWORDS)


def screen_universe(items: List[Dict], *, min_price: float = 0.0, min_turnover: float = 0.0,
                    exclude_leveraged: bool = True) -> Tuple[List[str], List[Tuple[str, str]]]:
    """items: [{code, name, price?, turnover?}]. Returns (kept_codes, dropped[(code, reason)])."""
    kept: List[str] = []
    dropped: List[Tuple[str, str]] = []
    for it in (items or []):
        code = str(it.get("code", "")).strip()
        if not code:
            continue
        name = it.get("name") or ""
        price = it.get("price")
        turnover = it.get("turnover")
        if exclude_leveraged and _is_leveraged(name):
            dropped.append((code, f"레버리지/인버스/ETN 배제: {name}")); continue
        if min_price and price is not None and float(price) > 0 and float(price) < float(min_price):
            dropped.append((code, f"저가주 배제: {float(price):,.0f} < {float(min_price):,.0f}")); continue
        if min_turnover and turnover is not None and float(turnover) < float(min_turnover):
            dropped.append((code, f"거래대금 미달 배제: {float(turnover):,.0f} < {float(min_turnover):,.0f}")); continue
        kept.append(code)
    return kept, dropped
