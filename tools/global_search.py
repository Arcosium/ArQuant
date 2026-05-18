"""
ArQuant — Deep Research Module (사장 피드백 2026-05-15 8차)

이전 Tavily 기반 검색 모듈에서 `alibaba/tongyi-deepresearch-30b-a3b` 모델 기반으로 전환.
이 모델은 검색·합성을 내부적으로 수행하는 deep-research agent로, 별도의 검색 API 키 없이
OpenRouter를 통해 호출하면 시황·정책·심리·수급 등의 종합 분석을 직접 합성해 반환합니다.

용도:
- 전략리서치팀장(매크로 분석가)이 매크로 분석 직전 호출
- 세션별(KR/US/공통) 시황·정책·심리 종합 검색

호환성:
- 이전 함수명 `tavily_global_search`는 제거됨 (모든 호출처에서 deep_research로 교체)
"""
from __future__ import annotations
import asyncio
import logging
import re
from typing import Optional

import aiohttp

logger = logging.getLogger("RESEARCH")


async def deep_research(query: str, max_tokens: int = 3000, model: Optional[str] = None,
                        timeout_sec: int = 120) -> str:
    """alibaba/tongyi-deepresearch로 매크로 리서치 합성.

    Args:
        query: 자연어 리서치 질문 (예: "현재 한국 증시 외국인 수급과 한은 정책 전망은?")
        max_tokens: 응답 토큰 한도 — alibaba는 reasoning 모델이라 여유 있게
        model: 모델 오버라이드 (None이면 config의 macro_researcher 사용)
        timeout_sec: 단일 호출 타임아웃

    Returns:
        합성된 리서치 응답 (가격·수치는 포함 가능하나 호출처에서 인용 가이드 부여).
        실패 시 빈 문자열 — fail-open (매크로 분석은 지수+뉴스만으로도 진행).
    """
    from config import (OPENROUTER_API_KEY, OPENROUTER_BASE_URL,
                        MODEL_ASSIGNMENTS, AGENT_MAX_TOKENS)
    if not OPENROUTER_API_KEY:
        logger.error("OPENROUTER_API_KEY 없음 — deep_research 호출 불가")
        return ""
    if model is None:
        model = MODEL_ASSIGNMENTS.get("macro_researcher",
                                       "alibaba/tongyi-deepresearch-30b-a3b")
    # AGENT_MAX_TOKENS 우선
    cfg_tok = AGENT_MAX_TOKENS.get("macro_researcher", max_tokens) or max_tokens
    max_tokens = max(int(max_tokens), int(cfg_tok))

    system = (
        "당신은 금융 시장 리서치 전문가입니다. 사용자가 묻는 매크로 주제에 대해 "
        "최신 데이터·정책·시장 심리·수급 흐름을 종합해 한국어로 명확하게 답하십시오. "
        "JSON·표·마크다운 강조 사용 금지, 줄글 + '-' 불릿만 사용. "
        "구체 출처가 있으면 인용 (예: 'WSJ 보도', 'OPEC 5/14 회의')."
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": query},
        ],
        "max_tokens": max_tokens, "temperature": 0.2,
    }
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}",
               "Content-Type": "application/json",
               "HTTP-Referer": "https://arquant.ai-ve.uk",
               "X-Title": "ArQuant-DeepResearch"}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout_sec)) as s:
            async with s.post(f"{OPENROUTER_BASE_URL}/chat/completions",
                              json=payload, headers=headers) as r:
                if r.status != 200:
                    txt = await r.text()
                    logger.warning(f"deep_research HTTP {r.status}: {txt[:300]}")
                    return ""
                data = await r.json()
    except asyncio.TimeoutError:
        logger.warning(f"deep_research 타임아웃 ({timeout_sec}s)")
        return ""
    except Exception as e:
        logger.warning(f"deep_research 예외: {e}")
        return ""

    # 응답 추출 — alibaba는 reasoning 모델이라 content가 비고 reasoning에 답이 들어갈 수 있음
    try:
        msg = (data.get("choices", [{}])[0] or {}).get("message", {}) or {}
        reply = (msg.get("content") or "").strip()
        if not reply:
            reply = (msg.get("reasoning") or "").strip()
        # reasoning에서 답변 부분만 추출하려는 시도 (긴 사고 텍스트일 수 있음)
        # 마지막 결론부 위주로 자르기 — '결론' / 'Conclusion' / 마지막 5-10줄
        if reply and len(reply) > max_tokens * 2:
            reply = reply[-int(max_tokens * 1.5):]
        return reply
    except Exception as e:
        logger.warning(f"deep_research 응답 파싱 예외: {e}")
        return ""
