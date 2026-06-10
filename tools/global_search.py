"""Macro research through Hermes Agent with DeepSeek and web-only tools."""
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
    """Let DeepSeek use Hermes' web_search/web_extract tools for macro research."""
    from config import DEEPSEEK_API_KEY
    from infra.admin_config import resolve_model

    key = (api_key or DEEPSEEK_API_KEY or "").strip()
    if not key:
        logger.error("DEEPSEEK_API_KEY 없음 — Hermes 리서치 호출 불가")
        return ""
    # web 도구는 tool-calling 지원 모델 필요 — flash 고정(pro=reasoning은 도구 미장착, 2026-06-10).
    selected_model = model or resolve_model("macro_researcher", "deepseek-v4-flash")
    prompt = (
        "당신은 금융시장 리서치 전문가입니다. web_search와 web_extract를 사용해 "
        "최신 근거를 확인한 뒤 한국어로 답하세요. 출처의 기관/매체와 날짜를 명시하고, "
        "검색하지 않은 사실을 최신 정보처럼 단정하지 마세요.\n\n" + query
    )
    argv = [
        HERMES_BIN, "-p", HERMES_PROFILE, "chat", "-q", prompt, "-Q",
        "--provider", "deepseek", "--model", selected_model,
        "--toolsets", "web", "--max-turns", "12", "--ignore-rules",
        "--source", "tool",
    ]
    env = os.environ.copy()
    env["DEEPSEEK_API_KEY"] = key
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
