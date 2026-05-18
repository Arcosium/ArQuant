"""
Arquant v1.0 - OpenDart Disclosure Crawler
공시 데이터 크롤링. DART 인증키는 환경변수 OPENDART_API_KEY 로만 주입한다
(config.OPENDART_API_KEY). 절대 소스/문서에 키 값을 박지 말 것.
"""
import aiohttp, logging, os
from datetime import datetime, timedelta
from typing import List, Dict, Optional
logger = logging.getLogger("DART")
DART_BASE = "https://opendart.fss.or.kr/api"

async def search_disclosures(corp_name: str = "", bgn_de: str = "", end_de: str = "",
                              pblntf_ty: str = "", page_count: int = 20) -> str:
    from config import OPENDART_API_KEY
    if not end_de: end_de = datetime.now().strftime("%Y%m%d")
    if not bgn_de: bgn_de = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
    params = {"crtfc_key": OPENDART_API_KEY, "bgn_de": bgn_de, "end_de": end_de,
              "page_count": str(page_count), "sort": "date", "sort_mth": "desc"}
    if corp_name: params["corp_name"] = corp_name
    if pblntf_ty: params["pblntf_ty"] = pblntf_ty
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{DART_BASE}/list.json", params=params, timeout=aiohttp.ClientTimeout(total=15)) as r:
                data = await r.json()
        if data.get("status") != "000": return f"[DART] 조회 실패: {data.get('message','')}"
        items = data.get("list", [])
        lines = [f"[DART 공시] {bgn_de}~{end_de} | {len(items)}건\n"]
        for i, d in enumerate(items[:15], 1):
            lines.append(f"  {i}. 📋 [{d.get('corp_name','')}] {d.get('report_nm','')}")
            lines.append(f"     접수일: {d.get('rcept_dt','')} | 공시유형: {d.get('pblntf_ty_nm','')}")
        return "\n".join(lines)
    except Exception as e: return f"[DART 에러] {e}"

async def get_corp_financials(corp_code: str, bsns_year: int) -> str:
    from config import OPENDART_API_KEY
    params = {"crtfc_key": OPENDART_API_KEY, "corp_code": corp_code,
              "bsns_year": str(bsns_year), "reprt_code": "11011"}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{DART_BASE}/fnlttSinglAcnt.json", params=params, timeout=aiohttp.ClientTimeout(total=15)) as r:
                data = await r.json()
        if data.get("status") != "000": return f"[DART 재무] 조회 실패: {data.get('message','')}"
        items = data.get("list", [])
        lines = [f"[DART 재무제표] {bsns_year}년 | {len(items)}항목\n"]
        for it in items[:20]:
            lines.append(f"  {it.get('account_nm','')}: {it.get('thstrm_amount','')}")
        return "\n".join(lines)
    except Exception as e: return f"[DART 재무 에러] {e}"


# 사장 피드백 2026-05-15 (#7): 직전연도 요약재무상태표 + 손익계산서 + 최근 공시를 한 번에.
# 핵심 계정만 골라 LLM 토큰 절약 (회계 전 계정을 다 보낼 필요는 없음).
_BS_KEYS = ("자산총계", "부채총계", "자본총계", "유동자산", "유동부채", "비유동자산", "비유동부채")
_IS_KEYS = ("매출액", "수익(매출액)", "영업수익", "매출원가", "매출총이익", "판매비와관리비", "영업이익", "영업이익(손실)",
            "법인세비용차감전순이익", "당기순이익", "지배기업소유주지분 순이익")


def _parse_fin_items(items: list) -> tuple:
    """fnlttSinglAcntAll list → (bs_lines, is_lines) 핵심 계정만."""
    bs_lines: List[str] = []
    is_lines: List[str] = []
    for it in items:
        name = (it.get("account_nm") or "").strip()
        amount = (it.get("thstrm_amount") or "").strip()
        sj_div = (it.get("sj_div") or "").strip()  # BS=재무상태표, IS=손익, CIS=포괄손익
        if not name or not amount:
            continue
        if sj_div == "BS" and name in _BS_KEYS and not any(name in l for l in bs_lines):
            bs_lines.append(f"  - {name}: {amount}원")
        elif sj_div in ("IS", "CIS") and name in _IS_KEYS and not any(name in l for l in is_lines):
            is_lines.append(f"  - {name}: {amount}원")
    return bs_lines, is_lines


# 최신성 순서: 3분기 > 반기 > 1분기 > (해당연도 사업보고서). 연도는 올해→작년.
_REPRT_RECENCY = [("11014", "3분기"), ("11012", "반기"), ("11013", "1분기"), ("11011", "사업")]


async def get_financial_summary(corp_code: str, bsns_year: Optional[int] = None) -> str:
    """**가장 최근** 분기/반기/연간 요약재무상태표 + 손익계산서.
    (연도 × 보고서코드)를 최신→과거로 순회하며 데이터가 잡히는 첫 보고서를 반환.
    corp_code는 8자리 DART 고유번호. 사장 피드백 2026-05-18 (최신 재무 강제)."""
    from config import OPENDART_API_KEY
    if not OPENDART_API_KEY:
        return "[DART 재무요약] OPENDART_API_KEY 없음 — 조회 불가"
    this_year = datetime.now().year
    years = [bsns_year] if bsns_year else [this_year, this_year - 1, this_year - 2]
    last_msg = ""
    try:
        async with aiohttp.ClientSession() as s:
            for yr in years:
                for reprt_code, reprt_ko in _REPRT_RECENCY:
                    for fs_div in ("CFS", "OFS"):  # 연결 우선, 없으면 별도
                        params = {"crtfc_key": OPENDART_API_KEY, "corp_code": corp_code,
                                  "bsns_year": str(yr), "reprt_code": reprt_code, "fs_div": fs_div}
                        try:
                            async with s.get(f"{DART_BASE}/fnlttSinglAcntAll.json", params=params,
                                              timeout=aiohttp.ClientTimeout(total=15)) as r:
                                data = await r.json()
                        except Exception as e:
                            last_msg = str(e); continue
                        if data.get("status") != "000":
                            last_msg = data.get("message", ""); continue
                        bs_lines, is_lines = _parse_fin_items(data.get("list", []))
                        if not bs_lines and not is_lines:
                            continue
                        kind = "연결" if fs_div == "CFS" else "별도"
                        parts = [f"[DART 요약재무 — {yr}년 {reprt_ko}보고서 ({kind}) · 최신 가용분]"]
                        if bs_lines:
                            parts.append("재무상태표 핵심:"); parts.extend(bs_lines)
                        if is_lines:
                            parts.append("손익계산서 핵심:"); parts.extend(is_lines)
                        return "\n".join(parts)
        return f"[DART 재무요약] {corp_code}: 최근 3년 분기/반기/연간 보고서에서 재무 미발견 ({last_msg})"
    except Exception as e:
        return f"[DART 재무요약 에러] {e}"


# DART 고유번호 (corp_code 8자리) ↔ stock_code (6자리) 매핑.
# OpenDART corpCode.xml로 한 번 받아서 메모리에 캐싱. 첫 호출 시 다운로드 ~수 초.
_CORP_CODE_CACHE: Dict[str, str] = {}        # stock_code → corp_code
_CORP_NAME_TO_CODE: Dict[str, str] = {}       # corp_name → corp_code
_CORP_CACHE_LOADED = False


async def _load_corp_code_map() -> None:
    """DART corpCode.xml을 다운로드해 stock_code → corp_code 매핑을 캐시.
    한 번만 로드되며 메모리 사용량 ~1MB. 사장 피드백 2026-05-15 #7."""
    global _CORP_CACHE_LOADED
    if _CORP_CACHE_LOADED:
        return
    from config import OPENDART_API_KEY
    if not OPENDART_API_KEY:
        _CORP_CACHE_LOADED = True
        return
    try:
        import zipfile, io, xml.etree.ElementTree as ET
        params = {"crtfc_key": OPENDART_API_KEY}
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{DART_BASE}/corpCode.xml", params=params, timeout=aiohttp.ClientTimeout(total=30)) as r:
                blob = await r.read()
        # corpCode.xml은 zip으로 옴
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            for name in zf.namelist():
                if name.lower().endswith(".xml"):
                    root = ET.fromstring(zf.read(name).decode("utf-8", errors="ignore"))
                    for el in root.findall("list"):
                        stock_code = (el.findtext("stock_code") or "").strip()
                        corp_code = (el.findtext("corp_code") or "").strip()
                        corp_name = (el.findtext("corp_name") or "").strip()
                        if corp_code:
                            if stock_code:
                                _CORP_CODE_CACHE[stock_code] = corp_code
                            if corp_name:
                                _CORP_NAME_TO_CODE[corp_name] = corp_code
                    break
        logger.info(f"[DART] corpCode 매핑 {len(_CORP_CODE_CACHE)}건 로드 (stock_code 기준)")
    except Exception as e:
        logger.warning(f"[DART] corpCode.xml 로드 실패: {e}")
    finally:
        _CORP_CACHE_LOADED = True


async def get_financial_summary_by_stock_code(stock_code: str) -> str:
    """6자리 stock_code → corp_code → 직전연도 요약재무.
    사장 피드백 2026-05-15 #7."""
    if not stock_code or not stock_code.isdigit() or len(stock_code) != 6:
        return ""
    await _load_corp_code_map()
    corp_code = _CORP_CODE_CACHE.get(stock_code)
    if not corp_code:
        return f"[DART 재무요약] {stock_code}: corp_code 매핑 없음 (corpCode.xml 미수록)"
    return await get_financial_summary(corp_code)

async def get_major_shareholder(corp_code: str) -> str:
    from config import OPENDART_API_KEY
    params = {"crtfc_key": OPENDART_API_KEY, "corp_code": corp_code}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{DART_BASE}/majorstock.json", params=params, timeout=aiohttp.ClientTimeout(total=15)) as r:
                data = await r.json()
        if data.get("status") != "000": return f"[DART] {data.get('message','')}"
        items = data.get("list", [])
        lines = [f"[DART 대주주현황] {len(items)}명\n"]
        for s in items[:10]:
            lines.append(f"  {s.get('nm','')}: {s.get('bsis_posesn_stock_co','')}주 ({s.get('bsis_posesn_stock_qota_rt','')}%)")
        return "\n".join(lines)
    except Exception as e: return f"[DART 에러] {e}"
