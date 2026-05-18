"""
NPS Swarm v1.0 - Coresight RAG Tool
Real-time knowledge retrieval from ArcAI.ve Coresight documents.
Agents call query_coresight() at runtime to retrieve contextual knowledge.
"""
import json
import os
import glob
from typing import Optional


async def query_coresight(query: str, top_k: int = 5) -> str:
    """
    Search Coresight knowledge base files in real-time.
    Performs keyword-based search across all Coresight JSON log files.

    Args:
        query: Search query text (Korean or English)
        top_k: Maximum number of results to return

    Returns:
        Formatted string of relevant knowledge snippets
    """
    from config import CORESIGHT_PATH

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
