"""
Arquant — 주간 피드백 루프 (사장 지시 2026-05-14).

매주 토요일 06:00 KST (금요일 미장 마감 ≈ 16:00 ET = 토 06:00 KST) 후에
지난 7일간의 cycle_store + news_classifier_log + equity_curve 데이터를 분석해
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
MARKER_FILE = PROJECT_ROOT / "data" / ".weekly_review_last.txt"


def _read_last_run() -> datetime | None:
    if not MARKER_FILE.exists():
        return None
    try:
        s = MARKER_FILE.read_text(encoding="utf-8").strip()
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
    except Exception:
        return None


def _write_last_run(when: datetime):
    try:
        MARKER_FILE.parent.mkdir(parents=True, exist_ok=True)
        MARKER_FILE.write_text(when.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    except Exception as e:
        logger.warning(f"marker 저장 실패: {e}")


def should_run_now(now: datetime | None = None) -> bool:
    """토요일 06:00 KST 이후 + 직전 실행 ≥ 6일 전이면 True."""
    now = now or datetime.now(KST)
    if now.weekday() != 5:   # 토(5)
        return False
    if now.hour < 6:
        return False
    last = _read_last_run()
    if last is None:
        return True
    return (now - last).total_seconds() > 6 * 86400


def build_review_summary() -> Dict[str, Any]:
    """지난 7일 데이터 집계 — cycles, trades, news classification, equity."""
    from infra import cycle_store, news_classifier_log
    cutoff = (datetime.now(KST) - timedelta(days=7))
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")

    # ── cycles 분석 ──
    cycles = cycle_store.list_cycles(limit=500)
    weekly_cycles = [c for c in cycles if (c.get("started_at") or "") >= cutoff_str]
    n_cycles = len(weekly_cycles)
    n_with_orders = sum(1 for c in weekly_cycles if c.get("orders_executed") and c.get("orders_executed") != "null")
    n_approved = sum(1 for c in weekly_cycles if c.get("risk_approved"))
    n_market_open = sum(1 for c in weekly_cycles if c.get("market_open"))
    avg_pnl = 0.0
    pnls = [c.get("bp_pnl_ratio") for c in weekly_cycles if c.get("bp_pnl_ratio") is not None]
    if pnls: avg_pnl = sum(pnls) / len(pnls)
    # candidate-target 전환율 (얼마나 골랐다가 실제로 샀나)
    total_candidates = 0; total_targets = 0
    for c in weekly_cycles:
        try:
            cc = json.loads(c.get("candidate_codes") or "[]")
            tc = json.loads(c.get("target_codes") or "[]")
            total_candidates += len(cc); total_targets += len(tc)
        except Exception: pass
    conversion = (total_targets / total_candidates * 100) if total_candidates else 0.0

    # ── 뉴스 분류 통계 + 결정론 진단 ──
    news_stats = news_classifier_log.recent_stats(days=7)
    try:
        from infra import news_weight_tuner
        news_tuning = news_weight_tuner.analyze(news_stats)
    except Exception as e:  # 진단 실패가 주간 리뷰를 막으면 안 됨
        logger.warning(f"news_weight_tuner 분석 실패(무시): {e}")
        news_tuning = {"ok": False, "verdict": "분석 실패", "findings": [str(e)]}

    # ── trade events (claude_response.json 참고) ──
    trades_executed = 0; trades_failed = 0
    try:
        evs_path = PROJECT_ROOT / "claude_response.json"
        if evs_path.exists():
            evs = json.loads(evs_path.read_text(encoding="utf-8"))
            for e in (evs or []):
                if (e.get("ts") or "") < cutoff_str: continue
                if e.get("type") == "trade_executed": trades_executed += 1
                elif e.get("type") == "trade_failed": trades_failed += 1
    except Exception: pass

    # ── equity 변화 ──
    eq_first = eq_last = None; eq_adj_first = eq_adj_last = None
    try:
        eq_path = PROJECT_ROOT / "data" / "equity_curve.json"
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
        "news": news_stats,
        "news_tuning": news_tuning,
        "trades_executed": trades_executed,
        "trades_failed": trades_failed,
        "equity_first": eq_first, "equity_last": eq_last,
        "equity_return_pct_adj": equity_return,
    }


def trigger_if_due() -> bool:
    """If it's Saturday 06:00+ KST and we haven't run this week, spawn the ops_support worker
    with a 주간 리뷰 directive. Returns True if triggered."""
    if not should_run_now():
        return False
    # 활성 계정(프로필) 조회 — 주간 조정은 이 프로필에 적용된다.
    try:
        from infra import credentials as _creds
        _act = _creds.current()
        _auid, _admin = _act.get("user_id"), bool(_act.get("is_admin"))
    except Exception as _e:
        logger.warning(f"활성 계정 조회 실패 — uid 없이 진행: {_e}")
        _auid, _admin = None, False
    # 운용지원 on/off 토글(프로필별) 존중 — 이 프로필이 OFF면 주간 피드백도 스킵.
    try:
        import runtime as _rt
        if not _rt.ops_feedback_enabled(_auid):
            logger.info(f"주간 피드백 스킵 — 운용지원 피드백 토글 OFF (uid={_auid})")
            return False
    except Exception:
        pass
    summary = build_review_summary()
    # 결정론 진단을 directive 최상단에 배치 — LLM 이 약해도 행동 가능한 구체 신호.
    try:
        from infra import news_weight_tuner
        diag = news_weight_tuner.summary_line(summary.get("news", {}))
    except Exception:
        diag = "[뉴스분류 진단 불가]"
    # 운용지원실장 워커에 manual 모드로 위임
    import subprocess
    directive = (
        "[주간 피드백 루프] 다음은 지난 7일간의 ArQuant 운영 통계입니다. "
        "이 데이터를 검토하여 ① 뉴스 분류 키워드 가중치, ② 퀀트점수 임계값, "
        "③ 사후관리실장 매도 룰, ④ 후보 사전 필터 cap 등에 대해 **이 프로필에 적용할 전략 튜닝 "
        "파라미터(param_overrides)** 조정안을 제시하십시오. 코드 자가수정·서버 재시작은 하지 않습니다. "
        "변경이 필요 없으면 솔직하게 답하세요.\n\n"
        f"★ 결정론 진단(우선 검토): {diag}\n\n"
        f"통계:\n{json.dumps(summary, ensure_ascii=False, indent=2)}"
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
        _write_last_run(datetime.now(KST))
        logger.info("주간 피드백 워커 spawn 완료")
        return True
    except Exception as e:
        logger.warning(f"주간 피드백 트리거 실패: {e}")
        return False
