"""모델 대결 하니스 — Qwen3.6-35B-A3B(OpenRouter) vs 운영 DeepSeek(flash+pro).

사장 지시 2026-06-17: 사이클을 별도 변형으로 다른 모델로 돌려(실거래 X) 운영 DeepSeek
사이클의 에이전트 통신과 비교한다. **라이브 프로세스(arquant.service)를 전혀 건드리지 않는**
독립 프로세스로, 다음을 보장한다:

  1. backend 별 LLM 라우팅:
     - deepseek        : 운영 그대로(per-agent flash+pro·admin override·api.deepseek.com).
     - qwen            : 전 에이전트 qwen/qwen3.6-35b-a3b(OpenRouter), reasoning OFF.
     - qwen-reasoning  : 동 모델, reasoning ON(추론 켬 — 느리나 사고 후 응답).
  2. 실거래 0 — LIVE_TRADING=False(내장 dry-run) + 브로커 주문메서드 dry stub(이중 안전).
     KIS 시세/잔고 '읽기'만 실제(uid1=hh09080 creds).
  3. 데이터 격리 — 가짜 uid(9001) → data/9001/* 만. cycle_store 도 격리. 라이브 무오염.
  4. ops 워커 spawn 비활성 · 전 에이전트 통신(_emit) 캡처(예외 시도 보존).
  5. session 인자로 세션 강제(예: KR_PRE_MARKET=8시 프리마켓) — 같은 데이터로 세션만 고정.

실행: PYTHONPATH=. python3.11 -u tools/model_bakeoff.py [backend] [session]
  backend ∈ {qwen, qwen-reasoning, deepseek}  (기본 qwen)
  session ∈ {KR_PRE_MARKET, KR_TRADING, US_TRADING, OFF_HOURS, auto}  (기본 auto=현재)
"""
from __future__ import annotations
import asyncio
import json
import sys
import time as _time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QWEN_MODEL = "qwen/qwen3.6-35b-a3b"
BAKEOFF_UID = 9001
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
ENV_PATH = "/home/opc/projects/.env"


def _load_openrouter_key() -> str:
    import os
    for line in open(ENV_PATH, encoding="utf-8"):
        if line.strip().startswith("OPENROUTER_API_KEY"):
            return line.partition("=")[2].strip().strip('"').strip("'")
    return os.environ.get("OPENROUTER_API_KEY", "")


async def run(backend: str, session_override: str = "") -> dict:
    import aiohttp
    from infra import deepseek_client, admin_config, cycle_store

    # OpenRouter 모델 맵: backend → (model_id, reasoning_on)
    OR_MODELS = {
        "qwen": ("qwen/qwen3.6-35b-a3b", False),
        "qwen-reasoning": ("qwen/qwen3.6-35b-a3b", True),
        "gemma": ("google/gemma-4-26b-a4b-it", False),
        "gemma-reasoning": ("google/gemma-4-26b-a4b-it", True),
        # 선택적 reasoning: deepseek 가 pro 쓰는 에이전트에만 reasoning ON, flash 엔 OFF
        # (사장 지시 2026-06-18). reasoning_on=None → tier 기반(get_model_override 미덮음).
        "qwen-selective": ("qwen/qwen3.6-35b-a3b", None),
    }
    is_or = backend in OR_MODELS
    reasoning_on = OR_MODELS.get(backend, ("", False))[1]
    selective = (reasoning_on is None)   # tier 기반 reasoning
    _call_n = {"i": 0}

    # ── 1. backend 별 LLM 라우팅 ──
    if is_or:
        key = _load_openrouter_key()
        if not key:
            raise SystemExit("OPENROUTER_API_KEY 없음")
        or_model = OR_MODELS[backend][0]
        deepseek_client.DEEPSEEK_BASE_URL = OPENROUTER_BASE
        if not selective:
            admin_config.get_model_override = lambda mk, default="": or_model   # 전 에이전트 단일 모델
        # selective: get_model_override 미덮음 → 들어오는 model 이 운영 tier(deepseek-v4-pro/flash)

        async def _chat(*, api_key, model, messages, max_tokens, temperature=0.3,
                        timeout_sec=300, thinking=None, response_format=None):
            if selective:
                _reason = ("pro" in (model or "").lower())   # pro tier 에만 reasoning ON
                _tier = "pro" if _reason else "flash"
            else:
                _reason = bool(reasoning_on)
                _tier = "-"
            payload = {"model": or_model, "messages": messages, "max_tokens": int(max_tokens),
                       "temperature": float(temperature), "stream": False,
                       "reasoning": {"enabled": _reason}}
            if response_format:
                payload["response_format"] = response_format
            headers = {"Authorization": f"Bearer {api_key.strip()}", "Content-Type": "application/json"}
            _call_n["i"] += 1
            _ci = _call_n["i"]
            _t0 = _time.time()
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout_sec)) as s:
                async with s.post(f"{OPENROUTER_BASE}/chat/completions", headers=headers, json=payload) as r:
                    d = await r.json()
            _ch = (d.get("choices") or [{}])[0]
            _msg = _ch.get("message") or {}
            _c = _msg.get("content") or ""
            _rsn = len(_msg.get("reasoning") or "")
            print(f"  [llm #{_ci}] {_time.time()-_t0:.1f}s tier={_tier} reasoning={'ON' if _reason else 'off'} "
                  f"content={len(_c)}자 think={_rsn}자 finish={_ch.get('finish_reason')} "
                  f"err={(d.get('error') or {}).get('message','')[:50]}", flush=True)
            return d
        deepseek_client.chat_completion = _chat
        overwrite_key = key
        model = or_model + (" (tier-selective reasoning: pro=ON/flash=off)" if selective else "")
    else:
        # deepseek: 운영 그대로. 호출 로깅만 래핑.
        model = "(운영 per-agent flash+pro)"
        overwrite_key = None
        _orig = deepseek_client.chat_completion

        async def _logged(**kw):
            _call_n["i"] += 1
            _ci = _call_n["i"]
            _t0 = _time.time()
            d = await _orig(**kw)
            _ch = (d.get("choices") or [{}])[0]
            _c = (_ch.get("message") or {}).get("content") or ""
            print(f"  [llm #{_ci}] {_time.time()-_t0:.1f}s model={kw.get('model')} content={len(_c)}자 "
                  f"finish={_ch.get('finish_reason')}", flush=True)
            return d
        deepseek_client.chat_completion = _logged

    # ── 3. cycle_store 격리 ──
    iso_dir = ROOT / "data" / str(BAKEOFF_UID)
    iso_dir.mkdir(parents=True, exist_ok=True)
    cycle_store.DB_PATH = iso_dir / f"cycles_{backend}.db"   # backend별 격리(동시/연속 실행 충돌 방지)
    cycle_store._conn = None

    # ── 2/5. LIVE_TRADING off · 세션 강제 ──
    import main_swarm
    main_swarm.LIVE_TRADING = False
    session = session_override if session_override and session_override != "auto" else main_swarm.get_current_session()
    main_swarm.get_current_session = lambda: session   # 내부 재조회도 강제 세션 사용
    market_open = False

    # ── creds: uid1(hh09080) KIS 읽기 + id=9001 ──
    from infra import auth_store
    base = auth_store.get_user_credentials(1)
    if not base:
        raise SystemExit("uid1 creds 복호화 실패")
    creds = dict(base)
    creds["id"] = BAKEOFF_UID
    if overwrite_key:
        creds["deepseek_api_key"] = overwrite_key   # qwen: base_agent api_key 로 OpenRouter 키 주입

    from infra.user_context import UserContext
    ctx = UserContext(creds)

    # ── 6. 브로커 주문메서드 dry stub (이중 안전) ──
    broker = ctx.broker

    async def _dry_order(*a, **k):
        return "[DRY-RUN] 주문 생략(model bakeoff)"
    for m in ("kr_buy", "kr_sell", "us_buy", "us_sell", "place_order"):
        if hasattr(broker, m):
            setattr(broker, m, _dry_order)

    # ── 7. 스왐 · emit 캡처 · ops spawn 비활성 ──
    swarm = ctx.swarm
    captured: list = []

    async def _cap(msg):
        captured.append(msg)
    swarm._emit = _cap
    swarm._emit_news_activity = _cap
    if hasattr(swarm, "_spawn_ops_support_worker"):
        swarm._spawn_ops_support_worker = lambda *a, **k: None

    # ── 8. 뉴스 수집 + 1 사이클 ──
    try:
        swarm.news_monitor.crawl_once()
    except Exception as e:
        print(f"[warn] crawl_once: {e}", flush=True)
    try:
        news = swarm.news_monitor.get_recent_articles(300)
    except Exception:
        news = []
    print(f"[bakeoff] backend={backend} model={model} session={session} news={len(news)} — 사이클 시작", flush=True)

    _t0 = _time.time()
    err = None
    try:
        await swarm._run_analysis_cycle(news, None, session, market_open=market_open)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        print(f"[bakeoff] 사이클 예외(캡처분 보존): {err}", flush=True)

    out = {"backend": backend, "model": model, "session": session, "news_count": len(news),
           "elapsed_sec": round(_time.time() - _t0, 1), "llm_calls": _call_n["i"],
           "error": err, "emit_count": len(captured), "emits": captured}
    fn = iso_dir / f"bakeoff_{backend}.json"
    fn.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[bakeoff] 완료 {out['elapsed_sec']}s · LLM {out['llm_calls']}콜 · 캡처 {len(captured)}건 → {fn}", flush=True)
    return out


if __name__ == "__main__":
    bk = sys.argv[1] if len(sys.argv) > 1 else "qwen"
    sess = sys.argv[2] if len(sys.argv) > 2 else "auto"
    asyncio.run(run(bk, sess))
