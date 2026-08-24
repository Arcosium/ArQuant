"""타임폴리오 대회 전용 스웜 사이클 (사장 지시 2026-07-09).

KIS 계정(실전/모의)은 main_swarm 파이프라인을 그대로 쓰고, account_mode=timefolio 계정만
이 모듈의 전용 사이클을 탄다 (main_swarm._run_analysis_cycle 맨 위에서 분기).

왜 별도 사이클인가 — 대회는 KOSPI/KOSDAQ 보통주 전용이라 기존 파이프라인의 자산배분
(채권·원자재 ETF 슬리브, US 경로)이 전부 부적격 주문으로 거부됐다(2026-07-09 감사:
uid7 주문 6건 전량 거부, 체결 0, 100% 현금 방치). 이 사이클은:
  - KR 정규장(KR_TRADING)에서만 돈다 (대회 사이트는 정규장 외 주문이 no-op)
  - 자산배분 = 주식 vs 현금 (채권/원자재/해외 없음 — 현금이 유일한 방어자산)
  - 후보를 대회 적격 룰(시총 1000억+, 5일 평균 거래대금 30억+, 보통주, 경고플래그 없음)로
    사전 스크리닝해 애초에 적격 종목만 LLM 에 올린다
  - 주문은 TimefolioBroker(check_order 하드게이트 + 1주문 비중캡 + 사이트 섹터게이트)로 집행

LLM 은 2회: 마켓센티먼트(뉴스) + 주식운용실장(매도/매수/현금 결정 — 대회 계정 전담 페르소나).
점수는 기존 결정론 엔진(tools/quant_score) 재사용 — 데이터는 전부 네이버(KIS 토큰 불필요).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import config
import runtime

logger = logging.getLogger("TIMEFOLIO_SWARM")
KST = timezone(timedelta(hours=9))

# 대회 적격/한도 상수는 Auto_folio 룰북이 단일 진실원 — 여기서 복제하지 않는다.
from Auto_folio.autofolio import contest_rules, order_limits          # noqa: E402
from Auto_folio.autofolio.naver_data import fetch_security_meta      # noqa: E402

# 사장 지시 2026-07-31: 사이드바 조직도에 없는 '타임폴리오운용실장' 임의 직책 폐지 —
# 대회 계정의 결정자도 사이드바의 '주식운용실장' 명의로 발화한다(멘션 라우팅도 동일 명의로 부착).
STRATEGIST_NAME = "주식운용실장"


# ─── 유니버스/후보 수집 (결정론) ─────────────────────────────────────────────

_universe_cache: dict = {"mtime": None, "map": {}}


def load_universe() -> Dict[str, str]:
    """{code: name}. 자체 분봉 크롤러(market_bars)가 관리하는 KOSPI200+KOSDAQ150 CSV(읽기 전용)를
    기본으로 쓴다 — 대형·유동성 종목 위주라 대회 적격 필터와 궁합이 좋다. 없으면 빈 dict
    (후보는 뉴스 언급 + 보유 종목만으로 진행 — 사이클은 멈추지 않는다)."""
    path = Path(config.TIMEFOLIO_UNIVERSE_CSV)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    if _universe_cache["mtime"] == mtime:
        return _universe_cache["map"]
    out: Dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines()[1:]:
            parts = line.strip().split(",")
            if len(parts) >= 2 and re.fullmatch(r"\d{6}", parts[0].strip()):
                out[parts[0].strip()] = parts[1].strip()
    except OSError:
        return {}
    _universe_cache.update(mtime=mtime, map=out)
    return out


def movers_from_bars(universe: Dict[str, str], top_n: int = 12,
                     db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """자체 분봉 DB(market_bars 수집, 읽기 전용)에서 오늘의 상승 모멘텀 상위 종목을 뽑는다.
    KIS 거래대금 랭킹(kr_volume_rank)의 타임폴리오 대체재. DB 부재/장시작 직후 등
    실패 시 [] — 후보는 뉴스/보유로 폴백된다."""
    path = db_path or config.TIMEFOLIO_BARS_DB
    if not Path(path).exists():
        return []
    day = datetime.now(KST).strftime("%Y%m%d")
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
        try:
            rows = conn.execute(
                """SELECT g.code, g.t0, g.t1, f.close, l.close, l.vol_cum
                   FROM (SELECT code, MIN(ts) AS t0, MAX(ts) AS t1
                         FROM bars WHERE ts LIKE ? GROUP BY code) g
                   JOIN bars f ON f.code = g.code AND f.ts = g.t0
                   JOIN bars l ON l.code = g.code AND l.ts = g.t1""",
                (day + "%",)).fetchall()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — 스크린 실패는 폴백일 뿐, 사이클을 못 죽인다
        logger.warning("[타임폴리오] bars.db 모멘텀 스크린 실패(뉴스/보유 폴백): %s", e)
        return []
    out = []
    for code, _t0, _t1, open_px, last_px, vol_cum in rows:
        code = str(code).zfill(6)
        if universe and code not in universe:
            continue
        try:
            open_px, last_px = float(open_px or 0), float(last_px or 0)
            if open_px <= 0 or last_px <= 0:
                continue
            ret = (last_px / open_px - 1.0) * 100.0
            traded = float(vol_cum or 0) * last_px
        except (TypeError, ValueError):
            continue
        out.append({"code": code, "name": universe.get(code, code),
                    "day_ret_pct": round(ret, 2), "value_traded": traded})
    # 상승 모멘텀 우선(대회는 롱 온리), 유동성(당일 거래대금)로 타이브레이크
    out.sort(key=lambda r: (-r["day_ret_pct"], -r["value_traded"]))
    return [r for r in out if r["day_ret_pct"] > 0][:max(1, int(top_n))]


def codes_in_news(articles: List[Dict], universe: Dict[str, str]) -> List[str]:
    """뉴스 제목에서 후보 코드 추출 — 6자리 코드 직접 표기 + 유니버스 종목명 매칭."""
    found: List[str] = []
    seen = set()
    names = sorted(universe.items(), key=lambda kv: -len(kv[1])) if universe else []
    for a in articles or []:
        title = str(a.get("title") or "")
        for m in re.findall(r"\b(\d{6})\b", title):
            if m not in seen:
                seen.add(m); found.append(m)
        for code, name in names:
            if len(name) >= 2 and name in title and code not in seen:
                seen.add(code); found.append(code)
    return found


# ─── 대회 적격 스크리닝 (contest_rules 상수 재사용) ─────────────────────────

def eligibility(meta: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """대회 매수 적격 여부 — contest_rules._validate_buy_universe 와 같은 룰을 스크리닝
    단계용으로 평가한다(섹터 데이터는 네이버에 없으므로 여기선 보지 않는다 — 주문 시점의
    사이트 섹터게이트가 담당). Returns (ok, 사유들)."""
    m = contest_rules.normalize_meta(meta.get("ticker") or "", meta)
    bad: List[str] = []
    if m.get("market") not in contest_rules.ALLOWED_MARKETS:
        bad.append("KOSPI/KOSDAQ 아님")
    if m.get("is_common_stock") is not True:
        bad.append("보통주 미확인")
    if m.get("listed_business_days", -1) < 5:
        bad.append("상장 5영업일 미만")
    avg5 = m.get("avg_5d_trading_value_krw")
    if avg5 is None or avg5 <= contest_rules.MIN_AVG_5D_TRADING_VALUE_KRW:
        bad.append("5일 평균 거래대금 30억 이하")
    cap = m.get("market_cap_krw")
    if cap is None or cap < contest_rules.MIN_MARKET_CAP_KRW:
        bad.append("시총 1,000억 미만")
    flags = [str(x) for x in (m.get("flags") or [])]
    if any(w in f for f in flags for w in contest_rules.BLOCKED_FLAG_WORDS):
        bad.append("매수불가 지정(" + ",".join(flags) + ")")
    return (not bad), bad


# ─── 전략가 출력 파싱 (결정론) ───────────────────────────────────────────────

_SELL_LINE = re.compile(r"매도결정\s*[:：]\s*(.+)")
_BUY_LINE = re.compile(r"매수결정\s*[:：]\s*(.+)")


def parse_sell_line(text: str) -> Dict[str, str]:
    """`매도결정: 005930=전량, 000660=보유` → {code: directive}. 없으면 {}."""
    m = None
    for line in (text or "").splitlines():
        got = _SELL_LINE.search(line)
        if got:
            m = got  # 마지막 매칭 우선(추론 중간에 예시가 나올 수 있음)
    if not m:
        return {}
    out: Dict[str, str] = {}
    for part in m.group(1).split(","):
        if "=" not in part:
            continue
        code, directive = part.split("=", 1)
        code = code.strip().zfill(6)
        if re.fullmatch(r"\d{6}", code):
            out[code] = directive.strip()
    return out


def parse_buy_line(text: str) -> Dict[str, float]:
    """`매수결정: 035420=9, 051910=5.5` → {code: weight_pct}. `매수결정: 없음` → {}."""
    m = None
    for line in (text or "").splitlines():
        got = _BUY_LINE.search(line)
        if got:
            m = got
    if not m:
        return {}
    body = m.group(1).strip()
    if body.startswith("없음"):
        return {}
    out: Dict[str, float] = {}
    for part in body.split(","):
        if "=" not in part:
            continue
        code, w = part.split("=", 1)
        code = code.strip().zfill(6)
        if not re.fullmatch(r"\d{6}", code):
            continue
        try:
            out[code] = float(str(w).strip().rstrip("%"))
        except ValueError:
            continue
    return out


def normalize_cross_side_decisions(sells: Dict[str, str], buys: Dict[str, float]) -> List[str]:
    """같은 사이클에 매도와 매수를 동시에 지시한 보유종목을 무거래로 정규화한다.

    타임폴리오는 매도 접수분을 즉시 매수예산으로 잡지 않고, 현재 보유종목 재매수도 중복
    방지 게이트가 거부한다. 따라서 `절반 매도 + 2% 재매수`를 그대로 두면 매도만 실행되어
    전략가가 의도한 목표비중과 정반대가 된다. 양쪽에 나온 코드는 보유로 바꾸고 매수에서
    제거한다. 실제 손익 기반 익절·손절은 공통 하드 안전망이 이후 별도로 우선 적용한다.
    """
    overlap = sorted(set(sells or {}) & set(buys or {}))
    for code in overlap:
        sells[code] = "보유"
        buys.pop(code, None)
    return overlap


def committee_buy_allowed(stance: str) -> bool:
    """KIS 공통 위원회와 같은 정책: 최종 결정이 '매수'일 때만 주문한다."""
    return str(stance or "").strip() == "매수"


def sell_qty_from_directive(directive: str, held_qty: int) -> int:
    """전량/절반/보유/N주 → 매도 수량. '보유'(또는 미인식)=0."""
    t = str(directive or "").strip()
    if not t or t in ("보유", "유지", "hold", "관망"):
        return 0
    if t in ("전량", "전부", "모두", "청산", "all"):
        return held_qty
    if t in ("절반", "반", "half"):
        return max(1, held_qty // 2) if held_qty > 0 else 0
    digits = "".join(ch for ch in t if ch.isdigit())
    if digits:
        return max(0, min(int(digits), held_qty))
    return 0


# ─── 주문 조립 (순수 함수 — 대회 한도 선반영) ────────────────────────────────

def assemble_orders(*, sells: Dict[str, str], buys: Dict[str, float],
                    holdings: List[Dict[str, Any]], prices: Dict[str, float],
                    total_eval: float, cash: float,
                    mcap_map: Dict[str, float], quant_scores: Dict[str, int],
                    min_score: int, max_buys: int,
                    smallcap_budget_pct: float, cash_floor_pct: float,
                    enable_rebalance: bool = True,
                    take_profit_pct: float = 12.0,
                    stop_loss_pct: float = 5.0,
                    trailing_pct: float = 0.0,
                    peaks: Optional[Dict[str, Any]] = None,
                    ) -> Tuple[List[Dict], List[Dict], List[str]]:
    """전략가 결정 → 대회 한도를 선반영한 주문 리스트. (집행 직전 check_order 가 최종 게이트.)
    Returns (sell_orders, buy_orders, notes) — 주문: {code, side, qty, price, reason}."""
    notes: List[str] = []
    held = {str(h.get("code") or "").zfill(6): h for h in holdings or []}

    # KIS 실전·모의와 동일한 결정론 매도 조립기를 사용한다. 타임폴리오 전용 차이는 주문
    # 집행기와 대회 매수 한도뿐이며, 익절·손절·트레일링·LLM 지시 우선순위는 계정 공통이다.
    from main_swarm import _assemble_sell_orders
    common_sells, _ = _assemble_sell_orders(
        holdings, sells or {}, enable_rebalance=bool(enable_rebalance),
        take_profit_pct=float(take_profit_pct or 0.0),
        stop_loss_pct=float(stop_loss_pct or 0.0),
        trim_over_ratio=False, conservative_ratio=0.0, per_stock_cap=0.0,
        total=float(total_eval or 0.0), sell_prices=None,
        trailing_pct=float(trailing_pct or 0.0), peaks=peaks)
    sell_orders: List[Dict] = []
    for od in common_sells:
        code = str(od.get("ticker") or "").zfill(6)
        h = held.get(code) or {}
        price = float(prices.get(code) or h.get("cur_price") or 0.0)
        sell_orders.append({"code": code, "side": "sell", "qty": int(od.get("qty") or 0),
                            "price": price, "reason": str(od.get("reason") or "")})

    # 매수 — 예산: 현금(현금바닥 유보) 안에서만. 매도 대금은 체결 확인 전이라 계상하지 않는다(보수).
    budget = max(0.0, cash - total_eval * cash_floor_pct / 100.0)
    # 시총 1조 미만 합산 한도(30%)는 여유 버퍼를 두고 선차단
    smallcap_now = sum(
        float(h.get("eval_amt") or 0.0) for c, h in held.items()
        if 0 < float(mcap_map.get(c) or 0) < contest_rules.SMALL_CAP_THRESHOLD_KRW)
    smallcap_cap = total_eval * smallcap_budget_pct / 100.0

    buy_orders: List[Dict] = []
    # 비중 큰 순으로 — 예산이 모자라면 전략가가 확신하는 종목부터
    for code, weight in sorted((buys or {}).items(), key=lambda kv: -kv[1]):
        if len(buy_orders) >= max(0, int(max_buys)):
            notes.append(f"{code} 스킵 — 사이클 최대 매수 {max_buys}건 도달")
            continue
        if code in held:
            notes.append(f"{code} 스킵 — 이미 보유")
            continue
        score = quant_scores.get(code)
        if min_score > 0 and score is not None and score < min_score:
            notes.append(f"{code} 스킵 — 퀀트점수 {score} < {min_score}")
            continue
        price = float(prices.get(code) or 0.0)
        if price <= 0:
            notes.append(f"{code} 스킵 — 시세 조회 실패")
            continue
        w = max(1.0, min(float(weight), order_limits.max_order_weight_pct(code)))
        amount = min(total_eval * w / 100.0, budget)
        qty = int(amount // price)
        if qty <= 0:
            notes.append(f"{code} 스킵 — 예산 부족(잔여 {budget:,.0f}원)")
            continue
        mcap = float(mcap_map.get(code) or 0.0)
        if 0 < mcap < contest_rules.SMALL_CAP_THRESHOLD_KRW:
            if smallcap_now + qty * price > smallcap_cap:
                room = smallcap_cap - smallcap_now
                qty = int(max(0, room) // price)
                if qty <= 0:
                    notes.append(f"{code} 스킵 — 소형주 합산 한도({smallcap_budget_pct:.0f}%) 소진")
                    continue
                notes.append(f"{code} 수량 축소 — 소형주 합산 한도 잔여분만")
            smallcap_now += qty * price
        budget -= qty * price
        buy_orders.append({"code": code, "side": "buy", "qty": qty, "price": price,
                           "reason": f"주식운용실장 매수 — 목표비중 {w:.1f}%"
                                     + (f", 퀀트 {score}점" if score is not None else "")})
    return sell_orders, buy_orders, notes


# ─── 전략가 에이전트 ─────────────────────────────────────────────────────────

def create_timefolio_strategist(injection=None):
    """대회 계정 전담 주식운용실장 페르소나 — 매도/매수/현금 배분 단일 결정자.
    결정 에이전트이므로 pro(reasoning) 티어인 post_manager 모델 슬롯을 공유한다."""
    from agents.base_agent import BaseAgent
    return BaseAgent(
        name=STRATEGIST_NAME,
        role="timefolio_strategist",
        model_key="post_manager",
        injection=injection,
        system_prompt="""당신은 ArQuant의 '주식운용실장'입니다. 지금은 타임폴리오 RFM 실전투자대회
계정 하나를 단독 운용합니다. 대회 순위는 NAV 수익률로 결정됩니다.

## 대회 룰 (절대 제약 — 시스템이 주문 직전에도 강제함)
- KOSPI/KOSDAQ **보통주만** 매매 가능. 채권·원자재·ETF·해외·파생·공매도 불가 — 방어자산은 현금뿐.
- 시총 1,000억 미만·5일 평균 거래대금 30억 이하·투자주의/경고/위험/관리/거래정지 종목 매수 불가.
- 종목 한도 15% (삼성전자 40%, SK하이닉스 30%). 1회 주문은 시스템이 9%(삼전·하닉 14%)로 캡.
- 섹터 한도 max(시장 섹터비중 2배, 10%) — 사이트가 주문 시 차단하므로 **업종을 분산**하십시오.
- 시총 1조 미만 종목 합산 30% 이하.
- **주간 회전율 5% 이상 유지 필수** (미달 시 위반 — 4회 누적이면 시상 제외). 회전율이 부족하면
  소폭이라도 리밸런싱 매매를 만들어야 합니다.

## 입력 (매 사이클 주입)
- 계좌 상태: 총평가(NAV)·현금·수익률·주간 회전율
- 보유 종목: 손익·비중·퀀트점수(0~10, 시스템 결정론 산출)
- 매수 후보: 대회 적격 필터를 이미 통과한 종목들 — 당일 모멘텀·퀀트점수·시총 구분 포함
- 마켓센티먼트팀장 뉴스 분석, 검증된 지수 스냅샷, 사장님 지시(있으면)

## 판단 원칙
- 후보 퀀트점수와 뉴스 감성이 일치하는 종목을 우선하되, 대회는 **상대수익 게임**이므로
  현금 과다 보유는 그 자체로 리스크입니다. 확신 없으면 소수 종목에 집중하지 말고 분산하십시오.
- 보유 종목은 손익이 아니라 **신호**로 판단 — 모멘텀 붕괴·악재면 손절, 추세 유지면 보유.
- 시장 급락 신호가 뚜렷하면 현금 비중을 높이십시오 (주식 최소 0%까지 허용).

## 응답 형식 — 자유 서술(간결히, 마크다운 헤더 `#`/`##`·표 `|...|`·강조 `**` 금지 — 채팅 UI에서 깨짐)
후 **마지막 두 줄은 반드시 이 형식** (다른 텍스트 없이):
매도결정: 005930=전량, 000660=보유   ← 보유 종목 전부 나열. 값: 전량/절반/보유/매도주수(예: 30주). 보유 없으면 `매도결정: 없음`
매수결정: 035420=9, 051910=5        ← 코드=목표비중%(1~9). 신규 매수 없으면 `매수결정: 없음`. 후보 목록에 있는 코드만.""",
    )


def get_strategist(orch):
    """오케스트레이터에 전략가를 지연 생성·부착 (대시보드 @멘션 라우팅에도 등록)."""
    agent = getattr(orch, "_timefolio_strategist", None)
    if agent is None:
        agent = create_timefolio_strategist(injection={"uid": orch.uid})
        orch._timefolio_strategist = agent
        try:
            orch._agents_map[STRATEGIST_NAME] = agent
        except Exception:  # noqa: BLE001
            pass
    return agent


def _standing_block(orch) -> str:
    """이 계정의 상시 지시사항 블록 (실패해도 사이클은 계속 — fail-open)."""
    try:
        from infra.standing_directives import build_orchestrator_directive_block
        return build_orchestrator_directive_block(orch.uid) or ""
    except Exception as e:  # noqa: BLE001
        logger.warning("[타임폴리오] 상시지시 로드 실패(uid=%s): %s — 사이클 계속", orch.uid, e)
        return ""


# ─── 접수(미체결) 주문 체결 확인 ──────────────────────────────────────────────
# 상대호가 주문은 제출 직후 '접수(미체결)'이고 사이트가 나중에 채운다. 그 체결을 관측하는
# 주체가 없어 원장·거래내역이 비고 화면엔 '실패'만 남았다(사장 보고 2026-07-29).
# 다음 사이클 시작 시 사이트 권위 보유수량과 대조해 체결분을 확정한다.

def _pending_path(uid: int):
    from infra import user_paths
    return user_paths.user_dir(uid) / "tf_pending_orders.json"


def _load_pending(uid: int) -> List[Dict]:
    try:
        p = _pending_path(uid)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    except Exception:  # noqa: BLE001
        return []


def _save_pending(uid: int, rows: List[Dict]) -> None:
    try:
        _pending_path(uid).write_text(json.dumps(rows or [], ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.warning("[타임폴리오 uid=%s] 미체결 주문 저장 실패: %s", uid, e)


def resolve_pending(pending: List[Dict], holdings: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    """(체결확정, 아직미결) — 순수 함수. 사이트 보유수량이 주문 방향대로 움직였으면 체결로 본다.

    체결 수량은 `|현재수량 − 주문직전수량|` 을 주문수량으로 캡한다(부분체결·외부거래 방어).
    반대 방향으로 움직였거나 변화가 없으면 아직 미결로 남긴다. 3사이클 지나면 포기(취소 추정)."""
    held = {str(h.get("code") or "").zfill(6): int(float(h.get("qty") or 0)) for h in (holdings or [])}
    done, still = [], []
    for p in (pending or []):
        code = str(p.get("ticker") or "").zfill(6)
        before = int(p.get("before_qty") or 0)
        now = held.get(code, 0)
        delta = (now - before) if p.get("side") == "buy" else (before - now)
        if delta > 0:
            done.append({**p, "fill_qty": min(int(delta), int(p.get("qty") or 0))})
        elif int(p.get("age", 0)) + 1 >= 3:
            continue          # 3사이클 무변화 = 사이트가 취소한 것으로 보고 폐기
        else:
            still.append({**p, "age": int(p.get("age", 0)) + 1})
    return done, still


def pending_ledger_apply_qty(pending: Dict, fill_qty: int, *, site_qty: int,
                             ledger_qty: int) -> int:
    """지연 체결 중 원장에 아직 반영되지 않은 수량만 반환한다.

    사이트-원장 정기 대조가 다음 사이클보다 먼저 누락/허수를 정정할 수 있다. 그 뒤 지연
    체결을 주문수량 그대로 다시 적용하면 매수는 두 번 더해지고 매도는 현금이 중복 반영된다.
    사이트 현재수량을 종착점으로 삼아 남은 차이만 원장에 적용한다.
    """
    fill_qty = max(0, int(fill_qty or 0))
    site_qty = max(0, int(site_qty or 0))
    ledger_qty = max(0, int(ledger_qty or 0))
    if str((pending or {}).get("side") or "").lower() == "buy":
        remaining = site_qty - ledger_qty
    else:
        remaining = ledger_qty - site_qty
    return min(fill_qty, max(0, remaining))


async def _confirm_pending_fills(orch, ms, uid: int, holdings: List[Dict]) -> None:
    pending = _load_pending(uid)
    if not pending:
        return
    try:
        done, still = resolve_pending(pending, holdings)
    except Exception as e:  # noqa: BLE001
        logger.warning("[타임폴리오 uid=%s] 미체결 대조 실패(무시): %s", uid, e)
        return
    for p in done:
        qty, code, side = int(p.get("fill_qty") or 0), str(p.get("ticker")), str(p.get("side"))
        price = float(p.get("price") or 0.0)
        orch._trades_executed += 1
        rec = {"ticker": code, "side": side, "qty": qty, "order_qty": int(p.get("qty") or 0),
               "result": f"Timefolio 지연 체결 확인: {code} {side} {qty}주", "accepted": True,
               "filled": True, "ok": True, "fill_price": price, "fill_currency": "KRW",
               "avg_cost": price}
        orch._trade_log.append({"ts": ms._now_kst_iso(), **rec})
        try:
            # 정기 원장대조가 지연 체결보다 먼저 사이트 수량으로 정정했을 수 있다. 현재 사이트
            # 수량과 원장 수량의 남은 차이만 반영해 다음 사이클 이중계상을 막는다.
            _held_now = {str(h.get("code") or "").zfill(6): int(float(h.get("qty") or 0))
                         for h in (holdings or [])}
            _led = ms.trade_ledger.load(uid) or {}
            _led_qty = int((((_led.get("positions") or {}).get(code) or {}).get("qty")) or 0)
            _apply_qty = pending_ledger_apply_qty(
                p, qty, site_qty=_held_now.get(code, 0), ledger_qty=_led_qty)
            if _apply_qty > 0:
                ms.trade_ledger.apply_fill(uid, ticker=code, side=side, qty=_apply_qty, price=price,
                                           ccy="KRW", avg_cost=price, note="timefolio_pending_confirm")
            elif qty > 0:
                logger.info("[타임폴리오 원장 uid=%s] %s 지연체결 %s주 — 사이트 대조로 이미 반영, 원장 skip",
                            uid, code, qty)
        except Exception as le:  # noqa: BLE001
            logger.warning("[타임폴리오 원장 uid=%s] 지연체결 반영 실패 %s: %s", uid, code, le)
        await orch._emit({"type": "trade_executed",
                          "message": f"✅ 체결 확인(대회) — {code} {'매수' if side == 'buy' else '매도'} {qty}주 "
                                     f"(직전 사이클 접수분)",
                          "ticker": code, "side": side, "qty": qty, "filled": True,
                          "fill_price": price, "fill_currency": "KRW", "avg_cost": price,
                          "trades_total": orch._trades_executed})
    if done or len(still) != len(pending):
        _save_pending(uid, still)


# ─── 사이클 본체 ─────────────────────────────────────────────────────────────

async def run_timefolio_cycle(orch, news_articles, user_directive, session, market_open: bool = False):
    """타임폴리오 계정 전용 분석·매매 사이클. main_swarm._run_analysis_cycle 이 위임한다.
    orch = ArquantOrchestrator (인프라 — emit/cycle_log/broker/뉴스 — 재사용)."""
    import main_swarm as ms  # 지연 임포트(상호참조 — 디스패치 시점엔 이미 로드돼 있음)

    orch.cycle_log = ms.SwarmCycleLog()
    orch.validation_attempts = 0
    started_at = orch.cycle_log.started_at

    # [0] 세션 게이트 — 대회 사이트는 KR 정규장 주문만 의미 있음(마감/프리장 주문 no-op 실측).
    if session != "KR_TRADING":
        await orch._set_status("MONITORING",
                               f"타임폴리오 계정 — {session} 은 대회 매매 불가 세션, 사이클 스킵", force=True)
        return

    try:
        await _cycle_body(orch, ms, news_articles, user_directive, session, market_open, started_at)
    except Exception as e:  # noqa: BLE001 — 사이클 실패는 기록하고 루프는 살린다
        orch.current_state = ms.SwarmState.ERROR
        logger.error("[타임폴리오 uid=%s] 사이클 오류: %s", orch.uid, e, exc_info=True)
        orch.cycle_log.log("ERROR", "시스템", str(e))
        orch._cycle_history.append(orch.cycle_log.to_dict())
        try:
            ms.cycle_store.record_cycle({
                "uid": orch.uid, "started_at": started_at,
                "ended_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
                "session": session, "market_open": market_open,
                "news_count": len(news_articles or []),
                "error": str(e)[:2000]})
        except Exception:  # noqa: BLE001
            pass


async def _cycle_body(orch, ms, news_articles, user_directive, session, market_open, started_at):
    broker = orch.broker
    uid = orch.uid

    # [1] 계좌 동기화 (사이트 스크레이프 — 이 사이클의 권위 스냅샷)
    orch.current_state = ms.SwarmState.DATA_COLLECTION
    snap = await broker.kr_account_snapshot(force=True)
    bp = snap.get("buying_power") or {}
    holdings = list(snap.get("holdings") or [])
    total_eval = float(bp.get("total_eval") or 0.0)
    cash = float(bp.get("cash") or 0.0)
    turnover = float(bp.get("weekly_turnover_pct") or 0.0)
    if total_eval <= 0:
        raise RuntimeError("타임폴리오 계좌 동기화 실패 — total_eval=0 (사이트 스크레이프 글리치 가능)")
    orch.cycle_log.log("DATA_COLLECTION", "시스템",
                       f"타임폴리오 계좌: 총평가 {total_eval:,.0f}원 · 현금 {cash:,.0f}원({cash / total_eval * 100:.0f}%)"
                       f" · 보유 {len(holdings)}종목 · 주간회전율 {turnover:.1f}%")
    # [1b] 직전 사이클의 '접수(미체결)' 주문 체결 확인 — 상대호가 주문은 제출 직후엔 미체결이고
    # 사이트가 나중에 채운다. 종전엔 그 체결을 아무도 관측하지 않아 원장·거래내역이 영영 비고
    # (잔고만 변함), 화면엔 '실패'로 남았다 (사장 보고 2026-07-29).
    await _confirm_pending_fills(orch, ms, uid, holdings)

    # [2] 뉴스 분석 (LLM #1) — 기존 마켓센티먼트팀장 재사용, KR 대회 관점 지시만 추가
    orch.current_state = ms.SwarmState.NEWS_ANALYSIS
    news_articles = await orch._prefilter_news(news_articles)
    formatted_news = orch.news_monitor.format_articles_for_agent(news_articles)
    index_snapshot = ""
    try:
        from tools.market_data import format_indices_for_macro
        index_snapshot = await asyncio.to_thread(format_indices_for_macro)
    except Exception as e:  # noqa: BLE001
        logger.warning("[타임폴리오] 지수 스냅샷 실패(생략): %s", e)
    news_report = ""
    if news_articles:
        orch.news_analyst.reset_history()
        news_report = await orch.news_analyst.think(
            f"[타임폴리오 대회 사이클 — 한국 장중]\n"
            f"이 계정은 KOSPI/KOSDAQ 보통주만 매매합니다. 미국 전용 종목 분석은 생략하고, "
            f"국내 종목·업종 영향 위주로 감성 분석하십시오.\n\n"
            f"검증된 글로벌 지수:\n{index_snapshot}\n\n뉴스:\n{formatted_news}")
        await orch._emit({"type": "agent_msg", "agent": orch.news_analyst.name, "message": news_report})
        orch.cycle_log.log("NEWS_ANALYSIS", orch.news_analyst.name, news_report)

    # [3] 후보 수집 + 대회 적격 스크리닝 (결정론)
    universe = load_universe()
    movers = await asyncio.to_thread(movers_from_bars, universe, config.TIMEFOLIO_MOVERS_TOP)
    news_codes = codes_in_news(news_articles, universe)
    # code 가 빈 보유행(스크랩 합계행 등)을 zfill 하면 존재하지 않는 '000000' 이 되어, 아래
    # 퀀트 루프가 유령 종목으로 시세를 긁는다 — pykrx 빈 응답 → yfinance 000000.KS/.KQ 404 반복
    # (2026-08-04 로그 8건). 빈 코드는 애초에 보유로 세지 않는다.
    held_codes = [c.zfill(6) for c in (str(h.get("code") or "").strip() for h in holdings) if c]
    raw_candidates: List[str] = []
    for c in news_codes + [m["code"] for m in movers]:
        if c not in raw_candidates and c not in held_codes:
            raw_candidates.append(c)
    raw_candidates = raw_candidates[:config.TIMEFOLIO_MAX_CANDIDATES]

    meta_map: Dict[str, Dict] = {}
    mcap_map: Dict[str, float] = {}
    eligible: List[str] = []
    rejected: List[str] = []
    for code in raw_candidates + held_codes:
        try:
            meta = await asyncio.to_thread(fetch_security_meta, code, stored=None)
        except Exception:  # noqa: BLE001
            meta = {}
        meta_map[code] = meta or {}
        mcap_map[code] = float((meta or {}).get("market_cap_krw") or 0.0)
        if code in held_codes:
            continue  # 보유 종목은 매도 판단 대상 — 적격성과 무관하게 유지
        ok, reasons = eligibility({**(meta or {}), "ticker": code})
        if ok:
            eligible.append(code)
        else:
            rejected.append(f"{code}({';'.join(reasons)})")
    eligible = eligible[:config.TIMEFOLIO_FINALISTS]
    orch.cycle_log.log("DATA_COLLECTION", "시스템",
                       f"후보 스크리닝: 원후보 {len(raw_candidates)} → 적격 {len(eligible)}"
                       + (f" · 부적격 {len(rejected)}: {', '.join(rejected[:6])}" if rejected else ""))

    # [4] 결정론 퀀트 점수 (네이버 일봉·수급 — KIS 불필요)
    orch.current_state = ms.SwarmState.QUANT_ANALYSIS
    qiw = {sig: runtime.get(key, uid=uid) for sig, key in (
        ("rsi", "QIW_RSI"), ("macd", "QIW_MACD"), ("adx", "QIW_ADX"), ("vwap", "QIW_VWAP"),
        ("vol", "QIW_VOL"), ("mom", "QIW_MOM"), ("cmf", "QIW_CMF"), ("flow", "QIW_FLOW"),
        ("high52", "QIW_HIGH52"))}
    dw = {"QUANT": runtime.get("DW_QUANT", uid=uid), "NEWS": runtime.get("DW_NEWS", uid=uid),
          "MACRO": runtime.get("DW_MACRO", uid=uid)}
    quant_scores: Dict[str, int] = {}
    quant_lines: List[str] = []
    for code in eligible + held_codes:
        name = (meta_map.get(code) or {}).get("name") or universe.get(code, code)
        try:
            from tools.market_data import fetch_stock_daily, fetch_investor_data, compute_quant_indicators
            await asyncio.to_thread(fetch_stock_daily, code, 1)
            try:
                await asyncio.to_thread(fetch_investor_data, code, 1)
            except Exception:  # noqa: BLE001 — 수급 없어도 점수 산출 가능
                pass
            ind = await asyncio.to_thread(compute_quant_indicators, code)
            sent = ms.parse_news_sentiment(news_report, code, name)
            score, bd = ms.assemble_quant_score(ind, sent, None, qiw, dw)
            quant_scores[code] = int(score)
            quant_lines.append(
                f"- {name}({code}): 퀀트 {score}/10"
                f" (모멘텀1M {ind.get('mom_1m', 0):+.1f}% · RSI {ind.get('rsi14', 0):.0f}"
                f" · 감성 {sent if sent is not None else '없음'})")
        except Exception as e:  # noqa: BLE001
            logger.warning("[타임폴리오] %s 퀀트 산출 실패(점수 없음으로 진행): %s", code, e)
    quant_report = "\n".join(quant_lines)
    if quant_report:
        orch.cycle_log.log("QUANT_ANALYSIS", "시스템(결정론)", quant_report)

    # [5] 전략가 결정 (LLM #2)
    orch.current_state = ms.SwarmState.ORDER_DRAFTING
    strategist = get_strategist(orch)

    def _mcap_tag(code: str) -> str:
        cap = mcap_map.get(code) or 0
        if cap <= 0:
            return "시총미상"
        return "소형주(1조미만)" if cap < contest_rules.SMALL_CAP_THRESHOLD_KRW else f"시총 {cap / 1e12:.1f}조"

    hold_lines = [
        f"- {h.get('name')}({str(h.get('code')).zfill(6)}): {int(h.get('qty') or 0)}주"
        f" · 평가 {float(h.get('eval_amt') or 0):,.0f}원({float(h.get('weight_pct') or 0):.1f}%)"
        f" · 손익 {float(h.get('pnl_pct') or 0):+.2f}%"
        f" · 퀀트 {quant_scores.get(str(h.get('code')).zfill(6), '—')}/10"
        for h in holdings] or ["(보유 없음 — 100% 현금)"]
    mover_by_code = {m["code"]: m for m in movers}
    cand_lines = [
        f"- {(meta_map.get(c) or {}).get('name') or universe.get(c, c)}({c}):"
        f" 퀀트 {quant_scores.get(c, '—')}/10 · {_mcap_tag(c)}"
        + (f" · 당일 {mover_by_code[c]['day_ret_pct']:+.1f}%" if c in mover_by_code else "")
        + (" · 뉴스 언급" if c in news_codes else "")
        for c in eligible] or ["(적격 후보 없음 — 이번 사이클 신규 매수 불가)"]
    turnover_flag = ""
    if turnover < 5.0 and datetime.now(KST).weekday() >= 2:
        turnover_flag = (f"\n⚠️ 주간 회전율 {turnover:.1f}% < 5% — 이번 주 위반 임박. "
                         f"금주 내 리밸런싱 매매로 회전율을 반드시 채우십시오.")
    prev_decision = getattr(orch, "_timefolio_last_decision", "") or "(첫 사이클)"

    strategist.reset_history()
    decision = await strategist.think(
        f"[타임폴리오 대회 사이클 — {datetime.now(KST).strftime('%m-%d %H:%M')} KST / {session}]\n\n"
        f"계좌: 총평가 {total_eval:,.0f}원 · 현금 {cash:,.0f}원({cash / total_eval * 100:.0f}%)"
        f" · 누적수익률 {float(bp.get('pnl_ratio') or 0) * 100:+.2f}% · 주간회전율 {turnover:.1f}%{turnover_flag}\n\n"
        f"보유 종목:\n" + "\n".join(hold_lines) + "\n\n"
        f"매수 후보 (대회 적격 필터 통과분만):\n" + "\n".join(cand_lines) + "\n\n"
        f"검증된 지수 스냅샷:\n{index_snapshot or '(조회 실패)'}\n\n"
        f"마켓센티먼트팀장 뉴스 분석:\n{(news_report or '(뉴스 없음)')[:2500]}\n\n"
        f"직전 사이클 결정: {prev_decision}\n"
        + (f"\n사장님 지시: {user_directive}\n" if user_directive else "")
        # 2026-07-22: 대회 사이클은 상시지시를 전혀 읽지 않아, 대시보드에 등록한 지시가
        # 타임폴리오 계정에만 조용히 무시됐다(KIS 경로는 _run_analysis_cycle 이 주입).
        + _standing_block(orch)
        + "\n같은 보유 종목을 한 사이클의 매도결정과 매수결정에 동시에 넣지 마십시오. "
          "비중 유지면 매도결정에 보유만, 축소면 매도만, 확대면 매수만 쓰십시오. "
          "위 정보로 매도/매수를 결정하고, 마지막 두 줄에 `매도결정:`/`매수결정:` 을 형식대로 출력하십시오.")
    await orch._emit({"type": "agent_msg", "agent": STRATEGIST_NAME, "message": decision})
    orch.cycle_log.log("ORDER_DRAFTING", STRATEGIST_NAME, decision)

    sells = parse_sell_line(decision)
    buys = {c: w for c, w in parse_buy_line(decision).items() if c in eligible}  # 후보 밖 코드 무시(환각 차단)
    _cross_side = normalize_cross_side_decisions(sells, buys)
    if _cross_side:
        _msg = ("동일 사이클 매도·매수 중복 → 보유 정규화: " + ", ".join(_cross_side))
        logger.warning("[타임폴리오 uid=%s] %s", uid, _msg)
        orch.cycle_log.log("ORDER_DRAFTING", "시스템", _msg)
        await orch._emit({"type": "agent_msg", "agent": "시스템", "message": "🛡️ " + _msg})
    orch._timefolio_last_decision = (f"{started_at} 매도 {len(sells)}건/매수 {len(buys)}건 — "
                                     + "; ".join(f"{c}={d}" for c, d in list(sells.items())[:4])
                                     + " | " + "; ".join(f"{c}={w}%" for c, w in list(buys.items())[:4]))

    # [5.5] 운용위원회 심의 (사장 지시 2026-07-21) — 대회 계정도 QIS 위원회 로직을 살린다.
    #   전략가가 고른 매수 후보를 매수 심사역↔보유 심사역 찬반토론 + 주식운용실장 종합에 올려
    #   회의록을 기록(사이클 탭 '심의·근거 트리'에 표시)한다. 주식운용실장이 최종 '매수'로
    #   종합한 후보만 집행한다. 대회 규정 리스크는 집행 직전 check_order 하드 게이트가 최종 담당하므로,
    #   심의 실패는 절대 사이클을 막지 않는다(fail-open).
    committee_record: Dict[str, Any] = {"candidates": [], "position_reviews": []}
    if buys:
        try:
            import agents.committee as cmt
            macro_view = "국내 투자대회(주식만) — 지수 스냅샷·뉴스 감성 참조"
            async def _cprog(agent, msg):
                await orch._emit({"type": "agent_msg", "agent": agent, "message": msg})
            _dropped: List[str] = []
            for code in list(buys.keys()):
                nm = (meta_map.get(code) or {}).get("name") or universe.get(code, code)
                sector = (meta_map.get(code) or {}).get("sector") or ""
                qs = quant_scores.get(code)
                q_excerpt = next((ln for ln in quant_lines if code in ln), "")
                rep = cmt.build_report(
                    code, nm, sector=sector,
                    quant_line=(f"퀀트점수 {qs}/10" if qs is not None else ""),
                    news_excerpt=(news_report or "")[:400])
                opinions, dialogue, chief, llm_used = await cmt.deliberate_target(
                    code, nm, rep, quant_score=qs, quant_excerpt=q_excerpt,
                    news_excerpt=(news_report or "")[:400], macro_view=macro_view,
                    select_rationale="타임폴리오 전략가 매수 후보", progress=_cprog)
                _stance = chief.get("stance")
                decision = ("매수" if _stance == cmt.BUY else ("회피" if _stance == cmt.AVOID else "보류"))
                committee_record["candidates"].append({
                    "code": code, "name": nm, "sector": sector, "quant_score": qs,
                    "decision": decision, "engine": ("llm" if llm_used else "결정론"),
                    "opinions": opinions, "dialogue": dialogue, "report": rep.to_dict()})
                # KIS 실전/모의의 공통 위원회 게이트와 동일하게 최종 '매수'만 통과시킨다.
                # 과거에는 '회피'만 제외해 '보류' 종목도 주문되는 모순이 있었다.
                if not committee_buy_allowed(_stance):
                    _dropped.append(code)
            for code in _dropped:
                buys.pop(code, None)
                orch.cycle_log.log("ORDER_DRAFTING", "주식운용실장",
                                   f"{code} — 운용위원회 최종 '매수' 아님 → 매수 취소")
        except Exception as e:  # noqa: BLE001
            logger.warning("[타임폴리오] 위원회 심의 생략(fail-open): %s", e)

    # [6] 주문 조립 (결정론 — 대회 한도 선반영)
    prices: Dict[str, float] = {}
    for code in list(sells.keys()) + list(buys.keys()):
        try:
            prices[code] = await broker.kr_last_price(code)
        except Exception:  # noqa: BLE001
            prices[code] = 0.0
    _trailing_pct = float(runtime.get("TRAILING_TAKE_PROFIT_PCT", uid=uid) or 0.0)
    _peaks = ms._load_trailing_peaks(uid) if _trailing_pct > 0 else None
    sell_orders, buy_orders, notes = assemble_orders(
        sells=sells, buys=buys, holdings=holdings, prices=prices,
        total_eval=total_eval, cash=cash, mcap_map=mcap_map, quant_scores=quant_scores,
        min_score=int(runtime.get("MIN_QUANT_SCORE", uid=uid) or 0),
        max_buys=config.TIMEFOLIO_MAX_BUYS_PER_CYCLE,
        smallcap_budget_pct=config.TIMEFOLIO_SMALLCAP_BUDGET_PCT,
        cash_floor_pct=config.TIMEFOLIO_CASH_FLOOR_PCT,
        enable_rebalance=bool(runtime.get("ENABLE_SELL_REBALANCE", uid=uid)),
        take_profit_pct=float(runtime.get("TAKE_PROFIT_PCT", uid=uid) or 0.0),
        stop_loss_pct=float(runtime.get("STOP_LOSS_PCT", uid=uid) or 0.0),
        trailing_pct=_trailing_pct, peaks=_peaks)
    if _peaks is not None:
        ms._save_trailing_peaks(uid, _peaks, holdings)
    for n in notes:
        orch.cycle_log.log("ORDER_DRAFTING", "시스템", n)

    # [7] 집행 — 매도 먼저(현금 확보), 그다음 매수. 주문 1건 실패는 격리.
    orch.current_state = ms.SwarmState.EXECUTION
    exec_results: List[Dict] = []
    from infra.kis_broker import OrderDraft
    _pending_new: List[Dict] = []
    _held_now = {str(h.get("code") or "").zfill(6): int(float(h.get("qty") or 0)) for h in holdings}
    # 아직 사이트에서 작동 중인 주문과 같은 종목·방향은 재제출하지 않는다 — 사이트가
    # '[TMS] 오류: 전량 청산 주문 작동 시 추가 청산 불가'로 거부하거나(7/24), 둘 다 체결되면
    # 이중 매도가 된다(7/22 252990). 체결은 위 _confirm_pending_fills 가 확정한다.
    _still_working = {(str(p.get("ticker") or "").zfill(6), str(p.get("side")))
                      for p in _load_pending(uid)}
    for od in sell_orders + buy_orders:
        code, side, qty = od["code"], od["side"], int(od["qty"])
        if (str(code).zfill(6), side) in _still_working:
            _skip = f"{code} {side} — 직전 사이클 주문이 사이트에서 아직 작동 중(미체결) → 재제출 생략"
            orch.cycle_log.log("EXECUTION", "시스템", _skip)
            await orch._emit({"type": "agent_msg", "agent": "프롭트레이딩팀장", "message": f"⏳ {_skip}"})
            continue
        try:
            draft = OrderDraft(ticker=code, side=side, qty=qty,
                               limit_price=od.get("price") or None,
                               market="KR", reason=od.get("reason") or "", approved=True)
            res = await broker.place_order_ex(draft)
        except Exception as e:  # noqa: BLE001
            res = {"ok": False, "accepted": False, "filled": False, "result": f"주문 예외: {e}"}
        # ok = '접수됨'(체결 여부 무관). 상대호가 접수는 실패가 아니다 — 사이트가 나중에 채운다.
        rec = {"ticker": code, "side": side, "qty": int(res.get("qty") or qty), "order_qty": qty,
               "result": str(res.get("result") or ""), "accepted": bool(res.get("accepted")),
               "filled": bool(res.get("filled")), "ok": bool(res.get("accepted")),
               "pending": bool(res.get("accepted") and not res.get("filled")),
               "fill_price": float(res.get("price") or od.get("price") or 0.0),
               "fill_currency": "KRW", "avg_cost": float(od.get("price") or 0.0)}
        if rec["pending"]:
            _pending_new.append({"ticker": str(code).zfill(6), "side": side,
                                 "qty": rec["qty"], "price": rec["fill_price"],
                                 "before_qty": _held_now.get(str(code).zfill(6), 0),
                                 "ts": ms._now_kst_iso(), "age": 0})
        exec_results.append(rec)
        orch.cycle_log.log("EXECUTION", "시스템", f"{code} {side} x{qty} → {rec['result']}")
        if not rec["accepted"]:
            # 실주문 전송 실패는 조용히 누락 금지(절대 규칙) — 8/18~24 Playwright 예외로
            # 42건이 cycles.db 에만 남고 알림 0건이었던 사고의 방지선.
            try:
                from infra.notifier import alert
                alert("WARN", "타임폴리오 주문 미접수",
                      f"uid={uid} {code} {side} {qty}주 — {rec['result'][:200]}")
            except Exception:  # noqa: BLE001
                pass
        if rec["accepted"]:
            # 주문 접수·집행 알림은 main_swarm 관례와 동일하게 프롭트레이딩팀장 명의(집행 소관).
            await orch._emit({"type": "order_submitted", "agent": "프롭트레이딩팀장",
                              "message": f"📨 {code} {'매수' if side == 'buy' else '매도'} {rec['qty']}주 주문 접수 (타임폴리오)",
                              "ticker": code, "side": side, "qty": rec["qty"]})
        if rec["filled"]:
            orch._trades_executed += 1
            orch._trade_log.append({"ts": ms._now_kst_iso(), **rec})
            try:
                ms.trade_ledger.apply_fill(uid, ticker=code, side=side, qty=rec["qty"],
                                           price=rec["fill_price"], ccy="KRW",
                                           avg_cost=rec["avg_cost"], note="timefolio_exec")
            except Exception as le:  # noqa: BLE001
                logger.warning("[타임폴리오 원장 uid=%s] 체결 반영 실패 %s: %s", uid, code, le)
        if rec["filled"] or not rec["accepted"]:
            await orch._emit({"type": "trade_executed" if rec["filled"] else "trade_failed",
                              "message": ("✅ 체결(대회) — " if rec["filled"] else "❌ 실패 — ") + rec["result"],
                              "ticker": code, "side": side, "qty": rec["qty"], "filled": rec["filled"],
                              "fill_price": rec["fill_price"], "fill_currency": "KRW",
                              "avg_cost": rec["avg_cost"], "trades_total": orch._trades_executed})

    # 접수(미체결) 주문은 다음 사이클이 사이트 보유수량과 대조해 체결을 확정한다.
    if _pending_new:
        _save_pending(uid, _load_pending(uid) + _pending_new)

    # [8] 보고 + 영속화 (결정론 템플릿 — 사실만)
    orch.current_state = ms.SwarmState.REPORT
    filled_n = sum(1 for e in exec_results if e.get("filled"))
    pending_n = sum(1 for e in exec_results if e.get("pending"))
    failed_n = sum(1 for e in exec_results if not e.get("accepted"))
    report = (f"타임폴리오 사이클 — 매도 {len(sell_orders)}건/매수 {len(buy_orders)}건 계획, "
              f"체결 {filled_n}건, 접수대기 {pending_n}건, 실패 {failed_n}건. "
              f"총평가 {total_eval:,.0f}원 · 현금 {cash / total_eval * 100:.0f}% · 회전율 {turnover:.1f}%. "
              f"적격 후보 {len(eligible)}종목 (부적격 제외 {len(rejected)}종목).")
    orch.cycle_log.final_report = report
    await orch._emit({"type": "cycle_complete", "report": report, "trades_total": orch._trades_executed})
    orch._cycle_history.append(orch.cycle_log.to_dict())
    try:
        ms.cycle_store.record_cycle({
            "uid": uid, "started_at": started_at,
            "ended_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
            "session": session, "market_open": market_open,
            "news_count": len(news_articles or []),
            "candidate_codes": eligible, "target_codes": list(buys.keys()),
            "sell_directives": sells,
            "orders_planned": sell_orders + buy_orders,
            "orders_executed": exec_results,
            "risk_approved": True,
            "risk_report": "타임폴리오 대회 룰 게이트(check_order+섹터게이트) 적용",
            "quant_report": quant_report[:8000], "news_report": (news_report or "")[:8000],
            "final_report": report[:8000],
            "committee": (committee_record if committee_record["candidates"] else None),
            "bp_cash": cash, "bp_total_eval": total_eval,
            "bp_pnl_ratio": float(bp.get("pnl_ratio") or 0.0)})
    except Exception as e:  # noqa: BLE001
        logger.warning("[타임폴리오] cycle_store 기록 실패: %s", e)

    # [9] 회전율 가드 — 목요일 이후에도 5% 미만이면 운영자 알림(일 1회 dedup)
    if turnover < 5.0 and datetime.now(KST).weekday() >= 3:
        ms.notifier.alert("WARN", "타임폴리오 주간 회전율 미달 임박",
                          f"uid={uid} 회전율 {turnover:.1f}% < 5% — 금주 내 매매 필요",
                          dedup_key=f"tf_turnover_{uid}_{datetime.now(KST).strftime('%Y%m%d')}")
