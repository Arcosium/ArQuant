"""
Arquant v1.0 - Naver Finance News Crawler (Continuous Monitor)
Crawls https://finance.naver.com/news/mainnews.naver in real-time.
No API key needed - pure HTML scraping with requests + BeautifulSoup.
Adapted from ArcAI.ve Daily/VC_Crawling/news_crawler.py pattern.

Articles are classified at crawl time into 'KR' (국내 시장), 'US' (미국 시장),
or 'BOTH' (양쪽 모두 영향) so the orchestrator can route them to the right
analysis cycle (KR 장 시간엔 KR+BOTH만 분석/매매, US 장 시간엔 US+BOTH만).
"""
import difflib
import json as _json
import logging
import os
import re
import datetime
from pathlib import Path
from typing import List, Dict, Optional
from urllib.parse import urljoin

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
_MAX_PERSIST_ARTICLES = 300   # 사장 지시 2026-06-10: 뉴스 피드 최대 300건만 기억, 초과분은 즉시 삭제
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
            # 사장 지시 2026-06-04: 시장 분류 폐지 — 재시작 전 영속화된 옛 기사의 잔존 market 키 제거.
            for _a in self._article_history:
                _a.pop("market", None)
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

    # 사장 지시 2026-06-04: 뉴스 KR/US 시장 분류 폐지(단일 풀). classify_market·LLM 분류기 전부 제거 —
    # 시장 구분은 사이클의 마켓센티먼트팀장이 직접 한다([[arquant-data-dir-and-news-pipeline]]).

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
        # 사장 지시 2026-05-21: 구 finance.naver.com/news/news_read.naver 는 폐기돼 '실시간 속보
        # 목록'으로 리다이렉트되고, href의 &section_id 가 &sect; HTML 엔티티로 디코딩돼 쿼리도
        # 깨진다. office_id/article_id 를 뽑아 최신 기사 URL로 재구성해 개별 기사로 정확히 연결한다.
        _oid = (re.search(r"office_id=(\d+)", href) or [None, None])
        _aid = (re.search(r"article_id=(\d+)", href) or [None, None])
        oid = _oid.group(1) if hasattr(_oid, "group") else None
        aid = _aid.group(1) if hasattr(_aid, "group") else None
        if not (oid and aid):
            m = re.search(r"/article/(?:mnews/)?(\d+)/(\d+)", href)
            if m:
                oid, aid = m.group(1), m.group(2)
        if oid and aid:
            link = f"https://n.news.naver.com/article/{oid}/{aid}"
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
                "crawled_at": self.last_crawl_time}

    def _accept(self, art: Optional[Dict], out: List[Dict]) -> bool:
        if not art:
            return False
        self._seen_links.add(art["link"]); self._article_history.append(art)
        # 사장 지시 2026-06-10: 300건 초과 시 즉시(append마다) 가장 오래된 기록부터 삭제
        if len(self._article_history) > _MAX_PERSIST_ARTICLES:
            self._article_history = self._article_history[-_MAX_PERSIST_ARTICLES:]
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
            self._mirror_to_archive(new_articles)

        return new_articles

    def _mirror_to_archive(self, articles: List[Dict]) -> None:
        """공용 뉴스 아카이브(arcnews)에 미러링 — 여기 300건 롤링에서 밀려나도 원본은 남는다.

        정본은 여전히 news_history.json 이다. arcnews.mirror 는 예외를 삼키므로 아카이브가
        잠기거나 없어도 매매 사이클은 그대로 돈다."""
        try:
            import arcnews
        except ImportError:
            return          # arcastack.pth 미등록 환경(테스트 등) — 조용히 건너뛴다
        n = arcnews.mirror(
            [{"title": a.get("title"), "url": a.get("link"), "summary": a.get("summary", ""),
              "source": a.get("source") or "naver_finance", "source_label": "네이버 금융",
              "published_at": a.get("date", ""), "collected_at": a.get("crawled_at")}
             for a in articles], app="arquant")
        if n:
            logger.info(f"🗄 공용 뉴스 아카이브에 {n}건 추가")

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
