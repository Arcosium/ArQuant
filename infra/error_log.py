"""
ArQuant — 상세 에러 로깅 (사장 지시 2026-05-19)

문제: 로그에 '[운용전략실장] 예외:' 처럼 원인 불명 에러가 찍혀
운용지원실장이 어디서·왜 났는지 진단할 수 없었다 (TimeoutError 등은 str(e)='').

해결: 시스템 어디서든 에러가 나면 record_error()로
  ① journald 에 타입+repr+풀 트레이스백을 남기고,
  ② claude_response.json 에 type="error" 이벤트로 영속화한다.
운용지원실장 워커의 fetch_cycle_context()가 type=="error" 이벤트를 읽으므로
다음 진단 사이클 프롬프트에 자동으로 포함된다.

fail-open: 이 모듈은 절대 예외를 밖으로 던지지 않는다 (로깅이 본 흐름을 깨면 안 됨).
"""
import logging
import traceback
from typing import Optional

logger = logging.getLogger("ERROR_LOG")


def record_error(component: str, exc: Optional[BaseException] = None,
                  *, context: str = "", level: str = "error", uid=None) -> None:
    """에러를 최대한 상세히 기록한다.

    component : 어디서 났는지 (예: "운용전략실장", "_run_analysis_cycle", "ops_support.llm_propose")
    exc       : 잡은 예외 객체 (없으면 context 만 기록)
    context   : 추가 정황 (모델명, 종목, 단계 등 — 진단에 필요한 무엇이든)
    uid       : Phase 2 멀티테넌트 — 그 유저의 이벤트 로그(data/<uid>/trade_log.json)에 적는다.
                None 이면 (전역 워커 등 uid 없는 컨텍스트) journald 에만 남고 파일엔 기록되지 않는다.
    """
    try:
        etype = type(exc).__name__ if exc is not None else "Error"
        # TimeoutError 등은 str(e)='' 이므로 repr 우선 — 원인 불명 에러의 핵심 수정점.
        erepr = (repr(exc) if exc is not None else "").strip()
        tb = ""
        if exc is not None:
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()

        headline = f"{component} | {etype}: {erepr or '(빈 메시지)'}"
        if context:
            headline += f" | {context}"

        # journald — 풀 트레이스백 포함 (사람·forensic 용)
        log_fn = getattr(logger, level if level in ("error", "warning", "critical") else "error")
        log_fn(f"{headline}\n{tb}" if tb else headline)

        # claude_response.json 영속화 — 운용지원실장이 다음 사이클에 열람.
        # message 앞부분에 진단 핵심을 모아 build_prompt 트렁케이션에서도 살아남게 한다.
        tb_tail = tb.splitlines()[-6:] if tb else []
        try:
            from main_swarm import log_response_event
            log_response_event({
                "source": "system_event",
                "type": "error",
                "component": component,
                "message": headline + (("\n…\n" + "\n".join(tb_tail)) if tb_tail else ""),
                "detail": {
                    "component": component,
                    "error_type": etype,
                    "error_repr": erepr,
                    "context": context,
                    "traceback": tb,
                },
            }, uid=uid)
        except Exception as _persist_err:  # noqa: BLE001 — 영속화 실패해도 본 흐름 유지
            logger.debug(f"error_log 영속화 실패: {_persist_err!r}")
    except Exception as _fatal:  # noqa: BLE001 — record_error 자체는 절대 예외 전파 금지
        try:
            logger.error(f"record_error 내부 실패({component}): {_fatal!r}")
        except Exception:
            pass
