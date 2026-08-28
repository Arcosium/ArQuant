from __future__ import annotations

import asyncio
import re
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Any, Dict, List, Optional


def _clean_site_msg(text: str, cap: int = 140) -> str:
    """사이트 응답 문자열을 로그 한 줄로 정제(사장 지시 2026-07-21) — 페이지 표 덤프가 새어들어도
    의미 있는 오류 문구만 뽑아 표시하고, 없으면 앞부분만 잘라 로그가 깨지지 않게 한다."""
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    if not s:
        return "확인 안 됨"
    for pat in (r"\[TMS\][^.]{0,120}", r"[^ ]*섹터[^.]{0,90}한도[^.]{0,60}",
                r"여유\s*부족[^.]{0,80}", r"규칙\s*위반[^.]{0,80}", r"미접수[^.]{0,60}", r"미체결[^.]{0,60}"):
        m = re.search(pat, s)
        if m:
            return m.group(0).strip()[:cap]
    return s[:cap]

from infra.kis_broker import OrderDraft
from Auto_folio.autofolio import contest_store
from Auto_folio.autofolio import order_limits
from Auto_folio.autofolio.naver_data import fetch_daily_ohlcv, fetch_security_meta
from Auto_folio.autofolio.timefolio_exec import submit_order, sync_site_account

_KST = timezone(timedelta(hours=9))


async def playwright_thread(fn, *args, **kw):
    """동기 Playwright 작업을 매번 새 전용 스레드에서 실행한다.

    공용 executor(asyncio.to_thread)를 쓰면 launch 실패(브라우저 바이너리 누락 등)가
    스레드에 잔재를 남겨, 이후 그 스레드에 배정된 모든 주문이 'Playwright Sync API
    inside the asyncio loop' 2차 오류로 죽는다 — 2026-08-18~24 실사고: 타임폴리오
    주문 42건이 조용히 전송 누락(ms-playwright 캐시 교체 공백기가 방아쇠).
    스레드 생성 ~ms 는 주문 지연(브라우저 로그인 8~12s)에 비해 무시 가능하다."""
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return await asyncio.get_running_loop().run_in_executor(ex, lambda: fn(*args, **kw))


def _kr_market_window_now() -> bool:
    """대회 사이트 스크레이프가 의미 있는 시간대(평일 08:30~15:45 KST)인가.
    밤(US 세션)엔 사이트 값이 변하지 않으므로 브라우저 로그인을 아낀다."""
    now = datetime.now(_KST)
    return now.weekday() < 5 and dtime(8, 30) <= now.time() <= dtime(15, 45)


class TimefolioBroker:
    """Broker adapter that lets the existing ArQuant swarm trade Timefolio accounts."""

    is_mock = True
    is_timefolio = True

    def __init__(self, creds: dict, token_path=None):
        self.uid = int(creds["id"])
        self.creds = creds
        self._last_site_order: dict[str, Any] | None = None
        # 한 계정의 사이트 세션은 단일 사용자 UI다. 안전감시 조회/주문과 정시 사이클이
        # 겹쳐도 브라우저 작업은 반드시 한 건씩 실행한다.
        self._site_lock = asyncio.Lock()

    async def close(self):
        return None

    async def _sync(self) -> dict[str, Any]:
        async with self._site_lock:
            return await playwright_thread(sync_site_account, self.uid, headless=True)

    def _account(self) -> dict[str, Any]:
        return contest_store.get_account(self.uid) or {}

    async def kr_account_snapshot(self, force: bool = False) -> Dict[str, Any]:
        # 사이트 동기화 = 헤드리스 브라우저 로그인(실측 7~15초). 장중이거나 명시 force 일 때만
        # 하고, 밤엔 로컬 장부 캐시로 응답한다 (메인 루프의 시간별 cash 사전게이트가 야간
        # US 세션마다 쓸데없는 로그인을 유발하던 것 차단 — 2026-07-09).
        if force or _kr_market_window_now():
            try:
                await self._sync()
            except Exception:
                pass
        acct = self._account()
        snap = contest_store.balance_snapshot(acct) if acct else {"buying_power": {}}
        bp = snap.get("buying_power") or {}
        return {"ok": True, "buying_power": {**bp, "ok": True}, "holdings": snap.get("holdings") or []}

    async def kr_balance(self) -> str:
        snap = await self.kr_account_snapshot(force=True)
        bp = snap.get("buying_power") or {}
        return (f"타임폴리오 총평가 {float(bp.get('total_eval') or 0):,.0f}원, "
                f"현금 {float(bp.get('cash') or 0):,.0f}원, "
                f"주간 회전율 {float(bp.get('weekly_turnover_pct') or 0):.1f}%")

    async def portfolio_holdings(self) -> Dict[str, Any]:
        snap = await self.kr_account_snapshot(force=True)
        holdings = []
        for h in snap.get("holdings") or []:
            holdings.append({**h, "category": h.get("category") or "타임폴리오", "ccy": h.get("ccy") or "KRW"})
        return {"buying_power": snap.get("buying_power") or {}, "holdings": holdings}

    async def kr_holdings(self) -> List[Dict[str, Any]]:
        try:
            await self._sync()
        except Exception:
            pass
        acct = self._account()
        snap = contest_store.balance_snapshot(acct) if acct else {}
        out = []
        for h in snap.get("holdings") or []:
            code = str(h.get("code") or "").zfill(6)
            qty = int(h.get("qty") or 0)
            out.append({
                "code": code,
                "name": h.get("name") or code,
                "qty": qty,
                "sellable_qty": qty,
                "avg_price": float(h.get("avg_price") or 0.0),
                "cur_price": float(h.get("cur_price") or 0.0),
                "eval_amt": float(h.get("eval_amt") or 0.0),
                "pnl": float(h.get("pnl") or 0.0),
                "pnl_pct": float(h.get("pnl_pct") or 0.0),
                "ccy": "KRW",
                "category": "타임폴리오",
            })
        return out

    async def kr_last_price(self, code: str, market: str = "J") -> float:
        code = str(code or "").zfill(6)
        stored = contest_store.get_security_meta(code) or {}
        try:
            meta = await asyncio.to_thread(fetch_security_meta, code, stored=stored)
            if meta:
                contest_store.upsert_security_meta(code, meta)
                return float(meta.get("last_price") or 0.0)
        except Exception:
            pass
        return float(stored.get("last_price") or 0.0)

    async def kr_price_str(self, code: str) -> str:
        code = str(code or "").zfill(6)
        price = await self.kr_last_price(code)
        return f"[타임폴리오/네이버시세] {code} | {price:,.0f}원"

    async def kr_daily_chart_deep(self, code: str, years: int = 2, max_calls: int = 10) -> List[Dict]:
        try:
            rows = await asyncio.to_thread(fetch_daily_ohlcv, str(code).zfill(6), pages=max(2, min(20, int(max_calls or 10))))
            return rows
        except Exception:
            return []

    async def kr_daily_chart(self, code: str, days: int = 60) -> List[Dict]:
        rows = await self.kr_daily_chart_deep(code, years=1, max_calls=4)
        return rows[-int(days):] if rows else []

    async def kr_minute_chart(self, code: str, interval: str = "1") -> List[Dict]:
        return []

    async def kr_pending_orders(self, code: Optional[str] = None) -> List[Dict]:
        return []

    async def kr_psbl_order(self, code: str, unpr: float = 0.0) -> Dict:
        snap = await self.kr_account_snapshot(force=False)
        cash = float(((snap.get("buying_power") or {}).get("cash")) or 0.0)
        price = float(unpr or await self.kr_last_price(code) or 0.0)
        qty = int(cash // price) if price > 0 else 0
        return {"ok": True, "buy_qty": qty, "cash": cash, "price": price}

    async def kr_psbl_sell_qty(self, code: str) -> Optional[int]:
        code = str(code or "").zfill(6)
        for h in await self.kr_holdings():
            if str(h.get("code") or "").zfill(6) == code:
                return int(h.get("sellable_qty") or h.get("qty") or 0)
        return 0

    async def kr_volume_rank(self) -> str:
        return "타임폴리오 모드: 거래대금 상위 후보는 네이버/대회 규칙 필터로 대체합니다."

    async def kr_volume_rank_list(self) -> List[Dict]:
        return []

    async def kr_index_daily(self, index_code: str = "0001", days: int = 40) -> List[Dict]:
        return []

    async def kr_index_now(self, index_code: str = "0001") -> float:
        return 0.0

    async def kr_account_asset(self) -> Dict[str, Any]:
        snap = await self.kr_account_snapshot(force=False)
        bp = snap.get("buying_power") or {}
        return {"ok": True, "tot_asst_amt": float(bp.get("total_eval") or 0.0)}

    async def kr_realized_pnl_audit(self) -> Dict[str, Any]:
        return {"ok": True, "items": [], "message": "Timefolio realized PnL audit is site-snapshot based"}

    def nxt_supported(self):
        return False

    def _get_overseas_cache(self):
        return (0.0, 0.0)

    async def _overseas_holdings(self) -> List[Dict]:
        return []

    async def overseas_fills(self, start_ymd: str, end_ymd: str) -> List[Dict]:
        return []

    async def idle_usd_deposit(self) -> Dict[str, Any]:
        return {"ok": False, "usd": 0.0, "reason": "Timefolio KR-only account"}

    async def us_to_krw_exchange(self, usd_amount: float, *, dry_run: bool = True, **kwargs) -> Dict[str, Any]:
        return {"ok": False, "reason": "Timefolio KR-only account", "dry_run": dry_run}

    async def us_last_price(self, ticker: str) -> float:
        return 0.0

    async def us_buying_power(self, ticker: str, unpr: float, excg: Optional[str] = None) -> Dict:
        return {"ok": False, "qty": 0, "reason": "Timefolio supports KR common stocks only"}

    async def us_daily_chart(self, ticker: str, days: int = 100) -> List[Dict]:
        return []

    async def us_price(self, ticker: str, excd: Optional[str] = None) -> str:
        return "Timefolio KR-only account"

    async def us_balance(self) -> str:
        return "타임폴리오 계정은 국내 보통주 전용입니다."

    async def get_account_balance(self) -> str:
        return await self.kr_balance()

    async def place_order_ex(self, order: OrderDraft) -> Dict[str, Any]:
        """구조화 주문 집행 — 타임폴리오 전용 사이클(timefolio_swarm)이 쓴다.
        Returns {ok, accepted, filled, qty(캡 반영), price, result(str)}."""
        ticker = str(order.ticker or "").zfill(6)
        side = str(order.side.value if hasattr(order.side, "value") else order.side).lower()
        qty = int(order.qty or 0)
        price = float(order.limit_price or 0.0)
        if price <= 0:
            price = await self.kr_last_price(ticker)
        # 1주문 비중 상한(섹터 한도 위반 방지, 사장 지시 2026-07-08): 매수는 총평가 대비 상한 비중까지만.
        # 일반 9%, 제외 대형주(삼전·SK하닉 등) 14%. 상승 시 10% 섹터 하한을 넘지 않게 목표 비중을 낮춘다.
        if side == "buy" and price > 0:
            acct = self._account() or {}
            total_eval = float((acct.get("portfolio") or {}).get("total_eval")
                               or acct.get("initial_cash") or contest_store.DEFAULT_INITIAL_CASH)
            max_qty = order_limits.max_order_qty(ticker, price, total_eval)
            if max_qty > 0 and qty > max_qty:
                qty = max_qty
        # 사장 지시 2026-07-03: 스웜 경로도 KIS 모의와 동일 파이프라인을 타되, 집행 직전에
        # 타임폴리오 대회 룰북(check_order)을 하드 게이트로 통과해야 한다. 섹터 데이터는
        # 네이버로 자동 수급이 안 되므로 relax_sector=True(부재 시 경고, 있으면 한도 검증).
        meta = contest_store.get_security_meta(ticker) or {}
        try:
            fetched = await asyncio.to_thread(fetch_security_meta, ticker, stored=meta)
            if fetched:
                contest_store.upsert_security_meta(ticker, fetched)
                meta = fetched
        except Exception:
            pass
        check = contest_store.check_order(self.uid, side, ticker, qty, price,
                                          meta=meta, relax_sector=True)
        if not check.get("ok"):
            reasons = "; ".join(v.get("message", "") for v in check.get("violations") or []) or "규칙 위반"
            return {"ok": False, "accepted": False, "filled": False, "qty": qty, "price": price,
                    "result": f"Timefolio 주문 거부(대회 룰): {ticker} {side} {qty}주 — {reasons}"}
        payload = {
            "ticker": ticker,
            "side": side,
            "qty": qty,
            "price": price,
            "limit_price": price,
            "amount": float(qty) * price,
        }
        async with self._site_lock:
            res = await playwright_thread(submit_order, self.uid, payload, headless=True)
        self._last_site_order = res
        if res.get("accepted"):
            # 체결은 로컬 장부에도 즉시 반영한다(다음 사이트 동기화가 최종 정합을 맞춤).
            if res.get("filled"):
                summary = res.get("summary") or {}
                try:
                    if summary.get("positions") is not None:
                        contest_store.sync_site_portfolio(
                            self.uid,
                            positions=summary.get("positions") or [],
                            total_eval=float(summary.get("total_eval") or 0) or contest_store.DEFAULT_INITIAL_CASH,
                            weekly_turnover_pct_value=summary.get("weekly_turnover_pct"),
                        )
                    else:
                        contest_store.place_order(self.uid, side, ticker, qty, price,
                                                  meta=meta, relax_sector=True)
                except Exception:
                    pass
            state = "체결" if res.get("filled") else "접수/대기"
            # 사장 보고 2026-07-29: 종전 `ok=filled` 라, 상대호가 미체결로 '접수(대기)'된 주문이
            # 전부 **실패로 표시**됐다(사이트에선 나중에 체결돼 잔고만 계속 변함). 접수는 실패가
            # 아니다 — KIS US 경로와 동일하게 accepted=ok 로 두고 filled 로 체결 여부를 구분한다.
            return {"ok": True, "accepted": True, "filled": bool(res.get("filled")),
                    "pending": bool(res.get("pending") or (not res.get("filled"))),
                    "qty": qty, "price": price,
                    "sector_clamped": res.get("sector_clamped"),
                    "result": f"Timefolio 주문 {state}: {ticker} {side} {qty}주"
                              + (f" — {res.get('result')}" if res.get("sector_clamped") else "")}
        return {"ok": False, "accepted": False, "filled": False, "qty": qty, "price": price,
                "result": f"Timefolio 주문 실패: {_clean_site_msg(res.get('result'))}"}

    async def place_order(self, order: OrderDraft) -> str:
        """기존 문자열 인터페이스 (KIS 파이프라인 호환) — place_order_ex 의 얇은 래퍼."""
        res = await self.place_order_ex(order)
        return str(res.get("result") or "")
