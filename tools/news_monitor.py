"""
Arquant v1.0 - Naver Finance News Crawler (Continuous Monitor)
Crawls https://finance.naver.com/news/mainnews.naver in real-time.
No API key needed - pure HTML scraping with requests + BeautifulSoup.
Adapted from ArcAI.ve Daily/VC_Crawling/news_crawler.py pattern.

Articles are classified at crawl time into 'KR' (국내 시장), 'US' (미국 시장),
or 'BOTH' (양쪽 모두 영향) so the orchestrator can route them to the right
analysis cycle (KR 장 시간엔 KR+BOTH만 분석/매매, US 장 시간엔 US+BOTH만).
"""
import asyncio
import difflib
import json as _json
import logging
import os
import re
import datetime
from pathlib import Path
from typing import List, Dict, Optional
from urllib.parse import urljoin

import aiohttp
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("NEWS_MONITOR")

KST = datetime.timezone(datetime.timedelta(hours=9))
# 증권 속보 (section_id=101 경제 / section_id2=258 증권, mode=LSS2D) — 메인 뉴스보다 빠르고 종목 기사가 많음
NAVER_FINANCE_NEWS_URL = "https://finance.naver.com/news/news_list.naver?mode=LSS2D&section_id=101&section_id2=258"
NAVER_FINANCE_NEWS_FALLBACK_URL = "https://finance.naver.com/news/mainnews.naver"
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

# 사장 피드백 2026-05-16: 뉴스 목록을 디스크에 영속화 — 서버 재시작(코드 갱신 후 자동 재개 포함)
# 후에도 누적 뉴스가 사라지지 않도록 한다.
_NEWS_STATE_FILE = Path(__file__).parent.parent / "data" / "news_history.json"
_MAX_PERSIST_ARTICLES = 600   # 최근 600건만 보관 (대시보드는 20건만 표시)
_MAX_PERSIST_LINKS = 3000     # 중복 차단용 링크 캐시 상한


class NaverFinanceMonitor:
    """
    Continuous Naver Finance news monitor.
    Tracks already-seen articles to avoid duplicates.
    """

    # Two headlines are "the same story" if their normalized forms are this similar.
    _TITLE_DUP_RATIO = 0.86

    def __init__(self):
        self._seen_links: set = set()
        self._seen_titles: List[str] = []      # normalized titles already emitted (persistent, not per-cycle)
        self._article_history: List[Dict] = []
        self.is_running = False
        self.last_crawl_time: Optional[str] = None
        self.total_articles_found = 0
        self._load_persisted()

    # ── Disk persistence (사장 피드백 2026-05-16) ─────────────────────────────
    def _load_persisted(self):
        """서버 시작 시 디스크에서 누적 뉴스/중복 캐시를 복원. 실패해도 빈 상태로 진행(fail-open)."""
        try:
            if not _NEWS_STATE_FILE.exists():
                return
            data = _json.loads(_NEWS_STATE_FILE.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            self._article_history = list(data.get("article_history") or [])[-_MAX_PERSIST_ARTICLES:]
            self._seen_links = set(data.get("seen_links") or [])
            self._seen_titles = list(data.get("seen_titles") or [])[-800:]
            self.total_articles_found = int(data.get("total_articles_found") or len(self._article_history))
            self.last_crawl_time = data.get("last_crawl_time")
            logger.info(f"📰 뉴스 영속 상태 복원: {len(self._article_history)}건 "
                        f"(누적 {self.total_articles_found}, 링크캐시 {len(self._seen_links)})")
        except Exception as e:
            logger.warning(f"뉴스 영속 상태 복원 실패 (빈 상태로 시작): {e}")

    def _save_persisted(self):
        """크롤 후 누적 뉴스/중복 캐시를 원자적으로 저장 (temp 작성 후 os.replace)."""
        try:
            _NEWS_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "article_history": self._article_history[-_MAX_PERSIST_ARTICLES:],
                "seen_links": list(self._seen_links)[-_MAX_PERSIST_LINKS:],
                "seen_titles": self._seen_titles[-800:],
                "total_articles_found": self.total_articles_found,
                "last_crawl_time": self.last_crawl_time,
            }
            tmp = _NEWS_STATE_FILE.with_suffix(".json.tmp")
            tmp.write_text(_json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, _NEWS_STATE_FILE)
        except Exception as e:
            logger.warning(f"뉴스 영속 상태 저장 실패: {e}")

    @staticmethod
    def _norm_title(title: str) -> str:
        # drop the leading [press]/【태그】, whitespace and punctuation; lowercase
        t = re.sub(r"^\s*[\[\(【][^\]\)】]{1,20}[\]\)】]\s*", "", title or "")
        return re.sub(r"[\s\W_]+", "", t).lower()

    def _is_dup_title(self, title: str) -> bool:
        n = self._norm_title(title)
        if not n:
            return True
        for e in self._seen_titles:
            if n == e or difflib.SequenceMatcher(None, n, e).ratio() >= self._TITLE_DUP_RATIO:
                return True
            # one fully contains the other AND they're close in length → same story re-listed
            if len(n) >= 14 and len(e) >= 14 and (n in e or e in n) and min(len(n), len(e)) / max(len(n), len(e)) >= 0.8:
                return True
        return False

    def _remember_title(self, title: str):
        n = self._norm_title(title)
        if n:
            self._seen_titles.append(n)
            if len(self._seen_titles) > 800:
                self._seen_titles = self._seen_titles[-800:]

    # ── Market classification (KR / US / BOTH) ───────────────────────────────
    # Heuristic: count keyword hits per market. KR-leaning words are weighted by 6자리 코드 매칭,
    # US-leaning by NASDAQ/NYSE/Fed-style references + 미국 티커 정규식. Tie → 'BOTH'.
    _US_TICKER_RE = re.compile(r"\b([A-Z]{2,5})\b")
    _KR_CODE_RE = re.compile(r"(?<!\d)\d{6}(?!\d)")
    _US_KW = (
        "나스닥", "뉴욕증시", "다우존스", "다우지수", "S&P", "에스앤피", "S&P500", "스앤피500",
        "FOMC", "연준", "연방준비", "파월", "옐런", "트럼프", "바이든", "월가", "월街",
        "미 증시", "미국증시", "미 연준", "미 국채", "미국채", "미국 국채",
        "Apple", "Microsoft", "Google", "Tesla", "Nvidia", "Meta", "Amazon",
        "애플", "테슬라", "엔비디아", "구글", "아마존", "메타플랫폼",
        "wall street", "fed ", "treasury yield", "us cpi",
    )
    _KR_KW = (
        "코스피", "코스닥", "KOSPI", "KOSDAQ", "KOSPI200", "코스피200",
        "삼성전자", "SK하이닉스", "현대차", "기아", "네이버", "카카오",
        "한국은행", "한은", "금감원", "금융위", "금융감독원", "원/달러", "원달러",
        "국내 증시", "국내증시", "한국증시", "거래소", "한국거래소", "KRX",
    )
    _US_SAFE_TICKERS = (
        "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "TSLA", "NVDA", "AMD",
        "NFLX", "INTC", "QCOM", "AVGO", "ORCL", "CRM", "ADBE", "PYPL", "DIS",
        "JPM", "BAC", "GS", "MS", "WFC", "C", "BRK", "V", "MA", "JNJ", "PG",
        "KO", "PEP", "WMT", "HD", "MCD", "NKE", "BA", "CAT", "GE", "F",
        "QQQ", "SPY", "DIA", "IWM", "VOO", "VTI", "SOXL", "SOXX", "SMH", "XLE", "XLF",
    )

    @classmethod
    def classify_market(cls, title: str, summary: str = "") -> str:
        """Return 'KR', 'US', or 'BOTH'. 'BOTH' is used both when the article is genuinely
        global (e.g. global commodities) AND when the heuristic is uncertain — those
        articles get included in BOTH the KR and US analysis pools."""
        text = f"{title or ''} {summary or ''}"
        kr = sum(1 for k in cls._KR_KW if k.lower() in text.lower())
        us = sum(1 for k in cls._US_KW if k.lower() in text.lower())
        if cls._KR_CODE_RE.search(text):
            kr += 2  # 6-digit Korean stock code → strong KR signal
        for tk in cls._US_TICKER_RE.findall(text):
            if tk in cls._US_SAFE_TICKERS:
                us += 2
        # uncertain (no signal at all) → BOTH so neither session loses the news entirely
        if kr == 0 and us == 0:
            return "BOTH"
        if kr > us * 2:
            return "KR"
        if us > kr * 2:
            return "US"
        # mixed signal — let both sessions see it
        return "BOTH"

    # Known junk: bare company names / stock labels that crawl as "articles"
    _JUNK_TITLES = {
        "삼성전자", "SK하이닉스", "LG전자", "현대차", "기아", "네이버", "카카오",
        "삼성SDI", "삼성바이오로직스", "셀트리온", "포스코홀딩스", "현대모비스",
        "LG에너지솔루션", "LG화학", "SK이노베이션", "한국전력", "KB금융",
        "신한지주", "하나금융지주", "우리금융지주", "삼성물산", "SK텔레콤",
        "현대건설", "대한항공", "한화에어로스페이스", "두산에너빌리티",
        "1", "2", "3", "4", "5", "6", "7", "8", "9", "10",
    }

    def _make_article(self, title, href, source, summary, date_str):
        """Validate + build an article dict, or return None if it's not a real news item.
        Filters out the trash that earlier slipped through: bare company names ('LG전자', '현대차',
        'SK하이닉스'), single chars ('1'), the empty thumbnail <a>, press-name spans, widget links."""
        title = (title or "").strip()
        href = (href or "").strip()
        if not title or not href or href.startswith("javascript:"):
            return None
        # must be an actual article URL on Naver
        if "news_read" not in href and "article_id" not in href and "/article/" not in href:
            return None
        link = urljoin("https://finance.naver.com", href)
        if link in self._seen_links:
            return None
        compact = re.sub(r"\s+", "", title)
        # reject: too short, all digits/punct, equal to the press name, or a bare ticker/company token
        if len(compact) < 8:
            return None
        if re.fullmatch(r"[\d\W_]+", title):
            return None
        if source and title == source:
            return None
        # reject known junk titles (bare company names, single digits)
        if title.strip() in self._JUNK_TITLES:
            return None
        # a real headline almost always has a space or sentence punctuation; a bare company
        # name like 'SK하이닉스'/'현대차' does not — require some structure for short titles
        if len(compact) < 14 and (" " not in title) and not re.search(r"[…·…\.\?!\"'“”‘’\[\]\(\)\-~]", title):
            return None
        # reject near-duplicate headlines (same story re-listed / reprinted by another outlet)
        if self._is_dup_title(title):
            return None
        self._remember_title(title)
        return {"title": title, "link": link, "summary": (summary or "")[:200],
                "source": (source or "").strip(), "date": (date_str or "").strip(),
                "crawled_at": self.last_crawl_time,
                "market": self.classify_market(title, summary or "")}

    def _accept(self, art: Optional[Dict], out: List[Dict]) -> bool:
        if not art:
            return False
        self._seen_links.add(art["link"]); self._article_history.append(art)
        out.append(art); self.total_articles_found += 1
        return True

    def _parse_page(self, url: str, now_kst: "datetime.datetime", out: List[Dict]) -> int:
        """Fetch one Naver-finance news-list page, append new (unseen) articles to `out`.
        Anchored on the `.articleSubject` headline element (works for both the 속보 list and the main-news page).
        Returns the number of headline links found (0 ⇒ layout unrecognized → caller should fall back)."""
        now_str = now_kst.strftime("%Y-%m-%d %H:%M")
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.encoding = 'euc-kr'
        soup = BeautifulSoup(resp.text, 'html.parser')
        # 각 기사 = .articleSubject (안에 <a>), 형제 .articleSummary 에 요약+언론사+날짜
        subjects = soup.select("dd.articleSubject, dt.articleSubject, .articleSubject")
        links_found = 0
        for subj in subjects:
            try:
                a_tag = subj.select_one("a")
                if a_tag is None or not a_tag.get_text(strip=True):
                    continue
                href = a_tag.get("href", "") or ""
                if "news_read" not in href and "article_id" not in href and "/article/" not in href:
                    continue
                links_found += 1
                # 같은 기사의 요약 <dd class="articleSummary">는 보통 subject의 바로 다음 형제
                sm_el = subj.find_next_sibling("dd", class_="articleSummary") or subj.find_next_sibling("dd")
                if sm_el is None:
                    p = subj.find_parent(["dl", "li"])
                    sm_el = p.select_one("dd.articleSummary, .articleSummary") if p else None
                source = ""; date_str = now_str; summary = ""
                if sm_el is not None:
                    src_el = sm_el.select_one("span.press, .press, .origin, span.paper")
                    if src_el: source = src_el.get_text(strip=True)
                    dt_el = sm_el.select_one("span.wdate, .wdate, .date, span.date")
                    if dt_el: date_str = dt_el.get_text(strip=True)
                    try:
                        sclone = BeautifulSoup(str(sm_el), "html.parser")
                        for sp in sclone.select("span"):
                            sp.extract()
                        summary = sclone.get_text(" ", strip=True)
                    except Exception:
                        summary = sm_el.get_text(" ", strip=True)
                self._accept(self._make_article(a_tag.get_text(strip=True), href, source, summary, date_str), out)
            except Exception:
                continue
        # generic fallback — any <a> linking to a news_read article
        if links_found == 0:
            for a_tag in soup.find_all("a", href=True):
                href = a_tag.get("href", "") or ""
                if "news_read" not in href and "article_id" not in href:
                    continue
                txt = a_tag.get_text(strip=True)
                if not txt or len(re.sub(r"\s+", "", txt)) < 8:
                    continue
                links_found += 1
                self._accept(self._make_article(txt, href, "", "", now_str), out)
        return links_found

    def crawl_once(self) -> List[Dict]:
        """Crawl the Naver-finance 증권 속보 list once (falls back to the main-news page). Returns NEW articles."""
        new_articles: List[Dict] = []
        now_kst = datetime.datetime.now(KST)
        self.last_crawl_time = now_kst.strftime('%Y-%m-%d %H:%M:%S')
        try:
            links = self._parse_page(NAVER_FINANCE_NEWS_URL, now_kst, new_articles)
            if links == 0:  # 속보 페이지 레이아웃을 못 알아봄 → 메인 뉴스로 폴백
                self._parse_page(NAVER_FINANCE_NEWS_FALLBACK_URL, now_kst, new_articles)
        except Exception as e:
            logger.error(f"네이버 금융 뉴스 크롤링 실패: {e}")
            try:
                self._parse_page(NAVER_FINANCE_NEWS_FALLBACK_URL, now_kst, new_articles)
            except Exception:
                pass

        if new_articles:
            logger.info(f"📰 신규 뉴스 {len(new_articles)}건 감지")
            # 메모리 누수 방지 — 누적 히스토리는 최근분만 유지 (디스크엔 동일 상한으로 저장)
            if len(self._article_history) > _MAX_PERSIST_ARTICLES:
                self._article_history = self._article_history[-_MAX_PERSIST_ARTICLES:]
            self._save_persisted()  # 사장 피드백 2026-05-16: 재시작 후에도 목록 유지

        return new_articles

    def get_recent_articles(self, count: int = 20) -> List[Dict]:
        """Get the most recent N articles from history."""
        return self._article_history[-count:]

    def clear_history(self, keep: int = 20) -> int:
        """뉴스 로그 비우기 — 최근 `keep`건만 남기고 디스크에도 반영 (사장 피드백 2026-05-18).
        seen 캐시는 유지해 방금 지운 옛 헤드라인이 다음 크롤에 다시 쏟아지지 않게 한다.
        Returns the number of articles kept."""
        keep = max(0, int(keep))
        self._article_history = self._article_history[-keep:] if keep else []
        # 사장 지시 2026-05-19: 초기화 시 대시보드·뉴스탭의 '누적' 개수도 리셋한다.
        # total_articles_found는 get_status()로 노출되는 누적 카운터 — 남긴 건수에 맞춘다.
        self.total_articles_found = len(self._article_history)
        try:
            self._save_persisted()
        except Exception as e:
            logger.warning(f"clear_history 저장 실패: {e}")
        return len(self._article_history)

    def reclassify_in_history(self, link_to_market: Dict[str, str]) -> int:
        """LLM 재분류 결과를 in-memory history에 반영. Returns the number of updated entries."""
        n = 0
        for a in self._article_history:
            m = link_to_market.get(a.get("link"))
            if m and m in ("KR", "US", "BOTH") and a.get("market") != m:
                a["market"] = m
                n += 1
        return n

    def format_articles_for_agent(self, articles: List[Dict]) -> str:
        """Format articles as a string for agent consumption."""
        if not articles:
            return "[네이버 금융 뉴스] 새로운 뉴스가 없습니다."

        lines = [f"[네이버 금융 뉴스 실시간] {len(articles)}건 감지 | {self.last_crawl_time}\n"]
        for i, a in enumerate(articles[:15], 1):
            lines.append(f"  {i}. 📰 {a['title']}")
            if a['summary']:
                lines.append(f"     요약: {a['summary'][:100]}")
            if a['source']:
                lines.append(f"     출처: {a['source']} | {a['date']}")
            lines.append("")

        return "\n".join(lines)

    def get_status(self) -> Dict:
        return {
            "is_running": self.is_running,
            "last_crawl": self.last_crawl_time,
            "total_articles": self.total_articles_found,
            "seen_links_count": len(self._seen_links),
        }


# Singleton
_monitor: Optional[NaverFinanceMonitor] = None

def get_monitor() -> NaverFinanceMonitor:
    global _monitor
    if _monitor is None:
        _monitor = NaverFinanceMonitor()
    return _monitor


# ── 사장 피드백 2026-05-15 (4차): LLM 기반 뉴스 분류 (tencent/hy3-preview) ────────
# 키워드 매칭이 부정확하다는 사장 지적에 따라 배치 LLM 분류로 전환.
# 크롤 직후 새로 들어온 헤드라인 N건을 한 번의 LLM 호출로 분류.
# 키워드 분류(classify_market)는 LLM 실패/타임아웃 시 폴백으로 유지.
# 사장 피드백 2026-05-15 (8차): alibaba/tongyi-deepresearch-30b-a3b는 검색·합성 능력이 있는 reasoning 모델.
# 모델이 헤드라인의 기업명을 직접 lookup해서 상장 시장을 판단할 수 있으므로 긴 화이트리스트·anti-example 불필요.
# 짧고 명확한 분류 규칙만 제공.
_CLASSIFIER_SYSTEM = """뉴스 헤드라인을 KR / US / BOTH 셋 중 하나로 분류하세요.

기준:
- KR = 한국 시장(KOSPI/KOSDAQ) 상장 기업/지수/한국 거시(한은·원달러)·정책
- US = 미국 시장(NYSE/NASDAQ) 상장 기업/지수/미국 거시(Fed·美 국채)
- BOTH = 양국 모두 영향 주는 매크로 (유가·미중관계·지정학·전쟁·글로벌 산업) 또는 양국 기업 동시 등장

핵심 원칙:
1. 헤드라인에 등장하는 기업명을 식별해 그 기업의 상장 시장으로 분류하세요.
2. 기업 실적/매출/영업이익/순이익 헤드라인은 항상 해당 기업의 상장 시장.
3. 기업명이 없는 순수 매크로/지정학/원자재 헤드라인만 BOTH로.
4. 한국 지수(코스피/코스닥) = KR, 미국 지수(나스닥/다우/S&P) = US.
5. 원/달러 환율은 한국 관점 → KR. "달러 강세"는 BOTH.

응답은 JSON 배열 한 줄만. 예: ["KR","US","BOTH","KR"]
다른 텍스트·설명 금지."""


async def llm_classify_articles(articles: List[Dict], model: str = "alibaba/tongyi-deepresearch-30b-a3b",
                                 max_tokens: int = 12000) -> Dict[str, str]:
    """입력 articles의 헤드라인을 LLM에 배치 전송 → {link: market} 매핑 반환.
    실패하면 빈 dict (호출처가 키워드 분류 결과를 그대로 사용).
    사장 피드백 8차: alibaba/tongyi-deepresearch (검색·reasoning 통합 모델)로 전환.
    - 짧은 프롬프트 + 모델이 기업명을 자체 lookup
    - reasoning 모델 → content가 비면 reasoning 필드에서 폴백 추출"""
    if not articles:
        return {}
    try:
        from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, MODEL_ASSIGNMENTS, AGENT_MAX_TOKENS
    except ImportError:
        return {}
    if not OPENROUTER_API_KEY:
        return {}
    try:
        model = MODEL_ASSIGNMENTS.get("news_classifier", model) or model
        max_tokens = AGENT_MAX_TOKENS.get("news_classifier", max_tokens) or max_tokens
    except Exception:
        pass

    # 20건씩 청크 — reasoning 모델 + JSON 응답 + 정확도 균형
    CHUNK = 20
    out: Dict[str, str] = {}
    for i in range(0, len(articles), CHUNK):
        chunk = articles[i:i + CHUNK]
        numbered = "\n".join(f"{j+1}. {(a.get('title') or '')[:160]}" for j, a in enumerate(chunk))
        # 사장 피드백 8차: alibaba 모델은 기업명 lookup 가능 → 짧고 명확한 지시.
        user_msg = (
            f"다음 {len(chunk)}개 헤드라인을 KR / US / BOTH로 분류:\n\n"
            f"{numbered}\n\n"
            f"각 헤드라인의 주제 기업이 한국(KOSPI/KOSDAQ) 상장이면 KR, 미국(NYSE/NASDAQ) 상장이면 US. "
            f"기업명이 없는 순수 매크로/지정학/원자재면 BOTH. "
            f"JSON 배열 한 줄만 응답 — 예: [\"KR\",\"US\",\"BOTH\",...]"
        )
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": _CLASSIFIER_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            "max_tokens": max_tokens, "temperature": 0.0,
        }
        headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json",
                   "HTTP-Referer": "https://arquant.ai-ve.uk", "X-Title": "ArQuant-NewsClassifier"}
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as s:
                async with s.post(f"{OPENROUTER_BASE_URL}/chat/completions", json=payload, headers=headers) as r:
                    if r.status != 200:
                        logger.warning(f"news_classifier HTTP {r.status}")
                        continue
                    data = await r.json()
        except Exception as e:
            logger.warning(f"news_classifier 호출 예외: {e}")
            continue
        try:
            _msg_obj = data.get("choices", [{}])[0].get("message", {}) or {}
            reply = (_msg_obj.get("content") or "").strip()
            # 사장 피드백 8차: alibaba는 reasoning 모델 — content가 비면 reasoning에서 추출.
            if not reply:
                reply = (_msg_obj.get("reasoning") or "").strip()
        except Exception:
            continue
        # JSON 배열 매칭 우선
        m = re.search(r"\[\s*(?:\"(?:KR|US|BOTH)\"\s*,?\s*){1,}\]", reply, re.IGNORECASE)
        labels = None
        if m:
            try: labels = _json.loads(m.group(0).upper())
            except _json.JSONDecodeError: labels = None
        if labels is None:
            # 폴백: 라벨만 추출 (따옴표 유무 무관)
            tokens = re.findall(r"\b(KR|US|BOTH)\b", reply, re.IGNORECASE)
            if tokens:
                labels = [t.upper() for t in tokens][:len(chunk)]
        if not labels:
            _fin = data.get("choices", [{}])[0].get("finish_reason","?")
            logger.warning(f"news_classifier 응답 파싱 실패 (finish={_fin}, len={len(reply)}): {reply[:200]!r}")
            continue
        if not isinstance(labels, list):
            continue
        for j, art in enumerate(chunk):
            if j >= len(labels):
                break
            lbl = str(labels[j]).upper().strip()
            if lbl in ("KR", "US", "BOTH"):
                lnk = art.get("link")
                if lnk:
                    out[lnk] = lbl
    return out
