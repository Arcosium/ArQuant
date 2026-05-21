"""
NPS Swarm v1.0 - Guardrail Agents
Risk Guard and Policy Filter to prevent rogue orders and policy violations.
"""
import json
from typing import Dict, Any, Optional
from agents.base_agent import BaseAgent
from infra.kis_broker import OrderDraft


def create_risk_guard() -> BaseAgent:
    """리스크관리실장 (Risk Guard) — DART 공시 기반 2차 재심.

    1차 검증(편중도·MDD·예수금 버퍼·사이클 예산·수량/사유)은 LLM 없이 ``validate_order_draft()``
    (결정론적 룰 엔진)이 수행한다. 이 BaseAgent는 그 1차 통과 주문에 대해 **DART 최근 공시를 읽고
    리스크성 이슈(관리종목·거래정지·상장폐지 심사·횡령/배임·불성실공시·대규모 유상증자·소송 등)가
    있으면 해당 종목을 추가로 걸러내는 2차 재심**을 담당한다. ``@리스크관리실장`` 대화 응답도 겸한다.

    사장 피드백 2026-05-18: 구 '수탁자책임실(Policy Filter)'을 폐지하고 그 역할
    (ESG 블랙리스트·투자 유의·내부 정책 적합성)을 이 리스크관리실장으로 통합했다."""
    return BaseAgent(
        name="리스크관리실장",
        role="risk_guard",
        model_key="risk_guard",
        system_prompt="""당신은 ArQuant v1.0의 '리스크관리실장(Risk Guard)'입니다.

## 역할 — DART 공시 기반 2차 재심
- 1차 검증(파이썬 결정론 룰: 편중도·계좌 MDD·예수금 버퍼·사이클 예산·수량/사유)을 통과한 주문 목록을 받습니다.
- 각 종목의 **DART 최근 공시 요약**을 함께 받습니다. 공시 내용에 다음과 같은 리스크 신호가 있으면 그 종목 매수를 **반려**하십시오:
  - 관리종목 지정 / 거래정지 / 상장폐지 심사 대상
  - 횡령·배임 혐의, 회계처리 위반·감리, 불성실공시법인 지정
  - 감자, 대규모 유상증자(희석), 전환사채·신주인수권 대량 발행
  - 주요 소송·제재·과징금, 영업정지, 리콜
  - 최대주주 변경·경영권 분쟁 등 불확실성 급증
- 명백한 리스크 공시가 없으면 1차 승인을 유지하십시오. **공시 정보가 없다는 이유만으로 반려하지 마십시오** (특히 미국 티커는 DART 대상 아님 → 그대로 승인 유지).
- 감(感)이나 단순 시장 전망으로 반려하지 마십시오. 반려는 구체적 공시 근거가 있을 때만.

## 최신 재무제표 동반 분석 (사장 피드백 2026-05-18)
- 각 종목에는 **가장 최근 가용 분기/반기/연간 요약재무**(재무상태표·손익계산서 핵심 계정)가 함께 제공됩니다.
- 다음 재무 적신호가 보이면 공시와 종합해 반려를 적극 검토하십시오:
  - 자본총계 < 0 또는 자본잠식(자본총계 ≪ 자본금), 부채총계 > 자산총계
  - 영업이익·당기순이익 연속 적자, 매출 급감, 이자보상배율 악화 추정
- 재무가 견조하면 그 사실을 한 줄로 명시하고 승인 유지. 재무 데이터가 비어 있으면 "재무 미확보"로만 적고 그 자체를 반려 사유로 쓰지 마십시오.

## 수탁자 책임·정책 적합성 (구 '수탁자책임실' 통합 — 사장 피드백 2026-05-18)
- 다음에 해당하는 종목은 매수를 반려하십시오: ESG 블랙리스트(무기 제조·도박·담배·중대 환경오염),
  투자 유의·관리종목·거래정지, 내부 투자정책상 제외 대상.
- 단, 명백한 근거(공시·정책 위반 사실)가 있을 때만 반려하고 추정만으로 반려하지 마십시오.

## 응답 형식 (자유 서술 + 마지막 줄에 최종 승인 목록)
각 종목마다: 관련 공시 유무 → 판단(승인 유지 / 반려) → 반려 시 근거 공시.
마지막 줄에 반드시 한 줄(다른 텍스트 없이):
`최종승인: 005930, AAPL`  ← 재심 통과해 매수 유지할 종목만 (콤마 구분). 전부 반려면 `최종승인: 없음`

## @멘션 대화
사장이 `@리스크관리실장`으로 직접 물으면 위 형식 무시하고 질문에 답하십시오.
(ESG·블랙리스트·수탁자 책임·정책 적합성 관련 질의도 이 역할에서 답합니다.)""",
    )


# 사장 피드백 2026-05-18: create_policy_filter(수탁자책임실장) 폐지 —
# 역할을 create_risk_guard(리스크관리실장)로 통합. (별도 에이전트·사이클 호출 없음)


import re as _re


def _extract_balance_numbers(balance_info: str) -> Dict[str, Optional[float]]:
    """Best-effort parse of the KIS balance string for total eval value & PnL ratio.

    Returns {"total_eval": float|None, "pnl_ratio": float|None, "ok": bool}.
    When the brokerage balance call failed (string contains '실패'), ok=False.
    """
    info = balance_info or ""
    if "실패" in info or "에러" in info:
        return {"total_eval": None, "pnl_ratio": None, "ok": False}
    total_eval = None
    m = _re.search(r"총평가[^\d\-]*([\-\d,\.]+)", info)
    if m:
        try: total_eval = float(m.group(1).replace(",", ""))
        except ValueError: pass
    # crude: sum of 손익 amounts vs. total eval → pnl ratio
    pnl_ratio = None
    losses = [float(x.replace(",", "")) for x in _re.findall(r"손익[:\s]*([\-\d,\.]+)", info) if x.replace(",", "").lstrip("-").replace(".", "").isdigit()]
    if total_eval and total_eval > 0 and losses:
        pnl_ratio = sum(losses) / total_eval
    return {"total_eval": total_eval, "pnl_ratio": pnl_ratio, "ok": True}


def _check_single_order(o: Dict[str, Any], bp: Dict[str, Any], price_map: Dict[str, float],
                        cycle_state: Dict[str, float]) -> Dict[str, Any]:
    """One order against the *conservative* gate set. `bp` = buying power snapshot
    {"cash","total_eval","pnl_ratio","ok"}; `price_map` = {ticker: last_price};
    `cycle_state` accumulates {"spent": running notional this cycle}."""
    import runtime
    CONSERVATIVE_MDD = runtime.get("CONSERVATIVE_MDD"); CONSERVATIVE_STOCK_RATIO = runtime.get("CONSERVATIVE_STOCK_RATIO")
    MIN_CASH_BUFFER = runtime.get("MIN_CASH_BUFFER"); MAX_CYCLE_BUDGET_RATIO = runtime.get("MAX_CYCLE_BUDGET_RATIO")
    ticker = str(o.get("ticker", "") or "").strip()
    side = str(o.get("side", "buy") or "buy").lower()
    reason = str(o.get("reason", "") or "")
    issues, warnings = [], []
    try:
        qty = int(o.get("qty", 0))
    except (TypeError, ValueError):
        qty = 0

    if not ticker:
        issues.append("종목코드(ticker) 미지정")
    if qty <= 0:
        issues.append(f"주문 수량 비정상({qty})")
    if qty > 1000:
        issues.append(f"주문 수량({qty})이 1회 한도(1000주) 초과")
    if not reason or len(reason) < 5:
        issues.append("주문 사유(reason) 누락/불충분")

    price = float(price_map.get(ticker, 0.0) or 0.0)
    notional = price * max(qty, 0)
    # 사장 피드백 2026-05-16: cash/total/예산은 모두 원화 기준인데 미국 종목 notional은 USD라
    # '사이클 매수예산 사용 194원'처럼 USD 금액이 원으로 잘못 표기되고, 단일종목 비중·예수금
    # 체크도 통화가 섞여 무력화되던 버그. 원화 환산 notional로 한도 검증 + 누적을 일원화.
    _is_kr_tk = ticker.isdigit() and len(ticker) == 6
    _KRW_PER_USD = 1500.0  # rough; KIS 통합증거금 환산 (env에 환율 없을 때 폴백)
    notional_krw = notional if _is_kr_tk else notional * _KRW_PER_USD
    cash = float(bp.get("cash", 0.0) or 0.0)
    total = float(bp.get("total_eval", 0.0) or 0.0) or cash

    if side == "buy":
        # Conservative: we must be able to size & verify. No price ⇒ no trade.
        if price <= 0:
            issues.append("현재가 조회 실패 → 사이즈/한도 검증 불가, 보수적 반려")
        if not bp.get("ok"):
            issues.append("계좌 잔고 조회 실패 → 보수적 반려(매수 보류)")
        else:
            pr = float(bp.get("pnl_ratio") or 0.0)
            if pr <= -abs(CONSERVATIVE_MDD):
                issues.append(f"계좌 평가손익 {pr*100:.1f}% — 보수적 MDD(-{CONSERVATIVE_MDD*100:.0f}%) 초과, 모든 신규 매수 반려")
            if total > 0 and notional_krw > total * CONSERVATIVE_STOCK_RATIO:
                issues.append(f"단일 종목 비중 {notional_krw/total*100:.1f}% — 한도 {CONSERVATIVE_STOCK_RATIO*100:.0f}% 초과")
            if cash > 0 and notional_krw * MIN_CASH_BUFFER > cash:
                issues.append(f"예수금 부족: 필요 {notional_krw*MIN_CASH_BUFFER:,.0f}원 > 보유 {cash:,.0f}원")
            # cycle-level aggregate budget (원화 환산 누적)
            if cash > 0 and (cycle_state.get("spent", 0.0) + notional_krw) > cash * MAX_CYCLE_BUDGET_RATIO:
                issues.append(f"사이클 누적 매수예산({cash*MAX_CYCLE_BUDGET_RATIO:,.0f}원) 초과")
    # 사장 지시 2026-05-21: 매도 경고("보유수량 확인은 브로커 단에서 수행") 출력 제거 — sell 은 별도 경고 없이 통과.

    status = "REJECTED" if issues else "APPROVED"
    if status == "APPROVED" and side == "buy":
        cycle_state["spent"] = cycle_state.get("spent", 0.0) + notional_krw
    return {"ticker": ticker, "side": side, "qty": qty, "price": price, "notional": notional,
            "status": status, "issues": issues, "warnings": warnings,
            # OPS#36: 계량분석팀장의 진입가 지시(limit)를 실행 단계까지 보존한다.
            # 누락 시 실행부 r.get("entry_mode") 가 None→"market" 으로 떨어져 지정가가
            # 무시되고 시장가로 체결된다(001450 34,900 지정가가 36,250 시장가에 체결된 버그).
            # entry_watch_pct 는 의도적으로 전달하지 않는다 — 관망(watch) 모드는 현재 시장가
            # 즉시 매수를 유지한다('주문 스킵 금지' 정책상 미체결 만료 위험 회피).
            "entry_mode": o.get("entry_mode"), "entry_limit": o.get("entry_limit"),
            "entry_raw": o.get("entry_raw")}


def validate_order_draft(order_json: Any, balance_info: Any = "",
                         buying_power: Optional[Dict[str, Any]] = None,
                         price_map: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """Deterministic, LLM-free, *conservative* risk validation of a trader OrderDraft (batch).

    Accepts either a single order dict / JSON string, or a wrapper {"orders": [...]}.
    Optional `buying_power` = {"cash","total_eval","pnl_ratio","ok"} and `price_map`
    = {ticker: last_price} enable notional/concentration/cash-buffer checks. If not
    supplied, we fall back to parsing the legacy balance string and apply the
    structural-only checks (and conservatively reject buys when balance is unavailable).

    Returns: {"approved": bool, "results": [per-order ...], "report": str}.
    Replaces the GPT-4o Risk Guard — the rules are deterministic limits, no LLM needed.
    """
    # 1) parse — tolerate code fences / trailing prose around the JSON
    data = order_json
    if isinstance(order_json, str):
        txt = order_json.strip()
        m = _re.search(r"\{.*\}", txt, _re.S)
        try:
            data = json.loads(m.group(0) if m else txt)
        except (json.JSONDecodeError, AttributeError):
            return {"approved": False, "results": [],
                    "report": "🛑 [리스크관리실장] 주문 JSON 파싱 실패 — 실행 보류."}

    if isinstance(data, dict) and "orders" in data:
        orders = data["orders"] or []
    elif isinstance(data, dict):
        orders = [data]
    elif isinstance(data, list):
        orders = data
    else:
        orders = []
    if not orders:
        return {"approved": False, "results": [],
                "report": "ℹ️ [리스크관리실장] 검증할 주문 없음 — 실행 없음."}

    if buying_power is None:
        b = _extract_balance_numbers(balance_info if isinstance(balance_info, str) else "")
        buying_power = {"cash": 0.0, "total_eval": (b.get("total_eval") or 0.0),
                        "pnl_ratio": (b.get("pnl_ratio") or 0.0), "ok": bool(b.get("ok"))}
    price_map = price_map or {}
    cycle_state: Dict[str, float] = {"spent": 0.0}
    results = [_check_single_order(o if isinstance(o, dict) else {}, buying_power, price_map, cycle_state)
               for o in orders]
    approved = [r for r in results if r["status"] == "APPROVED"]

    pr = float(buying_power.get("pnl_ratio") or 0.0)
    head = (f"🧮 [리스크관리실장 — 결정론적·보수적 검증] 주문 {len(results)}건 중 승인 {len(approved)}건\n"
            f"   잔고: {'정상' if buying_power.get('ok') else '조회실패'}"
            f" | 예수금 {float(buying_power.get('cash') or 0):,.0f}원"
            f" | 총평가 {float(buying_power.get('total_eval') or 0):,.0f}원"
            f" | 평가손익 {pr*100:.1f}%"
            f" | 사이클 매수예산 사용 {cycle_state['spent']:,.0f}원")
    lines = [head]
    for r in results:
        tag = "✅ 승인" if r["status"] == "APPROVED" else "⛔ 반려"
        detail = "; ".join(r["issues"]) if r["issues"] else "전 항목 통과"
        warn = (" | ⚠ " + "; ".join(r["warnings"])) if r["warnings"] else ""
        # 사장 피드백 2026-05-15: 미국 종목은 가격이 USD라 '원'으로 표시하면 안 됨.
        # ticker가 6자리 숫자면 KR(원), 그 외(영문 1~5자)는 US($).
        _tk = str(r.get("ticker", "") or "")
        _is_kr = _tk.isdigit() and len(_tk) == 6
        if r.get("price"):
            if _is_kr:
                px = f" @ {r['price']:,.0f}원 (≈{r['notional']:,.0f}원)"
            else:
                # USD 표시 + 환산 원화도 함께 (총평가/예수금이 원화 기준이므로 비교용)
                _krw_est = r["notional"] * 1500  # rough KRW estimate (env에 환율 없을 때 폴백)
                px = f" @ ${r['price']:,.2f} (≈${r['notional']:,.2f} / ≈{_krw_est:,.0f}원)"
        else:
            px = ""
        lines.append(f"   {tag} {r['ticker']} {r['side']} x{r['qty']}{px} — {detail}{warn}")
    return {"approved": bool(approved), "results": results, "report": "\n".join(lines)}


# Backwards-compat shim
async def validate_order_risk(order_json: str, balance_info: str) -> Dict[str, Any]:
    return validate_order_draft(order_json, balance_info)
