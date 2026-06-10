"""Small async client for the official DeepSeek API."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

import aiohttp

logger = logging.getLogger("DEEPSEEK")

DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# 사장 지시 2026-06-10 (공식 문서 정독 반영): 동시성 초과/서버부하/타임아웃은 백오프 재시도한다.
# 문서 근거 — 429(Rate Limit, pro 동시성 500/flash 2500), 500(Server Error), 503(Overloaded)은
# "잠시 후 재시도" 권고. 400/401/402(잔액)/422 는 영구오류라 재시도 무의미 → 즉시 실패.
_RETRYABLE_STATUS = (429, 500, 503)
_MAX_ATTEMPTS = 3


class DeepSeekAPIError(RuntimeError):
    pass


async def chat_completion(
    *,
    api_key: str,
    model: str,
    messages: list[dict],
    max_tokens: int,
    temperature: float = 0.3,
    timeout_sec: int = 300,
    thinking: Optional[bool] = None,
    response_format: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not (api_key or "").strip():
        raise DeepSeekAPIError("DEEPSEEK_API_KEY가 없습니다.")
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": int(max_tokens),
        "temperature": float(temperature),
        "stream": False,
    }
    if thinking is not None:
        payload["thinking"] = {"type": "enabled" if thinking else "disabled"}
    if response_format:
        payload["response_format"] = response_format
    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json",
    }
    url = f"{DEEPSEEK_BASE_URL}/chat/completions"

    last_err: Optional[str] = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            timeout = aiohttp.ClientTimeout(total=timeout_sec)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        if not isinstance(data, dict) or not data.get("choices"):
                            raise DeepSeekAPIError(
                                f"DeepSeek 응답 형식 오류: {str(data)[:300]}")
                        return data
                    body = await response.text()
                    last_err = f"HTTP {response.status}: {body[:300]}"
                    # 영구오류 또는 마지막 시도 → 즉시 실패
                    if response.status not in _RETRYABLE_STATUS:
                        raise DeepSeekAPIError(f"DeepSeek HTTP {response.status}: {body[:600]}")
                    if attempt >= _MAX_ATTEMPTS - 1:
                        raise DeepSeekAPIError(
                            f"DeepSeek HTTP {response.status} ({_MAX_ATTEMPTS}회 재시도 실패): {body[:300]}")
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            last_err = f"{type(e).__name__}: {str(e)[:200]}"
            if attempt >= _MAX_ATTEMPTS - 1:
                raise DeepSeekAPIError(
                    f"DeepSeek 연결/timeout 실패({_MAX_ATTEMPTS}회): {last_err}")
        # 재시도 — 백오프(1.5/3/6s) 후 다음 시도
        _wait = 1.5 * (2 ** attempt)
        logger.warning("DeepSeek 재시도 %d/%d (%s) — %.1fs 후 재요청",
                       attempt + 1, _MAX_ATTEMPTS, last_err, _wait)
        await asyncio.sleep(_wait)
    raise DeepSeekAPIError(f"DeepSeek 호출 실패: {last_err}")


def response_text(data: Dict[str, Any]) -> str:
    choice = (data.get("choices") or [{}])[0] or {}
    message = choice.get("message") or {}
    return str(message.get("content") or "").strip()
