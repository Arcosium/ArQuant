"""Macro research through Hermes Agent backed by the local LLM and web-only tools."""
from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger("RESEARCH")
HERMES_BIN = os.getenv("HERMES_BIN", str(Path.home() / ".local/bin/hermes"))
HERMES_PROFILE = os.getenv("ARQUANT_HERMES_PROFILE", "arcai-swarm")
_SID_RE = re.compile(r"^session_id:\s*(\S+)\s*$")


async def deep_research(
    query: str,
    max_tokens: int = 8000,
    model: Optional[str] = None,
    timeout_sec: int = 180,
    api_key: Optional[str] = None,
) -> str:
    """Let the active LLM use Hermes' web_search/web_extract tools for macro research.

    Hermes에는 로컬 OpenAI 호환 서버를 공급자로 전달한다. web 도구(tool-calling)를 써야 하므로
    reasoning OFF 평문 모델명을 전달한다. ``api_key``는 이전 호출부 호환용이며 무시한다."""
    from config import LOCAL_LLM_BASE_URL, LOCAL_LLM_MODEL
    from infra.admin_config import resolve_model
    from infra.deepseek_client import split_thinking

    selected_model = model or resolve_model("macro_researcher", LOCAL_LLM_MODEL)
    real_model, _ = split_thinking(selected_model)   # hermes 엔 평문 슬러그 전달
    provider = "openai"
    prompt = (
        "당신은 금융시장 리서치 전문가입니다. web_search와 web_extract를 사용해 "
        "최신 근거를 확인한 뒤 한국어로 답하세요. 출처의 기관/매체와 날짜를 명시하고, "
        "검색하지 않은 사실을 최신 정보처럼 단정하지 마세요.\n\n" + query
    )
    argv = [
        HERMES_BIN, "-p", HERMES_PROFILE, "chat", "-q", prompt, "-Q",
        "--provider", provider, "--model", real_model,
        "--toolsets", "web", "--max-turns", "12", "--ignore-rules",
        "--source", "tool",
    ]
    env = os.environ.copy()
    env["OPENAI_BASE_URL"] = LOCAL_LLM_BASE_URL
    env["OPENAI_API_KEY"] = ""
    env["HERMES_MAX_TOKENS"] = str(max(512, int(max_tokens)))
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        logger.warning("Hermes 리서치 시간 초과: %ss", timeout_sec)
        return ""
    except Exception as exc:
        logger.warning("Hermes 리서치 실행 실패: %s", exc)
        return ""
    if proc.returncode != 0:
        logger.warning("Hermes 리서치 종료코드 %s: %s", proc.returncode, stderr.decode(errors="replace")[:500])
        return ""
    lines = []
    output = stdout.decode(errors="replace")
    error_output = stderr.decode(errors="replace")
    for line in output.splitlines():
        if _SID_RE.match(line.strip()) or line.lstrip().startswith("↻ "):
            continue
        lines.append(line)
    result = "\n".join(lines).strip()
    if not result:
        logger.warning("Hermes 리서치가 빈 응답을 반환했습니다: %s", error_output[:300])
    return result
