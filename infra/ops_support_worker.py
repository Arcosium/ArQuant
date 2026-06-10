"""
Arquant — 운용지원실장 Worker (사장 지시 2026-05-14, 자가수정 폐지 2026-05-20)

This is a *separate* Python process. main_swarm.py spawns it after each cycle
via subprocess.Popen. The worker:

  1. Reads this actor's recent cycles from data/cycles.db (uid-scoped).
  2. Asks the LLM to review the cycle and propose strategy *parameter* tuning.
  3. Applies the proposed param_overrides — **profile-scoped only** — and records
     any source-level suggestions as read-only proposals (never applied).

WHY a separate process?
  - Worker crashes don't crash the trading loop.
  - The worker runs heavy LLM analysis off the hot path of the swarm.

CODE SELF-EDIT IS RETIRED (사장 지시 2026-05-20): the worker no longer edits source
or restarts the server. The hard-guard machinery (apply_changes/FORBIDDEN_*/ALLOWED_EDITS)
is preserved for regression tests only in infra/ops_guards.py and is never called here.

USAGE:
  python3.11 infra/ops_support_worker.py --cycle-id <int>   # post-cycle hook
  python3.11 infra/ops_support_worker.py --manual "사장 지시" # @운용지원실장 멘션 경로
"""
from __future__ import annotations
import sys, re, json, asyncio, argparse, logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 사장 지시 2026-05-21: 워커의 LLM 호출 예외(타임아웃 등)를 claude_response.json 에
# type="error" 로 표면화 → 다음 사이클 운용지원실장이 '지난 진단 실패'를 인지한다.
# error_log 는 top-level 의존성이 가벼우며(main_swarm 은 record_error 내부 지연 import).
from infra.error_log import record_error

KST = timezone(timedelta(hours=9))
LOG_DIR = PROJECT_ROOT / "data"
LOG_DIR.mkdir(parents=True, exist_ok=True)
WORKER_LOG = LOG_DIR / "ops_support.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ops] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(WORKER_LOG, encoding="utf-8"), logging.StreamHandler()])
logger = logging.getLogger("OPS")

# 코드 자가수정 안전 가드(apply_changes/FORBIDDEN_*/ALLOWED_EDITS 등)는 사장 지시 2026-05-20
# 으로 폐지되어 infra/ops_guards.py 에 회귀 테스트용으로만 격리 보존됨 (실행 경로 미호출).

# ─── LLM client (official DeepSeek API, no BaseAgent dependency) ───
from config import DEEPSEEK_API_KEY, MODEL_ASSIGNMENTS, AGENT_MAX_TOKENS
from infra.deepseek_client import chat_completion, response_text

# ─── Tunable limits (매직넘버 상수화) ─────────────────────────────────────
LLM_TIMEOUT_SEC = 300
LLM_TEMPERATURE = 0.2
RECENT_EVENT_SCAN = 200            # fetch_cycle_context: 훑을 최근 이벤트 수
RECENT_EVENT_KEEP = 20             # 그 중 보관할 error/skip 이벤트 수
MAX_REPORT_CHARS = 2500            # 사이클 리포트 필드 절단 길이
MAX_ERROR_MSG_CHARS = 700          # error 이벤트 메시지 절단
MAX_SKIP_MSG_CHARS = 200           # skip 이벤트 메시지 절단
MAX_RATIONALE_CHARS = 2000         # 대시보드 메시지에 표시할 rationale 상한 (사장 지시 2026-06-09: 500→2000, 중간 끊김 방지)
TRACEBACK_TAIL_LINES = 8           # error traceback 표시 줄 수
MAX_PROPOSED_SHOWN = 5             # 개선 제안 표시 개수

OPS_MODEL = MODEL_ASSIGNMENTS.get("ops_support", "deepseek-v4-pro")
OPS_MAX_TOKENS = AGENT_MAX_TOKENS.get("ops_support", 4096)

# 사장 지시 2026-05-20: 산하 팀장(investment/operations/finance) 및 코드 자가수정 기능 제거.
# 운용지원실장 단일 역할만 남으며, 소스 코드는 절대 수정하지 않고 프로필 한정 전략 파라미터
# (param_overrides) 조정안만 제시한다. role 인자는 _build_system_prompt 페르소나 문구만 바꾼다.
ROLE_PERSONAS = {
    "ops_support": ("운용지원실장",
        "**진단·프로필 튜닝 제안자** — 직전 사이클·주간 실제 데이터를 분석해 *무엇이 "
        "문제이고 무엇이 정상 동작인지*를 진단하고, 개선이 필요하면 **이 프로필에만 적용되는 전략 튜닝 "
        "파라미터(param_overrides)** 로 조정안을 제시합니다. 조정 범위는 사용자가 전략 커스터마이즈에서 "
        "바꿀 수 있는 '적용 가능 전략' 파라미터에 한정됩니다(계정마다 프로필 분리). 데이터 부족·출력 깨짐 "
        "버그를 최우선으로 진단하고, 그 외 개선점은 '제안'으로 기록합니다."),
}

def _clean_trim(text: str, limit: int) -> str:
    """limit 초과 시 문장/줄 경계에서 잘라 '…'를 붙인다 — 중간 단어·문장 끊김 방지(사장 지시 2026-06-09).
    경계를 못 찾으면(한 문장이 limit보다 길면) 그대로 잘라 '…'만 붙인다."""
    if not text or len(text) <= limit:
        return text or ""
    cut = text[:limit]
    boundary = max(cut.rfind("\n"), cut.rfind("다. "), cut.rfind(". "), cut.rfind("다."))
    if boundary > int(limit * 0.6):
        cut = cut[:boundary + 1]
    return cut.rstrip() + " …"


_OPS_SYSTEM_PROMPT_BODY = """## 임무 (사장 지시 2026-05-22 — 파라미터 점검 전용)
당신이 시스템을 개선할 수 있는 **유일한 수단은 이 프로필의 전략 튜닝 파라미터(param_overrides) 조정**입니다.
이 시스템에는 소스 코드를 바꾸는 기능이 없습니다 — 개선 수단은 아래 '튜닝 파라미터' 조정뿐입니다. 직전 사이클(또는 주간) 실제 데이터를 근거로, 아래 '튜닝 파라미터'를
조정해 운용을 개선하는 게 당신의 일입니다.

## 점검 방법 (반드시 이 순서로)
1. 직전 사이클 데이터를 읽으십시오 — 매수/매도 결정, 체결/접수, 예수금·평가액, 퀀트점수, 발생한 에러, 보유 종목 손익.
2. **아래 튜닝 파라미터를 하나씩** 그 데이터에 비춰 점검하십시오. 예시 질문:
   - 손절이 너무 늦거나 빨랐나? → STOP_LOSS_PCT
   - 익절을 너무 일찍/늦게 했나? → TAKE_PROFIT_PCT
   - 한 종목 비중이 과했나? → CONSERVATIVE_STOCK_RATIO / TRIM_OVER_RATIO
   - 매매가 과하거나 부족했나? → MAX_TRADES_PER_CYCLE / ENABLE_SELL_REBALANCE
   - 주문 예산이 과/소했나, 체결 거부가 잦았나? → PER_ORDER_BUDGET_RATIO / MAX_CYCLE_BUDGET_RATIO / MIN_CASH_BUFFER
   - 계좌 손실이 한도에 근접했나? → CONSERVATIVE_MDD
   - 데이트레이딩(단기 매도) 허용이 실제 동작과 맞나? → ALLOW_DAY_TRADING
   - ※ 자산군·엔진 정책 플래그(ALLOW_US_STOCKS·ALLOW_DERIVATIVES·ENABLE_CHEAP_FALLBACK·DETERMINISTIC_SCORING)는
     사장 전용이므로 당신이 끄거나 켤 수 없다(제안하면 자동 무시됨). 정책 변경이 필요하면 rationale 에 '제안'으로만 적으십시오.
3. **데이터로 정당화되는 조정만** param_overrides 에 담으십시오 (근거 없는 추측·취향성 변경 금지, 한 번에 과도한 폭 금지).
4. 점검 결과 정말 손볼 게 없으면 param_overrides 를 빈 객체로 두되, rationale 에 *어떤 파라미터들을 왜 그대로 두는지* 적으십시오 — "이상 없음" 한 줄로 끝내지 마십시오.

## 파라미터로 못 고치는 문제를 발견하면
로직·데이터·UI 버그 등 튜닝 파라미터로 해결되지 않는 문제는 **rationale 에 '진단'으로만** 적으십시오(무엇이·왜).
다른 조치를 시도하거나 계정·권한·다른 담당을 언급하지 마십시오 — 당신의 조치 수단은 param_overrides 뿐입니다.

(구체적인 허용 파라미터 키·단위·응답 JSON 형식은 아래에 이어집니다.)
"""


def _param_tuning_addendum() -> str:
    """시스템 프롬프트에 덧붙이는 param-only 규칙 (사장 지시 2026-05-20: 코드 자가수정 폐지).
    모든 계정이 동일하게 — 소스 변경 기능 없이 — 프로필 한정 튜닝 파라미터(param_overrides)로만
    운용 의도를 표현한다."""
    try:
        import config
        # 사장 지시 2026-06-04: 라벨만이 아니라 범위 + '효과(올리면/내리면)'까지 담은 카탈로그를
        # 주입해 운용지원실장이 전략 지시를 파라미터로 충실히 번역하도록 한다(STRATEGY_KEY_META에서 동적 생성).
        keylist = config.strategy_param_catalog_text()
    except Exception:
        keylist = ""
    return (
        "\n\n## 개선 수단 = 프로필 전용 튜닝 파라미터(param_overrides) 뿐\n"
        "이 시스템에는 소스 코드를 바꾸는 기능이 없습니다. 운용을 개선하는 유일한 방법은 "
        "이 프로필에만 적용되는 튜닝 파라미터를 `param_overrides` 객체로 출력하는 것입니다.\n"
        "- 계정·권한·관리자 여부를 언급하거나 다른 계정에 떠넘기지 마십시오 — 모든 계정이 동일하게 param_overrides 만 씁니다.\n"
        "- 자격증명·계좌·소스 식별자는 절대 param_overrides 에 넣지 마십시오(거부됨).\n\n"
        "### param_overrides 허용 키 — 라벨·범위·효과 카탈로그 (이 외 키는 조용히 탈락)\n"
        "각 키의 '효과'는 값을 올리면/내리면(켜면/끄면) 어느 방향(공격↔방어, 추세↔역추세)으로 가는지입니다. "
        "전략 지시를 이 효과에 맞춰 여러 파라미터로 **함께** 번역하십시오(한 개만 만지지 말 것).\n"
        f"{keylist}\n\n"
        "### 전략 지시 → 파라미터 번역 플레이북 (예시일 뿐 — 데이터·맥락에 맞게 LLM이 직접 판단)\n"
        "퀀트 점수는 결정론 엔진(지표별 QIW_*·차원 DW_*, 음수 가능)으로 산정됩니다. 전략 색깔은 이 가중치로 표현하십시오.\n"
        "- **급락장 대비/방어**: MIN_CASH_BUFFER↑ · PER_ORDER_BUDGET_RATIO↓ · STOP_LOSS_PCT 타이트↓ · CONSERVATIVE_MDD↓ · "
        "QIW_VOL↑(고변동 페널티)·QIW_FLOW↑(수급)·QIW_CMF↑ · DW_MACRO↑ · MIN_QUANT_SCORE↑(예 7) · MAX_TRADES_PER_CYCLE↓.\n"
        "- **추세추종**: QIW_ADX↑·QIW_MOM↑·QIW_HIGH52↑ · QIW_VWAP 음수(추격 허용) · DW_QUANT↑ · TAKE_PROFIT_PCT↑(길게).\n"
        "- **역추세/평균회귀**: QIW_RSI↑(과매도 매수)·QIW_VWAP↑(과이격 회피) · QIW_MOM 음수(낙폭과대) · TAKE_PROFIT_PCT↓.\n"
        "- **모멘텀/테마**: QIW_MOM↑·QIW_MACD↑ · DW_NEWS↑(뉴스 비중) · MIN_QUANT_SCORE↑ · PER_ORDER_BUDGET_RATIO↑.\n"
        "- **뉴스 경시(차트만)**: DW_NEWS↓ 또는 음수 · DW_QUANT↑.  **매크로 추종 강화**: DW_MACRO↑.\n"
        "- **고배당/저변동 방어**: QIW_VOL↑(또는 매우 크게) · QIW_HIGH52 음수(고점 회피) · CONSERVATIVE_STOCK_RATIO↓.\n"
        "- **공격적 확대**: PER_ORDER_BUDGET_RATIO↑ · MAX_CYCLE_BUDGET_RATIO↑ · QIW_MOM↑ · QIW_VOL↓(또는 음수) · MIN_QUANT_SCORE↓.\n"
        "- **점수 자체를 못 믿겠다(LLM로 회귀)**: DETERMINISTIC_SCORING=false (구 LLM 정성 채점으로 롤백).\n"
        "- **포지션 사이징(제도권식)**: POSITION_SIZING_MODE=risk_weighted 면 점수↑·변동성↓ 종목에 예산을 더 싣습니다. "
        "급락장엔 SIZING_TILT_STRENGTH↑(고확신·저변동 집중)·SIZING_MAX_TILT↑, 분산 강화엔 SIZING_TILT_STRENGTH↓(균등에 근접). equal=기존 균등분배.\n"
        "- **유니버스 정제(급락장·품질)**: UNIVERSE_MIN_TURNOVER↑(유동성 확보)·UNIVERSE_MIN_PRICE↑(동전주 배제)·UNIVERSE_EXCLUDE_LEVERAGED=true(레버리지/인버스 배제).\n"
        "- **집중 vs 분산**: MAX_BUY_NAMES↓ = 퀀트점수 상위 소수 집중(고확신), ↑ = 폭넓은 분산.\n"
        "- **증거기반 튜닝**: 대시보드 /api/scorecard 의 에이전트 성과(퀀트·뉴스 IC, 슬리피지, 알파/베타)를 참고하십시오 — "
        "IC 가 음수인 차원의 가중치(DW_*·QIW_*)를 재검토하고, 성과 좋은 차원에 비중을 더 싣습니다.\n"
        "지시가 위에 없으면(예: '레버리지 비슷하게 가줘') 위 효과 카탈로그를 근거로 가장 가까운 방향의 파라미터 조합을 직접 구성하십시오.\n\n"
        "### 응답 형식 (JSON 한 블록)\n"
        "```json\n"
        "{\n"
        '  "summary": "한 줄 요약",\n'
        '  "rationale": "왜 이 튜닝이 필요한가 (사이클 데이터 근거)",\n'
        '  "param_overrides": { "TAKE_PROFIT_PCT": 8.0, "STOP_LOSS_PCT": 3.0 }\n'
        "}\n"
        "```\n"
        "값 단위는 위 표기 그대로 — pct_ratio 류는 비율(0.10=10%)이 아니라 % 정수/실수로 줘도 "
        "워커가 환산·클램프합니다. 바꿀 게 없으면 param_overrides 를 빈 객체로 두고 솔직히 답하십시오."
    )


def _build_system_prompt(role: str, param_tuning: bool = False) -> str:
    """역할별 페르소나 헤더 + 공통 본문. f-string 충돌 회피 위해 헤더만 동적 구성.
    param_tuning=True 면 소스 불가침·param_overrides 강제 규칙을 덧붙인다."""
    display, focus = ROLE_PERSONAS.get(role, ROLE_PERSONAS["ops_support"])
    header = (
        f"당신은 ArQuant **{display}** (별도 프로세스 실행). "
        f"매 사이클 종료 후·사장 지시 시 호출되어 시스템 진화를 책임집니다.\n\n"
        f"## 당신의 담당 영역\n{focus}\n"
        f"(이 영역 밖이라고 판단되면 다른 팀장 담당이라고 거부할 필요는 없습니다 — 보안 가드를 통과하면 적용됩니다. "
        f"단, 본인 전문 영역 변경에 우선 집중하세요.)\n\n"
    )
    body = header + _OPS_SYSTEM_PROMPT_BODY
    if param_tuning:
        body += _param_tuning_addendum()
    return body


def _extract_json_block(reply: str) -> Optional[Dict[str, Any]]:
    """LLM 응답에서 JSON 객체 한 블록을 추출·파싱. ```json 펜스 우선, 없으면 bare {…}.
    미발견/파싱실패면 None (호출부가 폴백 처리)."""
    m = re.search(r"```json\s*(\{.+?\})\s*```", reply, re.S) or re.search(r"(\{.+\})", reply, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


async def llm_propose(prompt: str, role: str = "ops_support",
                      param_tuning: bool = False,
                      api_key: Optional[str] = None) -> Dict[str, Any]:
    """Send `prompt` to the ops_support model. Returns parsed change-plan dict, or {} on failure."""
    selected_key = (api_key or DEEPSEEK_API_KEY or "").strip()
    if not selected_key:
        logger.error("DEEPSEEK_API_KEY 없음 — LLM 호출 불가")
        return {}
    messages = [
            {"role": "system", "content": _build_system_prompt(role, param_tuning=param_tuning)},
            {"role": "user", "content": prompt},
        ]
    try:
        d = await chat_completion(
            api_key=selected_key, model=OPS_MODEL, messages=messages,
            max_tokens=OPS_MAX_TOKENS, temperature=LLM_TEMPERATURE,
            timeout_sec=LLM_TIMEOUT_SEC, thinking=True,
            response_format={"type": "json_object"},
        )
        reply = response_text(d)
    except Exception as e:
        # 사장 지시 2026-05-19: str(e)='' 예외(타임아웃 등)도 원인 식별되게 타입+repr+tb.
        logger.error(f"LLM 호출 예외: {type(e).__name__}: {e!r}", exc_info=True)
        record_error("ops_support.llm_propose", e, context=f"role={role}, prompt_len={len(prompt)}")
        return {}

    parsed = _extract_json_block(reply)
    if parsed is None:
        logger.warning(f"LLM 응답에서 JSON 미발견/파싱실패 — 변경 없음으로 처리. 응답 일부: {reply[:400]}")
        return {"summary": "JSON 응답 파싱 실패", "rationale": reply[:600], "changes": [], "restart": False}
    return parsed


# ─── Cycle data fetch (read-only) ─────────────────────────────────────────
def fetch_cycle_context(cycle_id: Optional[int], uid: Optional[int] = None) -> Dict[str, Any]:
    """Pull recent cycles from cycles.db + the last few error/skip events from this uid's log.

    Phase 2 멀티테넌트 (사장 지시 2026-05-26): 사이클·이벤트 컨텍스트는 **이 워커를 트리거한
    actor uid 의 데이터만** 읽는다. cycles 는 cycle_store.list_cycles(uid=uid) 로 필터하고,
    에러·스킵 보조 이벤트는 전역 claude_response.json 대신 uid 별 trade_log.json 을
    main_swarm.get_recent_events(uid=uid) 로 읽는다. uid 가 None 이면 (예: 활성 계정 없음)
    다른 계정 데이터를 읽지 않도록 **빈 컨텍스트**로 처리한다 (deny-by-default)."""
    from infra import cycle_store
    if uid is None:
        # default-deny: actor uid 없으면 어느 계정 데이터도 읽지 않는다 (계정 혼선 방지).
        logger.info("actor uid 없음 — 사이클 컨텍스트를 빈 값으로 처리 (계정 혼선 방지)")
        return {"target_cycle": None, "recent_cycles": [], "recent_errors_skips": []}

    cycles = cycle_store.list_cycles(limit=5, uid=uid)
    target = None
    if cycle_id is not None:
        _c = cycle_store.get_cycle(cycle_id)
        # 지정 cycle_id 가 이 uid 소유일 때만 사용 — 타 계정 사이클 누출 방지.
        if _c is not None and _c.get("uid") == uid:
            target = _c
        else:
            logger.info(f"cycle_id={cycle_id} 가 uid={uid} 소유 아님 — 최신 사이클로 대체")
            target = cycles[0] if cycles else None
    elif cycles:
        target = cycles[0]
    # Recent events (errors / skips) for additional signal — uid 별 로그에서만 읽는다.
    recent_events: List[Dict] = []
    try:
        import main_swarm
        evs = main_swarm.get_recent_events(limit=RECENT_EVENT_SCAN, uid=uid)
        if isinstance(evs, list):
            for e in evs:
                t = e.get("type", "")
                if t in ("error", "execution_skipped", "trade_failed"):
                    recent_events.append(e)
            recent_events = recent_events[-RECENT_EVENT_KEEP:]
    except Exception as e:
        logger.warning(f"events 로드 실패(uid={uid}): {e}")
    return {"target_cycle": target, "recent_cycles": cycles, "recent_errors_skips": recent_events}


def _summarize_exec_results(orders_executed) -> str:
    """직전 사이클 주문 실행결과를 '체결확인 / 접수—체결폴링중(실패아님) / 미접수·반려' 로 명확히 구분.

    버그 2026-05-22: 주문은 비동기 체결(접수 후 5분 폴링)인데 운용지원실장이 사이클 종료
    스냅샷의 filled=false 를 '실패'로 단정해 MAX_TRADES_PER_CYCLE·ENABLE_CHEAP_FALLBACK 를
    잘못 변경했다(실제로는 직후 정상 체결). accepted=true·filled=false 는 폴링 진행중이며
    실패가 아님을 라벨로 못박는다.

    버그 2026-06-05: cycle_store 는 orders_executed 를 JSON **문자열**로 저장(list_cycles 는 un-parsed
    반환)하므로 문자열 입력을 파싱한다. 미파싱 시 문자열을 dict 리스트로 순회해 매 사이클 ops 워커가
    'str object has no attribute get' 로 사망했다(재시작 후 ops_history 기록 0의 정체)."""
    if isinstance(orders_executed, str):
        try:
            orders_executed = json.loads(orders_executed)
        except Exception:
            return "없음"
    if not orders_executed:
        return "없음"
    out = []
    for e in orders_executed:
        if not isinstance(e, dict):
            continue
        tk = e.get("ticker", "?"); side = e.get("side", "?"); qty = e.get("qty", "?")
        if e.get("filled"):
            st = "체결확인"
        elif e.get("accepted"):
            st = "접수—체결폴링중(실패아님)"
        else:
            st = "미접수·반려"
        out.append(f"{tk} {side} x{qty}: {st}")
    return " | ".join(out) if out else "없음"


def _task_block_for_trigger(trigger: str) -> str:
    """트리거별 [과제] 블록 — cycle(시간당)=적극, weekly=매우 적극(전 튜닝키 큰 폭), manual=지시 우선.
    사장 지시 2026-06-05: 시간당 적극 튜닝 + 주간 매우 적극 튜닝(범위는 시스템이 자동 클램프)."""
    if trigger == "weekly":
        return ("\n[과제 — 주간 정밀 점검] 최근 7일 실데이터를 근거로 **전 튜닝 파라미터**를 하나씩 점검하고, "
                "데이터로 정당화되면 **매우 적극적으로(큰 폭도 허용)** param_overrides 에 담으십시오. "
                "범위는 시스템이 자동 클램프하니 과감히 제안하되, 각 변경의 정량 근거를 rationale 에 적으십시오. "
                "손익·체결·반려·에러 추세에서 개선 여지를 적극 발굴하십시오. 파라미터로 못 고치는 문제는 rationale 에 '진단'으로만 적으십시오.")
    if trigger == "manual":
        return ("\n[과제 — 사장 지시 처리] 위 사장 지시를 최우선으로 반영하십시오. 지시가 특정 파라미터·값을 "
                "지정하면 그 값을 param_overrides 에 담으십시오(범위는 자동 클램프). 지시 외 파라미터는 "
                "데이터 근거가 있을 때만 함께 조정하고, 근거 없으면 건드리지 마십시오.")
    # cycle (시간당 자동)
    return ("\n[과제 — 시간당 점검] 최근 사이클(들)을 근거로 개선 가능한 파라미터를 **적극적으로 제안**하십시오. "
            "근거가 있으면 '변경 없음'에 머무르지 말고 구체 조정을 param_overrides 에 담되, 한 번에 과도한 개수는 "
            "피하십시오(범위는 자동 클램프). 정말 손볼 게 없으면 빈 객체로 두고 이유를 rationale 에 적으십시오. "
            "파라미터로 못 고치는 문제는 rationale 에 '진단'으로만 적으십시오.")


def build_prompt(ctx: Dict[str, Any], manual_directive: Optional[str] = None,
                 delegated: bool = False, trigger: str = "cycle") -> str:
    """Compose the prompt for the LLM. Includes truncated cycle reports so the
    LLM has signal without exhausting the token budget.
    delegated=True 면 manual_directive 는 운용지원실장이 내린 '문제·수용 기준'이다 —
    팀장은 첨부 파일을 직접 읽고 구현(HOW)을 스스로 설계하되, 진단된 그 문제로 범위를 한정한다."""
    parts = []
    if manual_directive:
        _hdr = "[🔴 운용지원실장 지시 — 문제·기대 동작 (구현 HOW는 팀장이 첨부 파일 보고 직접 설계)]" if delegated else "[🔴 사장 직접 지시]"
        parts.append(f"{_hdr}\n{manual_directive}\n")

    tgt = ctx.get("target_cycle")
    if tgt:
        parts.append("[직전 사이클 요약]")
        parts.append(f"- 시작 {tgt.get('started_at')} / 종료 {tgt.get('ended_at')}")
        parts.append(f"- 세션: {tgt.get('session')} / 개장 사이클: {bool(tgt.get('market_open'))}")
        parts.append(f"- 후보 종목: {tgt.get('candidate_codes')}")
        parts.append(f"- 최종 매수: {tgt.get('target_codes')}")
        parts.append(f"- 사후관리실장 매도결정: {tgt.get('sell_directives')}")
        parts.append(f"- 계획 주문: {tgt.get('orders_planned')}")
        parts.append(f"- 실행 결과: {_summarize_exec_results(tgt.get('orders_executed'))}")
        parts.append("  ⚠️ 주문은 비동기 체결이다 — '접수—체결폴링중(실패아님)'은 5분 폴링 진행중이지 실패가 아니다. "
                     "미체결을 '실패'로 단정해 MAX_TRADES_PER_CYCLE·ENABLE_CHEAP_FALLBACK 등 파라미터를 바꾸지 말 것.")
        parts.append(f"- 리스크 승인: {bool(tgt.get('risk_approved'))}")
        parts.append(f"- 잔고: cash {tgt.get('bp_cash')} / total {tgt.get('bp_total_eval')} / pnl {tgt.get('bp_pnl_ratio')}")
        for fld in ("macro_report", "quant_report", "news_report", "final_report", "risk_report", "error"):
            v = tgt.get(fld)
            if v:
                parts.append(f"\n[{fld}]\n{str(v)[:MAX_REPORT_CHARS]}")

    if ctx.get("recent_cycles") and len(ctx["recent_cycles"]) > 1:
        parts.append("\n[지난 5개 사이클 요약 (감점 추세 확인용)]")
        for c in ctx["recent_cycles"][:5]:
            parts.append(
                f"- {c.get('started_at')} {c.get('session')}: 후보 {c.get('candidate_codes')} → 최종 {c.get('target_codes')} "
                f"/ 실행 {c.get('orders_executed') and 'YES' or 'NO'} / pnl {c.get('bp_pnl_ratio')} / err={c.get('error') or '없음'}")

    if ctx.get("recent_errors_skips"):
        parts.append("\n[최근 에러·스킵 이벤트 (최대 20건)]")
        for e in ctx["recent_errors_skips"]:
            # 사장 지시 2026-05-19: type=error 는 진단용이므로 상세히(메시지+트레이스백) 노출.
            if e.get("type") == "error":
                _msg = str(e.get("message", ""))[:MAX_ERROR_MSG_CHARS]
                parts.append(f"- {e.get('ts')} error[{e.get('component','?')}]: {_msg}")
                _tb = ((e.get("detail") or {}).get("traceback") or "").strip()
                if _tb:
                    parts.append("  ↳ traceback(tail):\n    " + "\n    ".join(_tb.splitlines()[-TRACEBACK_TAIL_LINES:]))
            else:
                parts.append(f"- {e.get('ts')} {e.get('type')}: {str(e.get('message',''))[:MAX_SKIP_MSG_CHARS]}")

    parts.append(_task_block_for_trigger(trigger))

    # 사장 지시 2026-05-22: 운용지원실장은 param_overrides 만 조정하므로 소스 본문 첨부 불필요.
    # (편집 가능 코드베이스 스냅샷 첨부를 제거 — 프롬프트 비대화 방지 + LLM을 소스 사고로 유도하지 않음)
    return "\n".join(parts)


# ─── Main ─────────────────────────────────────────────────────────────────
def _gate_overrides_by_data(raw_overrides: Dict[str, Any], has_cycle_data: bool,
                            is_manual: bool = False):
    """데이터 없는 *자율* 제안에서 LLM 이 가짜 거래를 지어내 파라미터를 바꾸는 것을 차단(날조 방지).
    단 사장 직접 지시(is_manual=True)는 권위이므로 데이터 게이트를 면제한다(클램프는 별도 적용).
    버그 2026-06-05: 기존엔 manual 지시까지 싸잡아 차단해 사장의 'TAKE_PROFIT 8%로' 같은 직접 지시
    100건이 전부 미적용됐다. 반환: (적용 후보 overrides, 거부사유)."""
    if is_manual:
        return (raw_overrides or {}), ""
    if not has_cycle_data:
        return {}, ("실제 직전 사이클 데이터가 없어 파라미터 변경을 보류합니다 — "
                    "근거 데이터 없이는 조정하지 않습니다(추측·날조 방지).")
    return (raw_overrides or {}), ""


def _handle_param_tuning(plan: Dict[str, Any], actor_uid: Optional[int], role: str,
                         started: str, trigger: str, cycle_id: Optional[int],
                         has_cycle_data: bool = True) -> None:
    """운용지원 단일 경로(사장 지시 2026-05-20): 소스 코드·서버 절대 불가침.
    param_overrides 만 프로필 한정으로 반영하고, changes 는 '제안'으로만 기록한다.
    ADMIN·일반 유저 모두 동일하게 이 경로를 탄다(코드 자가수정 제거).
    has_cycle_data=False(실데이터 없음) 면 param_overrides 적용을 코드로 거부한다."""
    display = ROLE_PERSONAS.get(role, ROLE_PERSONAS["ops_support"])[0]
    summary = plan.get("summary") or "변경 사항 없음"
    rationale = plan.get("rationale", "") or ""
    is_manual = (trigger == "manual")
    raw_ov, _gate_reason = _gate_overrides_by_data(
        plan.get("param_overrides") or {}, has_cycle_data, is_manual=is_manual)
    if _gate_reason:
        rationale = (f"{_gate_reason} " + rationale).strip()
    # 거버넌스 2026-06-05 — 정책/구조 플래그(자산군·엔진) 처리는 트리거별로 다르다:
    #   manual(사장)=즉시 / weekly(토)=사장 승인 대기 회부 / cycle(평일)=차단.
    # ops 가 미국 주식을 꺼 실거래 매수가 막혔던 사고 재발 방지.
    _to_review: Dict[str, Any] = {}
    if raw_ov:
        from infra.ops_param_clamp import partition_protected
        raw_ov, _to_review, _prot_notes = partition_protected(raw_ov, trigger=trigger)
        if _prot_notes:
            rationale = (rationale + " | 정책: " + "; ".join(_prot_notes)).strip()
    # 사장 지시 2026-06-09 #7 — cycle 트리거의 weekly-tier 파라미터(점수엔진·사이징모델·유니버스·
    # 슬리브 구조값 등)는 즉시 적용 보류 → 토요일 백테스트 검증 큐로 회부(weekly/manual 은 전부 통과).
    _deferred: Dict[str, Any] = {}
    if raw_ov:
        from infra.ops_param_clamp import partition_by_tier
        raw_ov, _deferred, _tier_notes = partition_by_tier(raw_ov, trigger=trigger)
        if _tier_notes:
            rationale = (rationale + " | tier: " + "; ".join(_tier_notes)).strip()
    # 가드레일 2026-06-05 — 적용 직전 범위 클램프(반려 아닌 보정). manual·cycle·weekly 공통.
    if raw_ov:
        from infra.ops_param_clamp import clamp_overrides
        raw_ov, _clamp_notes = clamp_overrides(raw_ov)
        if _clamp_notes:
            rationale = (rationale + " | 클램프: " + "; ".join(_clamp_notes)).strip()
    # weekly 정책 키는 승인 대기함에 적재 + 사장에게 알림(자동 적용 금지).
    if _to_review and actor_uid is not None:
        try:
            import runtime
            from infra import policy_approval_inbox
            _review_lines = []
            for _k, _v in _to_review.items():
                _cur = runtime.get(_k, uid=int(actor_uid))
                policy_approval_inbox.enqueue(int(actor_uid), _k, _v, _cur, rationale)
                _review_lines.append(f"  • {_k}: {_cur} → {_v}")
            import main_swarm
            main_swarm.log_response_event({
                "source": "system_event", "type": "agent_msg",
                "agent": display,
                "message": ("🔐 [정책 변경 승인 요청] 토요일 점검에서 아래 정책 플래그 변경을 제안합니다 — "
                            "대시보드 '정책 변경 승인 대기'에서 승인해야 적용됩니다:\n" + "\n".join(_review_lines)),
            }, uid=int(actor_uid))
        except Exception as e:
            logger.warning(f"정책 승인 회부 실패: {e}")
    # 사장 지시 2026-06-09 #7 — cycle 에서 회부된 weekly-tier 제안을 토요일 검증 큐에 적재 + 고지.
    if _deferred and actor_uid is not None:
        try:
            from infra import weekly_defer_queue
            _defer_lines = []
            for _k, _v in _deferred.items():
                weekly_defer_queue.enqueue(int(actor_uid), _k, _v, rationale)
                _defer_lines.append(f"  • {_k} → {_v}")
            import main_swarm
            main_swarm.log_response_event({
                "source": "system_event", "type": "agent_msg", "agent": display,
                "message": ("🗓 [토요일 검증 대기] 아래 구조 파라미터 제안은 매 사이클 즉시 적용 대상이 아니라 "
                            "토요일 백테스트+실데이터 검증 후 반영됩니다:\n" + "\n".join(_defer_lines)),
            }, uid=int(actor_uid))
        except Exception as e:
            logger.warning(f"주간 검증 큐 회부 실패: {e}")
    # 소스 변경 제안은 사람이 읽을 설명으로만 보존 (적용 안 함)
    proposed = []
    for ch in (plan.get("changes") or []):
        f = (ch.get("file") or "?")
        a = (ch.get("action") or "modify")
        d = ch.get("description") or ch.get("summary") or ""
        proposed.append(f"{f} [{a}] {d}".strip())

    applied_ov: Dict[str, Any] = {}
    if actor_uid is not None and isinstance(raw_ov, dict) and raw_ov:
        try:
            from infra import profile_overrides
            applied_ov = profile_overrides.set_overrides(int(actor_uid), raw_ov)
        except Exception as e:
            logger.warning(f"profile_overrides.set_overrides 실패: {e}")

    if applied_ov:
        head = f"✅ 전략 파라미터 튜닝 {len(applied_ov)}건 반영 (다음 로그인 시 활성화)"
    elif proposed:
        head = f"📝 개선 제안 {len(proposed)}건 — 참고용 (자동 적용 안 함)"
    elif "변경 없음" in summary or "변경 사항 없음" in summary:
        head = "ℹ️ 변경 없음 — 점검 결과 손볼 곳 없음"
    else:
        head = summary

    msg_lines = [f"🛠 [OPS#{cycle_id or 'manual'}] {display}: {head}"]
    if rationale and not ("변경 없음" in summary and not applied_ov and not proposed):
        msg_lines.append(f"근거: {_clean_trim(rationale, MAX_RATIONALE_CHARS)}")
    for k, v in applied_ov.items():
        msg_lines.append(f"  • {k} = {v}")
    if proposed:
        msg_lines.append("개선 제안(참고용):")
        for s in proposed[:MAX_PROPOSED_SHOWN]:
            msg_lines.append(f"  • {s}")
    message = "\n".join(msg_lines)
    logger.info(f"--- 운용지원 요약 ---\n{message}\n----------")

    # 구조화된 전역 이력 — ops_history.json (사장 지시 2026-05-14).
    # /api/ops_history 엔드포인트와 "@운용지원실장 수정한 코드 보여줘" 질의가 이 전역 이력을
    # 읽는다. 코드 자가수정 폐지 후에도 파라미터 튜닝/제안 이력은 계속 누적돼야 한다.
    try:
        from infra import ops_history
        ops_history.append_run({
            "role": role, "role_display": display, "trigger": trigger, "cycle_id": cycle_id,
            "summary": summary, "rationale": rationale or "",
            "applied": [f"{k} = {v}" for k, v in applied_ov.items()],
            "rejected": [], "compile_errors": [],
            "proposed": proposed, "restarted": False,
        })
    except Exception as e:
        logger.warning(f"ops_history append 실패: {e}")

    if actor_uid is not None:
        try:
            from infra import profile_overrides
            profile_overrides.record_proposal(int(actor_uid), {
                "role": role, "role_display": display, "trigger": trigger,
                "cycle_id": cycle_id, "summary": summary, "rationale": rationale,
                "overrides_applied": applied_ov,
                "proposed_source_changes": proposed,
                "restarted": False,
            })
        except Exception as e:
            logger.warning(f"record_proposal 실패: {e}")
    else:
        logger.info("활성 계정 uid 없음 — 프로필 기록 생략 (서버 유휴 상태로 추정)")

    # 대시보드 로그(uid 별 trade_log.json)에도 남겨 사용자가 바로 확인.
    # Phase 2 멀티테넌트 (사장 지시 2026-05-26): 전역 claude_response.json 대신
    # main_swarm.log_response_event(uid=actor_uid) 로 라우팅 → 해당 유저 대시보드에만 표시.
    if actor_uid is not None:
        try:
            import main_swarm
            main_swarm.log_response_event(
                {"source": "system_event", "type": "agent_msg",
                 "agent": display, "message": message},
                uid=int(actor_uid))
        except Exception as e:
            logger.warning(f"log_response_event append 실패(uid={actor_uid}): {e}")
    else:
        logger.info("actor uid 없음 — 대시보드 로그 기록 생략 (계정 혼선 방지)")


async def run(cycle_id: Optional[int], manual: Optional[str], role: str = "ops_support",
              actor_uid: Optional[int] = None, actor_admin: bool = False,
              delegated: bool = False):
    started = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    display = ROLE_PERSONAS.get(role, ROLE_PERSONAS["ops_support"])[0]
    logger.info(f"=== {display} 워커 시작 (role={role}, cycle_id={cycle_id}, "
                f"manual={'있음' if manual else '없음'}, delegated={delegated}, "
                f"actor_uid={actor_uid}, admin={actor_admin}) ===")

    # 사장 지시 2026-05-20: 코드 자가수정·서버 재시작·팀장 위임 폐지. 운용지원실장은 ADMIN·일반
    # 구분 없이 ① 진단 + ② 프로필 한정 파라미터(param_overrides) 조정 + ③ 소스 변경 '제안'
    # 기록만 수행한다. 조정 범위는 '적용 가능 전략' 수준의 프로필 파라미터에 한정된다(프로필 분리).

    # trigger 분류 — 주간 리뷰 키워드 감지
    trigger = "weekly" if (manual and "주간 피드백 루프" in manual) else ("manual" if manual else "cycle")

    # 버그수정 2026-06-05: 전역 try/except — 워커는 fire-and-forget 서브프로세스라 예외가 무음으로
    # 사라진다(매 사이클 _summarize_exec_results 크래시가 2주간 안 보였던 이유). 실패도 ops_history 에
    # 남겨 가시화한다.
    try:
        ctx = fetch_cycle_context(cycle_id, uid=actor_uid)
        if not ctx.get("target_cycle") and not manual:
            logger.info("분석할 사이클 데이터 없음 — 종료")
            return
        # 실데이터 유무 — 자율 제안은 직전/최근 사이클 데이터가 없으면 거부(날조 방지).
        # manual 직접 지시는 _gate_overrides_by_data 에서 면제된다.
        has_cycle_data = bool(ctx.get("target_cycle")) or bool(ctx.get("recent_cycles"))

        prompt = build_prompt(ctx, manual, delegated=False, trigger=trigger)
        logger.info(f"LLM 프롬프트 길이: {len(prompt)} chars (trigger={trigger})")
        # param_tuning=True: LLM 에게 '프로필 한정 param_overrides 만, 소스 변경은 제안으로만 기록,
        # 서버 재시작 없음'을 지시한다. 코드 자가수정·재시작·팀장 위임 경로는 폐지됨(ops_guards 격리).
        user_api_key = ""
        if actor_uid is not None:
            try:
                from infra.auth_store import get_user_credentials
                user_api_key = (get_user_credentials(actor_uid) or {}).get("deepseek_api_key", "")
            except Exception as e:
                logger.warning("계정별 DeepSeek 키 로드 실패(uid=%s): %s", actor_uid, e)
        plan = await llm_propose(
            prompt, role="ops_support", param_tuning=True, api_key=user_api_key)

        # 진단 + 프로필 한정 파라미터(param_overrides) 조정 + 소스 변경 '제안' 기록만 수행.
        _handle_param_tuning(plan, actor_uid, "ops_support", started, trigger, cycle_id,
                             has_cycle_data=has_cycle_data)
    except Exception as e:
        import traceback
        logger.error(f"ops run 실패: {type(e).__name__}: {e!r}", exc_info=True)
        try:
            from infra import ops_history
            ops_history.append_run({
                "role": role, "role_display": display, "trigger": trigger, "cycle_id": cycle_id,
                "summary": f"ops 진단 실패: {type(e).__name__}: {e}",
                "rationale": traceback.format_exc()[-800:],
                "applied": [], "rejected": [], "compile_errors": [], "proposed": [], "restarted": False,
            })
        except Exception:
            pass
        raise


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycle-id", type=int, default=None, help="cycles.db row id to analyze (default: latest)")
    ap.add_argument("--manual", type=str, default=None, help="사장 직접 지시 (멘션 경로)")
    ap.add_argument("--role", type=str, default="ops_support",
                    choices=list(ROLE_PERSONAS.keys()),
                    help="페르소나 역할: ops_support(기본) / investment / operations / finance")
    ap.add_argument("--actor-user", type=int, default=None,
                    help="이 작업을 트리거한 활성 계정 user_id (없으면 비관리자 취급)")
    ap.add_argument("--actor-admin", type=str, default="0", choices=["0", "1"],
                    help="활성 계정 ADMIN 여부 (정책 플래그 거버넌스 분류용 — 모든 계정 동일하게 param_overrides 만 조정)")
    ap.add_argument("--delegated", type=str, default="0", choices=["0", "1"],
                    help="1=운용지원실장이 위임한 코딩 지시 (팀장은 지시 그대로만 구현)")
    args = ap.parse_args()
    # default-deny: 인자 없거나 '1'이 아니면 전부 비관리자.
    asyncio.run(run(args.cycle_id, args.manual, role=args.role,
                    actor_uid=args.actor_user, actor_admin=(args.actor_admin == "1"),
                    delegated=(args.delegated == "1")))


if __name__ == "__main__":
    main()
