"""Macro research via the server-controlled browser pipeline (ArachneControl).

(2026-06-29 사장 지시) Hermes 에이전트(웹툴 서브프로세스)를 제거하고, 서버가 헤드리스
브라우저를 원격 제어해 수집하는 **Arachne 리서치 companion**(기본 127.0.0.1:8771)을 쓴다.
흐름: 질의 → companion `/research/gather`(Bing 검색 + 본문 추출) → 근거(evidence)를
로컬 LLM(OpenAI 호환)으로 한국어 합성. 외부 유료 API·Hermes 불필요. 실패는 fail-open("").
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

import aiohttp

logger = logging.getLogger("RESEARCH")

# 서버제어 브라우저 리서치 companion (ArachneControl). 로컬 전용 기본.
RESEARCH_URL = os.getenv("ARQUANT_RESEARCH_URL", "http://127.0.0.1:8771").rstrip("/")
RESEARCH_K = int(os.getenv("ARQUANT_RESEARCH_K", "6"))
RESEARCH_FETCH_K = int(os.getenv("ARQUANT_RESEARCH_FETCH_K", "4"))
# 출처별 본문 인용 상한(합성 프롬프트 비대화 방지)
_PER_SOURCE_CHARS = int(os.getenv("ARQUANT_RESEARCH_SRC_CHARS", "2500"))


async def _gather(query: str, timeout_sec: int) -> Optional[dict]:
    """Arachne companion 에서 검색+본문추출 근거를 수집한다."""
    url = f"{RESEARCH_URL}/research/gather"
    payload = {"query": query, "k": RESEARCH_K, "fetch_k": RESEARCH_FETCH_K}
    try:
        timeout = aiohttp.ClientTimeout(total=min(timeout_sec, 120))
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    logger.warning("리서치 gather HTTP %s", resp.status)
                    return None
                return await resp.json()
    except Exception as exc:
        logger.warning("리서치 gather 실패: %s", exc)
        return None


def _format_evidence(data: dict) -> str:
    """수집 근거를 출처 표기가 있는 프롬프트 블록으로 변환."""
    blocks: list[str] = []
    idx = 1
    for r in data.get("results", []) or []:
        title = (r.get("title") or "").strip()
        url = (r.get("url") or "").strip()
        content = (r.get("content") or "").strip()
        if not (content or title):
            continue
        blocks.append(
            f"[출처{idx}] {title}\nURL: {url}\n본문발췌: {content[:_PER_SOURCE_CHARS]}\n"
        )
        idx += 1
    # 본문 추출은 안 됐지만 검색에 잡힌 항목은 제목/스니펫만 보조 제공
    for r in data.get("extra", []) or []:
        title = (r.get("title") or "").strip()
        url = (r.get("url") or "").strip()
        snip = (r.get("snippet") or "").strip()
        if not title:
            continue
        blocks.append(f"[참고{idx}] {title}\nURL: {url}\n요약: {snip}\n")
        idx += 1
    return "\n".join(blocks)


async def deep_research(
    query: str,
    max_tokens: int = 8000,
    model: Optional[str] = None,
    timeout_sec: int = 180,
    api_key: Optional[str] = None,
) -> str:
    """서버제어 브라우저로 근거를 모은 뒤 로컬 LLM 으로 한국어 매크로 리서치를 합성한다.

    ``api_key``/``model`` 인자는 이전 호출부 호환용. ``model`` 미지정 시 macro_researcher
    배정 모델을 쓰되, 합성은 reasoning OFF(빠르고 빈응답 버그 회피)로 수행한다."""
    from config import LOCAL_LLM_MODEL
    from infra.admin_config import resolve_model
    from infra.local_llm_client import chat_completion, response_text, split_thinking

    data = await _gather(query, timeout_sec)
    if not data:
        return ""  # fail-open(상위에서 빈값 처리)
    evidence = _format_evidence(data)
    if not evidence.strip():
        logger.warning("리서치 근거가 비었습니다(검색/추출 0건).")
        return ""

    selected_model = model or resolve_model("macro_researcher", LOCAL_LLM_MODEL)
    real_model, _ = split_thinking(selected_model)  # 합성은 thinking OFF로 강제

    system = (
        "당신은 금융시장 리서치 전문가입니다. 아래 [수집 근거]는 방금 웹에서 수집한 "
        "최신 자료입니다. 이 근거에 기반해서만 한국어로 분석하세요. "
        "각 핵심 주장에 출처(기관/매체)와 가능하면 날짜를 명시하고, 근거에 없는 사실을 "
        "단정하지 마세요. 근거가 빈약한 부분은 '확인 불가'로 표시하세요."
    )
    user = f"[질의]\n{query}\n\n[수집 근거]\n{evidence}"

    try:
        result = await chat_completion(
            api_key="",
            model=real_model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            max_tokens=max(512, int(max_tokens)),
            temperature=0.3,
            timeout_sec=min(timeout_sec, 300),
            thinking=False,
        )
    except Exception as exc:
        logger.warning("리서치 합성(LLM) 실패: %s", exc)
        return ""
    text = response_text(result)
    if not text:
        logger.warning("리서치 합성이 빈 응답을 반환했습니다.")
    return text
