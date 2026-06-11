"""실거래 원장(trade ledger) — KIS 집계 TR 비의존 자산평가 (사장 지시 2026-06-11).

배경: KIS 통합총자산 TR 3종이 서로 불일치하고(자기모순), USD 결제 과도기·해외평가
결손(외화예수금 미포함, 모의 해외데이터 garbage)으로 자산곡선·수익률 KPI가 환각을
일으켰다 (uid1 누적 -43% 표시 사례). KIS 계정 '집계값' 대신 우리가 직접 체결 원장을
굴려 평가한다 — KIS는 ① 체결 사실(보유 변동) ② 종목 단위 평단/시세만 신뢰한다.

원리:
- 시드(1회): KIS 보유종목(qty/평단) + 예수금(KRW D+2, USD 외화예수금)으로 초기 원장 구성.
- 진화: 이후엔 '우리 체결'만으로 현금·포지션을 갱신한다 (US 수수료 매수·매도 각 0.3%).
- 평가(M2M): 자체 시세(스냅샷 종목시세·us_last_price·일봉CSV 폴백, 실패 시 직전가
  carry-forward) × 5분 크롤 환율로 원화 평가. 입출금·수동거래는 reconcile 이 qty 괴리로
  탐지해 경고하고, /api/ledger/reseed 로 재시드한다.

저장소: data/<uid>/ledger.json. '거래 내역 비우기'와 무관(별도 파일)하다.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("arquant.trade_ledger")

KST = ZoneInfo("Asia/Seoul")
_DATA_DIR = Path(__file__).parent.parent / "data"
_DEFAULT_DATA_DIR = _DATA_DIR


def _writes_allowed() -> bool:
    """운영호스트 pytest 가 라이브 원장을 오염시키지 않게 하는 가드
    (전례: test_ceo_directive_routing 이 라이브 ops_history 오염 — 2026-06-09 memory).
    테스트가 _DATA_DIR 를 tmp 로 monkeypatch 하면 쓰기 허용."""
    return not (os.environ.get("PYTEST_CURRENT_TEST") and _DATA_DIR == _DEFAULT_DATA_DIR)

# 해외(US) 거래비용 — 매수·매도 각 leg 0.3% (main_swarm.US_TRADE_COST_RATE 와 동일 정책,
# 순환 import 방지를 위해 여기 별도 정의). 국내(KR)는 0%.
US_TRADE_COST_RATE = 0.003

_KR_CODE_RE = re.compile(r"^\d{6}$")
_FILLS_CAP = 300  # 감사용 체결 이력 보존 상한


def _now_str() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


def _f(v, default: float = 0.0) -> float:
    try:
        x = float(v)
        return x if x == x else default  # NaN 가드
    except (TypeError, ValueError):
        return default


def _ledger_path(uid) -> Path:
    d = _DATA_DIR / str(int(uid))
    d.mkdir(parents=True, exist_ok=True)
    return d / "ledger.json"


def load(uid) -> Optional[dict]:
    p = _ledger_path(uid)
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and isinstance(d.get("positions"), dict) else None
    except Exception as e:
        logger.warning(f"[원장 uid={uid}] 읽기 실패: {e}")
        return None


def save(uid, led: dict) -> None:
    if not _writes_allowed():
        return
    try:
        _ledger_path(uid).write_text(json.dumps(led, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"[원장 uid={uid}] 저장 실패: {e}")


def reset(uid) -> bool:
    """원장 삭제(재시드 유도). /api/ledger/reseed 가 사용."""
    if not _writes_allowed():
        return False
    p = _ledger_path(uid)
    try:
        if p.exists():
            p.unlink()
            return True
    except Exception as e:
        logger.warning(f"[원장 uid={uid}] 삭제 실패: {e}")
    return False


def _is_kr(code: str) -> bool:
    return bool(_KR_CODE_RE.match(str(code or "").strip()))


def us_csv_close(ticker: str) -> float:
    """data/daily_US_<tk>.csv (us_daily_chart 가 적재) 마지막 종가 — 브로커 비의존 US 가격 폴백."""
    tk = str(ticker or "").strip().upper()
    p = _DATA_DIR / f"daily_US_{tk}.csv"
    if not tk or not p.exists():
        return 0.0
    try:
        rows = list(csv.DictReader(p.open(encoding="utf-8")))
        for row in reversed(rows):
            c = _f(row.get("close") or row.get("Close"))
            if c > 0:
                return c
    except Exception:
        pass
    return 0.0


def apply_fill(uid, *, ticker: str, side: str, qty, price=None, ccy: str = None,
               avg_cost=None, note: str = "") -> bool:
    """체결 1건을 원장에 반영. 원장이 아직 시드 전이면 조용히 skip(False) —
    시드 스냅샷이 이미 그 체결을 반영한 상태로 찍히므로 이중계상을 피한다.

    가격 폴백: price 없으면 매수=avg_cost, 매도=직전가(last_price)→평단. 그래도 없으면
    skip + degraded 카운트(현금만 틀어지는 반쪽 반영을 하지 않는다)."""
    if not _writes_allowed():
        return False
    led = load(uid)
    if led is None:
        return False
    tk = str(ticker or "").strip()
    # OrderSide(str,Enum) 가드 — str(enum) 은 'OrderSide.SELL' 이 되어 조용히 거부됐다
    # (2026-06-11 라이브: uid2 132030 매도 72주가 원장에 미반영). value 를 우선 사용.
    side = str(getattr(side, "value", side) or "").strip().lower()
    try:
        qty = int(qty or 0)
    except (TypeError, ValueError):
        qty = 0
    if not tk or side not in ("buy", "sell") or qty <= 0:
        return False
    ccy = (str(ccy or "").upper() or ("KRW" if _is_kr(tk) else "USD"))
    positions: Dict[str, dict] = led.setdefault("positions", {})
    pos = positions.get(tk)
    px = _f(price)
    approx = False
    if px <= 0:
        if side == "buy":
            px = _f(avg_cost)
        else:
            px = _f((pos or {}).get("last_price")) or _f((pos or {}).get("avg_cost")) or _f(avg_cost)
        approx = True
    if px <= 0:
        led["degraded_fills"] = int(led.get("degraded_fills") or 0) + 1
        save(uid, led)
        logger.warning(f"[원장 uid={uid}] {tk} {side} x{qty} 가격 미상 — 원장 미반영(degraded)")
        return False

    fee = US_TRADE_COST_RATE * px * qty if ccy == "USD" else 0.0
    cash_key = "cash_usd" if ccy == "USD" else "cash_krw"
    if side == "buy":
        led[cash_key] = _f(led.get(cash_key)) - (px * qty + fee)
        if pos:
            old_q = int(pos.get("qty") or 0)
            old_avg = _f(pos.get("avg_cost"))
            new_q = old_q + qty
            pos["avg_cost"] = ((old_avg * old_q + px * qty) / new_q) if new_q > 0 else px
            pos["qty"] = new_q
        else:
            pos = {"qty": qty, "avg_cost": px, "ccy": ccy}
            positions[tk] = pos
        pos["last_price"] = px
        if approx:
            pos["approx_basis"] = True
    else:  # sell
        led[cash_key] = _f(led.get(cash_key)) + (px * qty - fee)
        if pos:
            pos["qty"] = int(pos.get("qty") or 0) - qty
            pos["last_price"] = px
            if pos["qty"] <= 0:
                positions.pop(tk, None)
        # 포지션 미등재 매도(시드 전 매수분 등) — 현금만 반영하면 자산이 부풀므로 경고만.
        else:
            led[cash_key] = _f(led.get(cash_key)) - (px * qty - fee)  # 롤백
            led["degraded_fills"] = int(led.get("degraded_fills") or 0) + 1
            save(uid, led)
            logger.warning(f"[원장 uid={uid}] {tk} 매도 체결인데 원장에 포지션 없음 — 미반영(reconcile 대상)")
            return False
    fills: List[dict] = led.setdefault("fills", [])
    fills.append({"ts": _now_str(), "ticker": tk, "side": side, "qty": qty,
                  "price": px, "ccy": ccy, "fee": round(fee, 4),
                  "approx_price": approx, "note": str(note or "")[:120]})
    led["fills"] = fills[-_FILLS_CAP:]
    save(uid, led)
    return True


def mark_to_market(uid, *, price_lookup: Dict[str, float], fx: float) -> Optional[dict]:
    """원장 평가. price_lookup 에 있는 종목은 last_price 갱신(영속), 없는 종목은
    직전가 carry-forward — 시세 결손으로 곡선이 튀지 않는다."""
    led = load(uid)
    if led is None:
        return None
    fx = _f(fx)
    total = _f(led.get("cash_krw")) + (_f(led.get("cash_usd")) * fx if fx > 0 else 0.0)
    stale: List[str] = []
    changed = False
    for tk, pos in (led.get("positions") or {}).items():
        live = _f((price_lookup or {}).get(tk))
        if live > 0 and live != _f(pos.get("last_price")):
            pos["last_price"] = live
            changed = True
        px = _f(pos.get("last_price")) or _f(pos.get("avg_cost"))
        if live <= 0:
            stale.append(tk)
        qty = int(pos.get("qty") or 0)
        if str(pos.get("ccy") or "").upper() == "USD":
            total += qty * px * (fx if fx > 0 else 0.0)
        else:
            total += qty * px
    if changed:
        save(uid, led)
    return {"value_krw": total, "stale": stale, "seeded_at": led.get("seeded_at")}


def value_from_snap(uid, snap: dict, fx: float) -> Optional[float]:
    """portfolio_holdings 스냅샷의 종목 시세만으로 즉시 평가 (서버 /api/balance 용, 시드 전이면 None).
    모의계정 US 시세는 garbage 일 수 있으나 last_price 는 폴러(ensure_value)가 실시세로
    유지하므로, 여기선 KR 시세만 반영하고 US 는 carry-forward 에 맡긴다."""
    led = load(uid)
    if led is None:
        return None
    lookup: Dict[str, float] = {}
    for h in (snap or {}).get("holdings") or []:
        code = str(h.get("code") or "").strip()
        cp = _f(h.get("cur_price"))
        if code and cp > 0 and _is_kr(code):
            lookup[code] = cp
    mtm = mark_to_market(uid, price_lookup=lookup, fx=fx)
    return mtm["value_krw"] if mtm else None


def reconcile(uid, holdings: List[dict]) -> List[str]:
    """KIS 보유 qty vs 원장 qty 괴리 목록 (수동거래/입출금/누락체결 탐지).
    KIS 보유 스냅샷이 일시 결손(빈 목록)일 수 있어, 빈 입력이면 비교하지 않는다."""
    led = load(uid)
    if led is None or not holdings:
        return []
    kis = {}
    for h in holdings:
        code = str(h.get("code") or "").strip()
        if code:
            kis[code] = kis.get(code, 0) + int(_f(h.get("qty")))
    diffs = []
    led_pos = led.get("positions") or {}
    for tk in set(kis) | set(led_pos):
        kq = kis.get(tk, 0)
        lq = int((led_pos.get(tk) or {}).get("qty") or 0)
        if kq != lq:
            diffs.append(f"{tk}: KIS {kq}주 vs 원장 {lq}주")
    return diffs


def _add_us_bdays(d: date, n: int) -> date:
    """주말만 건너뛰는 영업일 가산 (미 휴장일 미반영 — 오차는 reconcile/재시드가 흡수)."""
    cur = d
    while n > 0:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            n -= 1
    return cur


async def pending_usd_from_fills(broker, today_us: Optional[date] = None) -> float:
    """미결제(T+2 영업일) USD 매도대금 — 결제 과도기엔 KIS 통합총자산/외화예수금 TR 이 전부
    0 을 줘(2026-06-11 라이브 확인: uid1 매도대금 ~$1,682 가 6504·6010 에 미반영, 6548 만 포함)
    체결내역(TTTS3035R)이 유일한 권위 소스다.

    일별 net = 매도대금 − 매수대금. **양수 net 만** 미결제 USD 로 더한다 — 음수(순매수) 일은
    통합증거금으로 KRW(D+2 예수금)에서 차감되므로(라이브 확인: −$369.67×환율 = KRW −568,522)
    USD 쪽엔 반영하지 않는다."""
    today_us = today_us or datetime.now(ZoneInfo("America/New_York")).date()
    start = today_us - timedelta(days=8)
    try:
        fills = await broker.overseas_fills(start.strftime("%Y%m%d"), today_us.strftime("%Y%m%d"))
    except Exception as e:
        logger.warning(f"[원장] 해외체결내역 조회 실패(미결제 USD 0 으로 진행): {e}")
        return 0.0
    by_date: Dict[str, float] = {}
    for f in fills or []:
        if str(f.get("ccy") or "USD").upper() != "USD":
            continue
        amt = _f(f.get("amount")) or (_f(f.get("price")) * _f(f.get("qty")))
        if amt <= 0:
            continue
        sign = 1.0 if f.get("side") == "sell" else -1.0
        by_date[f["date"]] = by_date.get(f["date"], 0.0) + sign * amt
    pend = 0.0
    for ymd, net in by_date.items():
        try:
            td = datetime.strptime(ymd, "%Y%m%d").date()
        except ValueError:
            continue
        if _add_us_bdays(td, 2) > today_us and net > 0:
            pend += net
    return pend


async def seed(uid, broker, snap: dict) -> Optional[dict]:
    """KIS 스냅샷으로 원장 1회 시드. bp.ok 아니면 None (글리치 시점 시드 방지).
    - KR: 평단·현재가 신뢰. US 실전: 평단 신뢰 + 외화예수금(CTRP6504R) → cash_usd.
    - US 모의: 평단·시세 garbage → us_last_price/일봉CSV 실시세로 근사(approx), cash_usd=0."""
    if not _writes_allowed():
        return None
    bp = (snap or {}).get("buying_power") or {}
    if not bp.get("ok"):
        return None
    is_mock = bool(getattr(broker, "is_mock", False))
    positions: Dict[str, dict] = {}
    for h in (snap or {}).get("holdings") or []:
        code = str(h.get("code") or "").strip()
        qty = int(_f(h.get("qty")))
        if not code or qty <= 0:
            continue
        ccy = "USD" if (str(h.get("ccy") or "").upper() == "USD" or not _is_kr(code)) else "KRW"
        avg = _f(h.get("avg_price") or h.get("avg_cost"))
        cur = _f(h.get("cur_price"))
        approx = False
        if ccy == "USD" and is_mock:
            live = 0.0
            try:
                live = _f(await broker.us_last_price(code))
            except Exception:
                live = 0.0
            if live <= 0:
                live = us_csv_close(code)
            if live > 0:
                avg = cur = live
                approx = True
        if avg <= 0:
            avg = cur
            approx = True
        if avg <= 0 and cur <= 0:
            # 가격 완전 미상 — 0원 평가로 자산을 깎느니 제외하고 reconcile 경고에 맡긴다.
            logger.warning(f"[원장시드 uid={uid}] {code} 가격 미상 — 시드 제외")
            continue
        pos = {"qty": qty, "avg_cost": avg, "ccy": ccy, "last_price": cur or avg}
        if approx:
            pos["approx_basis"] = True
        positions[code] = pos
    cash_usd = 0.0
    pending = 0.0
    if not is_mock:
        try:
            pk = await broker._overseas_present_krw()
            exrt = _f(pk.get("exrt"))
            if pk.get("ok") and exrt > 500:
                cash_usd = max(0.0, _f(pk.get("deposit_krw")) / exrt)
        except Exception as e:
            logger.warning(f"[원장시드 uid={uid}] 외화예수금 조회 실패(0으로 시드): {e}")
        # 사장 지시 2026-06-11(라이브 디버깅): 미결제(T+2) USD 매도대금은 어떤 예수금 TR 에도
        # 안 잡힌다 — 체결내역 기반으로 가산해야 자산 과소평가(-2.5M 사례)가 없다.
        pending = await pending_usd_from_fills(broker)
        cash_usd += pending
    led = {
        "version": 1,
        "seeded_at": _now_str(),
        "seed_source": "kis_mock" if is_mock else "kis_live",
        "cash_krw": _f(bp.get("cash")),
        "cash_usd": cash_usd,
        "positions": positions,
        "fills": [],
        "degraded_fills": 0,
    }
    if pending:
        led["seed_pending_usd"] = round(pending, 4)   # 정보성 — 시드 당시 미결제 매도대금
    save(uid, led)
    logger.info(f"[원장시드 uid={uid}] 완료 — KRW {led['cash_krw']:,.0f} / USD {cash_usd:,.2f} "
                f"(미결제 {pending:,.2f}) / 종목 {len(positions)}개 ({led['seed_source']})")
    return led


async def ensure_value(uid, broker, snap: dict, fx: float) -> Optional[float]:
    """폴러용 원스톱: (필요시) 시드 → US 실시세 보강 → M2M 평가값(KRW) 반환.
    실패 시 None — 호출부는 ledger 포인트 없이 기존 KIS 곡선만 기록한다."""
    led = load(uid)
    if led is None:
        led = await seed(uid, broker, snap)
        if led is None:
            return None
    is_mock = bool(getattr(broker, "is_mock", False))
    lookup: Dict[str, float] = {}
    for h in (snap or {}).get("holdings") or []:
        code = str(h.get("code") or "").strip()
        cp = _f(h.get("cur_price"))
        if not code or cp <= 0:
            continue
        # 모의 해외 시세는 garbage(사장 확인 2026-06-10) — KR만 채택. 실전은 둘 다 채택.
        if _is_kr(code) or not is_mock:
            lookup[code] = cp
    for tk, pos in (led.get("positions") or {}).items():
        if str(pos.get("ccy") or "").upper() != "USD" or tk in lookup:
            continue
        live = 0.0
        try:
            live = _f(await broker.us_last_price(tk))
        except Exception:
            live = 0.0
        if live <= 0:
            live = us_csv_close(tk)
        if live > 0:
            lookup[tk] = live
    mtm = mark_to_market(uid, price_lookup=lookup, fx=fx)
    return mtm["value_krw"] if mtm else None
