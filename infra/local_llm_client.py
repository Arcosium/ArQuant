"""Async client for the local OpenAI-compatible LLM server.

No cloud-provider API key is accepted or sent.  ``+thinking`` is an internal
model suffix that is converted to the common llama.cpp/OpenAI-compatible
thinking request fields immediately before dispatch.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger("LOCAL_LLM")

# Thinking ON 을 나타내는 가상 접미사(실제 모델명엔 없음 — 전송 전 제거).
_THINK_SUFFIX = "+thinking"

# 로컬 OpenAI 호환 서버가 재기동 중이거나 일시적으로 바쁠 수 있으므로 백오프 재시도한다.
# 400/401/402/422 같은 영구 오류는 재시도하지 않고 즉시 실패한다.
_RETRYABLE_STATUS = (429, 500, 503)
_MAX_ATTEMPTS = 3
_LOCAL_LLM_GRACE_SEC = int(os.environ.get("LOCAL_LLM_GRACE_SEC", "1800"))
_LOCAL_LLM_TIMEOUT_SEC = int(os.environ.get("LOCAL_LLM_TIMEOUT_SEC", "900"))


class LocalLLMError(RuntimeError):
    pass


# ── 슬러그/백엔드 판별 ────────────────────────────────────────────────────────

def split_thinking(model: str) -> Tuple[str, bool]:
    """'+thinking' 가상 접미사를 떼어 (실제_슬러그, reasoning_on) 으로 분리한다."""
    m = model or ""
    if m.endswith(_THINK_SUFFIX):
        return m[: -len(_THINK_SUFFIX)], True
    return m, False


# ── 요청 조립(순수 함수 — I/O 없음, 단위테스트 대상) ──────────────────────────

def build_request(
    *,
    api_key: str,
    model: str,
    messages: list[dict],
    max_tokens: int,
    temperature: float,
    thinking: Optional[bool],
    response_format: Optional[Dict[str, Any]],
) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
    """로컬 OpenAI 호환 서버의 (url, headers, payload)를 반환한다.

    ``api_key`` 인자는 기존 호출부와의 호환을 위해 남겨두지만 사용하지 않는다.
    """
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": int(max_tokens),
        "temperature": float(temperature),
        "stream": False,
    }
    from config import LOCAL_LLM_BASE_URL
    real_model, suffix_thinking = split_thinking(model)
    reasoning_on = bool(thinking) if thinking is not None else suffix_thinking
    payload["model"] = real_model
    # llama.cpp accepts chat_template_kwargs; reasoning is also understood by
    # several OpenAI-compatible servers. Sending both preserves the existing
    # thinking/non-thinking agent split without an external provider.
    payload["chat_template_kwargs"] = {"enable_thinking": reasoning_on}
    payload["reasoning"] = {"enabled": reasoning_on}
    url = f"{LOCAL_LLM_BASE_URL}/chat/completions"
    if response_format:
        payload["response_format"] = response_format
    headers = {"Content-Type": "application/json"}
    return url, headers, payload


# ── HTTP + 백오프 재시도(공유) ────────────────────────────────────────────────

def _is_local_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


async def _post_with_retry(url: str, headers: Dict[str, str],
                           payload: Dict[str, Any], timeout_sec: int) -> Dict[str, Any]:
    last_err: Optional[str] = None
    is_local = _is_local_url(url)
    grace_deadline = asyncio.get_running_loop().time() + _LOCAL_LLM_GRACE_SEC
    attempt = 0
    while True:
        try:
            effective_timeout_sec = (
                max(int(timeout_sec), _LOCAL_LLM_TIMEOUT_SEC)
                if is_local
                else int(timeout_sec)
            )
            timeout = aiohttp.ClientTimeout(total=effective_timeout_sec)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        if not isinstance(data, dict) or not data.get("choices"):
                            raise LocalLLMError(
                                f"LLM 응답 형식 오류: {str(data)[:300]}")
                        return data
                    body = await response.text()
                    last_err = f"HTTP {response.status}: {body[:300]}"
                    # 영구오류 또는 마지막 시도 → 즉시 실패
                    if response.status not in _RETRYABLE_STATUS:
                        raise LocalLLMError(f"LLM HTTP {response.status}: {body[:600]}")
                    if not is_local and attempt >= _MAX_ATTEMPTS - 1:
                        raise LocalLLMError(
                            f"LLM HTTP {response.status} ({_MAX_ATTEMPTS}회 재시도 실패): {body[:300]}")
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            last_err = f"{type(e).__name__}: {str(e)[:200]}"
            if not is_local and attempt >= _MAX_ATTEMPTS - 1:
                raise LocalLLMError(
                    f"LLM 연결/timeout 실패({_MAX_ATTEMPTS}회): {last_err}")
        attempt += 1
        if is_local:
            remaining = grace_deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise LocalLLMError(
                    f"로컬 LLM 재기동 대기 초과({_LOCAL_LLM_GRACE_SEC}s): {last_err}")
            _wait = min(1.5 * (2 ** min(attempt - 1, 5)), 15.0, remaining)
            logger.warning("로컬 LLM 대기 %d회 (%s) — %.1fs 후 재요청",
                           attempt, last_err, _wait)
        else:
            if attempt >= _MAX_ATTEMPTS:
                raise LocalLLMError(
                    f"LLM 연결/timeout 실패({_MAX_ATTEMPTS}회): {last_err}")
            _wait = 1.5 * (2 ** (attempt - 1))
            logger.warning("LLM 재시도 %d/%d (%s) — %.1fs 후 재요청",
                           attempt, _MAX_ATTEMPTS, last_err, _wait)
        await asyncio.sleep(_wait)
    raise LocalLLMError(f"LLM 호출 실패: {last_err}")


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
    url, headers, payload = build_request(
        api_key=api_key, model=model, messages=messages, max_tokens=max_tokens,
        temperature=temperature, thinking=thinking, response_format=response_format,
    )
    return await _post_with_retry(url, headers, payload, timeout_sec)


def response_text(data: Dict[str, Any]) -> str:
    choice = (data.get("choices") or [{}])[0] or {}
    message = choice.get("message") or {}
    return str(message.get("content") or "").strip()
