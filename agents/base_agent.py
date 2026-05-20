"""
NPS Swarm v1.0 - Base Agent
OpenRouter-powered agent base class with dynamic model allocation.
"""
import json
import logging
import time
import aiohttp
from typing import List, Dict, Any, Optional, Callable

from infra.error_log import record_error  # 사장 지시 2026-05-19 — 상세 에러를 운용지원실장이 열람 가능하게

logger = logging.getLogger("AGENT")

# 사장 피드백 2026-05-15: API 비용 추적 — OpenRouter usage 필드를 모델별 단가에 곱해 추정.
# 단가는 대략적인 OpenRouter 공시가 (input/output USD per 1M tokens). 정확도는 ±20% 수준.
_MODEL_PRICING = {  # (input $/M, output $/M)
    "moonshotai/kimi-k2.6":          (0.73, 3.49),
    "deepseek/deepseek-v4-flash":    (0.10, 0.30),
    "deepseek/deepseek-v4-pro":      (0.50, 1.50),
    "xiaomi/mimo-v2.5-pro":          (0.40, 1.20),
    "openrouter/free":                (0.00, 0.00),
    "openrouter/pareto-code":         (0.40, 1.20),
}
_DEFAULT_PRICING = (0.50, 1.50)  # 모르는 모델은 보수적으로 deepseek-pro 단가로 추정

_API_CALL_LOG: List[Dict] = []   # {ts, model, prompt_tokens, completion_tokens, cost_usd, agent}
_API_LOG_CAP = 500

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


class BaseAgent:
    """
    Base agent class that communicates via OpenRouter API.
    Each agent has a persona, system prompt, assigned LLM model, and tool set.
    """

    def __init__(
        self,
        name: str,
        role: str,
        system_prompt: str,
        model_key: str = "quant_analyst",
        tools: Optional[List[Dict[str, Any]]] = None,
    ):
        from config import (OPENROUTER_API_KEY, OPENROUTER_BASE_URL, MODEL_ASSIGNMENTS,
                            AGENT_MAX_TOKENS, ENABLE_PROMPT_CACHE, AGENT_HISTORY_TURNS)
        self.name = name
        self.role = role
        self.system_prompt = system_prompt
        self.model = MODEL_ASSIGNMENTS.get(model_key, "deepseek/deepseek-v4-flash")
        self.api_key = OPENROUTER_API_KEY
        self.base_url = OPENROUTER_BASE_URL
        self.tools = tools or []
        self.conversation_history: List[Dict[str, str]] = []
        self._tool_functions: Dict[str, Callable] = {}
        # ── Cost-reduction knobs ──────────────────────────────────────────
        self.max_tokens = AGENT_MAX_TOKENS.get(model_key, AGENT_MAX_TOKENS.get(role, 2048))
        self.history_turns = AGENT_HISTORY_TURNS
        # Anthropic prompt caching (cache_control) only applies to Anthropic-routed models
        self.use_prompt_cache = bool(ENABLE_PROMPT_CACHE) and self.model.startswith("anthropic/")

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
        # System prompt — mark as a cache breakpoint for Anthropic prompt caching.
        # (For non-Anthropic models we keep the plain-string form to avoid surprises.)
        if self.use_prompt_cache:
            messages = [{"role": "system", "content": [
                {"type": "text", "text": self.system_prompt, "cache_control": {"type": "ephemeral"}}
            ]}]
        else:
            messages = [{"role": "system", "content": self.system_prompt}]

        # Add trailing conversation history only (was: last 10 messages → now last N, default 3)
        for h in self.conversation_history[-self.history_turns:]:
            messages.append(h)

        user_content = message
        if context:
            user_content = f"[컨텍스트]\n{context}\n\n[메시지]\n{message}"

        messages.append({"role": "user", "content": user_content})

        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "model": self.model,
                    "messages": messages,
                    "max_tokens": self.max_tokens,
                    "temperature": 0.3,
                }

                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://arquant.ai-ve.uk",
                    "X-Title": f"NPS-Swarm-{self.name}",
                }

                async with session.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                    # 2026-05-19: reasoning 모델(kimi-k2.6 등)은 대형 max_tokens에서
                    # 응답에 분 단위가 걸린다 — 120s론 timeout. ceiling이므로 빠른
                    # 비-reasoning 에이전트엔 영향 없음(즉시 반환).
                    timeout=aiohttp.ClientTimeout(total=300),
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        record_error(self.name, context=(
                            f"OpenRouter HTTP {resp.status} | model={self.model} | "
                            f"resp={error_text[:600]}"))
                        return f"[{self.name} 에러] API 호출 실패: {resp.status}"

                    data = await resp.json()

            # 사장 피드백 2026-05-15: 가끔 OpenRouter가 빈 JSON/None을 돌려줘서 'NoneType has no attribute get' 예외 발생.
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
                    async with aiohttp.ClientSession() as _s2:
                        async with _s2.post(
                            f"{self.base_url}/chat/completions",
                            json={**payload, "max_tokens": _retry_max},
                            headers=headers,
                            timeout=aiohttp.ClientTimeout(total=300),
                        ) as _r2:
                            if _r2.status == 200:
                                _d2 = await _r2.json()
                                _c2 = _d2.get("choices") or []
                                if _c2:
                                    _r2c = (_c2[0] or {}).get("message", {}).get("content", "") or ""
                                    if _r2c.strip():
                                        reply = _r2c
                                        data = _d2  # 비용/사용량도 재시도분으로 갱신
                                        logger.info(f"[{self.name}] 빈응답 재시도 성공 "
                                                    f"({len(reply)} chars, max_tokens={_retry_max})")
                            else:
                                record_error(self.name, context=(
                                    f"빈응답 재시도 실패 HTTP {_r2.status} | model={self.model} | "
                                    f"max_tokens={_retry_max}"))
                except Exception as _re:
                    record_error(self.name, _re, context=(
                        f"빈응답 재시도 예외 | model={self.model} | max_tokens={_retry_max}"))

            # 사장 피드백 2026-05-15: API 사용량 → 비용 추정 → 글로벌 로그 적재
            try:
                _usage = (data.get("usage") or {})
                _pt = int(_usage.get("prompt_tokens", 0) or 0)
                _ct = int(_usage.get("completion_tokens", 0) or 0)
                _in_rate, _out_rate = _MODEL_PRICING.get(self.model, _DEFAULT_PRICING)
                _cost = (_pt / 1_000_000.0) * _in_rate + (_ct / 1_000_000.0) * _out_rate
                _API_CALL_LOG.append({"ts": time.time(), "model": self.model, "agent": self.name,
                                      "prompt_tokens": _pt, "completion_tokens": _ct, "cost_usd": _cost})
                if len(_API_CALL_LOG) > _API_LOG_CAP:
                    del _API_CALL_LOG[:len(_API_CALL_LOG) - _API_LOG_CAP]
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
            record_error(self.name, e, context=f"think() | model={self.model}")
            return f"[{self.name} 에러] {type(e).__name__}: {str(e) or repr(e)}"

    def reset_history(self):
        self.conversation_history.clear()

    def __repr__(self):
        return f"<Agent:{self.name} role={self.role} model={self.model}>"
