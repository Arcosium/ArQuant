"""
Arquant — 운용지원실장 Worker (사장 지시 2026-05-14)

This is a *separate* Python process. main_swarm.py spawns it after each cycle
via subprocess.Popen. The worker:

  1. Reads the latest cycle from data/cycles.db.
  2. Asks the LLM what code changes would improve strategy or fix observed bugs.
  3. Applies those changes (subject to hard guards — see FORBIDDEN_* below).
  4. If anything changed, restarts the server via start_server.sh.

WHY a separate process?
  - main_swarm.py has already loaded its modules. Editing them in-process is
    racy (Python's import system caches bytecode). A separate process means
    edits land on disk cleanly, and the next server start picks them up fresh.
  - The worker CANNOT modify its own running code — by the time we run, the
    interpreter has already loaded this file; later edits only affect future
    runs. The FORBIDDEN_FILES guard makes self-edits explicit anyway.
  - Worker crashes don't crash the trading loop.

USAGE:
  python3.11 infra/ops_support_worker.py --cycle-id <int>   # post-cycle hook
  python3.11 infra/ops_support_worker.py --manual "사장 지시" # @운용지원실장 멘션 경로

HARD GUARDS (LLM cannot override):
  - infra/ops_support_worker.py — self-edits forbidden
  - .env, data/kis_token.json, data/cycles.db — never touched
  - config.py KIS credentials — regex-protected
  - infra/kis_broker.py order-execution methods — regex-protected
  - main_swarm.py core-loop methods — regex-protected
"""
from __future__ import annotations
import sys, os, re, json, asyncio, argparse, subprocess, logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

KST = timezone(timedelta(hours=9))
LOG_DIR = PROJECT_ROOT / "data"
LOG_DIR.mkdir(parents=True, exist_ok=True)
WORKER_LOG = LOG_DIR / "ops_support.log"
SELF_REL = Path(__file__).resolve().relative_to(PROJECT_ROOT).as_posix()  # 'infra/ops_support_worker.py'

# 사장 피드백 2026-05-18: 백업 파일(*.bak.*)을 원본 옆이 아니라 backup/ 폴더에 모은다.
# 원본 디렉터리 구조를 그대로 미러링해 서로 다른 폴더의 동명 파일 충돌을 방지한다.
BACKUP_ROOT = PROJECT_ROOT / "backup"

def _backup_path(target: Path) -> Path:
    """target 의 백업 경로를 backup/<원본 상대경로>.bak.<타임스탬프> 로 생성."""
    rel = Path(target).resolve().relative_to(PROJECT_ROOT)
    bdir = BACKUP_ROOT / rel.parent
    bdir.mkdir(parents=True, exist_ok=True)
    return bdir / f"{rel.name}.bak.{datetime.now(KST).strftime('%Y%m%d_%H%M%S')}"

def _list_backups(target: Path) -> List[Path]:
    """target 의 기존 백업들을 오래된→최신 순으로 반환 (backup/ 폴더 기준)."""
    rel = Path(target).resolve().relative_to(PROJECT_ROOT)
    bdir = BACKUP_ROOT / rel.parent
    return sorted(bdir.glob(rel.name + ".bak.*")) if bdir.exists() else []

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ops] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(WORKER_LOG, encoding="utf-8"), logging.StreamHandler()])
logger = logging.getLogger("OPS")

# ─── Safety guards (LLM CANNOT override) ──────────────────────────────────
# Files the worker MUST NEVER write to, under any circumstance.
FORBIDDEN_FILES = {
    SELF_REL,                          # self — the running worker
    ".env",
    ".gitignore",
    "data/kis_token.json",
    "data/cycles.db",
    "data/equity_curve.json",
    "data/strategy_history.json",
    "claude_response.json",
    "start_server.sh",
    "stop_server.sh",
    "supervise.sh",
}

# Regex patterns the worker MUST NEVER modify, even if the file is otherwise editable.
# Format: (relative_path, compiled_regex). If any FORBIDDEN_PATTERN matches the
# `search` string OR the `replace` string, the change is rejected.
FORBIDDEN_PATTERNS = [
    # config.py: KIS API credentials & account number
    ("config.py", re.compile(r"KIS_APP_KEY|KIS_APP_SECRET|KIS_ACCOUNT_NO")),
    # infra/kis_broker.py: order execution methods (must not be tampered with)
    ("infra/kis_broker.py", re.compile(r"\b(place_order|kr_buy|kr_sell|us_buy|us_sell|bond_buy|futures_buy)\b")),
    # main_swarm.py: core trading loop methods (the heart of the system)
    ("main_swarm.py", re.compile(r"\b(_run_analysis_cycle|_build_orders|start_continuous)\b")),
    # cycle_store: persistence layer — schema changes need a migration plan
    ("infra/cycle_store.py", re.compile(r"CREATE TABLE|INSERT INTO cycles")),
]

# Files the worker IS allowed to edit (whitelist). Anything outside this list is rejected.
ALLOWED_EDITS = {
    "tools/news_monitor.py", "tools/market_data.py", "tools/quant_indicators.py",
    "tools/coresight_rag.py", "tools/dart_disclosure.py", "tools/global_search.py",
    "tools/naver_search.py",
    "agents/specialists.py", "agents/guardrails.py", "agents/base_agent.py",
    "infra/kis_broker.py",        # selectively — order methods are regex-protected
    "main_swarm.py",              # selectively — core loop methods are regex-protected
    "config.py",                  # selectively — credentials are regex-protected
    "server/app.py",
    "server/static/index.html",
}

# ─── Change-size guards (사장 피드백 2026-05-18) ──────────────────────────
# LLM 이 작은 search 앵커로 파일 전체를 갈아엎는 것을 막는다. 운용지원실장의
# 변경은 '국소 조정' 이어야 한다 — 대수술은 사람 손으로.
MAX_CHANGE_BYTES = 24_000          # 단일 change 의 replace/content 최대 바이트
MAX_NET_NEW_LINES = 400            # modify 1건이 늘릴 수 있는 순 라인 수 상한

# ─── LLM client (minimal — direct OpenRouter, no BaseAgent dependency) ───
import aiohttp
from config import (OPENROUTER_API_KEY, OPENROUTER_BASE_URL, MODEL_ASSIGNMENTS, AGENT_MAX_TOKENS)

# 사장 지시 2026-05-14: 운용지원실장 산하 팀장(investment/operations/finance)도
# 운용지원실장과 동일한 LLM 모델·토큰 한도를 공유한다.
# role은 오직 _build_system_prompt 안의 페르소나 문구만 바꾸고, 모델은 단일로 고정.
OPS_MODEL = MODEL_ASSIGNMENTS.get("ops_support", "openrouter/pareto-code")
OPS_MAX_TOKENS = AGENT_MAX_TOKENS.get("ops_support", 4096)

# 사장 지시 2026-05-14: 운용지원실장 산하 팀장 분담.
# 동일한 보안 가드(FORBIDDEN_PATTERNS/ALLOWED_EDITS)를 공유하되, 시스템 프롬프트의 역할 페르소나만 바꾼다.
# 키 = main_swarm._classify_ops_role()이 반환하는 코드, 값 = (한글 디스플레이명, 포커스 설명)
ROLE_PERSONAS = {
    "ops_support": ("운용지원실장",
        "**진단·지시자 역할 (사장 피드백 2026-05-18)** — 직접 코딩하지 않습니다. 직전 사이클 데이터를 "
        "스스로 분석해 *무엇이 버그/문제인지, 어느 파일의 어디를, 정확히 어떻게 고쳐야 하는지*까지 "
        "구체적으로 진단합니다. 그 다음 ① 고칠 게 없으면 산하 팀장을 부르지 않고 실장 선에서 피드백을 "
        "종료하고, ② 고칠 게 있으면 담당 팀장(investment/operations/finance) 1명에게 그 **구체 수정 "
        "지시**를 위임합니다. 데이터 부족·출력 깨짐 같은 버그를 최우선으로 잡아냅니다."),
    "investment":  ("투자관리팀장",
        "전략·매매 정책 코더. 전략 프리셋(config.py), 후보 필터링/사이징, 매도/익절/손절, "
        "퀀트 지표 임계값, 뉴스 분류, 종목별 고정 보유 정책 영역의 코드를 다룹니다. "
        "**운용지원실장이 내린 구체 지시 그대로만 구현**하고 즉시 서버 재시작 (restart: true)."),
    "operations":  ("경영관리팀장",
        "운영 인프라 코더. server/app.py 엔드포인트, server/static/index.html 대시보드 UX, "
        "infra/cycle_store.py·weekly_review.py·news_classifier_log.py·ops_history.py, 로깅·모니터링. "
        "매매 로직엔 손대지 않습니다. **운용지원실장 지시 그대로만 구현**하고 즉시 재시작 (restart: true)."),
    "finance":     ("재무관리팀장",
        "자산·리스크 코더. 예산 비율(PER_ORDER_BUDGET_RATIO 등), 리스크 한도, P&L·equity curve, "
        "환율·평가액 산출, 외부 입출금 감지, 표시 단위(원/달러) 정확성. "
        "**운용지원실장 지시 그대로만 구현**하고 즉시 서버 재시작 (restart: true)."),
}

# 사장 피드백 2026-05-18: 운용지원실장(진단)·팀장(코딩) 책임 분리.
# Phase A — 운용지원실장이 사이클 데이터를 보고 스스로 진단해 아래 JSON 하나로만 답한다.
_DIAGNOSE_SYSTEM_PROMPT = """당신은 ArQuant **운용지원실장** (별도 프로세스). 매 사이클 종료 후 호출되어
직전 사이클을 점검하고 **무엇을, 어디를, 어떻게 고칠지 직접 진단**합니다. 당신은 코드를 직접 쓰지 않습니다 —
대신 ① 고칠 게 없으면 산하 팀장을 부르지 않고 실장 선에서 종료하거나, ② 고칠 게 있으면 담당 팀장 1명에게
**구체 수정 지시**를 위임합니다.

## 진단 우선순위 (이런 버그를 먼저 잡으십시오)
1. **데이터 부족 버그** — 계량분석팀장이 "데이터 부족"을 반복, 일봉/수급/DART/시세가 비어 분석 불능.
2. **출력 깨짐 버그** — 에이전트가 분석 대신 JSON·코드·tool-call을 출력, 머리말 중복, 표/마크다운 깨짐, 빈 응답.
3. 명백한 로직 결함·예외·체결/접수 혼동 등 사이클 데이터에 증거가 있는 문제.
※ 검증 어려운 큰 리팩토링·취향성 개선은 위임하지 마십시오(보수적). 증거 없는 추측 금지.

## 담당 팀장 (delegate 시 sub_role 택1)
- investment: 전략·매매 정책·후보/사이징·익절/손절·퀀트 임계값·뉴스 분류
- operations: server/app.py·대시보드 UI(index.html)·cycle_store/weekly_review/news_classifier_log·로깅
- finance:    예산·리스크 한도·P&L/equity·환율·평가액·표시 단위

## 응답 형식 — 아래 JSON **한 블록만** (다른 텍스트 금지)
고칠 게 없을 때:
```json
{"action":"none","summary":"이번 사이클 점검 결과 — 즉시 고칠 버그 없음","rationale":"무엇을 확인했고 왜 손볼 게 없는지 1~3줄"}
```
고칠 게 있을 때:
```json
{"action":"delegate","sub_role":"investment",
 "summary":"한 줄 요약(무슨 버그를 누구에게 위임)",
 "rationale":"사이클 데이터의 어떤 신호가 근거인지",
 "directive":"담당 팀장에게 내리는 **구체 수정 지시**: 파일 경로 + 문제 위치 + 무엇이 잘못됐는지 + 정확히 어떻게 바꿔야 하는지. 팀장은 이 지시를 그대로 코드로 옮기기만 한다."}
```
directive는 팀장이 추가 판단 없이 바로 search/replace를 작성할 수 있을 만큼 구체적이어야 합니다."""


_OPS_SYSTEM_PROMPT_BODY = """## 임무 (사장 피드백 2026-05-15 #16)
1. 직전 사이클의 결과를 분석해 **전략 발전 방향**과 **버그 수정 사항**을 도출하십시오.
2. 필요한 코드 변경을 JSON으로 출력하면 워커가 직접 적용하고 서버를 재시작합니다.
3. 변경할 게 없으면 솔직하게 "변경 없음"이라고 답하십시오 — 무리한 수정은 시스템을 불안정하게 만듭니다.
   ⚠️ 절대 "(요약 없음)" 같은 빈 응답은 보내지 마십시오 (사장 피드백 #9). 고칠 게 없으면 명시적으로 그 이유를 적으십시오.
4. **(사장 피드백 2026-05-18) 당신은 운용지원실장의 지시를 받아 코딩만 수행하는 팀장입니다.**
   - 프롬프트 맨 위 [🔴 운용지원실장 수정 지시] / [🔴 사장 직접 지시]에 적힌 **그 수정만 정확히 구현**하십시오.
   - 스스로 범위를 넓히거나 다른 문제를 추가로 손대지 말고, 재진단·전략 의견 제시도 하지 마십시오.
   - 지시가 보안 가드에 막히거나 기술적으로 불가하면 changes:[]로 두고 rationale에 그 사유만 적으십시오.

## 코드 컨텍스트 (사장 지시 2026-05-14)
프롬프트 끝에 ArQuant의 **모든 편집 가능 파일 원본**(ALLOWED_EDITS 화이트리스트, 약 11개 파일)이
'===== <상대경로> ===== ... ===== /<상대경로> =====' 마커로 감싸져 첨부됩니다.
search 문자열은 그 첨부 본문에서 **정확히 1회만 일치하는** 텍스트(공백·들여쓰기 포함 그대로 복사)
여야 합니다 — 0회나 2회 이상이면 워커가 변경을 거부합니다.

## 절대 규칙 (워커가 코드로 강제 — LLM 우회 불가)
- **자기 자신(infra/ops_support_worker.py) 수정 절대 금지** — 새 프로세스에서만 가능
- .env, data/*.db, data/*.json, claude_response.json 수정 금지
- config.py의 KIS_APP_KEY/KIS_APP_SECRET/KIS_ACCOUNT_NO 수정 금지
- infra/kis_broker.py의 place_order/kr_buy/kr_sell/us_buy/us_sell 수정 금지
- main_swarm.py의 _run_analysis_cycle/_build_orders/start_continuous 함수 시그니처 변경 금지
- start_server.sh/stop_server.sh/supervise.sh 수정 금지

## 응답 형식
변경이 필요하면 JSON 한 블록으로만 답하십시오:
```json
{
  "summary": "변경 한줄 요약",
  "rationale": "사이클 데이터의 어떤 신호 때문에 이 변경이 필요한가",
  "changes": [
    {
      "file": "tools/market_data.py",
      "action": "modify",
      "search": "정확히 일치할 기존 문자열",
      "replace": "교체할 새 문자열"
    }
  ],
  "restart": true
}
```
- action: modify(검색-치환) / append(파일 끝 추가) / create(새 파일)
- search는 파일 안에 정확히 1회만 나타나야 함 — 0회나 2회 이상이면 변경 거부
- restart: true이면 변경 적용 후 서버 재시작 트리거

변경이 필요 없으면 단순히: `{"summary": "변경 없음", "rationale": "...", "changes": [], "restart": false}`
"""


def _non_admin_addendum() -> str:
    """비관리자 프로필에서 실행될 때 시스템 프롬프트에 덧붙이는 강제 규칙.
    공유 소스(.py)는 전 유저 공통이라 비관리자가 못 바꾼다 — 대신 프로필 한정
    튜닝 파라미터(param_overrides)로만 의도를 표현하게 한다."""
    try:
        import config
        keys = config.STRATEGY_TUNABLE_KEYS
        meta = config.STRATEGY_KEY_META or {}
    except Exception:
        keys, meta = [], {}
    lines = []
    for k in keys:
        m = meta.get(k, {})
        if m.get("deprecated"):
            continue
        lbl = m.get("label", "")
        rng = ""
        if m.get("type") == "bool":
            rng = "true/false"
        elif "min" in m and "max" in m:
            rng = f"{m['min']}~{m['max']} {m.get('unit','')}".strip()
        lines.append(f"  - {k}: {lbl} ({rng})" if rng else f"  - {k}: {lbl}")
    keylist = "\n".join(lines)
    return (
        "\n\n## ⛔ 비관리자(non-ADMIN) 프로필 — 강제 모드 (사장 피드백 2026-05-18)\n"
        "이 워커는 **비관리자 계정**의 지시로 실행 중입니다. ArQuant는 단일 공유 소스이므로 "
        "당신이 .py 소스를 고치면 전 유저가 영향을 받습니다 — 그래서 **소스 변경은 적용되지 않습니다**.\n"
        "- `changes`(search/replace 소스 수정)는 작성해도 **'제안'으로 기록만** 되고 실제 적용/재시작 없음.\n"
        "- 대신 이 프로필에만 적용될 **튜닝 파라미터**를 `param_overrides` 객체로 출력하십시오.\n"
        "- `restart` 는 무시됩니다. 항상 false 로 두십시오.\n"
        "- 자격증명·계좌·소스 식별자는 절대 param_overrides 에 넣지 마십시오(거부됨).\n\n"
        "### param_overrides 허용 키 (이 외 키는 조용히 탈락)\n"
        f"{keylist}\n\n"
        "### 비관리자 응답 형식 (JSON 한 블록)\n"
        "```json\n"
        "{\n"
        '  "summary": "한 줄 요약",\n'
        '  "rationale": "왜 이 튜닝이 필요한가 (사이클 데이터 근거)",\n'
        '  "param_overrides": { "TAKE_PROFIT_PCT": 8.0, "STOP_LOSS_PCT": 3.0 },\n'
        '  "changes": [],\n'
        '  "restart": false\n'
        "}\n"
        "```\n"
        "값 단위는 위 표기 그대로 — pct_ratio 류는 비율(0.10=10%)이 아니라 % 정수/실수로 줘도 "
        "워커가 환산·클램프합니다. 바꿀 게 없으면 param_overrides 를 빈 객체로 두고 솔직히 답하십시오."
    )


def _build_system_prompt(role: str, non_admin: bool = False) -> str:
    """역할별 페르소나 헤더 + 공통 본문. f-string 충돌 회피 위해 헤더만 동적 구성.
    non_admin=True 면 소스 불가침·param_overrides 강제 규칙을 덧붙인다."""
    display, focus = ROLE_PERSONAS.get(role, ROLE_PERSONAS["ops_support"])
    header = (
        f"당신은 ArQuant **{display}** (별도 프로세스 실행). "
        f"매 사이클 종료 후·사장 지시 시 호출되어 시스템 진화를 책임집니다.\n\n"
        f"## 당신의 담당 영역\n{focus}\n"
        f"(이 영역 밖이라고 판단되면 다른 팀장 담당이라고 거부할 필요는 없습니다 — 보안 가드를 통과하면 적용됩니다. "
        f"단, 본인 전문 영역 변경에 우선 집중하세요.)\n\n"
    )
    body = header + _OPS_SYSTEM_PROMPT_BODY
    if non_admin:
        body += _non_admin_addendum()
    return body


async def llm_propose(prompt: str, role: str = "ops_support",
                      non_admin: bool = False) -> Dict[str, Any]:
    """Send `prompt` to the ops_support model. Returns parsed change-plan dict, or {} on failure."""
    if not OPENROUTER_API_KEY:
        logger.error("OPENROUTER_API_KEY 없음 — LLM 호출 불가")
        return {}
    payload = {
        "model": OPS_MODEL,
        "messages": [
            {"role": "system", "content": _build_system_prompt(role, non_admin=non_admin)},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": OPS_MAX_TOKENS,
        "temperature": 0.2,
    }
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json",
               "HTTP-Referer": "https://arquant.ai-ve.uk", "X-Title": "ArQuant-OpsSupport-Worker"}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=180)) as s:
            async with s.post(f"{OPENROUTER_BASE_URL}/chat/completions", json=payload, headers=headers) as r:
                if r.status != 200:
                    txt = await r.text()
                    logger.error(f"LLM HTTP {r.status}: {txt[:300]}")
                    return {}
                d = await r.json()
        reply = d.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
    except Exception as e:
        logger.error(f"LLM 호출 예외: {e}")
        return {}

    # Find the JSON block — accept ``` fenced or bare
    m = re.search(r"```json\s*(\{.+?\})\s*```", reply, re.S)
    if not m:
        m = re.search(r"(\{.+\})", reply, re.S)
    if not m:
        logger.warning(f"LLM 응답에서 JSON 미발견 — 변경 없음으로 처리. 응답 일부: {reply[:400]}")
        return {"summary": "JSON 응답 파싱 실패", "rationale": reply[:600], "changes": [], "restart": False}
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as e:
        logger.warning(f"JSON 파싱 실패: {e}. 응답 일부: {reply[:400]}")
        return {"summary": "JSON 파싱 실패", "rationale": str(e), "changes": [], "restart": False}


async def llm_diagnose(prompt: str) -> Dict[str, Any]:
    """Phase A — 운용지원실장 진단 전용 호출. _DIAGNOSE_SYSTEM_PROMPT 사용,
    {"action": "none"|"delegate", ...} 한 블록을 파싱해 반환. 실패 시 안전하게 'none'."""
    if not OPENROUTER_API_KEY:
        logger.error("OPENROUTER_API_KEY 없음 — 진단 불가")
        return {"action": "none", "summary": "진단 불가 (API 키 없음)", "rationale": "OPENROUTER_API_KEY 미설정"}
    payload = {
        "model": OPS_MODEL,
        "messages": [
            {"role": "system", "content": _DIAGNOSE_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": OPS_MAX_TOKENS,
        "temperature": 0.2,
    }
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json",
               "HTTP-Referer": "https://arquant.ai-ve.uk", "X-Title": "ArQuant-OpsSupport-Diagnose"}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=180)) as s:
            async with s.post(f"{OPENROUTER_BASE_URL}/chat/completions", json=payload, headers=headers) as r:
                if r.status != 200:
                    txt = await r.text()
                    logger.error(f"진단 LLM HTTP {r.status}: {txt[:300]}")
                    return {"action": "none", "summary": "진단 미완 (LLM HTTP 오류)", "rationale": f"HTTP {r.status}"}
                d = await r.json()
        reply = d.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
    except Exception as e:
        logger.error(f"진단 LLM 예외: {e}")
        return {"action": "none", "summary": "진단 미완 (LLM 호출 예외)", "rationale": str(e)}
    m = re.search(r"```json\s*(\{.+?\})\s*```", reply, re.S) or re.search(r"(\{.+\})", reply, re.S)
    if not m:
        logger.warning(f"진단 JSON 미발견 — none 처리. 일부: {reply[:300]}")
        return {"action": "none", "summary": "진단 결과 해석 실패 — 이번엔 변경 없음",
                "rationale": reply[:400]}
    try:
        plan = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        logger.warning(f"진단 JSON 파싱 실패: {e}")
        return {"action": "none", "summary": "진단 JSON 파싱 실패 — 이번엔 변경 없음", "rationale": str(e)}
    if plan.get("action") not in ("none", "delegate"):
        plan["action"] = "none"
    return plan


def _spawn_subordinate(sub_role: str, directive: str, cycle_id: Optional[int],
                       actor_uid: Optional[int]) -> bool:
    """운용지원실장 → 담당 팀장 코딩 위임. 같은 워커를 자식 프로세스로 재실행한다
    (--delegated 1 + --manual <directive>). ADMIN 게이트는 부모(_spawn_ops_support_worker)에서
    이미 통과했으므로 --actor-admin 1 을 그대로 전달한다."""
    cmd = ["python3.11", str(Path(__file__).resolve()),
           "--role", sub_role, "--manual", directive, "--delegated", "1",
           "--actor-admin", "1"]
    if cycle_id is not None:
        cmd += ["--cycle-id", str(int(cycle_id))]
    if actor_uid is not None:
        cmd += ["--actor-user", str(int(actor_uid))]
    try:
        log_path = PROJECT_ROOT / "data" / "ops_support.spawn.log"
        f = open(log_path, "a", encoding="utf-8", buffering=1)
        f.write(f"\n=== {datetime.now(KST):%Y-%m-%d %H:%M:%S} 운용지원실장→{sub_role} 위임 "
                f"(cycle_id={cycle_id}) ===\n지시: {directive[:300]}\n")
        subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT,
                         start_new_session=True, cwd=str(PROJECT_ROOT))
        logger.info(f"팀장 워커 위임 spawn: role={sub_role}, cycle_id={cycle_id}")
        return True
    except Exception as e:
        logger.error(f"팀장 워커 위임 spawn 실패: {e}")
        return False


# ─── Editable codebase snapshot (passed to the LLM as context) ───────────
# Files capped at this size each to keep total prompt under model limits.
_FILE_MAX_CHARS = 60_000
_INDEX_HTML_MAX_CHARS = 25_000  # HTML is dense and less likely to need edits — smaller cap


def gather_editable_files() -> str:
    """Read every file in ALLOWED_EDITS and return one big string with section markers.
    Each file is wrapped in '===== <relpath> =====' / '===== /<relpath> =====' for the LLM
    to anchor on. Oversized files are truncated with a clear marker so the LLM doesn't
    write search strings into the truncated portion (which would never match)."""
    parts: List[str] = []
    total = 0
    for rel in sorted(ALLOWED_EDITS):
        fp = PROJECT_ROOT / rel
        if not fp.exists():
            continue
        try:
            content = fp.read_text(encoding="utf-8")
        except Exception as e:
            parts.append(f"\n===== {rel} (read failed: {e}) =====\n")
            continue
        cap = _INDEX_HTML_MAX_CHARS if rel.endswith(".html") else _FILE_MAX_CHARS
        if len(content) > cap:
            content = content[:cap] + f"\n... [TRUNCATED at {cap} chars — 이 아래 영역은 search 대상 금지]"
        parts.append(f"\n===== {rel} =====\n{content}\n===== /{rel} =====\n")
        total += len(content)
    parts.insert(0, f"[총 {total:,} chars / {len(ALLOWED_EDITS)} 파일 — 화이트리스트 외 파일은 절대 수정 거부됨]\n")
    return "\n".join(parts)


# ─── Cycle data fetch (read-only) ─────────────────────────────────────────
def fetch_cycle_context(cycle_id: Optional[int]) -> Dict[str, Any]:
    """Pull recent cycles from cycles.db + the last few error/skip events from claude_response.json."""
    from infra import cycle_store
    cycles = cycle_store.list_cycles(limit=5)
    target = None
    if cycle_id is not None:
        target = cycle_store.get_cycle(cycle_id)
    elif cycles:
        target = cycles[0]
    # Recent events (errors / skips) for additional signal
    recent_events: List[Dict] = []
    try:
        evs_path = PROJECT_ROOT / "claude_response.json"
        if evs_path.exists():
            evs = json.loads(evs_path.read_text(encoding="utf-8"))
            if isinstance(evs, list):
                for e in evs[-200:]:
                    t = e.get("type", "")
                    if t in ("error", "execution_skipped", "trade_failed"):
                        recent_events.append(e)
                recent_events = recent_events[-20:]
    except Exception as e:
        logger.warning(f"events 로드 실패: {e}")
    return {"target_cycle": target, "recent_cycles": cycles, "recent_errors_skips": recent_events}


def build_prompt(ctx: Dict[str, Any], manual_directive: Optional[str] = None,
                 delegated: bool = False) -> str:
    """Compose the prompt for the LLM. Includes truncated cycle reports so the
    LLM has signal without exhausting the token budget.
    delegated=True 면 manual_directive 는 운용지원실장이 내린 구체 수정 지시다 —
    팀장은 이 지시를 그대로 코드로만 옮긴다(재진단·범위확장 금지)."""
    parts = []
    if manual_directive:
        _hdr = "[🔴 운용지원실장 수정 지시 — 이대로만 구현]" if delegated else "[🔴 사장 직접 지시]"
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
        parts.append(f"- 실행 결과: {tgt.get('orders_executed')}")
        parts.append(f"- 리스크 승인: {bool(tgt.get('risk_approved'))}")
        parts.append(f"- 잔고: cash {tgt.get('bp_cash')} / total {tgt.get('bp_total_eval')} / pnl {tgt.get('bp_pnl_ratio')}")
        for fld in ("macro_report", "quant_report", "news_report", "final_report", "risk_report", "error"):
            v = tgt.get(fld)
            if v:
                parts.append(f"\n[{fld}]\n{str(v)[:2500]}")

    if ctx.get("recent_cycles") and len(ctx["recent_cycles"]) > 1:
        parts.append("\n[지난 5개 사이클 요약 (감점 추세 확인용)]")
        for c in ctx["recent_cycles"][:5]:
            parts.append(
                f"- {c.get('started_at')} {c.get('session')}: 후보 {c.get('candidate_codes')} → 최종 {c.get('target_codes')} "
                f"/ 실행 {c.get('orders_executed') and 'YES' or 'NO'} / pnl {c.get('bp_pnl_ratio')} / err={c.get('error') or '없음'}")

    if ctx.get("recent_errors_skips"):
        parts.append("\n[최근 에러·스킵 이벤트 (최대 20건)]")
        for e in ctx["recent_errors_skips"]:
            parts.append(f"- {e.get('ts')} {e.get('type')}: {str(e.get('message',''))[:200]}")

    parts.append(
        "\n[과제] 위 사이클 결과를 검토하여 ① 전략 발전 방향, ② 발견된 버그·이상 동작에 대한 코드 수정안을 제시하십시오. "
        "변경이 필요 없으면 솔직하게 변경 없음으로 답하십시오. 보수적으로 — 검증 어려운 큰 리팩토링은 피하세요. "
        "한 번에 1~3개의 작은 변경이 이상적입니다.\n\n"
        "⚠️ search 문자열은 아래 첨부된 파일 내용에서 **정확히 1회만 나타나는** 텍스트여야 합니다 "
        "(공백·들여쓰기·줄바꿈 포함 그대로 복사). 모호하면 search 범위를 더 넓혀 유니크하게 만드십시오. "
        "TRUNCATED 표시 아래 영역은 search 대상으로 쓰면 안 됩니다.")

    # 사장 지시 2026-05-14: 코드베이스 전체(화이트리스트)를 워커에 첨부해 LLM이 정확한 search/replace를 작성하게 함
    parts.append("\n\n[ArQuant 편집 가능 코드베이스 스냅샷]")
    try:
        parts.append(gather_editable_files())
    except Exception as e:
        logger.warning(f"gather_editable_files 실패: {e}")
        parts.append(f"(코드베이스 로드 실패: {e})")

    return "\n".join(parts)


# ─── Change application ──────────────────────────────────────────────────
def _violates_pattern(rel_path: str, payload: str) -> Optional[str]:
    """Return the failing pattern description if `payload` violates any FORBIDDEN_PATTERN for `rel_path`."""
    for fpath, rx in FORBIDDEN_PATTERNS:
        if rel_path == fpath and rx.search(payload or ""):
            return f"{fpath} 보호 패턴 매칭: {rx.pattern}"
    return None


def apply_changes(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Apply each change with hard-guard checks. Returns
    {applied, rejected, compile_errors, rolled_back}. Never raises.

    안전 불변식 (사장 피드백 2026-05-18): 최종 컴파일 검증이 실패하면 이
    함수가 만든 모든 변경을 **백업에서 비트 단위로 원복**한다 — 깨진 코드를
    디스크에 남겨 다음 supervise.sh 재기동에서 전체 다운되던 문제를 차단.
    """
    applied: List[str] = []
    rejected: List[str] = []
    # 롤백 원장: (target, backup_path|None, created_bool) — 적용 순서대로.
    ledger: List[tuple] = []
    for ch in (plan.get("changes") or []):
        rel = (ch.get("file") or "").strip().lstrip("/").replace("\\", "/")
        action = (ch.get("action") or "modify").strip().lower()
        desc = ch.get("description") or ch.get("summary") or action

        # ── Guard 1: forbidden files (self, secrets, dbs, scripts) ──
        if rel in FORBIDDEN_FILES:
            rejected.append(f"❌ {rel}: 보호 파일 — 절대 수정 불가 ({desc})")
            continue
        # ── Guard 2: whitelist ──
        if rel not in ALLOWED_EDITS:
            rejected.append(f"❌ {rel}: 화이트리스트 외 — 수정 거부 ({desc})")
            continue
        # ── Guard 3: path containment (no traversal) ──
        try:
            target = (PROJECT_ROOT / rel).resolve()
            target.relative_to(PROJECT_ROOT)
        except Exception:
            rejected.append(f"❌ {rel}: 프로젝트 루트 밖 경로 — 거부")
            continue

        search = ch.get("search") or ""
        replace = ch.get("replace") or ch.get("content") or ""

        # ── Guard 4: forbidden patterns in either search OR replace ──
        bad = _violates_pattern(rel, search) or _violates_pattern(rel, replace)
        if bad:
            rejected.append(f"❌ {rel}: 보호 패턴 위반 — {bad}")
            continue

        # ── Guard 5: change-size cap (작은 앵커로 파일 전체 갈아엎기 차단) ──
        payload = replace if action in ("append", "create") else replace
        if len(payload.encode("utf-8")) > MAX_CHANGE_BYTES:
            rejected.append(f"❌ {rel}: 변경 크기 {len(payload.encode('utf-8'))}B > "
                            f"한도 {MAX_CHANGE_BYTES}B — 국소 변경만 허용 ({desc})")
            continue
        if action == "modify":
            net_new = replace.count("\n") - search.count("\n")
            if net_new > MAX_NET_NEW_LINES:
                rejected.append(f"❌ {rel}: 순증 라인 {net_new} > 한도 "
                                f"{MAX_NET_NEW_LINES} — 대규모 재작성 거부 ({desc})")
                continue

        try:
            if action == "modify":
                if not search:
                    rejected.append(f"❌ {rel}: modify에 search 비어있음")
                    continue
                content = target.read_text(encoding="utf-8")
                cnt = content.count(search)
                if cnt == 0:
                    rejected.append(f"❌ {rel}: search 문자열 미발견 ({desc})")
                    continue
                if cnt > 1:
                    rejected.append(f"❌ {rel}: search가 {cnt}회 등장 — 모호함 (정확히 1회여야 함)")
                    continue
                # backup — backup/ 폴더에 원본 디렉터리 구조를 미러링하여 저장
                backup = _backup_path(target)
                backup.write_text(content, encoding="utf-8")
                target.write_text(content.replace(search, replace, 1), encoding="utf-8")
                ledger.append((target, backup, False))
                applied.append(f"✅ {rel}: modify ({desc}) [백업 {backup.relative_to(PROJECT_ROOT)}]")
            elif action == "append":
                if not replace:
                    rejected.append(f"❌ {rel}: append에 추가 내용 비어있음")
                    continue
                # 사장 피드백 2026-05-18: append 도 백업 — 롤백 가능하도록.
                _existed = target.exists()
                before = target.read_text(encoding="utf-8") if _existed else ""
                backup = _backup_path(target) if _existed else None
                if backup is not None:
                    backup.write_text(before, encoding="utf-8")
                with target.open("a", encoding="utf-8") as f:
                    f.write("\n" + replace)
                ledger.append((target, backup, not _existed))
                _bk = f" [백업 {backup.relative_to(PROJECT_ROOT)}]" if backup else " [신규파일]"
                applied.append(f"✅ {rel}: append ({desc}){_bk}")
            elif action == "create":
                if target.exists():
                    rejected.append(f"❌ {rel}: 이미 존재 — create 거부 (modify로 바꾸세요)")
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(replace or "", encoding="utf-8")
                ledger.append((target, None, True))
                applied.append(f"✅ {rel}: create ({desc})")
            else:
                rejected.append(f"❌ {rel}: 알 수 없는 action={action}")
        except Exception as e:
            rejected.append(f"❌ {rel}: 적용 예외 — {e}")

    # ── Final compile check on any modified .py file ──
    import py_compile
    bad_compile = []
    for entry in applied:
        m = re.match(r"^✅ (\S+):", entry)
        if not m: continue
        rel = m.group(1)
        if rel.endswith(".py"):
            try:
                py_compile.compile(str(PROJECT_ROOT / rel), doraise=True)
            except py_compile.PyCompileError as e:
                bad_compile.append(f"⚠ {rel}: 구문 오류 — {e}")

    # ── 컴파일 실패 시 전면 롤백 (안전 불변식) ──
    rolled_back: List[str] = []
    if bad_compile:
        for target, backup, created in reversed(ledger):
            try:
                rel = target.relative_to(PROJECT_ROOT)
                if created:
                    if target.exists():
                        target.unlink()
                    rolled_back.append(f"↩ {rel}: 신규 파일 삭제")
                elif backup is not None and backup.exists():
                    target.write_text(backup.read_text(encoding="utf-8"), encoding="utf-8")
                    rolled_back.append(f"↩ {rel}: 백업에서 원복")
                else:
                    rolled_back.append(f"⚠ {rel}: 백업 없음 — 원복 불가(수동 확인 필요)")
            except Exception as e:  # 롤백 실패도 절대 raise 안 함
                rolled_back.append(f"⚠ {target}: 롤백 예외 — {e}")
        rejected.append(
            f"🛑 컴파일 실패 {len(bad_compile)}건 → 변경 {len(ledger)}건 전면 롤백 "
            f"(디스크 원복 완료, 서버 재시작 안 함)")
        try:
            from infra import notifier
            notifier.alert("CRITICAL", "운용지원실장 자가수정 롤백",
                           "; ".join(bad_compile)[:1500],
                           dedup_key="ops_self_edit_rollback")
        except Exception:
            pass
        applied = []  # 효과적으로 적용된 변경 없음 → 호출부의 재시작 조건 미충족

    return {"applied": applied, "rejected": rejected,
            "compile_errors": bad_compile, "rolled_back": rolled_back}


# ─── Server restart trigger ───────────────────────────────────────────────
def restart_server() -> str:
    """Spawn start_server.sh detached so this worker can exit cleanly while the server bounces.
    Server reboots auto-resume the watch loop if RESUME_ON_BOOT marker exists (see main_swarm)."""
    try:
        # leave a marker so the new server knows to auto-start its watch loop
        marker = PROJECT_ROOT / "data" / ".resume_on_boot"
        marker.write_text(datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
        subprocess.Popen(
            ["bash", "-c", f"sleep 2 && bash {PROJECT_ROOT}/start_server.sh >> /tmp/arquant_restart.log 2>&1"],
            start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return "🔄 서버 재시작 예약됨 (2초 후) + RESUME_ON_BOOT 마커 설정"
    except Exception as e:
        return f"⚠ 서버 재시작 실패: {e}"


# ─── Main ─────────────────────────────────────────────────────────────────
def _handle_non_admin(plan: Dict[str, Any], actor_uid: Optional[int], role: str,
                      started: str, trigger: str, cycle_id: Optional[int]) -> None:
    """비관리자 경로: 공유 소스·서버 절대 불가침.
    param_overrides 만 프로필 한정으로 반영하고, changes 는 '제안'으로만 기록한다."""
    display = ROLE_PERSONAS.get(role, ROLE_PERSONAS["ops_support"])[0]
    summary = plan.get("summary") or "변경 사항 없음"
    rationale = plan.get("rationale", "") or ""
    raw_ov = plan.get("param_overrides") or {}
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
        head = f"✅ 이 프로필 전용 튜닝 {len(applied_ov)}건 반영 (소스/서버 불변, 다음 로그인 시 활성화)"
    elif proposed:
        head = f"📝 소스 변경 제안 {len(proposed)}건 — 비관리자라 미적용 (ADMIN만 전체 반영)"
    elif "변경 없음" in summary or "변경 사항 없음" in summary:
        head = "ℹ️ 변경 없음 — 점검 결과 손볼 곳 없음"
    else:
        head = summary

    msg_lines = [f"🛠 [OPS#{cycle_id or 'manual'}] {display} (비관리자 프로필): {head}"]
    if rationale and not ("변경 없음" in summary and not applied_ov and not proposed):
        msg_lines.append(f"근거: {rationale[:500]}")
    for k, v in applied_ov.items():
        msg_lines.append(f"  • {k} = {v}  (이 프로필 한정)")
    if proposed:
        msg_lines.append("소스 변경 제안(미적용):")
        for s in proposed[:5]:
            msg_lines.append(f"  • {s}")
    msg_lines.append("ℹ️ 공유 소스 코드·서버 재시작은 ADMIN(hh09080) 계정에서만 전체 반영됩니다.")
    message = "\n".join(msg_lines)
    logger.info(f"--- 비관리자 요약 ---\n{message}\n----------")

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

    # 대시보드 로그(claude_response.json)에도 남겨 사용자가 바로 확인
    try:
        evs_path = PROJECT_ROOT / "claude_response.json"
        data = json.loads(evs_path.read_text(encoding="utf-8")) if evs_path.exists() else []
        if not isinstance(data, list):
            data = []
        data.append({"ts": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
                     "source": "system_event", "type": "agent_msg",
                     "agent": display, "message": message})
        if len(data) > 4000:
            data = data[-4000:]
        evs_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"claude_response.json append 실패: {e}")


async def run(cycle_id: Optional[int], manual: Optional[str], role: str = "ops_support",
              actor_uid: Optional[int] = None, actor_admin: bool = False,
              delegated: bool = False):
    started = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    display = ROLE_PERSONAS.get(role, ROLE_PERSONAS["ops_support"])[0]
    logger.info(f"=== {display} 워커 시작 (role={role}, cycle_id={cycle_id}, "
                f"manual={'있음' if manual else '없음'}, delegated={delegated}, "
                f"actor_uid={actor_uid}, admin={actor_admin}) ===")

    # 사장 피드백 2026-05-18: 운용지원실장·산하 팀장은 ADMIN(hh09080) 전용.
    # 비관리자면 LLM 호출·컨텍스트 수집도 하기 전에 **즉시 종료** — 샌드박스/프로필
    # 오버라이드조차 수행하지 않는다(완전 비활성). 정상 경로(main_swarm)는 애초에
    # spawn 하지 않으므로 이는 CLI 직접 실행 등 우회 경로에 대한 default-deny 방어선.
    if not actor_admin:
        logger.warning(f"비관리자 계정(uid={actor_uid}) — 운용지원 워커 전면 비활성, 즉시 종료")
        return

    # 사장 지시 2026-05-14: trigger 분류 — 주간 리뷰 키워드 감지
    trigger = "weekly" if (manual and "주간 피드백 루프" in manual) else ("manual" if manual else "cycle")

    ctx = fetch_cycle_context(cycle_id)
    if not ctx.get("target_cycle") and not manual:
        logger.info("분석할 사이클 데이터 없음 — 종료")
        return

    # ── Phase A: 운용지원실장 진단 (사이클 자동 트리거 & 사장 직접지시 아님) ──
    # 사장 피드백 2026-05-18: 실장이 스스로 무엇/어디/어떻게를 진단 →
    #   고칠 게 없으면 팀장 안 부르고 실장 선에서 종료, 있으면 담당 팀장에게 구체 지시 위임.
    if role == "ops_support" and trigger == "cycle" and not manual and not delegated:
        diag_prompt = build_prompt(ctx, None)
        logger.info(f"진단 프롬프트 길이: {len(diag_prompt)} chars")
        dx = await llm_diagnose(diag_prompt)
        d_summary = dx.get("summary") or "사이클 점검 완료"
        d_rationale = dx.get("rationale") or ""
        sub_role = (dx.get("sub_role") or "").strip()
        directive = (dx.get("directive") or "").strip()
        if dx.get("action") == "delegate" and sub_role in ("investment", "operations", "finance") and directive:
            ok = _spawn_subordinate(sub_role, directive, cycle_id, actor_uid)
            sub_kr = ROLE_PERSONAS.get(sub_role, (sub_role,))[0]
            if ok:
                _write_summary(
                    started,
                    f"{d_summary} → {sub_kr}에게 코딩 위임",
                    f"{d_rationale}\n\n[{sub_kr}에게 내린 구체 수정 지시]\n{directive}",
                    [], [], None, trigger=trigger, cycle_id=cycle_id,
                    compile_errors=[], role="ops_support")
            else:
                _write_summary(started, f"{d_summary} (위임 spawn 실패 — 보류)",
                               d_rationale, [], [], None, trigger=trigger,
                               cycle_id=cycle_id, compile_errors=[], role="ops_support")
            return
        # action == none → 실장 선에서 피드백 종료 (팀장 미호출)
        logger.info("진단 결과: 고칠 것 없음 — 실장 선에서 종료")
        _write_summary(started,
                       d_summary if "변경 없음" in d_summary or "없음" in d_summary
                       else f"변경 없음 — {d_summary}",
                       d_rationale, [], [], None, trigger=trigger,
                       cycle_id=cycle_id, compile_errors=[], role="ops_support")
        return

    prompt = build_prompt(ctx, manual, delegated=delegated)
    logger.info(f"LLM 프롬프트 길이: {len(prompt)} chars")

    # 여기 도달 = ADMIN 확정(상단 default-deny 가드 통과). 비관리자 분기는 제거됨.
    plan = await llm_propose(prompt, role=role, non_admin=False)

    # 사장 피드백 2026-05-15 (#9): "(요약 없음)" 대신 명시적 메시지 — 변경 사항이 없으면 그렇다고 말하기.
    summary = plan.get("summary") or "변경 사항 없음 — 현 사이클에서 즉시 고칠 버그·개선점이 식별되지 않음 (사장 피드백 #9)"
    rationale = plan.get("rationale", "")
    changes = plan.get("changes") or []
    want_restart = bool(plan.get("restart"))

    logger.info(f"LLM 제안: {summary}")
    if rationale:
        logger.info(f"근거: {rationale[:500]}")

    if not changes:
        logger.info("변경 사항 없음 — 종료")
        _write_summary(started, summary, rationale, [], [], None,
                       trigger=trigger, cycle_id=cycle_id, compile_errors=[], role=role)
        return

    logger.info(f"변경 {len(changes)}건 검토 중...")
    result = apply_changes(plan)
    for line in result["applied"]: logger.info(line)
    for line in result["rejected"]: logger.warning(line)
    for line in result["compile_errors"]: logger.error(line)

    # 컴파일 실패 시 롤백은 이제 apply_changes 가 플랜 전체를 원장 역순으로
    # 원복(부분 적용 불일치 차단)한다. 여기서는 그 결과만 로깅한다.
    for line in result.get("rolled_back", []):
        logger.warning(line)

    restart_msg = None
    if want_restart and result["applied"] and not result["compile_errors"]:
        restart_msg = restart_server()
        logger.info(restart_msg)
    elif want_restart and result["compile_errors"]:
        logger.warning("재시작 요청됐으나 구문 오류로 인해 보류")

    _write_summary(started, summary, rationale, result["applied"], result["rejected"], restart_msg,
                   trigger=trigger, cycle_id=cycle_id, compile_errors=result.get("compile_errors") or [], role=role)


def _write_summary(started: str, summary: str, rationale: str, applied: List[str], rejected: List[str], restart: Optional[str],
                   trigger: str = "cycle", cycle_id: Optional[int] = None, compile_errors: Optional[List[str]] = None,
                   role: str = "ops_support"):
    """Persist a per-run summary to data/ops_support.log AND append to claude_response.json
    so the dashboard shows what the worker did. 추가로 사장 지시 2026-05-14: ops_history.json에
    구조화된 이력도 누적해 "@운용지원실장 수정한 코드 보여줘" 질의에 즉답 가능."""
    try:
        display = ROLE_PERSONAS.get(role, ROLE_PERSONAS["ops_support"])[0]
        # 사장 피드백 2026-05-15 (4차): spawn 메시지와 시각적으로 묶기 위해 동일 마커 prefix.
        _marker = f"OPS#{cycle_id}" if cycle_id else "OPS#manual"
        # 결과 분류 — 사장이 한눈에 파악할 수 있도록 첫 줄에 핵심 결과만.
        # 사장 피드백 2026-05-16: 결과를 정직하게 — 원인 모르면 '고쳤다/변경없음'으로 단정하지 말 것.
        _parse_failed = ("파싱 실패" in summary or "JSON" in summary)
        if applied:
            _headline = f"✅ {len(applied)}건 코드 수정 완료" + (" + 서버 재시작" if restart and "재시작" in str(restart) else "")
        elif rejected:
            _headline = f"⛔ 변경 제안 {len(rejected)}건 모두 거부 (보호 패턴 등)"
        elif _parse_failed:
            _headline = "⚠️ 분석 미완 — LLM 응답을 해석하지 못해 이번엔 아무것도 변경하지 않았습니다 (원인 불명, 강제 수정 안 함)"
        elif "변경 없음" in summary or "변경 사항 없음" in summary:
            _headline = "ℹ️ 변경 없음 — 점검 결과 손볼 곳 없음"
        else:
            _headline = summary
        msg_lines = [f"🛠 [{_marker}] {display}: {_headline}"]
        _no_change = (not applied and not rejected and
                      ("변경 없음" in summary or "변경 사항 없음" in summary))
        if rationale and _parse_failed:
            # 파싱 실패의 rationale은 JSON 에러 문자열일 뿐 '수정 근거'가 아니다 — 진단으로만.
            msg_lines.append(f"진단(참고): {str(rationale)[:200]} — 다음 사이클에 다시 시도합니다.")
        elif rationale and not _no_change:
            # '변경 없음'이면 한 줄로 끝낸다 (사장 피드백 2026-05-16: 고칠 게 없으면 그것만).
            msg_lines.append(f"근거: {rationale[:600]}")
        if applied: msg_lines.extend(applied)
        if rejected: msg_lines.extend(rejected)
        if restart: msg_lines.append(restart)
        message = "\n".join(msg_lines)
        logger.info(f"--- 요약 ---\n{message}\n----------")

        # 구조화된 이력 — ops_history.json
        try:
            from infra import ops_history
            ops_history.append_run({
                "role": role,
                "role_display": display,
                "trigger": trigger,
                "cycle_id": cycle_id,
                "summary": summary,
                "rationale": rationale or "",
                "applied": applied or [],
                "rejected": rejected or [],
                "compile_errors": compile_errors or [],
                "restarted": bool(restart and "재시작" in restart),
            })
        except Exception as _e:
            logger.warning(f"ops_history append 실패: {_e}")

        # append to claude_response.json (best effort)
        evs_path = PROJECT_ROOT / "claude_response.json"
        try:
            data = json.loads(evs_path.read_text(encoding="utf-8")) if evs_path.exists() else []
            if not isinstance(data, list): data = []
            data.append({"ts": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
                         "source": "system_event", "type": "agent_msg",
                         "agent": display, "message": message})
            if len(data) > 4000: data = data[-4000:]
            evs_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"claude_response.json append 실패: {e}")
    except Exception as e:
        logger.warning(f"_write_summary 예외: {e}")


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
                    help="활성 계정 ADMIN 여부 (1=소스 수정+재시작 전체 반영 / 0=프로필 한정 샌드박스)")
    ap.add_argument("--delegated", type=str, default="0", choices=["0", "1"],
                    help="1=운용지원실장이 위임한 코딩 지시 (팀장은 지시 그대로만 구현)")
    args = ap.parse_args()
    # default-deny: 인자 없거나 '1'이 아니면 전부 비관리자.
    asyncio.run(run(args.cycle_id, args.manual, role=args.role,
                    actor_uid=args.actor_user, actor_admin=(args.actor_admin == "1"),
                    delegated=(args.delegated == "1")))


if __name__ == "__main__":
    main()
