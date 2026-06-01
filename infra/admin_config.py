"""전역 ADMIN 설정 — 전체 계정 공통 (사장 지시 2026-05-22).

운용지원실장의 '프로필 한정' 오버라이드와 달리, ADMIN 탭에서 설정하는 항목
(각 에이전트·뉴스 크롤러의 모델, 뉴스 크롤 주기)은 data/admin_config.json 에 저장돼
**모든 계정·전체 시스템**에 적용된다.
 - 모델 변경: 에이전트가 기동 시 모델을 읽으므로 **다음 재시작에 반영**(오타가 라이브 매매를
   조용히 깨지 않도록, 관리자가 재시작하며 확인하게 하는 안전장치).
 - 뉴스 크롤 주기: 감시 루프가 매 주기 읽으므로 **즉시 반영**.
"""
import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional

_PATH = Path(__file__).resolve().parent.parent / "data" / "admin_config.json"
_LOCK = threading.Lock()


def _read() -> Dict[str, Any]:
    try:
        if _PATH.exists():
            d = json.loads(_PATH.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return d
    except Exception:
        pass
    return {}


def get_model_override(model_key: str) -> str:
    """해당 model_key 의 전역 모델 오버라이드(OpenRouter 모델명). 없으면 ''."""
    return str((_read().get("model_overrides") or {}).get(model_key) or "").strip()


def resolve_model(model_key: str, default: str = "") -> str:
    """이 model_key 의 실제 사용 모델 — ADMIN 오버라이드 우선, 없으면 config 기본값, 그것도 없으면 default.
    사장 지시 2026-05-30: 매크로 리서치(deep_research)·뉴스 분류기(llm_classify_articles)는
    BaseAgent 가 아니라 tool 함수라 그동안 config.MODEL_ASSIGNMENTS 만 읽어 ADMIN 모델 오버라이드를
    '무시'했다(사장이 모델을 바꿔도 재시작 후 늘 config 기본값으로 보임). BaseAgent(base_agent.py)와
    동일한 우선순위를 이 헬퍼로 통일해 두 tool 에도 오버라이드가 먹히게 한다."""
    ov = get_model_override(model_key)
    if ov:
        return ov
    try:
        from config import MODEL_ASSIGNMENTS
        cfg = str(MODEL_ASSIGNMENTS.get(model_key) or "").strip()
    except Exception:
        cfg = ""
    return cfg or default


def news_crawl_interval(default_sec: int) -> int:
    """뉴스 크롤 주기(초). 0/미설정이면 config 기본값을 그대로 쓴다."""
    try:
        v = int(_read().get("news_crawl_interval_sec") or 0)
    except (TypeError, ValueError):
        v = 0
    return v if v > 0 else int(default_sec)


def get_all() -> Dict[str, Any]:
    d = _read()
    return {"model_overrides": dict(d.get("model_overrides") or {}),
            "news_crawl_interval_sec": int(d.get("news_crawl_interval_sec") or 0)}


def set_config(*, model_overrides: Optional[Dict[str, str]] = None,
               news_crawl_interval_sec: Optional[int] = None) -> Dict[str, Any]:
    """전역 설정 갱신. model_overrides 의 빈 값은 해당 오버라이드 제거로 처리한다."""
    with _LOCK:
        d = _read()
        if model_overrides is not None:
            d["model_overrides"] = {str(k): str(v).strip()
                                    for k, v in model_overrides.items() if str(v).strip()}
        if news_crawl_interval_sec is not None:
            try:
                d["news_crawl_interval_sec"] = max(0, int(news_crawl_interval_sec))
            except (TypeError, ValueError):
                d["news_crawl_interval_sec"] = 0
        try:
            _PATH.parent.mkdir(exist_ok=True)
            _PATH.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    return get_all()
