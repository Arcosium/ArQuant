"""
NPS Swarm v1.0 - Base Agent
OpenRouter-powered agent base class with dynamic model allocation.
"""
import json
import logging
import time
import aiohttp
from typing import List, Dict, Any, Optional, Callable

logger = logging.getLogger("AGENT")

# 사장 피드백 2026-05-15: API 비용 추적 — OpenRouter usage 필드를 모델별 단가에 곱해 추정.
# 단가는 대략적인 OpenRouter 공시가 (input/output USD per 1M tokens). 정확도는 ±20% 수준.
_MODEL_PRICING = {  # (input $/M, output $/M)
    "google/gemini-3.1-pro-preview": (1.25, 5.00),
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
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error(f"[{self.name}] OpenRouter 에러 {resp.status}: {error_text[:200]}")
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
                logger.debug(f"[{self.name}] 사용량 추적 실패: {_ue}")

            # Update history (bounded — only the last few exchanges are ever resent anyway)
            self.conversation_history.append({"role": "user", "content": user_content})
            self.conversation_history.append({"role": "assistant", "content": reply})
            if len(self.conversation_history) > max(self.history_turns * 2, 6):
                self.conversation_history = self.conversation_history[-(self.history_turns * 2):]

            logger.info(f"[{self.name}] 응답 생성 완료 ({len(reply)} chars, model: {self.model})")
            return reply

        except Exception as e:
            logger.error(f"[{self.name}] 예외: {e}")
            return f"[{self.name} 에러] {str(e)}"

    def reset_history(self):
        self.conversation_history.clear()

    def __repr__(self):
        return f"<Agent:{self.name} role={self.role} model={self.model}>"
