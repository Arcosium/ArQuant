"""
ArQuant v1.0 — (RETIRED in Phase 2) 런타임 활성 계정 레이어.

Phase 1 에선 '단일 활성 계정 하나가 프로세스를 장악'하는 모델이었고, 이 모듈이
config 전역 재할당 + 싱글턴 리셋 + .active_account 영속을 담당했다.

Phase 2 멀티테넌트로 전환하며 그 전역 활성 계정 개념은 폐지됐다:
  • 각 유저의 자격증명은 infra.user_context.UserContext 가 보유하고, KISBroker/스왐을
    유저별로 lazy 생성한다(injection 기반 격리 — config 전역을 더는 건드리지 않는다).
  • '누가 이 요청의 유저인가'는 server/app.py 가 request.state.user_id 로 판단한다.
  • 매매 루프 라이프사이클은 app._start_uid/_stop_uid 가 유저별로 관리한다.

따라서 set_active/clear_active/current/account_switch_policy/reactivate_last/
_apply_to_config/_reset_singletons/_ACTIVE_FILE 는 모두 제거됐다.
자격증명 자체의 저장·복호는 infra.auth_store 가 단일 진실원이다.

이 모듈은 잔존 import 호환을 위해 남겨두되 활성 계정 상태는 보유하지 않는다.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("CREDS")
