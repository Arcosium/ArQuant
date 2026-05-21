"""
Arquant v1.0 - KIS Broker (확장판)
한국투자증권 OpenAPI 전체 카테고리 지원:
  국내주식 시세/주문/잔고, 해외주식, 장내채권, 해외선물옵션, 국내선물옵션
  일봉/분봉 실시간 데이터 CSV 누적 수집
"""
import asyncio, aiohttp, time, logging, os, csv, json
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
from enum import Enum
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("KIS")
KST = timezone(timedelta(hours=9))
DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)
# KIS access token (24h 유효). 디스크에 캐싱 → 프로세스 재시작/멀티 클라이언트라도 만료 전엔 재발급 안 함.
TOKEN_CACHE_FILE = DATA_DIR / "kis_token.json"

import re as _re

def _clean_kis_msg(msg: str) -> str:
    """사장 피드백 2026-05-16: KIS 원문(msg1)에서 '해외투자영업부(3276-5300)문의'·전화번호 같은
    안내 보일러플레이트를 제거하고 **실제 거부/처리 사유만** 남긴다.
    (예: "가격 $0.01 미만시 온라인 주문불가조건입니다. 해외투자영업부(3276-5300)문의"
       → "가격 $0.01 미만시 온라인 주문불가조건입니다.")
    과거 해외 ETP 거래제한이 이 메시지로 뜬 적이 있으나, 지금은 거래 가능 — 표시는 실제 사유만."""
    if not msg:
        return ""
    s = str(msg)
    # 부서/센터 + (전화) + 문의 형태의 안내 꼬리 제거
    s = _re.sub(r"\s*[가-힣A-Za-z]{0,12}(?:영업부|고객\s*센터|상담\s*센터|콜센터|데스크)\s*"
                r"[\(（]?\s*\d[\d\-\s]{4,}[\)）]?\s*(?:로)?\s*문의\s*(?:바랍니다|하세요|요망)?\.?",
                "", s)
    # 잔여 전화번호 토큰 제거
    s = _re.sub(r"[\(（]?\b\d{2,4}-\d{3,4}-?\d{0,4}\b[\)）]?", "", s)
    # 꼬리에 남은 '문의' 단독 잔재 제거
    s = _re.sub(r"\s*문의\s*(?:바랍니다|하세요)?\.?\s*$", "", s)
    s = _re.sub(r"\s{2,}", " ", s).strip(" .·,")
    return s.strip()


def excd_to_excg(excd: Optional[str]) -> str:
    """시세 프로브 거래소코드(NAS/NYS/AMS) → 주문 거래소코드(NASD/NYSE/AMEX).
    미상·기타는 NASD 로 안전 폴백. 이미 주문코드면 그대로 통과."""
    e = (excd or "").strip().upper()
    if e in ("NASD", "NYSE", "AMEX"):
        return e
    return {"NAS": "NASD", "NYS": "NYSE", "AMS": "AMEX"}.get(e, "NASD")


class OrderSide(str, Enum):
    BUY = "buy"; SELL = "sell"
class PriceType(str, Enum):
    MARKET = "market"; LIMIT = "limit"
class OrderDraft(BaseModel):
    ticker: str; side: OrderSide; qty: int = Field(gt=0)
    price_type: PriceType = PriceType.MARKET; limit_price: Optional[float] = None
    market: str = "KR"; reason: str = ""; approved: bool = False
    rejection_reason: Optional[str] = None

class KISBroker:
    def __init__(self):
        from config import KIS_APP_KEY, KIS_APP_SECRET, KIS_BASE_URL, KIS_ACCOUNT_NO
        self.app_key = KIS_APP_KEY; self.app_secret = KIS_APP_SECRET
        self.base_url = KIS_BASE_URL; self.account_no = KIS_ACCOUNT_NO
        # 사장 피드백 2026-05-16: 실전/모의 정식 지원. base_url 이 KIS 모의투자 서버면
        # 주문/잔고 tr_id 를 모의용으로 변환 (실전 경로는 전혀 안 건드림 — 무위험).
        _bu = (self.base_url or "")
        self.is_mock = ("openapivts" in _bu) or (":29443" in _bu)
        self._token: Optional[str] = None; self._token_exp: float = 0
        self._session: Optional[aiohttp.ClientSession] = None

    async def _s(self):
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        return self._session

    async def close(self):
        if self._session and not self._session.closed: await self._session.close()

    # token re-issue is allowed only when within this many seconds of expiry (keep a safety margin)
    _TOKEN_SAFETY_SEC = 600

    def _load_token_file(self) -> Optional[Dict[str, Any]]:
        try:
            if TOKEN_CACHE_FILE.exists():
                d = json.loads(TOKEN_CACHE_FILE.read_text(encoding="utf-8"))
                if isinstance(d, dict) and d.get("access_token") and d.get("appkey") == self.app_key:
                    return d
        except Exception as e:
            logger.warning(f"토큰 캐시 읽기 실패: {e}")
        return None

    def _save_token_file(self, token: str, exp: float):
        try:
            TOKEN_CACHE_FILE.write_text(json.dumps(
                {"access_token": token, "expires_at": exp, "appkey": self.app_key,
                 "issued_at": time.time()}, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.warning(f"토큰 캐시 저장 실패: {e}")

    async def token(self) -> str:
        now = time.time()
        # 1) in-memory
        if self._token and now < self._token_exp - self._TOKEN_SAFETY_SEC:
            return self._token
        # 2) disk cache (survives restarts / shared across clients using the same appkey)
        cached = self._load_token_file()
        if cached and now < float(cached.get("expires_at", 0)) - self._TOKEN_SAFETY_SEC:
            self._token = cached["access_token"]; self._token_exp = float(cached["expires_at"])
            return self._token
        # 3) expired (or none) → issue a fresh one, once, and persist it
        s = await self._s()
        async with s.post(f"{self.base_url}/oauth2/tokenP", json={
            "grant_type":"client_credentials","appkey":self.app_key,"appsecret":self.app_secret}) as r:
            d = await r.json()
        if "access_token" not in d:
            # rate-limited (EGW00133) or transient error → fall back to a still-valid disk token if any
            if cached and now < float(cached.get("expires_at", 0)) - 60:
                logger.warning(f"토큰 재발급 실패({d}) — 캐시된 토큰 재사용")
                self._token = cached["access_token"]; self._token_exp = float(cached["expires_at"])
                return self._token
            raise Exception(f"토큰 실패: {d}")
        self._token = d["access_token"]; self._token_exp = now + d.get("expires_in", 86400)
        self._save_token_file(self._token, self._token_exp)
        logger.info(f"KIS 신규 토큰 발급 (만료 {datetime.fromtimestamp(self._token_exp, KST):%Y-%m-%d %H:%M})")
        return self._token

    # 모의투자 tr_id: 대부분 실전 첫 글자 'T'→'V'. 단 해외주식 매도만 예외
    # (실전 TTTT1006U → 모의 VTTT1001U, 단순 T→V 아님). 시세성(FH...)은 변환 불필요.
    _MOCK_TR_OVERRIDE = {"TTTT1006U": "VTTT1001U"}

    def _mock_tr(self, tr_id: str) -> str:
        if not self.is_mock or not tr_id:
            return tr_id
        if tr_id in self._MOCK_TR_OVERRIDE:
            return self._MOCK_TR_OVERRIDE[tr_id]
        return ("V" + tr_id[1:]) if tr_id[0] == "T" else tr_id

    def _h(self, tok, tr_id):
        return {"content-type":"application/json;charset=utf-8","authorization":f"Bearer {tok}",
                "appkey":self.app_key,"appsecret":self.app_secret,"tr_id":self._mock_tr(tr_id)}

    def _acnt(self):
        # Tolerate formats like "12345678-01", "1234567801", with/without spaces.
        digits = "".join(ch for ch in (self.account_no or "") if ch.isdigit())
        cano = digits[:8]
        prdt = digits[8:10] if len(digits) >= 9 else "01"
        return (cano, prdt or "01")

    # ═══════════════════ 국내주식 시세 ═══════════════════
    async def kr_price(self, code: str) -> Dict:
        tok = await self.token(); s = await self._s()
        async with s.get(f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price",
            headers=self._h(tok,"FHKST01010100"),
            params={"FID_COND_MRKT_DIV_CODE":"J","FID_INPUT_ISCD":code}) as r:
            return (await r.json()).get("output",{})

    async def kr_price_str(self, code: str) -> str:
        d = await self.kr_price(code)
        return (f"[국내시세] {code} | {d.get('stck_prpr','?')}원 | "
                f"전일비: {d.get('prdy_vrss','')} ({d.get('prdy_ctrt','')}%) | "
                f"거래량: {d.get('acml_vol','')}")

    async def kr_daily_chart(self, code: str, days: int = 60) -> List[Dict]:
        """일봉 조회 → CSV 누적"""
        tok = await self.token(); s = await self._s()
        end = datetime.now(KST).strftime("%Y%m%d")
        start = (datetime.now(KST) - timedelta(days=days*2)).strftime("%Y%m%d")
        async with s.get(f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            headers=self._h(tok,"FHKST03010100"),
            params={"FID_COND_MRKT_DIV_CODE":"J","FID_INPUT_ISCD":code,
                    "FID_INPUT_DATE_1":start,"FID_INPUT_DATE_2":end,
                    "FID_PERIOD_DIV_CODE":"D","FID_ORG_ADJ_PRC":"0"}) as r:
            data = (await r.json()).get("output2",[])
        rows = []
        for x in data:
            rows.append({"date":x.get("stck_bsop_date",""),"open":x.get("stck_oprc",""),
                "high":x.get("stck_hgpr",""),"low":x.get("stck_lwpr",""),
                "close":x.get("stck_clpr",""),"volume":x.get("acml_vol","")})
        self._append_csv(f"daily_{code}.csv", rows, ["date","open","high","low","close","volume"])
        return rows

    async def kr_daily_chart_deep(self, code: str, years: int = 2, max_calls: int = 10) -> List[Dict]:
        """KIS 일봉을 날짜 윈도우로 페이지네이션해 ~years년치 깊게 수집 → CSV 누적.
        KIS inquire-daily-itemchartprice는 호출당 ~100행만 주므로 윈도우를 과거로
        굴리며 여러 번 호출한다 (사장 피드백 2026-05-18 — KIS 우선·'데이터 부족' 해소).
        날짜를 네이버 경로와 동일한 YYYY-MM-DD로 정규화해 같은 CSV에 안전 누적."""
        tok = await self.token(); s = await self._s()
        target = (datetime.now(KST) - timedelta(days=int(max(1, years) * 365))).strftime("%Y%m%d")
        win_end = datetime.now(KST)
        all_rows: List[Dict] = []
        seen: set = set()
        for _ in range(max(1, max_calls)):
            e = win_end.strftime("%Y%m%d")
            b = (win_end - timedelta(days=150)).strftime("%Y%m%d")  # ≈100 거래일
            try:
                async with s.get(f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                    headers=self._h(tok, "FHKST03010100"),
                    params={"FID_COND_MRKT_DIV_CODE":"J","FID_INPUT_ISCD":code,
                            "FID_INPUT_DATE_1":b,"FID_INPUT_DATE_2":e,
                            "FID_PERIOD_DIV_CODE":"D","FID_ORG_ADJ_PRC":"0"}) as r:
                    data = (await r.json()).get("output2", []) or []
            except Exception as ex:
                logger.warning(f"[일봉deep] {code} 조회 예외: {ex}")
                break
            win_rows = []
            for x in data:
                d = (x.get("stck_bsop_date") or "").strip()
                if len(d) != 8 or d in seen:
                    continue
                seen.add(d)
                win_rows.append({"date": f"{d[:4]}-{d[4:6]}-{d[6:8]}",
                    "open":x.get("stck_oprc",""),"high":x.get("stck_hgpr",""),
                    "low":x.get("stck_lwpr",""),"close":x.get("stck_clpr",""),
                    "volume":x.get("acml_vol","")})
            if not win_rows:
                break
            all_rows.extend(win_rows)
            oldest = min(r["date"].replace("-", "") for r in win_rows)
            if oldest <= target:
                break
            win_end = datetime.strptime(oldest, "%Y%m%d") - timedelta(days=1)
            await asyncio.sleep(0.25)  # KIS 유량 보호
        if all_rows:
            all_rows.sort(key=lambda r: r["date"])
            self._append_csv(f"daily_{code}.csv", all_rows, ["date","open","high","low","close","volume"])
        return all_rows

    async def kr_minute_chart(self, code: str, interval: str = "1") -> List[Dict]:
        """분봉 조회 → CSV 누적"""
        tok = await self.token(); s = await self._s()
        now = datetime.now(KST).strftime("%H%M%S")
        async with s.get(f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
            headers=self._h(tok,"FHKST03010200"),
            params={"FID_COND_MRKT_DIV_CODE":"J","FID_INPUT_ISCD":code,
                    "FID_INPUT_HOUR_1":now,"FID_PW_DATA_INCU_YN":"Y"}) as r:
            data = (await r.json()).get("output2",[])
        rows = []
        for x in data:
            rows.append({"datetime":f"{x.get('stck_bsop_date','')}{x.get('stck_cntg_hour','')}",
                "open":x.get("stck_oprc",""),"high":x.get("stck_hgpr",""),
                "low":x.get("stck_lwpr",""),"close":x.get("stck_prpr",""),
                "volume":x.get("cntg_vol","")})
        self._append_csv(f"minute_{code}.csv", rows, ["datetime","open","high","low","close","volume"])
        return rows

    # ═══════════════════ 국내주식 주문 ═══════════════════
    async def kr_buy(self, code: str, qty: int, price: int = 0) -> str:
        tok = await self.token(); s = await self._s(); c, p = self._acnt()
        # 사장 지시 2026-05-19: 지정가(price>0)면 ORD_DVSN="00"(지정가), 없으면 "01"(시장가).
        # 기존 '"01" if price else "01"'은 지정가 주문도 시장가로 체결시키던 버그.
        body = {"CANO":c,"ACNT_PRDT_CD":p,"PDNO":code,"ORD_DVSN":"00" if price else "01",
                "ORD_QTY":str(qty),"ORD_UNPR":str(price) if price else "0","CTAC_TLNO":""}
        async with s.post(f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash",
            headers=self._h(tok,"TTTC0802U"), json=body) as r:
            d = await r.json()
        return f"[국내매수] {code} {qty}주 → {_clean_kis_msg(d.get('msg1',''))}" if d.get("rt_cd")=="0" else f"[실패] {_clean_kis_msg(d.get('msg1',''))}"

    async def kr_sell(self, code: str, qty: int, price: int = 0) -> str:
        tok = await self.token(); s = await self._s(); c, p = self._acnt()
        # 사장 지시 2026-05-19: 지정가(price>0)면 ORD_DVSN="00"(지정가), 없으면 "01"(시장가).
        # 기존 '"01" if price else "01"'은 지정가 매도도 시장가로 체결시키던 버그.
        body = {"CANO":c,"ACNT_PRDT_CD":p,"PDNO":code,"ORD_DVSN":"00" if price else "01",
                "ORD_QTY":str(qty),"ORD_UNPR":str(price) if price else "0","CTAC_TLNO":""}
        async with s.post(f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash",
            headers=self._h(tok,"TTTC0801U"), json=body) as r:
            d = await r.json()
        return f"[국내매도] {code} {qty}주 → {_clean_kis_msg(d.get('msg1',''))}" if d.get("rt_cd")=="0" else f"[실패] {_clean_kis_msg(d.get('msg1',''))}"

    # ── one canonical balance read (paginated) → cached 8s; everything else derives from it ──
    _SNAP_TTL = 8.0
    async def _raw_balance(self) -> Dict:
        """Single inquire-balance call with tr_cont pagination. Returns {output1:[...],output2:{...},ok,rt_cd,msg1}."""
        tok = await self.token(); s = await self._s(); c, p = self._acnt()
        out1: List[Dict] = []; out2: Dict = {}; rt = ""; msg = ""
        fk = ""; nk = ""; tr_cont = ""
        for _ in range(5):
            headers = self._h(tok, "TTTC8434R")
            if tr_cont in ("F", "M"):
                headers["tr_cont"] = "N"
            async with s.get(f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-balance",
                headers=headers,
                params={"CANO":c,"ACNT_PRDT_CD":p,"AFHR_FLPR_YN":"N","OFL_YN":"","INQR_DVSN":"02",
                        "UNPR_DVSN":"01","FUND_STTL_ICLD_YN":"N","FNCG_AMT_AUTO_RDPT_YN":"N",
                        "PRCS_DVSN":"01","CTX_AREA_FK100":fk,"CTX_AREA_NK100":nk}) as r:
                tr_cont = r.headers.get("tr_cont","")
                d = await r.json()
            rt = d.get("rt_cd",""); msg = d.get("msg1","")
            if rt != "0":
                break
            out1.extend(d.get("output1") or [])
            o2list = d.get("output2") or []
            if o2list: out2 = o2list[0]
            fk = (d.get("ctx_area_fk100") or "").strip(); nk = (d.get("ctx_area_nk100") or "").strip()
            if tr_cont not in ("F", "M") or not nk:
                break
        ok = (rt == "0")
        logger.info(f"[잔고] rt_cd={rt} msg='{msg}' output1_rows={len(out1)} tot_evlu={out2.get('tot_evlu_amt','?')} dnca={out2.get('dnca_tot_amt','?')}")
        return {"output1": out1, "output2": out2, "ok": ok, "rt_cd": rt, "msg1": msg}

    async def kr_account_snapshot(self, force: bool = False) -> Dict:
        """{"buying_power":{cash,total_eval,pnl_ratio,ok}, "holdings":[{code,name,qty,avg_price,cur_price,pnl_amt,pnl_pct}], "ok":bool, "ts":float}.
        Cached for _SNAP_TTL seconds so repeated callers (cycle + /api/balance) don't double-hit KIS."""
        now = time.time()
        cached = getattr(self, "_acct_snap", None)
        if cached and not force and (now - cached.get("ts", 0)) < self._SNAP_TTL:
            return cached
        def _f(x):
            try: return float(str(x).replace(",",""))
            except (TypeError, ValueError): return 0.0
        def _i(x):
            try: return int(float(str(x).replace(",","")))
            except (TypeError, ValueError): return 0
        try:
            d = await self._raw_balance()
        except Exception as e:
            logger.warning(f"[잔고스냅샷] 실패: {e}")
            d = {"output1": [], "output2": {}, "ok": False}
        o2 = d.get("output2") or {}
        # ── Cash: KIS의 dnca_tot_amt(D0 예수금)는 당일 매도대금(D+2 미수령)을 포함하지 않아,
        #    매도→매수 직후엔 실제 '주문가능금액'보다 한참 작게 잡힌다. 그래서 D+2/D+1 정산예수금
        #    (prvs_rcdl_excc_amt / nxdy_excc_amt — 매도대금 반영분)을 우선 사용하고, 없을 때만 D0 예수금. ──
        cash_d2 = _f(o2.get("prvs_rcdl_excc_amt")); cash_d1 = _f(o2.get("nxdy_excc_amt")); cash_d0 = _f(o2.get("dnca_tot_amt"))
        cash = cash_d2 or cash_d1 or cash_d0
        scts = _f(o2.get("scts_evlu_amt"))   # 유가증권평가금액
        # 총평가(순자산): nass_amt(순자산금액) → tot_evlu_amt 순으로 신뢰. 단, KIS가 D+2 매도대금을
        # 아직 합산하지 않은 과도기 값이면(유가증권+현금보다 작으면) 직접 재구성해 평가금액 추이가 튀지 않게 한다.
        total = _f(o2.get("nass_amt")) or _f(o2.get("tot_evlu_amt")) or 0.0
        recon = scts + cash
        if recon > 0 and total < recon * 0.97:
            total = recon
        if total <= 0:
            total = cash
        pnl = _f(o2.get("evlu_pfls_smtl_amt"))
        pnl_ratio = (pnl / total) if total > 0 else 0.0
        holdings = []
        for h in (d.get("output1") or []):
            q = _i(h.get("hldg_qty"))
            if q <= 0:
                continue
            holdings.append({"code": (h.get("pdno") or "").strip(), "name": (h.get("prdt_name") or "").strip(), "qty": q,
                             "avg_price": _f(h.get("pchs_avg_pric")), "cur_price": _f(h.get("prpr")),
                             "pnl_amt": _f(h.get("evlu_pfls_amt")), "pnl_pct": _f(h.get("evlu_pfls_rt"))})
        snap = {"buying_power": {"cash": cash, "total_eval": total, "pnl_ratio": pnl_ratio, "ok": bool(d.get("ok"))},
                "holdings": holdings, "ok": bool(d.get("ok")), "ts": now}
        # Keep last-good holdings if this read succeeded structurally but came back empty while
        # total_eval clearly implies positions exist (transient KIS quirk) — avoids UI flicker.
        if cached and not holdings and total > cash + 5000 and cached.get("holdings"):
            snap["holdings"] = cached["holdings"]
            snap["holdings_stale"] = True
            # 사장 지시 2026-05-21: KIS가 보유목록을 빈 채 주면서 nass_amt(총평가)를 부풀려
            # 반환하는 글리치 폴(보유=0인데 총평가−예수금이 큼) → 자산곡선·주문 사이징이 튄다.
            # 보유목록뿐 아니라 총평가도 직전 정상 스냅샷 값으로 유지해 안정화한다.
            prev_total = float((cached.get("buying_power") or {}).get("total_eval") or 0.0)
            if prev_total > 0:
                snap["buying_power"]["total_eval"] = prev_total
                snap["buying_power"]["total_stale"] = True
        # Transient cash=0 protection (관측된 버그 2026-05-14 09:58): KIS가 잔고는 정상이지만
        # 예수금 필드 3개(D0/D1/D2) 모두 0으로 반환하는 케이스가 있다. 이 경우 신규 매수가
        # 통째로 막힘. 캐시된 직전 cash가 있으면 그것을 유지하고 stale 플래그를 단다.
        if cached and snap["ok"] and cash <= 0:
            prev_cash = float((cached.get("buying_power") or {}).get("cash") or 0.0)
            if prev_cash > 0:
                snap["buying_power"]["cash"] = prev_cash
                snap["buying_power"]["cash_stale"] = True
                logger.warning(f"[잔고스냅샷] KIS가 cash=0 반환 — 캐시된 예수금 {prev_cash:,.0f}원 유지 (stale)")
        self._acct_snap = snap
        return snap

    async def kr_balance(self) -> str:
        snap = await self.kr_account_snapshot()
        bp = snap["buying_power"]
        if not bp["ok"]:
            return f"[잔고실패] KIS inquire-balance rt_cd≠0"
        lines = [f"[국내 계좌잔고] 예수금: {bp['cash']:,.0f}원 | 총평가: {bp['total_eval']:,.0f}원 | 평가손익률: {bp['pnl_ratio']*100:.2f}%\n"]
        for h in snap["holdings"]:
            lines.append(f"  📊 {h['name']} ({h['code']}): {h['qty']}주 | 평단 {h['avg_price']:,.0f} | 현재 {h['cur_price']:,.0f} | 손익 {h['pnl_amt']:,.0f} ({h['pnl_pct']:+.2f}%)")
        return "\n".join(lines)

    async def kr_holdings(self) -> List[Dict]:
        return (await self.kr_account_snapshot())["holdings"]

    async def kr_buying_power(self) -> Dict[str, float]:
        return (await self.kr_account_snapshot())["buying_power"]

    async def kr_last_price(self, code: str) -> float:
        """Current price as a float. KIS primary → 네이버 금융 폴백 (사장 지시 2026-05-14 — 028670 0원 이슈 해결).
        Returns 0.0 only when both sources fail."""
        try:
            d = await self.kr_price(code)
            px = float(str(d.get("stck_prpr","0")).replace(",",""))
            if px > 0:
                return px
        except Exception as e:
            logger.warning(f"[가격] KIS 조회 예외 ({code}): {e}")
        # Fallback: Naver Finance polling API (KIS가 0/실패 반환 시)
        try:
            loop = asyncio.get_event_loop()
            from tools.market_data import kr_price_naver
            px = await loop.run_in_executor(None, kr_price_naver, code)
            if px > 0:
                logger.info(f"[가격] {code} KIS 0원 → 네이버 폴백 성공: {px:,.0f}원")
            return float(px)
        except Exception as e:
            logger.warning(f"[가격] 네이버 폴백 실패 ({code}): {e}")
            return 0.0

    # ═══════════════════ 통합 종목 포트폴리오 (국내주식+해외주식+국내채권+펀드) ═══════════════════
    @staticmethod
    def _num(x):
        try: return float(str(x).replace(",",""))
        except (TypeError, ValueError): return 0.0
    @staticmethod
    def _int(x):
        try: return int(float(str(x).replace(",","")))
        except (TypeError, ValueError): return 0

    async def _overseas_holdings(self) -> List[Dict]:
        """해외주식 보유 종목 (NASD/NYSE/AMEX 순회). 실패/미보유 시 [].
        사장 피드백 2026-05-20(2차): KIS는 동일 보유분을 거래소별 inquire-balance 응답마다
        중복 노출한다(예: UUP가 NASD·AMEX 양쪽에 등장). 따라서 code 기준으로 **중복 제거**(첫 건만)
        해야 한다 — 이전엔 qty를 합산해 2주가 4주로 부풀던 버그. 실보유는 present-balance로 검증함."""
        seen: Dict[str, Dict] = {}
        try:
            tok = await self.token(); s = await self._s(); c, p = self._acnt()
            for excd in ("NASD", "NYSE", "AMEX"):
                try:
                    async with s.get(f"{self.base_url}/uapi/overseas-stock/v1/trading/inquire-balance",
                        headers=self._h(tok,"TTTS3012R"),
                        params={"CANO":c,"ACNT_PRDT_CD":p,"OVRS_EXCG_CD":excd,"TR_CRCY_CD":"USD",
                                "CTX_AREA_FK200":"","CTX_AREA_NK200":""}) as r:
                        d = await r.json()
                    for h in (d.get("output1") or []):
                        q = self._int(h.get("ovrs_cblc_qty"))
                        if q <= 0:
                            continue
                        code = (h.get("ovrs_pdno") or "").strip()
                        if not code or code in seen:
                            continue   # 거래소 간 중복 노출 — 첫 건만 채택(합산 금지)
                        seen[code] = {"code": code,
                                      "name": (h.get("ovrs_item_name") or "").strip() or code,
                                      "qty": q, "avg_price": self._num(h.get("pchs_avg_pric")),
                                      "cur_price": self._num(h.get("now_pric2")),
                                      "pnl_amt": self._num(h.get("frcr_evlu_pfls_amt") or h.get("evlu_pfls_amt")),
                                      "pnl_pct": self._num(h.get("evlu_pfls_rt")),
                                      "category": "해외주식", "ccy": "USD"}
                except Exception:
                    continue
        except Exception:
            pass
        return list(seen.values())

    async def _bond_holdings(self) -> List[Dict]:
        """장내채권 보유 잔고. API 미지원/실패 시 []."""
        out: List[Dict] = []
        try:
            tok = await self.token(); s = await self._s(); c, p = self._acnt()
            async with s.get(f"{self.base_url}/uapi/domestic-bond/v1/trading/inquire-balance",
                headers=self._h(tok,"CTSC8407R"),
                params={"CANO":c,"ACNT_PRDT_CD":p,"INQR_CNDT":"00","PDNO":"","BUY_DT":"",
                        "CTX_AREA_FK200":"","CTX_AREA_NK200":""}) as r:
                d = await r.json()
            for h in (d.get("output1") or d.get("output") or []):
                if not isinstance(h, dict):
                    continue
                q = self._int(h.get("cblc_qty") or h.get("hldg_qty") or h.get("nrcvb_buy_qty"))
                if q <= 0:
                    continue
                out.append({"code": (h.get("pdno") or "").strip(),
                            "name": (h.get("prdt_name") or "").strip() or (h.get("pdno") or "").strip(),
                            "qty": q, "avg_price": self._num(h.get("buy_unpr") or h.get("pchs_avg_pric")),
                            "cur_price": self._num(h.get("prpr") or h.get("evlu_pric") or h.get("bond_prpr")),
                            "pnl_amt": self._num(h.get("evlu_pfls_amt")), "pnl_pct": self._num(h.get("evlu_pfls_rt")),
                            "category": "국내채권", "ccy": "KRW"})
        except Exception:
            pass
        return out

    async def _fund_holdings(self) -> List[Dict]:
        """펀드 보유 잔고. KIS OpenAPI는 종목 단위 펀드 잔고 조회를 공개하지 않아 빈 목록 반환(안전).
        (국내주식 inquire-balance의 FUND_STTL_ICLD_YN='Y'는 펀드결제분 '금액'만 포함하며 보유내역이 아님.)"""
        return []

    async def _overseas_present_krw(self) -> Dict:
        """해외주식 원화환산 평가합계 + 기준환율 (CTRP6504R inquire-present-balance).
        사장 피드백 2026-05-20: 국내 inquire-balance 의 nass_amt(총평가)는 해외주식 평가를
        포함하지 않으므로, 통합 총평가 보정에 쓸 해외 원화평가합계를 별도 조회한다.
        실패/모의투자 미지원 시 {ok:False}. (실전 전용 — 모의 base_url 이면 시도하되 실패 허용.)"""
        try:
            tok = await self.token(); s = await self._s(); c, p = self._acnt()
            async with s.get(f"{self.base_url}/uapi/overseas-stock/v1/trading/inquire-present-balance",
                headers=self._h(tok, "CTRP6504R"),
                params={"CANO":c,"ACNT_PRDT_CD":p,"WCRC_FRCR_DVSN_CD":"02","NATN_CD":"840",
                        "TR_MKET_CD":"00","INQR_DVSN_CD":"00"}) as r:
                d = await r.json()
            if d.get("rt_cd") != "0":
                return {"ok": False, "krw_value": 0.0, "exrt": 0.0}
            o3 = d.get("output3") or {}
            krw_value = self._num(o3.get("evlu_amt_smtl_amt"))   # 해외 평가금액합계(원화환산)
            o2 = d.get("output2") or []
            exrt = self._num(o2[0].get("frst_bltn_exrt")) if o2 else 0.0
            return {"ok": True, "krw_value": krw_value, "exrt": exrt}
        except Exception as e:
            logger.warning(f"[해외원화평가] CTRP6504R 실패: {e}")
            return {"ok": False, "krw_value": 0.0, "exrt": 0.0}

    # 사장 지시 2026-05-21: 해외 원화평가 캐시 — KIS 해외잔고 조회가 간헐 실패하면 US 평가가
    # 통째로 빠져 통합 총평가가 ~16% 급락(자산곡선 -16% 글리치)한다. 마지막 정상값을 디스크에
    # 영속해 일시적 조회 실패를 메우고(재시작 콜드스타트 포함), 실제 매도(조회 성공+평가 0)면
    # 즉시 캐시를 비운다.
    _OVERSEAS_CACHE_TTL = 7200  # 2시간 — 일시 실패 보강용(실제 매도는 즉시 반영되므로 무관)

    def _overseas_cache_path(self) -> Path:
        return Path(__file__).resolve().parent.parent / "data" / "overseas_krw_cache.json"

    def _get_overseas_cache(self):
        c = getattr(self, "_overseas_krw_cache", None)
        if c is not None:
            return c
        try:
            p = self._overseas_cache_path()
            if p.exists():
                d = json.loads(p.read_text(encoding="utf-8"))
                self._overseas_krw_cache = (float(d.get("krw") or 0.0), float(d.get("ts") or 0.0))
                return self._overseas_krw_cache
        except Exception:
            pass
        self._overseas_krw_cache = (0.0, 0.0)
        return self._overseas_krw_cache

    def _set_overseas_cache(self, krw: float, ts: float) -> None:
        self._overseas_krw_cache = (float(krw), float(ts))
        try:
            self._overseas_cache_path().write_text(
                json.dumps({"krw": float(krw), "ts": float(ts)}, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    async def portfolio_holdings(self) -> Dict:
        """대시보드 '종목 포트폴리오' — 국내주식 + 해외주식 + 국내채권 + 펀드 통합 잔고.
        kr_account_snapshot()의 국내주식 잔고를 기준으로 나머지를 best-effort 병합한다.
        사장 피드백 2026-05-20: (code, category) 키로 최종 dedupe — 외부 거래 종목도 KIS 잔고가
        신뢰원천이므로 자동 포함되지만, 중복 노출만 방지(같은 카테고리 내 같은 code 합치기)."""
        snap = await self.kr_account_snapshot()
        kr = [{**h, "category": "국내주식", "ccy": "KRW"} for h in (snap.get("holdings") or [])]
        extra: List[Dict] = []
        for fn in (self._overseas_holdings, self._bond_holdings, self._fund_holdings):
            try:
                extra += await fn()
            except Exception:
                pass
        merged: Dict = {}
        for h in kr + extra:
            code = (h.get("code") or "").strip()
            cat = h.get("category") or ""
            if not code:
                continue
            key = (code, cat)
            if key in merged:
                ex = merged[key]
                q1, q2 = self._int(ex.get("qty")), self._int(h.get("qty"))
                tot = q1 + q2
                if tot > 0:
                    ex["avg_price"] = ((self._num(ex.get("avg_price")) * q1) + (self._num(h.get("avg_price")) * q2)) / tot
                ex["qty"] = tot
                ex["pnl_amt"] = self._num(ex.get("pnl_amt")) + self._num(h.get("pnl_amt"))
                if self._num(h.get("cur_price")) > 0:
                    ex["cur_price"] = self._num(h.get("cur_price"))
                if h.get("pnl_pct"):
                    ex["pnl_pct"] = h.get("pnl_pct")
            else:
                merged[key] = dict(h)
        holdings = list(merged.values())
        # ── 통합 총평가 보정: 국내 nass_amt(buying_power.total_eval)는 해외주식 평가를 포함하지
        #    않는다. USD 보유가 있으면 present-balance(원화환산 평가합계)를 더해 총자산을 맞춘다.
        #    + 각 USD 종목에 원화환산 평가액(krw_value)을 부여(프론트 표시용). ──
        bp = dict(snap["buying_power"])
        _now = time.time()
        _us_in_holdings = any(h.get("ccy") == "USD" for h in holdings)
        # 항상 권위 조회(ok 플래그 보유)로 US 원화평가를 확인 — 실패/진짜없음/정상을 구분해
        # 곡선·총평가가 조회 실패로 ~16% 급락하지 않게 한다.
        pk = await self._overseas_present_krw()
        krw = None
        if pk["ok"] and pk["krw_value"] > 0:
            krw = pk["krw_value"]                         # 조회 성공 + 평가 있음 = 권위값
            self._set_overseas_cache(krw, _now)
            if pk["exrt"] > 0:
                bp["fx_rate"] = pk["exrt"]
                for h in holdings:
                    if h.get("ccy") == "USD":
                        h["krw_value"] = round(self._num(h.get("qty")) * self._num(h.get("cur_price")) * pk["exrt"])
        elif pk["ok"] and pk["krw_value"] == 0 and not _us_in_holdings:
            self._set_overseas_cache(0.0, _now)           # 조회 성공 + 평가 0 + 보유목록도 US 없음 = 실제 매도 → 캐시 무효화
        else:
            # 조회 실패(ok=False) 또는 모순(보유목록엔 US인데 평가 0) → 최근 캐시로 보강(곡선 안정)
            _ck, _ct = self._get_overseas_cache()
            if _ck > 0 and (_now - _ct) < self._OVERSEAS_CACHE_TTL:
                krw = _ck
                bp["overseas_krw_stale"] = True
        if krw and krw > 0:
            bp["total_eval"] = self._num(bp.get("total_eval")) + krw
            bp["overseas_krw"] = krw
        return {"buying_power": bp, "holdings": holdings,
                "holdings_stale": snap.get("holdings_stale", False), "ok": snap.get("ok", False)}

    # ═══════════════════ 국내주식 순위/업종 ═══════════════════
    async def kr_volume_rank(self) -> str:
        tok = await self.token(); s = await self._s()
        async with s.get(f"{self.base_url}/uapi/domestic-stock/v1/quotations/volume-rank",
            headers=self._h(tok,"FHPST01710000"),
            params={"FID_COND_MRKT_DIV_CODE":"J","FID_COND_SCR_DIV_CODE":"20171",
                    "FID_INPUT_ISCD":"0000","FID_DIV_CLS_CODE":"0","FID_BLNG_CLS_CODE":"0",
                    "FID_TRGT_CLS_CODE":"111111111","FID_TRGT_EXLS_CLS_CODE":"000000",
                    "FID_INPUT_PRICE_1":"","FID_INPUT_PRICE_2":"","FID_VOL_CNT":"",
                    "FID_INPUT_DATE_1":""}) as r:
            data = (await r.json()).get("output",[])
        self._last_volrank = data  # cache raw for kr_volume_rank_list()
        lines = ["[거래량 순위 TOP 10]\n"]
        for i, d in enumerate(data[:10], 1):
            lines.append(f"  {i}. {d.get('hts_kor_isnm','')} ({d.get('mksc_shrn_iscd','')}) | "
                         f"{d.get('stck_prpr','')}원 | {d.get('prdy_ctrt','')}% | 거래량: {d.get('acml_vol','')}")
        return "\n".join(lines)

    async def kr_volume_rank_list(self) -> List[Dict]:
        """Parsed volume-rank: [{code,name,price}]. [] on failure. (Reuses the last raw if fresh.)"""
        try:
            data = getattr(self, "_last_volrank", None)
            if data is None:
                await self.kr_volume_rank()
                data = getattr(self, "_last_volrank", []) or []
            out = []
            for d in data:
                try: px = float(str(d.get("stck_prpr","0")).replace(",",""))
                except (TypeError, ValueError): px = 0.0
                code = (d.get("mksc_shrn_iscd") or "").strip()
                if code and px > 0:
                    out.append({"code": code, "name": (d.get("hts_kor_isnm") or "").strip(), "price": px})
            return out
        except Exception:
            return []

    async def kr_sector(self, sector_code: str = "0001") -> str:
        tok = await self.token(); s = await self._s()
        async with s.get(f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice",
            headers=self._h(tok,"FHKUP03500100"),
            params={"FID_COND_MRKT_DIV_CODE":"U","FID_INPUT_ISCD":sector_code,
                    "FID_INPUT_DATE_1":(datetime.now()-timedelta(days=30)).strftime("%Y%m%d"),
                    "FID_INPUT_DATE_2":datetime.now().strftime("%Y%m%d"),
                    "FID_PERIOD_DIV_CODE":"D"}) as r:
            data = (await r.json()).get("output2",[])
        return f"[업종지수 {sector_code}] {len(data)}일 데이터 조회 완료"

    async def kr_index_daily(self, index_code: str = "0001", days: int = 40) -> List[Dict]:
        """지수 일별 종가 (KOSPI=0001, KOSDAQ=1001). 벤치마크 오버레이용.
        반환: [{"date":"YYYY-MM-DD","close":float}, ...] 오름차순. 실패 시 []."""
        tok = await self.token(); s = await self._s()
        end = datetime.now(KST).strftime("%Y%m%d")
        start = (datetime.now(KST) - timedelta(days=days * 2)).strftime("%Y%m%d")
        out: List[Dict] = []
        try:
            async with s.get(f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice",
                headers=self._h(tok, "FHKUP03500100"),
                params={"FID_COND_MRKT_DIV_CODE":"U","FID_INPUT_ISCD":index_code,
                        "FID_INPUT_DATE_1":start,"FID_INPUT_DATE_2":end,
                        "FID_PERIOD_DIV_CODE":"D"}) as r:
                data = (await r.json()).get("output2", []) or []
            for x in data:
                d = (x.get("stck_bsop_date") or "").strip()
                c = x.get("bstp_nmix_prpr") or x.get("stck_clpr") or ""
                if len(d) == 8 and c not in ("", None):
                    try:
                        out.append({"date": f"{d[:4]}-{d[4:6]}-{d[6:8]}", "close": float(c)})
                    except (TypeError, ValueError):
                        pass
            out.sort(key=lambda r: r["date"])
        except Exception as ex:
            logger.warning(f"[지수일봉] {index_code} 조회 예외: {ex}")
        return out

    # ═══════════════════ 해외주식 ═══════════════════
    # KIS overseas-price (HHDFS00000300) EXCD codes: NAS(나스닥) / NYS(뉴욕) / AMS(아멕스).
    # JNJ·PG are NYSE names so a NASDAQ-only query returns an empty 'last'. Probe these, cache the hit.
    _US_EXCH_CODES = ("NAS", "NYS", "AMS")
    _us_excd_cache: Dict[str, str] = {}
    # 2026-05-19: 모든 거래소가 rt_cd=0(정상)인데 일봉 0건인 티커(예: 상장폐지 "X"=US Steel)를
    # 기억해 이후 사이클의 3회 중복 KIS 호출·경보 로그를 생략. 프로세스 재시작 시 비워져 재검증됨.
    _us_dataless: set = set()

    async def _us_price_raw(self, ticker: str) -> Dict:
        """First non-empty overseas-price 'output' across NAS/NYS/AMS ({} on total failure).
        Remembers which exchange answered for a ticker so later lookups are a single request."""
        tk = (ticker or "").strip().upper()
        if not tk:
            return {}
        tok = await self.token(); s = await self._s()
        codes = list(self._US_EXCH_CODES)
        if tk in self._us_excd_cache:  # try the known exchange first
            codes = [self._us_excd_cache[tk]] + [c for c in codes if c != self._us_excd_cache[tk]]
        for i, excd in enumerate(codes):
            try:
                if i:
                    await asyncio.sleep(0.3)  # ease KIS TPS between probes
                async with s.get(f"{self.base_url}/uapi/overseas-price/v1/quotations/price",
                    headers=self._h(tok,"HHDFS00000300"),
                    params={"AUTH":"","EXCD":excd,"SYMB":tk}) as r:
                    d = (await r.json()).get("output", {}) or {}
                if d.get("last") not in (None, "", "0", "0.0", "0.00", "0.0000"):
                    self._us_excd_cache[tk] = excd
                    return {**d, "_excd": excd}
            except Exception:
                continue
        return {}

    async def us_price(self, ticker: str, excd: Optional[str] = None) -> str:
        if excd:
            tok = await self.token(); s = await self._s()
            async with s.get(f"{self.base_url}/uapi/overseas-price/v1/quotations/price",
                headers=self._h(tok,"HHDFS00000300"),
                params={"AUTH":"","EXCD":excd,"SYMB":ticker}) as r:
                d = (await r.json()).get("output",{}) or {}
        else:
            d = await self._us_price_raw(ticker)
        if not d or d.get("last") in (None, ""):
            return f"[US시세] {ticker} | 시세 조회 실패 (거래소 미확인)"
        return f"[US시세] {ticker} | ${d.get('last','?')} | {d.get('rate','')}% | 거래량: {d.get('tvol','')}" + (f" | {d.get('_excd')}" if d.get('_excd') else "")

    async def us_last_price(self, ticker: str) -> float:
        """US 현재가(float, USD). 거래소를 자동 탐색. 실패 시 0.0."""
        d = await self._us_price_raw(ticker)
        try:
            return float(str(d.get("last", "0")).replace(",", ""))
        except (TypeError, ValueError):
            return 0.0

    async def us_daily_chart(self, ticker: str, days: int = 100) -> List[Dict]:
        """미국주식 일봉 조회 (HHDFS76240000) — 거래소(NAS/NYS/AMS) 직접 프로브 후 CSV 누적.
        Returns parsed rows; writes to data/daily_US_{ticker}.csv with the same OHLCV schema as KR.
        사장 지시(2026-05-14): 미국 종목도 historical 데이터를 가져야 퀀트 분석이 정상 작동.

        버그 수정 2026-05-19: 과거엔 실시간시세 프로브(_us_price_raw)로만 거래소를
        알아내, 그게 실패하면 excd가 'NAS'로 폴백돼 NYSE 종목(예: BAC)이 0행이 됐다.
        일봉은 실시간 호가가 필요 없으므로 dailyprice 엔드포인트 자체를 거래소별로
        직접 시도하고 첫 비어있지 않은 응답을 채택한다 (장시간·시세구독과 무관)."""
        tk = (ticker or "").strip().upper()
        if not tk:
            return []
        if tk in self._us_dataless:
            logger.info(f"[US일봉] {tk} 상장폐지/데이터없음 캐시 — KIS 조회 생략 (재시작 시 재검증)")
            return []
        tok = await self.token(); s = await self._s()
        # 알려진 거래소가 있으면 그것부터, 없으면 NAS→NYS→AMS 순으로 일봉 직접 프로브
        codes = list(self._US_EXCH_CODES)
        if tk in self._us_excd_cache:
            codes = [self._us_excd_cache[tk]] + [c for c in codes if c != self._us_excd_cache[tk]]
        data: List[Dict] = []
        exchange_results = []
        for i, excd in enumerate(codes):
            try:
                if i:
                    await asyncio.sleep(0.3)  # KIS TPS 완화
                async with s.get(f"{self.base_url}/uapi/overseas-price/v1/quotations/dailyprice",
                    headers=self._h(tok, "HHDFS76240000"),
                    params={"AUTH":"","EXCD":excd,"SYMB":tk,
                            "GUBN":"0",          # 0=일, 1=주, 2=월
                            "BYMD": datetime.now(KST).strftime("%Y%m%d"),
                            "MODP":"1"}) as r:   # 1=수정주가 반영
                    resp_json = await r.json()
                    status = resp_json.get("rt_cd", "?")
                    _out = resp_json.get("output2", []) or []
                    exchange_results.append({"excd": excd, "status": status, "output2_len": len(_out)})
                    if status != "0":
                        logger.warning(f"[US일봉] {tk} {excd} rt_cd={status} msg1={resp_json.get('msg1','')} output2_len={len(_out)}")
                    elif not _out:
                        logger.info(f"[US일봉] {tk} {excd} rt_cd=0 but output2 empty (msg1={resp_json.get('msg1','')})")
                if _out:
                    data = _out
                    self._us_excd_cache[tk] = excd   # 다음 조회는 단일 요청으로
                    break
            except Exception as e:
                logger.warning(f"[US일봉] {tk} {excd} 조회 예외: {e}")
                continue
        if not data:
            details = "; ".join(f"{r['excd']}: status={r['status']}, output2_len={r['output2_len']}" for r in exchange_results)
            _all_clean_empty = (len(exchange_results) == len(codes)
                                and all(r["status"] == "0" and r["output2_len"] == 0 for r in exchange_results))
            if _all_clean_empty:
                # 모든 거래소가 rt_cd=0(정상)인데 데이터 0 → 상장폐지/미지원 종목으로 확정.
                # 캐시 등록해 이후 사이클의 중복 호출·경보를 제거 (프로세스 재시작 시 재검증).
                self._us_dataless.add(tk)
                logger.info(f"[US일봉] {tk} 상장폐지/데이터없음 확정 (전 거래소 rt_cd=0+빈응답) — "
                            f"이후 사이클 조회 생략. 상세: {details}")
            else:
                # 일부 거래소 에러/비정상 → 일시적일 수 있어 캐시하지 않고 경고 유지(다음 사이클 재시도).
                logger.warning(f"[US일봉] {tk} 모든 거래소(NAS/NYS/AMS) output2 비어있음 — 상세: {details}")
            return []
        rows = []
        for x in data[:max(1, days)]:
            d = (x.get("xymd") or "").strip()       # yyyymmdd
            if not d or len(d) < 8: continue
            iso = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
            try:
                rows.append({"date": iso,
                             "open":  float(x.get("open")  or 0),
                             "high":  float(x.get("high")  or 0),
                             "low":   float(x.get("low")   or 0),
                             "close": float(x.get("clos")  or 0),
                             "volume":int(float(x.get("tvol") or 0))})
            except Exception:
                continue
        # newest-first → sort ascending so CSV reads naturally
        rows.sort(key=lambda r: r["date"])
        self._append_csv(f"daily_US_{tk}.csv", rows, ["date","open","high","low","close","volume"])
        return rows

    async def _overseas_order_body(self, ticker: str, qty: int, price: float,
                                   *, side: str, excd: str):
        """KIS 해외주식 주문 바디 조립.

        버그(2026-05-19): KIS 해외주식 주문 TR(TTTT1002U/1006U)은 국내 '01'
        시장가가 없고 유효 지정가 단가를 요구한다. price 미지정(시장가 의도)에
        OVRS_ORD_UNPR="0" 을 보내면 KIS 가 "가격 $0.01 미만 주문불가" 로 거부.
        → 시장가 의도면 현재가 기반 **체결가능 지정가**로 환산해 전송한다.
        현재가 미확보면 0 전송 대신 실패 문자열 반환(원칙 #14: 데이터 결손을
        주문가능 조건으로 오인 금지). 거래소는 시세 프로브가 캐싱한 값을 매핑.
        반환: dict(주문 바디) 또는 str(실패 — 미전송)."""
        import math
        tk = (ticker or "").strip().upper()
        explicit = False
        try:
            lp = float(price or 0)
        except (TypeError, ValueError):
            lp = 0.0
        if lp > 0:
            explicit = True   # 호출자가 명시 지정가 → 그대로 사용(버퍼 X)
        else:
            # 시장가 의도 → 다중 폴백으로 끝까지 가격 확보(주문 스킵 금지).
            lp = await self.us_last_price(tk)
            if not lp or lp <= 0:                 # ① 실시간 실패 → 일봉 종가
                try:
                    rows = await self.us_daily_chart(tk, days=5)
                except Exception:
                    rows = []
                if rows:
                    try:
                        lp = float(rows[-1].get("close") or 0)
                    except (TypeError, ValueError):
                        lp = 0.0
            if not lp or lp <= 0:
                # 실시간·일봉 모두 비어 KIS 지정가에 넣을 단가가 물리적으로
                # 없음(상장폐지/미지원 추정). 0 전송은 원래 버그 재현이라 금지.
                return (f"[US{'매수' if side == 'buy' else '매도'} 실패] {tk} "
                        f"현재가·일봉 모두 미확보 — 단가 산출 불가, 주문 미전송")
        if explicit:           # 명시 지정가: 센트 정밀도만 정규화
            unpr = round(lp, 2)
        elif side == "buy":    # 매수: 현재가보다 살짝 위(체결 보장), 센트 올림
            unpr = math.ceil(lp * 1.003 * 100) / 100.0
        else:                   # 매도: 현재가보다 살짝 아래, 센트 내림
            unpr = math.floor(lp * 0.997 * 100) / 100.0
        excg = excd_to_excg(self._us_excd_cache.get(tk) or excd)
        c, p = self._acnt()
        return {"CANO": c, "ACNT_PRDT_CD": p, "OVRS_EXCG_CD": excg, "PDNO": tk,
                "ORD_QTY": str(int(qty)), "OVRS_ORD_UNPR": f"{unpr:.2f}",
                "ORD_SVR_DVSN_CD": "0", "ORD_DVSN": "00"}

    async def us_buy(self, ticker: str, qty: int, price: float = 0, excd: str = "NASD") -> str:
        body = await self._overseas_order_body(ticker, qty, price, side="buy", excd=excd)
        if isinstance(body, str):
            return body
        tok = await self.token(); s = await self._s()
        async with s.post(f"{self.base_url}/uapi/overseas-stock/v1/trading/order",
            headers=self._h(tok,"TTTT1002U"), json=body) as r:
            d = await r.json()
        msg = _clean_kis_msg(d.get('msg1', ''))
        # KR(kr_buy)과 대칭: rt_cd≠0(거부)면 [실패] 프리픽스 — 호출부 accepted 휴리스틱이
        # "주문가능금액을 초과" 같은 거부를 잡아 잠정 체결로 오판하지 않게 한다.
        return (f"[US매수] {ticker} {qty}주 @ ${body['OVRS_ORD_UNPR']} → {msg}"
                if d.get("rt_cd") == "0" else
                f"[US매수 실패] {ticker} {qty}주 → {msg}")

    async def us_sell(self, ticker: str, qty: int, price: float = 0, excd: str = "NASD") -> str:
        body = await self._overseas_order_body(ticker, qty, price, side="sell", excd=excd)
        if isinstance(body, str):
            return body
        tok = await self.token(); s = await self._s()
        async with s.post(f"{self.base_url}/uapi/overseas-stock/v1/trading/order",
            headers=self._h(tok,"TTTT1006U"), json=body) as r:
            d = await r.json()
        msg = _clean_kis_msg(d.get('msg1', ''))
        return (f"[US매도] {ticker} {qty}주 @ ${body['OVRS_ORD_UNPR']} → {msg}"
                if d.get("rt_cd") == "0" else
                f"[US매도 실패] {ticker} {qty}주 → {msg}")

    async def us_balance(self) -> str:
        tok = await self.token(); s = await self._s(); c, p = self._acnt()
        async with s.get(f"{self.base_url}/uapi/overseas-stock/v1/trading/inquire-balance",
            headers=self._h(tok,"TTTS3012R"),
            params={"CANO":c,"ACNT_PRDT_CD":p,"OVRS_EXCG_CD":"NASD","TR_CRCY_CD":"USD",
                    "CTX_AREA_FK200":"","CTX_AREA_NK200":""}) as r:
            d = await r.json()
        out1 = d.get("output1",[]); out2 = d.get("output2",{})
        lines = [f"[해외 계좌잔고] 총평가: ${out2.get('tot_evlu_pfls_amt','?')}\n"]
        for h in out1:
            lines.append(f"  📊 {h.get('ovrs_item_name','')} ({h.get('ovrs_pdno','')}): {h.get('ovrs_cblc_qty','')}주 | "
                         f"${h.get('now_pric2','')} | 손익: ${h.get('evlu_pfls_amt','')}")
        return "\n".join(lines)

    # ═══════════════════ 장내채권 ═══════════════════
    async def bond_price(self, code: str) -> str:
        tok = await self.token(); s = await self._s()
        async with s.get(f"{self.base_url}/uapi/domestic-bond/v1/quotations/inquire-price",
            headers=self._h(tok,"FHKBJ773401C0"),
            params={"FID_COND_MRKT_DIV_CODE":"B","FID_INPUT_ISCD":code}) as r:
            d = (await r.json()).get("output",{})
        return f"[채권시세] {code} | {d.get('bond_prpr','')} | 수익률: {d.get('bond_ytm','')}"

    async def bond_buy(self, code: str, qty: int, price: float) -> str:
        tok = await self.token(); s = await self._s(); c, p = self._acnt()
        async with s.post(f"{self.base_url}/uapi/domestic-bond/v1/trading/order",
            headers=self._h(tok,"TTTC0951U"),
            json={"CANO":c,"ACNT_PRDT_CD":p,"PDNO":code,"ORD_DVSN":"00",
                  "ORD_QTY":str(qty),"BOND_ORD_UNPR":str(price)}) as r:
            d = await r.json()
        return f"[채권매수] {code} {qty}매 → {d.get('msg1','')}"

    # ═══════════════════ 해외선물옵션 ═══════════════════
    async def futures_price(self, code: str, excd: str = "CME") -> str:
        tok = await self.token(); s = await self._s()
        async with s.get(f"{self.base_url}/uapi/overseas-futureoption/v1/quotations/inquire-price",
            headers=self._h(tok,"HHDFS76200200"),
            params={"EXCD":excd,"SYMB":code}) as r:
            d = (await r.json()).get("output",{})
        return f"[해외선물] {code} | {d.get('last','?')} | {d.get('rate','')}%"

    async def futures_buy(self, code: str, qty: int, price: float, excd: str = "CME") -> str:
        tok = await self.token(); s = await self._s(); c, p = self._acnt()
        async with s.post(f"{self.base_url}/uapi/overseas-futureoption/v1/trading/order",
            headers=self._h(tok,"TTTS6036U"),
            json={"CANO":c,"ACNT_PRDT_CD":p,"OVRS_FUOP_ECNG_MRKT_CD":excd,
                  "PDNO":code,"SLL_BUY_DVSN_CD":"02","ORD_QTY":str(qty),
                  "OVRS_FUOP_LMT_PRIC":str(price),"ORD_DVSN_CD":"00"}) as r:
            d = await r.json()
        return f"[해외선물매수] {code} → {d.get('msg1','')}"

    # ═══════════════════ 국내선물옵션 ═══════════════════
    async def kr_futures_price(self, code: str) -> str:
        tok = await self.token(); s = await self._s()
        async with s.get(f"{self.base_url}/uapi/domestic-futureoption/v1/quotations/inquire-price",
            headers=self._h(tok,"FHMIF10000000"),
            params={"FID_COND_MRKT_DIV_CODE":"F","FID_INPUT_ISCD":code}) as r:
            d = (await r.json()).get("output",{})
        return f"[국내선물] {code} | {d.get('futs_prpr','?')} | {d.get('prdy_ctrt','')}%"

    # ═══════════════════ 통합 잔고 ═══════════════════
    async def get_account_balance(self) -> str:
        kr = await self.kr_balance(); us = await self.us_balance()
        return f"{kr}\n\n{us}"

    # ═══════════════════ 통합 주문 ═══════════════════
    async def place_order(self, order: OrderDraft) -> str:
        if not order.approved:
            return "[주문 거부] 리스크관리실 승인 필요"
        if order.market == "KR":
            if order.side == OrderSide.BUY:
                return await self.kr_buy(order.ticker, order.qty, int(order.limit_price or 0))
            else:
                return await self.kr_sell(order.ticker, order.qty, int(order.limit_price or 0))
        elif order.market == "US":
            if order.side == OrderSide.BUY:
                return await self.us_buy(order.ticker, order.qty, order.limit_price or 0)
            else:
                return await self.us_sell(order.ticker, order.qty, order.limit_price or 0)
        elif order.market == "BOND":
            return await self.bond_buy(order.ticker, order.qty, order.limit_price or 0)
        elif order.market == "FUTURES":
            return await self.futures_buy(order.ticker, order.qty, order.limit_price or 0)
        return f"[에러] 미지원 시장: {order.market}"

    # ═══════════════════ CSV 누적 ═══════════════════
    def _append_csv(self, filename: str, rows: List[Dict], columns: List[str]):
        if not rows: return
        path = DATA_DIR / filename
        exists = path.exists()
        seen = set()
        if exists:
            with open(path, "r", encoding="utf-8") as f:
                for line in csv.DictReader(f):
                    seen.add(line.get(columns[0], ""))
        new_rows = [r for r in rows if r.get(columns[0], "") and r[columns[0]] not in seen]
        if not new_rows: return
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=columns)
            if not exists: w.writeheader()
            w.writerows(new_rows)
        logger.info(f"CSV 누적: {filename} +{len(new_rows)}행")

_broker: Optional[KISBroker] = None
def get_broker() -> KISBroker:
    global _broker
    if _broker is None: _broker = KISBroker()
    return _broker
