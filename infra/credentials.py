"""
ArQuant v1.0 — 런타임 활성 계정 레이어 (사장 피드백 2026-05-16)

여러 KIS 계정이 등록될 수 있으나 스왐은 단일 프로세스이므로 '로그인한 계정 하나'가
현재 봇을 장악한다. 로그인/계정전환 시 이 모듈이:
  1) auth_store에서 해당 계정 자격증명을 복호
  2) config 모듈 전역(KIS_*, OPENROUTER_API_KEY, OPENDART_API_KEY)을 재할당
     → dart_disclosure / global_search 는 함수 안에서 매번 `from config import`
       하므로 즉시 반영. KISBroker / BaseAgent 는 __init__에서 읽으므로
       싱글턴(infra.kis_broker._broker, main_swarm._swarm)을 리셋해 재생성 유도.
  3) 활성 계정 id를 data/.active_account 에 영속 → 코드 갱신 재시작(RESUME_ON_BOOT)
     후에도 같은 계정으로 복귀.

⚠️ 실거래 안전: 매매 루프가 도는 중에 '다른 계정'으로 전환하면 잘못된 계좌로 주문이
   나갈 수 있다. 그 정책은 account_switch_policy()에서 사장님이 정의한다.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from infra import auth_store

logger = logging.getLogger("CREDS")

_ACTIVE_FILE = Path(__file__).parent.parent / "data" / ".active_account"
_active: Dict[str, Any] = {"user_id": None, "label": None, "kis_account_no": None,
                           "is_admin": False}


# ─── 활성 계정 안전 전환 정책 ─────────────────────────────────────────────────
def account_switch_policy(current_uid: Optional[int], target_uid: int,
                          loop_running: bool) -> Dict[str, str]:
    """매매 루프가 도는 중 활성 계정을 바꾸려 할 때의 동작을 결정한다.

    반환: {"action": "...", "reason": "..."}
      • "proceed"               — 즉시 전환 (서버는 그대로 진행)
      • "stop_loop_then_proceed"— 루프를 먼저 멈추고 전환 (수동 재시작 필요)
      • "refuse"                — 전환 거부 (먼저 사용자가 루프를 멈춰야 함)

    ── 사장님 직접 정의 필요 ──
    이건 '실거래 자금 안전' 결정입니다. 트레이드오프:
      - "proceed": 끊김 없지만, 같은 사이클 도중 계좌가 바뀌면 잘못된 계좌로
        잔여 주문/체결확인이 갈 위험.
      - "stop_loop_then_proceed"(기본값): 가장 안전. 단 사용자가 다시 ▶실행을
        눌러야 함.
      - "refuse": 가장 보수적. 사용자가 명시적으로 멈추기 전엔 계정 변경 불가.
    같은 계정 재로그인(current_uid == target_uid)은 위험이 없습니다.
    """
    # 안전한 기본값. 정책을 바꾸려면 아래 분기를 사장님 기준으로 수정하십시오.
    if current_uid == target_uid:
        return {"action": "proceed", "reason": "동일 계정 재인증 — 위험 없음"}
    if not loop_running:
        return {"action": "proceed", "reason": "매매 루프 미가동 — 안전하게 전환"}
    # TODO(사장님): 루프 가동 중 '다른 계정' 전환 시 정책을 확정하십시오.
    #   기본값은 가장 안전한 'stop_loop_then_proceed'. 'refuse' 또는 'proceed'로
    #   바꾸려면 아래 return을 교체하십시오 (1줄).
    return {"action": "stop_loop_then_proceed",
            "reason": "다른 계정 전환 — 잘못된 계좌 주문 방지 위해 루프 정지 후 전환"}


# ─── 자격증명을 런타임에 주입 ─────────────────────────────────────────────────
def _apply_to_config(creds: Dict[str, Any]) -> None:
    """config 모듈 전역을 활성 계정 값으로 재할당.
    dart_disclosure/global_search 는 호출 시점에 from config import 하므로 즉시 반영."""
    import config
    config.KIS_APP_KEY = creds["kis_app_key"]
    config.KIS_APP_SECRET = creds["kis_app_secret"]
    config.KIS_ACCOUNT_NO = creds["kis_account_no"]
    config.KIS_BASE_URL = creds["kis_base_url"] or config.KIS_BASE_URL
    config.OPENROUTER_API_KEY = creds["openrouter_key"]
    config.OPENDART_API_KEY = creds.get("dart_key") or ""  # 없으면 빈 문자열 → 공시 분석 생략


def _reset_singletons() -> None:
    """KISBroker / 스왐 싱글턴을 폐기해 다음 접근 시 새 자격증명으로 재생성."""
    try:
        import infra.kis_broker as kb
        kb._broker = None
    except Exception as e:
        logger.warning("broker 싱글턴 리셋 실패: %s", e)
    try:
        import main_swarm
        main_swarm._swarm = None
    except Exception as e:
        logger.warning("swarm 싱글턴 리셋 실패: %s", e)


def set_active(user_id: int) -> Dict[str, Any]:
    """계정을 활성화 — 자격증명 주입 + 싱글턴 리셋 + 영속.
    호출 전 server가 account_switch_policy 결과에 따라 루프를 정리해야 한다."""
    creds = auth_store.get_user_credentials(user_id)
    if not creds:
        raise ValueError(f"user_id={user_id} 자격증명 없음")
    _apply_to_config(creds)
    _reset_singletons()
    is_admin = bool(creds.get("is_admin"))
    # 사장 피드백 2026-05-18: 활성 프로필 한정 오버라이드를 runtime 최상위 레이어로 교체.
    # ADMIN 이면 {} 로 비워서(전역 전략만 적용) 직전 비관리자 튜닝이 새지 않게 한다.
    try:
        from infra import profile_overrides
        applied_ov = profile_overrides.activate(creds["id"], is_admin=is_admin)
    except Exception as e:
        logger.warning("프로필 오버라이드 활성화 실패: %s", e)
        applied_ov = {}
    _active.update(user_id=creds["id"], label=creds["label"],
                   kis_account_no=creds["kis_account_no"], is_admin=is_admin)
    try:
        _ACTIVE_FILE.write_text(str(creds["id"]), encoding="utf-8")
    except Exception as e:
        logger.warning("활성 계정 영속 실패: %s", e)
    logger.info("활성 계정 전환 → id=%s label=%s admin=%s overrides=%s",
                creds["id"], creds["label"], is_admin, list(applied_ov.keys()))
    return {"user_id": creds["id"], "label": creds["label"],
            "kis_account_no": creds["kis_account_no"],
            "is_admin": is_admin, "profile_overrides": applied_ov,
            "has_dart": bool(creds.get("dart_key"))}


def clear_active() -> None:
    _active.update(user_id=None, label=None, kis_account_no=None, is_admin=False)
    # 로그아웃 시 직전 프로필 튜닝이 무로그인 상태로 새지 않도록 레이어 해제.
    try:
        import runtime
        runtime.set_profile_overrides({})
    except Exception:
        pass
    try:
        if _ACTIVE_FILE.exists():
            _ACTIVE_FILE.unlink()
    except Exception:
        pass


def current() -> Dict[str, Any]:
    return dict(_active)


def reactivate_last() -> Optional[int]:
    """서버 부팅 시 직전 활성 계정 복구 (RESUME_ON_BOOT 자동재개와 함께 사용)."""
    try:
        if not _ACTIVE_FILE.exists():
            return None
        uid = int(_ACTIVE_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        return None
    try:
        set_active(uid)
        return uid
    except Exception as e:
        logger.warning("직전 활성 계정 복구 실패: %s", e)
        return None
