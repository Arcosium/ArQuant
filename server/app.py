"""Arquant v1.0 - FastAPI Server"""
import asyncio, logging, os
from datetime import datetime
from pathlib import Path
from typing import Optional
import aiohttp
from fastapi import FastAPI, Request, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from infra import auth_store, credentials as creds_layer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
app = FastAPI(title="ArQuant v1.0", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(auth_store.FernetKeyLost)
async def _fernet_lost_handler(request: Request, exc: auth_store.FernetKeyLost):
    # 멀티유저 계정 보호: 키 분실 시 전 유저 계정이 위험 — 새 키 생성 대신 503으로 명확히 안내.
    return JSONResponse(status_code=503, content={
        "error": str(exc), "code": "fernet_key_lost",
        "hint": "data/.fernet.key 백업을 복구하거나 ARQUANT_FERNET_KEY 환경변수로 키를 주입한 뒤 서버를 재시작하세요."})

# 사장 피드백 2026-05-16: Cloudflare Access 제거 → 앱 자체 로그인(세션 쿠키/X-Session).
# 인증 불필요 경로 — SPA 셸(/)은 자체적으로 로그인 화면을 띄우므로 공개.
_PUBLIC_PATHS = {"/health", "/api/health", "/", "/favicon.ico",
                 "/api/login", "/api/register", "/api/auth_status", "/api/check_username"}
_PUBLIC_PREFIXES = ("/static/",)


# 사장 피드백 2026-05-16: 세션 쿠키 Secure 강화 (HYFE COOKIE_SECURE env 패턴).
# 기본 켜짐 — https 터널에선 쿠키, 로컬 http에선 X-Session 헤더(이중화)로 동작.
_COOKIE_SECURE = os.getenv("ARQUANT_COOKIE_SECURE", "1").lower() in ("1", "true", "yes")


def _session_token(request: Request) -> str:
    return (request.cookies.get(auth_store.SESSION_COOKIE)
            or request.headers.get("X-Session") or "").strip()


@app.middleware("http")
async def app_auth(request: Request, call_next):
    path = request.url.path
    if path in _PUBLIC_PATHS or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
        return await call_next(request)
    if not path.startswith("/api/"):
        return await call_next(request)  # non-API (정적/기타)는 통과
    uid = auth_store.lookup_session(_session_token(request))
    if uid is None:
        return JSONResponse(status_code=401,
                            content={"error": "로그인이 필요합니다", "code": "unauthorized"})
    request.state.user_id = uid
    return await call_next(request)


# ─── 자격증명 검증 (등록 시 — HYFE가 WQB/Gemini를 검증하던 것과 동치) ──────────
async def _validate_kis(app_key: str, app_secret: str, base_url: str) -> tuple[bool, str]:
    base_url = (base_url or "https://openapi.koreainvestment.com:9443").rstrip("/")
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.post(f"{base_url}/oauth2/tokenP",
                              json={"grant_type": "client_credentials",
                                    "appkey": app_key, "appsecret": app_secret}) as r:
                d = await r.json(content_type=None)
        if isinstance(d, dict) and d.get("access_token"):
            return True, "ok"
        return False, f"KIS 인증 실패: {(d or {}).get('error_description') or (d or {}).get('msg1') or '응답에 access_token 없음'}"
    except Exception as e:
        return False, f"KIS 연결 실패: {e}"


async def _validate_openrouter(api_key: str) -> tuple[bool, str]:
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.get("https://openrouter.ai/api/v1/key",
                              headers={"Authorization": f"Bearer {api_key}"}) as r:
                if r.status == 200:
                    return True, "ok"
                return False, f"OpenRouter 키 검증 실패 (HTTP {r.status})"
    except Exception as e:
        return False, f"OpenRouter 연결 실패: {e}"


def _issue_session(uid: int, remember: bool) -> JSONResponse:
    """HYFE _issue_session(app.py:257-271) 패턴 — 불투명 토큰 쿠키 발급.
    token은 body에도 실어 쿠키 못 쓰는 클라이언트(모바일)가 X-Session으로 쓰게 한다."""
    token = auth_store.create_session(uid)
    resp = JSONResponse(content={"ok": True, "user_id": uid, "token": token})
    kw = dict(httponly=True, samesite="lax", secure=_COOKIE_SECURE, path="/")
    if remember:
        kw["max_age"] = auth_store.SESSION_TTL_SEC
    resp.set_cookie(auth_store.SESSION_COOKIE, token, **kw)
    return resp


async def _activate_with_policy(uid: int) -> dict:
    """로그인/전환 시 활성 계정 적용 — 실거래 안전 정책 준수."""
    global _task
    cur = creds_layer.current().get("user_id")
    loop_running = bool(_task and not _task.done())
    decision = creds_layer.account_switch_policy(cur, uid, loop_running)
    if decision["action"] == "refuse":
        raise HTTPException(409, f"계정 전환 거부 — {decision['reason']} (먼저 매매를 중지하세요)")
    if decision["action"] == "stop_loop_then_proceed" and loop_running:
        from main_swarm import get_swarm
        get_swarm().stop()
        if _task:
            _task.cancel()
        _task = None
    info = creds_layer.set_active(uid)
    info["switch"] = decision
    return info

class WS:
    def __init__(self): self.conns: list[WebSocket] = []
    async def connect(self, ws): await ws.accept(); self.conns.append(ws)
    def disconnect(self, ws):
        if ws in self.conns: self.conns.remove(ws)
    async def broadcast(self, msg):
        dead=[]
        for c in self.conns:
            try: await c.send_json(msg)
            except: dead.append(c)
        for d in dead: self.disconnect(d)
ws_mgr = WS()
from main_swarm import set_broadcast_callback
set_broadcast_callback(ws_mgr.broadcast)

class Req(BaseModel):
    directive: Optional[str] = None
class CeoReq(BaseModel):
    message: str
class RegisterReq(BaseModel):
    username: str                   # 사용자가 정하는 아이디 (중복 불가)
    password: str                   # 10자 이상 + 특수문자 1개 이상
    openrouter_key: str
    kis_app_key: str
    kis_app_secret: str
    kis_account_no: str
    kis_base_url: str = "https://openapi.koreainvestment.com:9443"
    dart_key: str = ""              # 선택 — 없으면 공시 분석 생략
    label: str = ""
    remember: bool = True
class LoginReq(BaseModel):
    username: str
    password: str
    remember: bool = True
class SwitchReq(BaseModel):
    user_id: int

_task: Optional[asyncio.Task] = None

# ─── 인증 엔드포인트 (HYFE app.py:160-282 패턴) ───────────────────────────────
@app.get("/api/auth_status")
async def auth_status(request: Request):
    """SPA가 로그인 화면을 띄울지 판단 — 계정 존재 여부 + 현재 세션 유효 여부."""
    auth_store.init()
    accts = auth_store.list_accounts()
    uid = auth_store.lookup_session(_session_token(request))
    return {"has_accounts": bool(accts), "authenticated": uid is not None,
            "active": creds_layer.current()}

@app.get("/api/check_username")
async def check_username(u: str = ""):
    """아이디 중복 확인 (등록 폼 실시간 체크용 — 공개). 서버가 최종 게이트도 겸함."""
    u = (u or "").strip()
    if not u:
        return {"ok": False, "available": False, "reason": "아이디를 입력하세요."}
    return {"ok": True, "available": not auth_store.username_exists(u)}

@app.post("/api/register")
async def register(req: RegisterReq):
    """최초 등록 — 아이디(중복 불가) + 비밀번호(정책) + API 자격증명(실검증) 후 저장·활성화."""
    username = (req.username or "").strip()
    if not username or len(username) < 3:
        raise HTTPException(400, "아이디는 3자 이상이어야 합니다.")
    perr = auth_store.password_policy_error(req.password or "")
    if perr:
        raise HTTPException(400, perr)
    if auth_store.username_exists(username):
        raise HTTPException(409, f"이미 사용 중인 아이디입니다: {username}")
    # API 자격증명은 실제 호출로 검증 (저장 전)
    ok, msg = await _validate_kis(req.kis_app_key, req.kis_app_secret, req.kis_base_url)
    if not ok:
        raise HTTPException(400, msg)
    ok, msg = await _validate_openrouter(req.openrouter_key)
    if not ok:
        raise HTTPException(400, msg)
    uid = auth_store.upsert_user(
        username=username, password=req.password,
        kis_app_key=req.kis_app_key.strip(), kis_app_secret=req.kis_app_secret.strip(),
        openrouter_key=req.openrouter_key.strip(), kis_account_no=req.kis_account_no.strip(),
        kis_base_url=req.kis_base_url.strip(), dart_key=(req.dart_key or "").strip(),
        label=(req.label or "").strip())
    await _activate_with_policy(uid)
    auth_store.touch_login(uid)
    return _issue_session(uid, req.remember)

@app.post("/api/login")
async def login(req: LoginReq):
    """재로그인 — 아이디 + 비밀번호만 (HYFE m_login 패턴, 복호 후 평문 비교)."""
    u = auth_store.verify_password((req.username or "").strip(), req.password or "")
    if not u:
        raise HTTPException(401, "아이디 또는 비밀번호가 일치하지 않습니다.")
    await _activate_with_policy(u["id"])
    auth_store.touch_login(u["id"])
    return _issue_session(u["id"], req.remember)

@app.post("/api/logout")
async def logout(request: Request):
    auth_store.delete_session(_session_token(request))
    resp = JSONResponse(content={"ok": True})
    resp.delete_cookie(auth_store.SESSION_COOKIE, path="/",
                       secure=_COOKIE_SECURE, httponly=True, samesite="lax")
    return resp

@app.get("/api/me")
async def me(request: Request):
    uid = request.state.user_id
    c = auth_store.get_user_credentials(uid) or {}
    return {"user_id": uid, "username": c.get("username"), "label": c.get("label"),
            "kis_app_key_masked": auth_store._mask(c.get("kis_app_key", "")),
            "kis_account_no_masked": auth_store._mask(c.get("kis_account_no", ""), 4),
            "has_dart": bool(c.get("dart_key")),
            "is_admin": bool(c.get("is_admin")),  # 사장 피드백 2026-05-18: 코드변경 전체반영 권한 표시
            "active": creds_layer.current()}

@app.get("/api/accounts")
async def accounts():
    return {"accounts": auth_store.list_accounts(), "active": creds_layer.current()}

@app.post("/api/switch")
async def switch_account(req: SwitchReq):
    """등록된 다른 KIS 계정으로 활성 전환 (실거래 안전 정책 준수)."""
    if not auth_store.get_user_credentials(req.user_id):
        raise HTTPException(404, "해당 계정 없음")
    info = await _activate_with_policy(req.user_id)
    return {"ok": True, "active": creds_layer.current(), "detail": info}

@app.get("/health")
async def health(): return {"status":"ok","service":"ArQuant v1.0","timestamp":datetime.now().isoformat()}

@app.get("/api/status")
async def status():
    from main_swarm import get_swarm
    s = get_swarm().get_status()
    # Expose whether the background task is actively running so frontend can sync buttons on reconnect
    s["is_running"] = bool(_task and not _task.done())
    # 사장 피드백 2026-05-15: 최근 사이클 API 비용 (USD 추정)
    try:
        from agents.base_agent import get_api_cost_last_cycle
        s["api_cost"] = get_api_cost_last_cycle(seconds_back=3600.0)
    except Exception:
        s["api_cost"] = {"cost_usd": 0.0, "calls": 0, "window_sec": 3600.0}
    # 사장 피드백 2026-05-18: 운용지원실장 피드백 on/off 토글 상태 (UI 버튼 표시용)
    try:
        import runtime
        s["ops_feedback_enabled"] = runtime.ops_feedback_enabled()
    except Exception:
        s["ops_feedback_enabled"] = True
    return s

@app.post("/api/start")
async def start(req: Req):
    global _task
    from main_swarm import get_swarm; s = get_swarm()
    if _task and not _task.done(): raise HTTPException(409,"이미 감시 중")
    _task = asyncio.create_task(s.start_continuous(req.directive))
    return {"message":"🟢 Arquant 감시 시작"}

@app.post("/api/stop")
async def stop():
    from main_swarm import get_swarm; get_swarm().stop(); return {"message":"🔴 중지 요청됨"}

@app.post("/api/ceo")
async def ceo_command(req: CeoReq):
    from main_swarm import get_swarm
    resp = await get_swarm().ceo_directive(req.message)
    return {"response": resp}

@app.get("/api/history")
async def history():
    from main_swarm import get_swarm; return {"cycles": get_swarm().get_history()}

@app.get("/api/events")
async def events(limit: int = 500):
    """Persisted display log — the dashboard fetches this on load so a page refresh
    restores the trade/agent log. Accumulates until /api/events/clear."""
    from main_swarm import get_recent_events
    return {"events": get_recent_events(limit)}

@app.post("/api/events/clear")
async def events_clear():
    from main_swarm import clear_event_log; clear_event_log()
    return {"message": "🧹 로그 초기화됨"}

@app.get("/api/alerts")
async def alerts(limit: int = 100, level: str = ""):
    """운영자 실패 알림 — 조용히 삼켜졌던 주문/체결/equity/루프 실패를 표면화.
    (auth 미들웨어로 로그인 필요 — 운영자 전용)"""
    from infra import notifier
    try:
        n = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        n = 100
    return {"alerts": notifier.recent(n, level or None)}

@app.get("/api/metrics")
async def metrics_snapshot(window_sec: int = 86400):
    """사이클 소요시간·주문 성공/실패·오류 카운트 최근 집계."""
    from infra import metrics
    try:
        w = max(60, min(int(window_sec), 7 * 86400))
    except (TypeError, ValueError):
        w = 86400
    return {"window_sec": w, "metrics": metrics.snapshot(w)}

@app.get("/api/news")
async def news(count: int = 20):
    # 사장 피드백 2026-05-18: 누적 뉴스를 깊게 받아 스크롤로 계속 열람 (1~500건).
    from tools.news_monitor import get_monitor; m = get_monitor()
    try:
        n = max(1, min(int(count), 500))
    except (TypeError, ValueError):
        n = 20
    return {"articles": m.get_recent_articles(n), "status": m.get_status()}

@app.post("/api/news/clear")
async def news_clear():
    # 사장 피드백 2026-05-18: 뉴스 로그 비우기 — 단, 최근 20건은 유지.
    from tools.news_monitor import get_monitor; m = get_monitor()
    kept = m.clear_history(keep=20)
    return {"message": f"🧹 뉴스 로그 초기화됨 (최근 {kept}건 유지)", "kept": kept}

@app.get("/api/agents")
async def agents(request: Request):
    from config import MODEL_ASSIGNMENTS
    roster = [
        {"name":"운용전략실장","role":"Chief Strategy","model":MODEL_ASSIGNMENTS["chief_orchestrator"]},
        {"name":"전략리서치팀장","role":"Macro Research","model":MODEL_ASSIGNMENTS["macro_analyst"]},
        {"name":"계량분석팀장","role":"Quant Analyst","model":MODEL_ASSIGNMENTS["quant_analyst"]},
        {"name":"뉴스분석팀장","role":"News Analyst","model":MODEL_ASSIGNMENTS["news_analyst"]},
        {"name":"트레이딩팀장","role":"Trader (fallback only)","model":MODEL_ASSIGNMENTS["trader"]},
        {"name":"리스크관리실장","role":"Risk Guard (결정론 룰 + DART 재심)","model":f"룰 엔진(Python) + {MODEL_ASSIGNMENTS['risk_guard']}"},
        {"name":"사후관리실장","role":"Post-Management (보유 종목 매도 판단)","model":MODEL_ASSIGNMENTS["post_manager"]},
        {"name":"운용지원실장","role":"Ops Support","model":MODEL_ASSIGNMENTS["ops_support"]},
    ]
    # 사장 피드백 2026-05-18: 운용지원실장(+산하 팀장)은 ADMIN(hh09080) 전용.
    # 비관리자에겐 명단에서 제외 → 클라이언트가 알 수도, 멘션할 수도 없게 한다.
    _admin = False
    try:
        _admin = auth_store.is_admin(getattr(request.state, "user_id", None))
    except Exception:
        _admin = False  # default-deny
    if not _admin:
        roster = [a for a in roster if a["name"] != "운용지원실장"]
    return {"agents": roster}

# 사장 피드백 2026-05-18: 운용지원실장 피드백 on/off 토글 (ADMIN 전용).
@app.get("/api/ops_feedback")
async def ops_feedback_get(request: Request):
    import runtime
    _admin = False
    try:
        _admin = auth_store.is_admin(getattr(request.state, "user_id", None))
    except Exception:
        _admin = False
    st = runtime.ops_feedback_state()
    return {"enabled": bool(st.get("enabled", True)), "since": st.get("since"),
            "by": st.get("by"), "is_admin": _admin}

@app.post("/api/ops_feedback")
async def ops_feedback_set(request: Request, req: dict):
    import runtime
    from main_swarm import _broadcast
    # 운용지원실장 자체가 ADMIN 전용이므로 토글도 ADMIN 만 변경 가능.
    try:
        _admin = auth_store.is_admin(getattr(request.state, "user_id", None))
    except Exception:
        _admin = False
    if not _admin:
        raise HTTPException(403, "운용지원 피드백 토글은 ADMIN(hh09080) 전용입니다.")
    enabled = bool((req or {}).get("enabled"))
    st = runtime.set_ops_feedback(enabled, by="dashboard")
    try:
        await _broadcast({"type": "status", "state": "IDLE",
                          "message": f"🛠 운용지원실장 피드백 {'켜짐(ON)' if enabled else '꺼짐(OFF)'}"})
    except Exception:
        pass
    return {"enabled": bool(st.get("enabled", True)), "since": st.get("since")}

@app.get("/api/dart")
async def dart_search(corp_name: str = "", days: int = 7):
    from tools.dart_disclosure import search_disclosures
    from datetime import timedelta
    bgn = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    result = await search_disclosures(corp_name=corp_name, bgn_de=bgn)
    return {"result": result}

@app.get("/api/price/kr/{code}")
async def kr_price(code: str):
    from infra.kis_broker import get_broker
    return {"result": await get_broker().kr_price_str(code)}

@app.get("/api/price/us/{ticker}")
async def us_price(ticker: str):
    from infra.kis_broker import get_broker
    return {"result": await get_broker().us_price(ticker)}

@app.get("/api/rank/volume")
async def volume_rank():
    from infra.kis_broker import get_broker
    return {"result": await get_broker().kr_volume_rank()}

@app.get("/api/balance")
async def balance():
    """Account snapshot for the dashboard 'holdings' panel (single cached KIS read)."""
    from infra.kis_broker import get_broker
    from main_swarm import record_equity
    try:
        snap = await get_broker().portfolio_holdings()
        try: record_equity(snap["buying_power"], "poll")
        except Exception: pass
        return {"buying_power": snap["buying_power"], "holdings": snap["holdings"],
                "holdings_stale": snap.get("holdings_stale", False)}
    except Exception:
        return {"buying_power": {"cash":0.0,"total_eval":0.0,"pnl_ratio":0.0,"ok":False}, "holdings": []}

@app.get("/api/equity")
async def equity(limit: int = 500, view: str = "realtime"):
    """Equity-curve points for the 수익률 그래프.
    view ∈ {'realtime', 'daily', 'monthly'} — see get_equity_series()."""
    from main_swarm import get_equity_series
    v = view if view in ("realtime", "daily", "monthly") else "realtime"
    return {"series": get_equity_series(limit, v), "view": v}

@app.get("/api/trades")
async def trades(limit: int = 500):
    """All real trade events (executed/failed), newest first — 전체 거래 내역."""
    from main_swarm import get_trade_history
    return {"trades": get_trade_history(limit)}

@app.post("/api/trades/clear")
async def trades_clear():
    """Wipe only the trade history (keeps system/agent logs intact). Used by the 수익률 탭 '비우기' button."""
    from main_swarm import clear_trade_log
    removed = clear_trade_log()
    return {"message": f"🗑️ 거래 내역 {removed}건 초기화됨", "removed": removed}

@app.get("/api/strategy")
async def strategy_get():
    """전략 탭에 필요한 모든 데이터 — active/presets/history + 한국어 메타데이터 (사장 지시 2026-05-14)."""
    import runtime
    from config import STRATEGY_KEY_META, STRATEGY_TUNABLE_KEYS
    return {"active": runtime.active(), "presets": runtime.list_presets(),
            "history": runtime.history(),
            "key_meta": STRATEGY_KEY_META, "key_order": STRATEGY_TUNABLE_KEYS}

@app.post("/api/strategy")
async def strategy_set(req: dict):
    import runtime
    from main_swarm import _broadcast
    name = (req or {}).get("name", "")
    custom = (req or {}).get("params")
    if not name and not custom:
        raise HTTPException(400, "name 또는 params 필요")
    active = runtime.set_strategy(name or "custom", custom=custom, by="dashboard")
    try:
        await _broadcast({"type": "status", "state": "IDLE",
                          "message": f"⚙️ 전략 변경 → {active['label']} ({active['name']})"})
    except Exception: pass
    return {"active": active}

@app.post("/api/strategy/preset")
async def strategy_save_preset(req: dict):
    """사용자 정의 프리셋 저장 (사장 지시 2026-05-14).
    Body: {"name": "<id>", "label": "<표시명>", "params": {...}}"""
    import runtime
    from main_swarm import _broadcast
    name = (req or {}).get("name", "")
    label = (req or {}).get("label", name)
    params = (req or {}).get("params") or {}
    res = runtime.save_user_preset(name, label, params, by="dashboard")
    if not res.get("ok"):
        raise HTTPException(400, res.get("message", "프리셋 저장 실패"))
    try:
        await _broadcast({"type": "status", "state": "IDLE",
                          "message": f"⚙️ 사용자 프리셋 저장: {label} ({name})"})
    except Exception: pass
    return res

@app.delete("/api/strategy/preset/{name}")
async def strategy_delete_preset(name: str):
    """사용자 프리셋 삭제. 빌트인은 삭제 불가. (사장 지시 2026-05-14)"""
    import runtime
    from main_swarm import _broadcast
    res = runtime.delete_user_preset(name)
    if not res.get("ok"):
        raise HTTPException(400, res.get("message", "삭제 실패"))
    try:
        await _broadcast({"type": "status", "state": "IDLE",
                          "message": f"🗑 사용자 프리셋 삭제: {name}"})
    except Exception: pass
    return res

@app.get("/api/cycles")
async def cycles(limit: int = 50, offset: int = 0):
    """Persisted analysis cycles (newest first). 사장 지시 2026-05-14 — 백테스트/장기 분석용."""
    from infra import cycle_store
    return {"cycles": cycle_store.list_cycles(limit, offset)}

@app.get("/api/cycles/{cycle_id}")
async def cycle_detail(cycle_id: int):
    from infra import cycle_store
    row = cycle_store.get_cycle(cycle_id)
    if not row:
        raise HTTPException(404, f"cycle {cycle_id} 없음")
    return row

@app.get("/api/ops_history")
async def ops_history_endpoint(limit: int = 100):
    """운용지원실장 자동 수정 이력 (사장 지시 2026-05-14). Newest-first 응답."""
    from infra import ops_history
    h = ops_history.load_history()
    return {"history": list(reversed(h))[:max(1, int(limit))], "stats": ops_history.stats()}

@app.websocket("/ws")
async def ws_ep(ws: WebSocket):
    # 사장 피드백 2026-05-16: WS는 HTTP 미들웨어를 안 타므로 여기서 직접 세션 검증.
    # 쿠키(브라우저) 또는 ?token= (모바일/쿠키 불가 클라이언트) 둘 다 허용.
    token = (ws.query_params.get("token")
             or ws.cookies.get(auth_store.SESSION_COOKIE) or "").strip()
    if auth_store.lookup_session(token) is None:
        await ws.close(code=4401)  # 4401 = unauthorized (app-defined)
        return
    await ws_mgr.connect(ws)
    try:
        while True: await ws.receive_text()
    except WebSocketDisconnect: ws_mgr.disconnect(ws)

SD = Path(__file__).parent / "static"; SD.mkdir(exist_ok=True)

@app.get("/", response_class=HTMLResponse)
async def dash():
    p = SD / "index.html"
    return HTMLResponse(content=p.read_text(encoding="utf-8") if p.exists() else "<h1>ArQuant v1.0</h1>")

if SD.exists(): app.mount("/static", StaticFiles(directory=str(SD)), name="static")


# ─── 부팅 시 인증 부트스트랩 (사장 피드백 2026-05-16) ─────────────────────────
# 등록 계정이 없으면 .env 값으로 사장님 프로필 1개 생성 → 이후 .env는 사용 안 함.
# 직전 활성 계정이 있으면 복구해 자격증명을 런타임에 주입.
@app.on_event("startup")
async def _auth_bootstrap():
    try:
        try:
            auth_store.migrate_passwords_and_bidx()
        except Exception as _e:
            logging.getLogger("auth_store").error("부팅 마이그레이션 실패: %s", _e)
        seeded = auth_store.bootstrap_from_env()
        if seeded:
            logging.getLogger("AUTH").info("부팅 시드: .env → 프로필 user_id=%s", seeded)
        reactivated = creds_layer.reactivate_last()
        if reactivated is None and seeded:
            creds_layer.set_active(seeded)  # 첫 부팅: 시드 계정을 바로 활성화
    except Exception as e:
        logging.getLogger("AUTH").warning("인증 부트스트랩 실패: %s", e)


# ─── RESUME_ON_BOOT — 운용지원실장이 코드 변경 후 재시작했을 때 자동으로 watch loop 재개 ───
# Worker leaves data/.resume_on_boot marker before triggering start_server.sh.
# On startup, if the marker exists (and is recent — within 10 min), auto-call
# start_continuous() so trading resumes without manual intervention.
@app.on_event("startup")
async def _auto_resume_if_marked():
    global _task
    marker = Path(__file__).parent.parent / "data" / ".resume_on_boot"
    if not marker.exists():
        return
    try:
        ts = marker.read_text(encoding="utf-8").strip()
        # consume the marker so a server restart for non-ops reasons doesn't auto-start
        marker.unlink()
    except Exception:
        ts = ""
    # Defer slightly so logging + ws are ready before swarm starts broadcasting
    async def _starter():
        global _task
        await asyncio.sleep(2)
        # 활성 계정(자격증명)이 없으면 매매를 자동 재개하지 않는다 — 로그인 필요.
        if not creds_layer.current().get("user_id"):
            logging.getLogger("AUTH").info("자동 재개 보류 — 활성 계정 없음(로그인 대기)")
            return
        from main_swarm import get_swarm
        s = get_swarm()
        if _task and not _task.done():
            return
        _task = asyncio.create_task(s.start_continuous(None))
        try:
            from main_swarm import log_response_event
            log_response_event({"source": "system_event", "type": "status", "state": "MONITORING",
                                "message": f"🟢 자동 재개 (운용지원실장 코드 갱신 후 부팅, marker ts={ts})"})
        except Exception:
            pass
    asyncio.create_task(_starter())

if __name__ == "__main__":
    import uvicorn; from config import APP_HOST, APP_PORT
    uvicorn.run(app, host=APP_HOST, port=APP_PORT)
