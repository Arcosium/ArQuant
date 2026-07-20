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


def _is_admin_active(uid: Optional[int] = None) -> bool:
    """주어진 uid 가 ADMIN 인지 확인. uid 불명·실패 → False (deny-by-default, fail-soft).

    Phase 2 멀티테넌트: 전역 활성 계정(credentials.current) 폐지 → 호출자가 uid 를
    명시적으로 넘긴다. query_coresight 는 현재 런타임 도구로 와이어링되어 있지 않고
    (프롬프트 노출은 _coresight_tool_line 이 주입 uid 로 게이트한다) uid 없이 직접
    호출되면 default-deny 다."""
    try:
        if uid is None:
            return False
        from infra.auth_store import is_admin
        return bool(is_admin(uid))
    except Exception as e:
        logger.debug("Coresight 게이트: admin 확인 실패 — 거부 처리: %s", e)
        return False


# ── 시맨틱(임베딩) 검색 — additive·fail-soft. 기본 OFF(env CORESIGHT_RAG_MODE) ──────
# CORESIGHT_RAG_MODE: keyword(기본, 기존동작) | hybrid(키워드+시맨틱 RRF) | semantic
# arcembed(:8765) 미가용·오류 시 무조건 키워드로 폴백 → 스웜 루프 절대 안 죽인다.
_CORESIGHT_VEC = None


def _arcembed():
    try:
        import arcembed
        return arcembed
    except Exception:
        import sys as _sys
        lib = os.path.expanduser("~/projects/lib")
        if lib not in _sys.path:
            _sys.path.insert(0, lib)
        try:
            import arcembed
            return arcembed
        except Exception:
            return None


def _file_text(data):
    rk = (data.get("refined_knowledge") or {}) if isinstance(data, dict) else {}
    parts = [rk.get("summary", "")] + list(rk.get("logic_points") or [])
    txt = "\n".join(p for p in parts if p).strip()
    if not txt and isinstance(data, dict):
        txt = data.get("extracted_text") or data.get("summary") or ""
    return (txt or "").strip()[:2000]


def _semantic_index(path):
    """CORESIGHT_PATH JSON → 임베딩 인덱스(모듈 캐시, 신규 파일만 증분). arcembed 없으면 None."""
    global _CORESIGHT_VEC
    ae = _arcembed()
    if ae is None:
        return None
    if _CORESIGHT_VEC is None:
        vecdb = os.path.join(os.path.dirname(path.rstrip("/")), "coresight_rag_vec.db")
        _CORESIGHT_VEC = ae.VectorIndex(vecdb)
    ix = _CORESIGHT_VEC
    todo = [fp for fp in glob.glob(os.path.join(path, "*.json")) if not ix.has(fp)]
    ids, texts, metas = [], [], []
    for fp in todo:
        try:
            d = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        t = _file_text(d)
        if not t:
            continue
        ids.append(fp)
        texts.append(t)
        metas.append({"title": os.path.basename(fp).replace(".json", ""), "path": fp})
    if texts:
        V = ae.embed(texts)
        ix.add_many([(ids[i], V[i], metas[i]) for i in range(len(ids))])
    return ix


def _rank_hybrid(query, keyword_results, path, top_k, mode):
    """키워드 결과 + 시맨틱 검색을 RRF 로 병합. 실패 시 키워드 top_k."""
    ae = _arcembed()
    ix = _semantic_index(path)
    if ae is None or ix is None:
        return keyword_results[:top_k]
    sem = ix.search(ae.embed(query), k=max(top_k * 3, 15))  # [(path, score, meta)]
    kw_rank = {r["path"]: i for i, r in enumerate(keyword_results)}
    sem_rank = {p: i for i, (p, s, m) in enumerate(sem)}
    kw_by_path = {r["path"]: r for r in keyword_results}
    if mode == "semantic":
        order = [p for p, _, _ in sem]
    else:  # hybrid RRF (k=60 관례)
        order = sorted(set(kw_rank) | set(sem_rank),
                       key=lambda p: 1.0 / (60 + kw_rank.get(p, 10**6))
                       + 1.0 / (60 + sem_rank.get(p, 10**6)), reverse=True)
    out = []
    for p in order[:top_k]:
        if p in kw_by_path:
            out.append(kw_by_path[p])
            continue
        summary = ""
        try:
            d = json.load(open(p, encoding="utf-8"))
            rk = (d.get("refined_knowledge") or {}) if isinstance(d, dict) else {}
            summary = (rk.get("summary") or (d.get("extracted_text", "")[:500]
                       if isinstance(d, dict) else ""))
        except Exception:
            pass
        out.append({"title": os.path.basename(p).replace(".json", ""),
                    "score": 0, "summary": summary, "path": p})
    return out


async def query_coresight(query: str, top_k: int = 5, uid: Optional[int] = None) -> str:
    """
    Search Coresight knowledge base files in real-time.
    Performs keyword-based search across all Coresight JSON log files.

    Admin-only gate: 비관리자 또는 uid 불명 시 빈/거부 문자열 반환.
    에이전트 루프에서 예외를 올리지 않는다(fail-soft).

    Phase 2 멀티테넌트: 전역 활성 계정 폐지 → 호출자가 uid 를 넘긴다(없으면 default-deny).

    Args:
        query: Search query text (Korean or English)
        top_k: Maximum number of results to return
        uid: 호출 유저 id — ADMIN 게이트 판정용 (없으면 거부)

    Returns:
        Formatted string of relevant knowledge snippets, or denial message for non-admin.
    """
    # ── ADMIN 전용 게이트 — deny-by-default, fail-soft ──────────────────────────
    # 예외도 포함해 fail-soft: 어떤 오류가 나도 에이전트 루프를 죽이지 않는다.
    try:
        _admin = _is_admin_active(uid)
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

    # 시맨틱/하이브리드(opt-in) — 실패 시 키워드로 폴백(fail-soft, 스웜 안 죽인다)
    mode = os.getenv("CORESIGHT_RAG_MODE", "keyword").lower()
    if mode in ("hybrid", "semantic"):
        try:
            top_results = _rank_hybrid(query, results, CORESIGHT_PATH, top_k, mode)
        except Exception as e:
            logger.info("Coresight 시맨틱 실패 — 키워드 폴백: %s", e)
            top_results = results[:top_k]
    else:
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
