"""
NPS Swarm v1.0 - Base Agent
Local-LLM-powered agent base class with dynamic model allocation.
"""
import json
import logging
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable

from infra.error_log import record_error  # 사장 지시 2026-05-19 — 상세 에러를 운용지원실장이 열람 가능하게

logger = logging.getLogger("AGENT")
KST = timezone(timedelta(hours=9))

# 로컬 GGUF 서버는 호출당 외부 API 비용이 없다.
_MODEL_PRICING: Dict[str, tuple[float, float]] = {}
_DEFAULT_PRICING = (0.0, 0.0)

_API_CALL_LOG: List[Dict] = []   # {ts, model, prompt_tokens, completion_tokens, cost_usd, agent}
_API_LOG_CAP = 500

# 사장 지시 2026-05-21: 비용 표시를 시간(/h)·일(/d)·월(/m)·총누적 으로 선택 가능하게.
#   • /h(회전식 최근 1시간)는 위 인메모리 _API_CALL_LOG 로 충분(재시작 시 0부터).
#   • /d·/m·총누적은 재시작에도 살아남아야 하므로 일/월/누적 버킷을 롤업 파일에 O(1) 갱신.
#     (매 폴링마다 거대한 로그를 재집계하지 않도록 — base_agent 는 서버 프로세스 단일 writer.)
_COST_ROLLUP_PATH = Path(__file__).resolve().parent.parent / "data" / "api_cost_rollup.json"
_COST_DAY_KEEP = 90  # 일 버킷 보존 일수 (월/누적은 항상 보존)
_cost_rollup: Optional[Dict[str, Any]] = None  # 지연 로드 캐시 (None=미로드)


def get_api_cost_since(epoch_secs: float) -> float:
    """epoch_secs 이후의 누적 API 비용(USD)."""
    return sum(e.get("cost_usd", 0.0) for e in _API_CALL_LOG if e.get("ts", 0) >= epoch_secs)

def get_api_cost_last_cycle(seconds_back: float = 3600.0) -> Dict[str, Any]:
    """직전 사이클(또는 seconds_back초 동안)의 API 비용·호출 수."""
    cut = time.time() - seconds_back
    calls = [e for e in _API_CALL_LOG if e.get("ts", 0) >= cut]
    return {"cost_usd": sum(e.get("cost_usd", 0.0) for e in calls),
            "calls": len(calls),
            "window_sec": seconds_back}

def reset_api_cost_log():
    _API_CALL_LOG.clear()


# ── 영속 롤업 (일/월/누적) ──────────────────────────────────────────────────────

def _load_cost_rollup() -> Dict[str, Any]:
    base = {"total": {"usd": 0.0, "calls": 0}, "day": {}, "month": {}}
    try:
        if _COST_ROLLUP_PATH.exists():
            d = json.loads(_COST_ROLLUP_PATH.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                base["total"] = d.get("total") or base["total"]
                base["day"] = d.get("day") or {}
                base["month"] = d.get("month") or {}
    except Exception as e:
        logger.warning("api_cost_rollup 로드 실패: %s", e)
    return base


def _save_cost_rollup() -> None:
    try:
        _COST_ROLLUP_PATH.parent.mkdir(parents=True, exist_ok=True)
        _COST_ROLLUP_PATH.write_text(json.dumps(_cost_rollup, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.warning("api_cost_rollup 저장 실패: %s", e)


def _ensure_rollup_loaded() -> None:
    global _cost_rollup
    if _cost_rollup is None:
        _cost_rollup = _load_cost_rollup()


def _reset_cost_rollup() -> None:
    """인메모리 롤업 캐시 해제 → 다음 접근 시 파일에서 재로드 (테스트·재로드용)."""
    global _cost_rollup
    _cost_rollup = None


def _bump_rollup(cost_usd: float, ts: float) -> None:
    _ensure_rollup_loaded()
    dt = datetime.fromtimestamp(ts, KST)
    day, mon = dt.strftime("%Y-%m-%d"), dt.strftime("%Y-%m")
    r = _cost_rollup
    r["total"]["usd"] = round(r["total"]["usd"] + cost_usd, 8)
    r["total"]["calls"] += 1
    d = r["day"].setdefault(day, {"usd": 0.0, "calls": 0})
    d["usd"] = round(d["usd"] + cost_usd, 8); d["calls"] += 1
    m = r["month"].setdefault(mon, {"usd": 0.0, "calls": 0})
    m["usd"] = round(m["usd"] + cost_usd, 8); m["calls"] += 1
    if len(r["day"]) > _COST_DAY_KEEP:
        for k in sorted(r["day"])[:-_COST_DAY_KEEP]:
            r["day"].pop(k, None)
    _save_cost_rollup()


def _record_api_call(model: str, agent: str, prompt_tokens: int, completion_tokens: int,
                     ts: Optional[float] = None, uid: Optional[int] = None) -> float:
    """Local LLM usage → 모델 단가로 비용 추정 → 회전식 로그 + 영속 롤업에 적재."""
    ts = ts if ts is not None else time.time()
    in_rate, out_rate = _MODEL_PRICING.get(model, _DEFAULT_PRICING)
    cost = (prompt_tokens / 1_000_000.0) * in_rate + (completion_tokens / 1_000_000.0) * out_rate
    _API_CALL_LOG.append({"ts": ts, "model": model, "agent": agent, "uid": uid,
                          "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                          "cost_usd": cost})
    if len(_API_CALL_LOG) > _API_LOG_CAP:
        del _API_CALL_LOG[:len(_API_CALL_LOG) - _API_LOG_CAP]
    _bump_rollup(cost, ts)
    return cost


def cost_summary() -> Dict[str, Any]:
    """우상단 표시용 — 시간(/h, 회전식)·일(/d)·월(/m)·총누적 비용·호출 수."""
    _ensure_rollup_loaded()
    now = time.time()
    cut = now - 3600.0
    hcalls = [e for e in _API_CALL_LOG if e.get("ts", 0) >= cut]
    dt = datetime.fromtimestamp(now, KST)
    r = _cost_rollup
    d = r["day"].get(dt.strftime("%Y-%m-%d"), {"usd": 0.0, "calls": 0})
    m = r["month"].get(dt.strftime("%Y-%m"), {"usd": 0.0, "calls": 0})
    return {
        "h": {"usd": sum(e.get("cost_usd", 0.0) for e in hcalls), "calls": len(hcalls)},
        "d": {"usd": d["usd"], "calls": d["calls"]},
        "m": {"usd": m["usd"], "calls": m["calls"]},
        "total": {"usd": r["total"]["usd"], "calls": r["total"]["calls"]},
    }


class BaseAgent:
    """
    Base agent class that communicates via the local OpenAI-compatible LLM server.
    Each agent has a persona, system prompt, assigned LLM model, and tool set.
    """

    def __init__(
        self,
        name: str,
        role: str,
        system_prompt: str,
        model_key: str = "quant_analyst",
        tools: Optional[List[Dict[str, Any]]] = None,
        injection: Optional[Dict[str, Any]] = None,
    ):
        from config import MODEL_ASSIGNMENTS, AGENT_MAX_TOKENS, AGENT_HISTORY_TURNS
        self.name = name
        self.role = role
        self.system_prompt = system_prompt
        inj = injection or {}
        self.uid = inj.get("uid")
        # Per-uid model override wins; else admin global override; else config default.
        _ov = (inj.get("model_overrides") or {}).get(model_key) or ""
        if not _ov:
            try:
                from infra import admin_config
                _ov = admin_config.get_model_override(model_key)
            except Exception:
                _ov = ""
        self.model = _ov or MODEL_ASSIGNMENTS.get(model_key, "")
        # Local server is unauthenticated. Keep the attribute for call-site compatibility.
        self.api_key = ""
        self.tools = tools or []
        self.conversation_history: List[Dict[str, str]] = []
        self._tool_functions: Dict[str, Callable] = {}
        # ── Cost-reduction knobs ──────────────────────────────────────────
        self.max_tokens = AGENT_MAX_TOKENS.get(model_key, AGENT_MAX_TOKENS.get(role, 2048))
        self.history_turns = AGENT_HISTORY_TURNS

    def register_tool_function(self, name: str, func: Callable):
        """Register a callable function that can be invoked as a tool."""
        self._tool_functions[name] = func

    async def think(self, message: str, context: Optional[str] = None) -> str:
        """
        Process a message and return the agent's response.

        Args:
            message: Input message from another agent or orchestrator
            context: Optional additional context (e.g., market data)

        Returns:
            Agent's response text
        """
        messages = [{"role": "system", "content": self.system_prompt}]

        # Add trailing conversation history only (was: last 10 messages → now last N, default 3)
        for h in self.conversation_history[-self.history_turns:]:
            messages.append(h)

        user_content = message
        if context:
            user_content = f"[컨텍스트]\n{context}\n\n[메시지]\n{message}"

        messages.append({"role": "user", "content": user_content})

        try:
            from infra.local_llm_client import chat_completion
            data = await chat_completion(
                api_key=self.api_key, model=self.model, messages=messages,
                max_tokens=self.max_tokens, temperature=0.3, timeout_sec=300,
            )

            # 공급자가 빈 JSON/None을 반환해도 명시적 오류로 변환한다.
            # 방어 코드로 비정상 응답을 빈 reply + 명시적 에러 메시지로 변환.
            if not data or not isinstance(data, dict):
                logger.error(f"[{self.name}] 비정상 응답 (빈 JSON 또는 dict 아님): {str(data)[:200]}")
                return f"[{self.name} 에러] API 응답 비어있음 (재시도 필요)"
            _choices = data.get("choices") or []
            if not _choices or not isinstance(_choices, list):
                logger.error(f"[{self.name}] choices 비어있음: {str(data)[:200]}")
                return f"[{self.name} 에러] API 응답에 choices 없음 (재시도 필요)"
            reply = (_choices[0] or {}).get("message", {}).get("content", "") or ""

            # 디버그+복원력 2026-05-19: HTTP 200인데 content가 비어있는 경우
            # (주로 finish_reason="length" — 큰 프롬프트 + 작은 max_tokens; 전략리서치팀장
            # 0-char 사례). finish_reason/usage를 남겨 원인을 진단하고, max_tokens를
            # 키워 1회 재시도한다. (캐시 오염 수정과 함께 — 빈 응답이 캐시되면 안 됨.)
            if not (reply or "").strip():
                _fr = (_choices[0] or {}).get("finish_reason")
                _u0 = data.get("usage") or {}
                logger.warning(
                    f"[{self.name}] 빈 응답 (status=200, finish_reason={_fr}, "
                    f"prompt_tok={_u0.get('prompt_tokens')}, completion_tok={_u0.get('completion_tokens')}, "
                    f"max_tokens={self.max_tokens}) → max_tokens 상향 후 1회 재시도")
                # ceiling 16000 — 큰 base(예: kimi 12000)에서도 재시도가 'base보다
                # 큰' 진짜 상향이 되도록 (8000이면 base 12000보다 작아 무의미했음).
                _retry_max = min(max(self.max_tokens * 2, 4000), 16000)
                try:
                    _d2 = await chat_completion(
                        api_key=self.api_key, model=self.model, messages=messages,
                        max_tokens=_retry_max, temperature=0.3, timeout_sec=300,
                    )
                    _c2 = _d2.get("choices") or []
                    if _c2:
                        _r2c = (_c2[0] or {}).get("message", {}).get("content", "") or ""
                        if _r2c.strip():
                            reply = _r2c
                            data = _d2
                            logger.info(f"[{self.name}] 빈응답 재시도 성공 "
                                        f"({len(reply)} chars, max_tokens={_retry_max})")
                except Exception as _re:
                    record_error(self.name, _re, context=(
                        f"빈응답 재시도 예외 | model={self.model} | max_tokens={_retry_max}"), uid=self.uid)

            # 사장 피드백 2026-05-15: API 사용량 → 비용 추정 → 회전식 로그 + 영속 롤업 적재
            try:
                _usage = (data.get("usage") or {})
                _pt = int(_usage.get("prompt_tokens", 0) or 0)
                _ct = int(_usage.get("completion_tokens", 0) or 0)
                _record_api_call(self.model, self.name, _pt, _ct, uid=self.uid)
            except Exception as _ue:
                # 클린코드 2026-05-19: 비용 추적 실패가 debug라 운영에서 안 보였음 → warning 승격.
                logger.warning(f"[{self.name}] 사용량 추적 실패: {type(_ue).__name__}: {_ue!r}")

            # Update history (bounded — only the last few exchanges are ever resent anyway)
            self.conversation_history.append({"role": "user", "content": user_content})
            self.conversation_history.append({"role": "assistant", "content": reply})
            if len(self.conversation_history) > max(self.history_turns * 2, 6):
                self.conversation_history = self.conversation_history[-(self.history_turns * 2):]

            logger.info(f"[{self.name}] 응답 생성 완료 ({len(reply)} chars, model: {self.model})")
            return reply

        except Exception as e:
            # 사장 지시 2026-05-19: TimeoutError 등 str(e)='' 인 예외도 타입+repr+트레이스백을
            # 남겨 운용지원실장이 '어디서 왜' 났는지 진단할 수 있게 한다.
            record_error(self.name, e, context=f"think() | model={self.model}", uid=self.uid)
            return f"[{self.name} 에러] {type(e).__name__}: {str(e) or repr(e)}"

    def reset_history(self):
        self.conversation_history.clear()

    def __repr__(self):
        return f"<Agent:{self.name} role={self.role} model={self.model}>"
