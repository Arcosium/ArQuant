"""
Arquant — 주간 피드백 루프 (사장 지시 2026-05-14).

매주 토요일 06:00 KST (금요일 미장 마감 ≈ 16:00 ET = 토 06:00 KST) 후에
지난 7일간의 cycle_store + equity_curve 데이터를 분석해
운용지원실장 워커에 '주간 리뷰' 컨텍스트를 보낸다.

운용지원실장은 이 데이터를 기반으로:
  - 퀀트점수 임계값 조정
  - 뉴스 분류 키워드 가중치 조정
  - 사후관리실장 매도 룰 정교화 등을 제안할 수 있다.
"""
from __future__ import annotations
import json, logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List

logger = logging.getLogger("WEEKLY")
KST = timezone(timedelta(hours=9))
PROJECT_ROOT = Path(__file__).resolve().parent.parent
MARKER_FILE = PROJECT_ROOT / "data" / ".weekly_review_last.txt"   # 전역 폴백(uid 없을 때)


def _marker_path(uid=None) -> Path:
    """중복방지 마커 경로. per-uid(Phase 2) — 전역 마커로 두 계정 중 한쪽이 막히던 버그 2026-06-06 수정.
    uid 없으면 전역 폴백(하위호환)."""
    if uid is None:
        return MARKER_FILE
    return PROJECT_ROOT / "data" / str(int(uid)) / ".weekly_review_last.txt"


def _read_last_run(uid=None) -> datetime | None:
    p = _marker_path(uid)
    if not p.exists():
        return None
    try:
        s = p.read_text(encoding="utf-8").strip()
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
    except Exception:
        return None


def _write_last_run(when: datetime, uid=None):
    try:
        p = _marker_path(uid)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(when.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    except Exception as e:
        logger.warning(f"marker 저장 실패: {e}")


def should_run_now(now: datetime | None = None, uid=None) -> bool:
    """토요일 06:00 KST 이후 + 직전 실행 ≥ 6일 전이면 True (per-uid 마커)."""
    now = now or datetime.now(KST)
    if now.weekday() != 5:   # 토(5)
        return False
    if now.hour < 6:
        return False
    last = _read_last_run(uid)
    if last is None:
        return True
    return (now - last).total_seconds() > 6 * 86400


def _run_current_backtest(uid=None) -> Dict[str, Any]:
    """현재 적용 파라미터(프로필 오버라이드 반영)로 단일 성과 백테스트. CSV 없으면 available=False.

    사장 지시 2026-06-09: 프리셋 폐지 + 비교 백테스트 폐지 → 토요일 점검 때 '현재 설정'을
    과거 일봉에 돌린 성과를 운용지원실장 튜닝 입력으로 직접 제공한다."""
    try:
        import config
        import runtime
        from backtest.engine import load_prices, run_backtest
        prices = load_prices()
        if not prices:
            return {"available": False, "reason": "no_daily_csv"}
        params = {k: runtime.get(k, uid=uid) for k in config.STRATEGY_TUNABLE_KEYS}
        m = run_backtest(params, prices)
        return {"available": True,
                "total_return_pct": m["total_return_pct"],
                "max_drawdown_pct": m["max_drawdown_pct"],
                "sharpe_like": m["sharpe_like"],
                "trades": m["trades"],
                "win_rate_pct": m["win_rate_pct"]}
    except Exception as e:
        return {"available": False, "reason": str(e)}


def _run_walkforward(uid=None) -> Dict[str, Any]:
    """현재 설정으로 롤링 아웃오브샘플(워크포워드) — 단일 백테스트가 '한 시기의 운'인지 본다.
    반환 집계(구간 일관성·최악 구간). 2026-06-15 ROI#1."""
    try:
        import config
        import runtime
        from backtest.engine import load_prices
        from backtest.walkforward import walk_forward
        prices = load_prices()
        if not prices:
            return {"available": False, "reason": "no_daily_csv"}
        params = {k: runtime.get(k, uid=uid) for k in config.STRATEGY_TUNABLE_KEYS}
        agg = walk_forward(params, prices, test_days=20, warmup_days=40)["aggregate"]
        agg["available"] = agg.get("n_windows", 0) > 0
        return agg
    except Exception as e:
        return {"available": False, "reason": str(e)}


def build_review_summary(uid=None) -> Dict[str, Any]:
    """지난 7일 데이터 집계 — cycles, trades, equity. **per-uid** (Phase 2 멀티테넌트).

    버그수정 2026-06-06: 이전엔 uid 없이 전 계정 사이클을 섞고, 전역(부재)
    data/equity_curve.json·claude_response.json 을 읽어 수익률·체결수가 항상 결손이었다.
    이제 uid 로 사이클을 필터하고, per-uid equity_curve 와 사이클 orders_executed 로 집계한다."""
    from infra import cycle_store
    cutoff = (datetime.now(KST) - timedelta(days=7))
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")

    # ── cycles 분석 (uid 한정) ──
    cycles = cycle_store.list_cycles(limit=500, uid=uid)
    weekly_cycles = [c for c in cycles if (c.get("started_at") or "") >= cutoff_str]
    n_cycles = len(weekly_cycles)
    n_with_orders = sum(1 for c in weekly_cycles if c.get("orders_executed") and c.get("orders_executed") != "null")
    n_approved = sum(1 for c in weekly_cycles if c.get("risk_approved"))
    n_market_open = sum(1 for c in weekly_cycles if c.get("market_open"))
    avg_pnl = 0.0
    pnls = [c.get("bp_pnl_ratio") for c in weekly_cycles if c.get("bp_pnl_ratio") is not None]
    if pnls: avg_pnl = sum(pnls) / len(pnls)
    # candidate-target 전환율 (얼마나 골랐다가 실제로 샀나) + 사이클 orders_executed 기반 체결/실패 집계
    total_candidates = 0; total_targets = 0
    trades_executed = 0; trades_failed = 0
    for c in weekly_cycles:
        try:
            cc = json.loads(c.get("candidate_codes") or "[]")
            tc = json.loads(c.get("target_codes") or "[]")
            total_candidates += len(cc); total_targets += len(tc)
        except Exception: pass
        oe = c.get("orders_executed")
        if oe and oe != "null":
            try:
                orders = json.loads(oe) if isinstance(oe, str) else oe
                for o in (orders or []):
                    if not isinstance(o, dict): continue
                    if o.get("filled"): trades_executed += 1
                    elif not o.get("accepted"): trades_failed += 1   # 미접수·반려만 실패(접수-폴링중은 제외)
            except Exception: pass
    conversion = (total_targets / total_candidates * 100) if total_candidates else 0.0

    # ── equity 변화 (per-uid data/<uid>/equity_curve.json) ──
    eq_first = eq_last = None; eq_adj_first = eq_adj_last = None
    try:
        eq_path = (PROJECT_ROOT / "data" / str(int(uid)) / "equity_curve.json") if uid is not None \
            else (PROJECT_ROOT / "data" / "equity_curve.json")
        if eq_path.exists():
            eq = json.loads(eq_path.read_text(encoding="utf-8"))
            within = [p for p in (eq or []) if (p.get("ts") or "") >= cutoff_str and p.get("total_eval")]
            if within:
                eq_first = within[0].get("total_eval"); eq_last = within[-1].get("total_eval")
                ext_f = within[0].get("external_flow_cum") or 0.0
                ext_l = within[-1].get("external_flow_cum") or 0.0
                eq_adj_first = eq_first - ext_f; eq_adj_last = eq_last - ext_l
    except Exception: pass
    equity_return = ((eq_adj_last/eq_adj_first - 1) * 100) if (eq_adj_first and eq_adj_last) else None

    return {
        "period": f"최근 7일 (~ {datetime.now(KST).strftime('%Y-%m-%d %H:%M')} KST)",
        "cycles": n_cycles, "with_orders": n_with_orders, "risk_approved": n_approved,
        "market_open_cycles": n_market_open,
        "avg_pnl_ratio": avg_pnl,
        "candidates_picked": total_candidates,
        "targets_final": total_targets,
        "candidate_to_target_pct": conversion,
        "trades_executed": trades_executed,
        "trades_failed": trades_failed,
        "equity_first": eq_first, "equity_last": eq_last,
        "equity_return_pct_adj": equity_return,
        "backtest": _run_current_backtest(uid=uid),
        "walkforward": _run_walkforward(uid=uid),   # ROI#1 — 롤링 아웃오브샘플 성과 안정성
        # 사장 지시 2026-06-09 #7: 평일 사이클에서 회부된 weekly-tier 제안(점수엔진·구조 파라미터) —
        # 토요일 워커가 백테스트와 함께 재평가해 적용 여부 결정.
        "deferred_weekly_proposals": _list_deferred(uid),
    }


def _list_deferred(uid=None):
    try:
        from infra import weekly_defer_queue
        return weekly_defer_queue.list_pending(int(uid)) if uid is not None else []
    except Exception:
        return []


def build_review_message(summary: Dict[str, Any]) -> str:
    """주간 피드백 결과를 사장에게 '직접' 보고할 한국어 메시지 (사장 지시 2026-05-24).
    이전엔 'data/...log 참고'로만 안내했으나, 무엇을 점검·진단했는지 메시지에 직접 담는다.
    (운용지원실장이 산출하는 구체 파라미터 조정안은 분석 완료 후 별도 🛠 OPS 메시지로 이어진다.)"""
    lines = [f"📅 [주간 피드백 루프] {summary.get('period', '최근 7일')} — 지난 7일 운영을 점검했습니다."]
    lines.append(
        f"• 사이클 {summary.get('cycles', 0)}회 (장중 {summary.get('market_open_cycles', 0)}회) · "
        f"주문실행 {summary.get('with_orders', 0)}회 · 리스크승인 {summary.get('risk_approved', 0)}회")
    lines.append(
        f"• 후보→매수 전환 {summary.get('candidate_to_target_pct', 0):.0f}% "
        f"(후보 {summary.get('candidates_picked', 0)} → 매수 {summary.get('targets_final', 0)})")
    lines.append(
        f"• 체결 {summary.get('trades_executed', 0)}건 · 실패 {summary.get('trades_failed', 0)}건")
    er = summary.get("equity_return_pct_adj")
    lines.append(f"• 7일 자산 수익률(입출금 보정) {er:+.2f}%" if isinstance(er, (int, float))
                 else "• 7일 자산 수익률: 데이터 부족")
    bt = summary.get("backtest") or {}
    if bt.get("available"):
        lines.append(
            f"• 현재 설정 백테스트: 수익률 {bt.get('total_return_pct', 0):+.1f}% · "
            f"MDD {bt.get('max_drawdown_pct', 0):.1f}% · 샤프* {bt.get('sharpe_like', 0):.2f} · "
            f"매매 {bt.get('trades', 0)}건 · 승률 {bt.get('win_rate_pct', 0):.0f}%")
    else:
        lines.append("• 현재 설정 백테스트: 데이터 부족(일봉 CSV 없음)")
    wf = summary.get("walkforward") or {}
    if wf.get("available"):
        lines.append(
            f"• 워크포워드({wf.get('n_windows', 0)}구간 아웃오브샘플): 평균 {wf.get('mean_return_pct', 0):+.1f}% · "
            f"양(+)구간 {wf.get('pct_positive', 0)*100:.0f}% · 최악구간 {wf.get('worst_return_pct', 0):+.1f}%/"
            f"MDD {wf.get('worst_mdd_pct', 0):.1f}% — 일관성↓면 과적합 의심")
    lines.append("→ 운용지원실장이 위 통계로 전략 파라미터 조정안을 분석 중입니다. "
                 "실제 적용 내역은 이어지는 🛠 OPS 메시지로 직접 보고됩니다.")
    return "\n".join(lines)


def trigger_if_due(uid=None, is_admin: bool = False):
    """If it's Saturday 06:00+ KST and we haven't run this week, spawn the ops_support worker
    with a 주간 리뷰 directive. Returns a human-readable 한국어 summary message (truthy) if triggered,
    else False (사장 지시 2026-05-24: '로그 참고' 대신 점검 내용 자체를 메시지로 보고).

    Phase 2 멀티테넌트: 전역 활성 계정 폐지 → 호출자(오케스트레이터)가 자신의 uid/is_admin 을
    명시적으로 넘긴다. 주간 조정은 그 프로필에 적용된다."""
    if not should_run_now(uid=uid):
        return False
    _auid, _admin = uid, bool(is_admin)
    # 운용지원 on/off 토글(프로필별) 존중 — 이 프로필이 OFF면 주간 피드백도 스킵.
    try:
        import runtime as _rt
        if not _rt.ops_feedback_enabled(_auid):
            logger.info(f"주간 피드백 스킵 — 운용지원 피드백 토글 OFF (uid={_auid})")
            return False
    except Exception:
        pass
    summary = build_review_summary(uid=_auid)
    # 운용지원실장 워커에 manual 모드로 위임
    import subprocess
    _deferred = summary.get("deferred_weekly_proposals") or []
    _defer_hint = ""
    if _deferred:
        _defer_hint = ("\n\n[지난 주 회부된 weekly-tier 제안] — 평일 사이클에서 즉시 적용을 보류하고 "
                       "오늘 백테스트 검증으로 미뤄둔 구조 파라미터 제안입니다. 아래 통계·백테스트를 근거로 "
                       "**정당하면 param_overrides 에 반영, 아니면 기각**하십시오:\n"
                       + json.dumps(_deferred, ensure_ascii=False, indent=2))
    directive = (
        "[주간 피드백 루프] 다음은 지난 7일간의 ArQuant 운영 통계입니다. "
        "이 데이터를 검토하여 ① 퀀트점수 임계값·지표 가중치, ② 사후관리실장 매도 룰, "
        "③ 후보 사전 필터·사이징 등에 대해 **이 프로필에 적용할 전략 튜닝 "
        "파라미터(param_overrides)** 조정안을 제시하십시오. 코드 자가수정·서버 재시작은 하지 않습니다. "
        "변경이 필요 없으면 솔직하게 답하세요. (토요일 트리거이므로 weekly-tier 구조 파라미터도 즉시 적용됩니다.)"
        + _defer_hint
        + f"\n\n통계:\n{json.dumps(summary, ensure_ascii=False, indent=2)}"
    )
    worker = PROJECT_ROOT / "infra" / "ops_support_worker.py"
    log_path = PROJECT_ROOT / "data" / "weekly_review.log"
    # 코드 자가수정 제거 후 주간 피드백은 프로필 한정 파라미터 조정만 — ADMIN·일반 공통.
    try:
        f = open(log_path, "a", encoding="utf-8", buffering=1)
        f.write(f"\n=== {datetime.now(KST):%Y-%m-%d %H:%M:%S} 주간 피드백 트리거 (uid={_auid}, admin={_admin}) ===\n")
        f.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
        _cmd = ["python3.11", str(worker), "--manual", directive, "--actor-admin", "1" if _admin else "0"]
        if _auid is not None:
            _cmd += ["--actor-user", str(int(_auid))]
        subprocess.Popen(
            _cmd, stdout=f, stderr=subprocess.STDOUT,
            start_new_session=True, cwd=str(PROJECT_ROOT))
        _write_last_run(datetime.now(KST), uid=_auid)
        # 회부된 weekly-tier 제안은 directive 에 실어 워커에 넘겼으므로 큐를 비운다(중복 회부 방지).
        if _deferred and _auid is not None:
            try:
                from infra import weekly_defer_queue
                weekly_defer_queue.clear(int(_auid))
            except Exception as _ce:
                logger.warning(f"주간 검증 큐 clear 실패: {_ce}")
        logger.info("주간 피드백 워커 spawn 완료")
        return build_review_message(summary)
    except Exception as e:
        logger.warning(f"주간 피드백 트리거 실패: {e}")
        return False
