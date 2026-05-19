"""
NPS Swarm v1.0 - Coresight RAG Tool
Real-time knowledge retrieval from ArcAI.ve Coresight documents.
Agents call query_coresight() at runtime to retrieve contextual knowledge.

보안 게이트 (TASK A — Implementation.md §3.2):
  query_coresight() 는 ADMIN(hh09080) 전용 지식원으로 게이트됨.
  비관리자·활성 계정 불명 시 '[Coresight] 비활성' 빈 결과 반환(fail-soft).
  에이전트 루프를 절대 크래시하지 않는다.
"""
import json
import os
import glob
import logging
from typing import Optional

logger = logging.getLogger("CORESIGHT_RAG")

# ── Admin 전용 게이트 ──────────────────────────────────────────────────────────
_DENY_RESULT = "[Coresight] 비활성 — 이 계정에서는 Coresight 조회를 사용할 수 없습니다."


def _get_active_uid() -> Optional[int]:
    """활성 계정 uid 반환. 실패 시 None (default-deny). main_swarm._active_actor() 와
    동일 패턴 — infra.credentials.current() 가 단일 진실원."""
    try:
        from infra import credentials as _creds
        act = _creds.current()
        return act.get("user_id")
    except Exception as e:
        logger.debug("Coresight 게이트: 활성 계정 조회 실패 — 거부 처리: %s", e)
        return None


def _is_admin_active() -> bool:
    """활성 계정이 ADMIN 인지 확인. 실패·None → False (deny-by-default, fail-soft)."""
    try:
        uid = _get_active_uid()
        if uid is None:
            return False
        from infra.auth_store import is_admin
        return bool(is_admin(uid))
    except Exception as e:
        logger.debug("Coresight 게이트: admin 확인 실패 — 거부 처리: %s", e)
        return False


async def query_coresight(query: str, top_k: int = 5) -> str:
    """
    Search Coresight knowledge base files in real-time.
    Performs keyword-based search across all Coresight JSON log files.

    Admin-only gate: 비관리자 또는 활성 계정 불명 시 빈/거부 문자열 반환.
    에이전트 루프에서 예외를 올리지 않는다(fail-soft).

    Args:
        query: Search query text (Korean or English)
        top_k: Maximum number of results to return

    Returns:
        Formatted string of relevant knowledge snippets, or denial message for non-admin.
    """
    # ── ADMIN 전용 게이트 — deny-by-default, fail-soft ──────────────────────────
    # 예외도 포함해 fail-soft: 어떤 오류가 나도 에이전트 루프를 죽이지 않는다.
    try:
        _admin = _is_admin_active()
    except Exception as _e:
        logger.info("Coresight 게이트 오류 — 거부 처리(fail-soft): %s", _e)
        _admin = False
    if not _admin:
        logger.info("Coresight 조회 거부 — 비관리자/계정 불명 (fail-soft 반환)")
        return _DENY_RESULT

    # ── Admin 경로: 실제 RAG 수행 ─────────────────────────────────────────────
    try:
        from config import CORESIGHT_PATH
    except Exception as e:
        logger.warning("Coresight: CORESIGHT_PATH 로드 실패 — 빈 결과 반환: %s", e)
        return f"[Coresight] 설정 오류 — CORESIGHT_PATH 로드 실패."

    results = []
    query_lower = query.lower()
    query_terms = [t.strip() for t in query_lower.split() if len(t.strip()) > 1]

    json_files = glob.glob(os.path.join(CORESIGHT_PATH, "*.json"))

    for filepath in json_files:
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)

            filename = os.path.basename(filepath)
            content_str = json.dumps(data, ensure_ascii=False).lower()

            score = sum(1 for term in query_terms if term in content_str)
            if score > 0:
                # Extract title from filename
                title = filename.replace(".json", "").split("_", 1)[-1] if "_" in filename else filename

                # Extract summary if available
                summary = ""
                if isinstance(data, dict):
                    summary = data.get("summary", data.get("content", ""))[:500]
                elif isinstance(data, list) and len(data) > 0:
                    first = data[0]
                    if isinstance(first, dict):
                        summary = first.get("summary", first.get("content", ""))[:500]

                results.append({
                    "title": title,
                    "score": score,
                    "summary": summary,
                    "path": filepath
                })
        except Exception:
            continue

    results.sort(key=lambda x: x["score"], reverse=True)
    top_results = results[:top_k]

    if not top_results:
        return f"[Coresight] '{query}'에 대한 관련 지식을 찾지 못했습니다."

    output_lines = [f"[Coresight 검색 결과] 쿼리: '{query}'\n"]
    for i, r in enumerate(top_results, 1):
        output_lines.append(f"  {i}. 📄 {r['title']} (관련도: {r['score']})")
        if r["summary"]:
            output_lines.append(f"     요약: {r['summary'][:200]}...")
        output_lines.append("")

    return "\n".join(output_lines)
