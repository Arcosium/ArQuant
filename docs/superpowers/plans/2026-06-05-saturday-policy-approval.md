# 토요일 정책 플래그 승인 흐름 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **커밋 정책:** 이 저장소는 사장 명시 요청 시에만 커밋한다(외부 자동 Backup 도구가 휩쓸어감). 각 Task의 "커밋" 대신 **전체 테스트 재실행**으로 체크포인트한다. `python3.11` 사용 필수(기본 python 은 argon2 import 실패).

**Goal:** 운용지원실장이 토요일(weekly) 점검에서 정책 플래그(OPS_PROTECTED_KEYS) 변경을 제안하면 사장 승인 대기함에 회부하고, 사장이 대시보드에서 승인해야만 적용한다(평일은 차단, 사장 직접지시는 즉시 적용).

**Architecture:** ops_param_clamp 에 트리거별 분류기(partition_protected) 추가, per-uid 승인 인박스(policy_approval_inbox) 신설(승인 시 set_overrides 적용), ops 워커에서 weekly 정책키를 enqueue+알림, FastAPI 엔드포인트 3종 + 대시보드 섹션.

**Tech Stack:** Python 3.11, FastAPI, 기존 profile_overrides/runtime, vanilla JS 대시보드(server/static/index.html).

---

### Task 1: `partition_protected` — 트리거별 정책 키 분류기

**Files:**
- Modify: `infra/ops_param_clamp.py` (strip_protected 대체)
- Test: `tests/test_ops_protected_keys.py` (strip_protected → partition_protected 갱신)

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_ops_protected_keys.py` 상단 import 와 유닛 테스트를 다음으로 교체:

```python
from infra.ops_param_clamp import partition_protected


def test_manual_keeps_all():
    kept, review, notes = partition_protected(
        {"ALLOW_US_STOCKS": True, "STOP_LOSS_PCT": 4.0}, trigger="manual")
    assert kept == {"ALLOW_US_STOCKS": True, "STOP_LOSS_PCT": 4.0}
    assert review == {} and notes == []


def test_weekly_routes_policy_to_review():
    kept, review, notes = partition_protected(
        {"ALLOW_US_STOCKS": True, "MIN_QUANT_SCORE": 5}, trigger="weekly")
    assert kept == {"MIN_QUANT_SCORE": 5}
    assert review == {"ALLOW_US_STOCKS": True}
    assert any("ALLOW_US_STOCKS" in n for n in notes)


def test_cycle_drops_policy():
    kept, review, notes = partition_protected(
        {"ALLOW_US_STOCKS": False, "MIN_QUANT_SCORE": 5}, trigger="cycle")
    assert kept == {"MIN_QUANT_SCORE": 5}
    assert review == {}
    assert any("ALLOW_US_STOCKS" in n for n in notes)


def test_tactical_only_passthrough_all_triggers():
    for trig in ("cycle", "weekly", "manual"):
        kept, review, notes = partition_protected({"MIN_QUANT_SCORE": 6}, trigger=trig)
        assert kept == {"MIN_QUANT_SCORE": 6} and review == {} and notes == []


def test_empty_and_none_safe():
    assert partition_protected({}, trigger="cycle") == ({}, {}, [])
    assert partition_protected(None, trigger="weekly") == ({}, {}, [])
```
(기존 `_patch_sideeffects`/`test_cycle_trigger_strips_protected_in_pipeline`/`test_manual_trigger_keeps_protected_in_pipeline` 와이어링 테스트는 그대로 유지 — Task 3 에서 의미 유지됨.)

- [ ] **Step 2: 실패 확인** — `python3.11 -m pytest tests/test_ops_protected_keys.py -q` → ImportError(partition_protected 없음).

- [ ] **Step 3: 구현** — `infra/ops_param_clamp.py` 의 `strip_protected` 를 다음으로 교체:

```python
def partition_protected(overrides: Dict[str, Any],
                        trigger: str = "cycle") -> Tuple[Dict[str, Any], Dict[str, Any], List[str]]:
    """트리거별로 정책 플래그(config.OPS_PROTECTED_KEYS)를 분류. 반환 (kept, to_review, notes).

    manual(사장 직접지시) → 전부 kept(권위). weekly(토 점검) → 정책 키는 to_review(승인 회부),
    나머지 kept. cycle(평일) → 정책 키 드롭(note), 나머지 kept. 회사 운영 거버넌스 2026-06-05."""
    import config
    protected = set(getattr(config, "OPS_PROTECTED_KEYS", ()))
    src = dict(overrides or {})
    if trigger == "manual":
        return src, {}, []
    kept: Dict[str, Any] = {}
    review: Dict[str, Any] = {}
    notes: List[str] = []
    for k, v in src.items():
        if k not in protected:
            kept[k] = v
        elif trigger == "weekly":
            review[k] = v
            notes.append(f"{k}: 정책 키 — 토요일 점검 → 사장 승인 대기로 회부")
        else:  # cycle
            notes.append(f"{k}: 정책 키 — 운용지원 자율 변경 불가(사장 전용) → 무시")
    return kept, review, notes
```

- [ ] **Step 4: 통과 확인** — `python3.11 -m pytest tests/test_ops_protected_keys.py -q` → PASS.

---

### Task 2: `policy_approval_inbox` — per-uid 승인 인박스

**Files:**
- Create: `infra/policy_approval_inbox.py`
- Test: `tests/test_policy_approval_inbox.py`

- [ ] **Step 1: 실패 테스트 작성**:

```python
import json
import infra.policy_approval_inbox as box
from infra import profile_overrides


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(box, "_PROFILES_DIR", tmp_path)
    return tmp_path


def test_enqueue_then_list(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    box.enqueue(7, "ALLOW_US_STOCKS", True, False, "미국 재개 권고")
    p = box.list_pending(7)
    assert len(p) == 1
    assert p[0]["key"] == "ALLOW_US_STOCKS"
    assert p[0]["proposed_value"] is True
    assert p[0]["status"] == "pending"


def test_enqueue_dedupes_by_key_updates_value(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    box.enqueue(7, "ALLOW_US_STOCKS", True, False, "v1")
    box.enqueue(7, "ALLOW_US_STOCKS", False, False, "v2")
    p = box.list_pending(7)
    assert len(p) == 1
    assert p[0]["proposed_value"] is False
    assert p[0]["rationale"] == "v2"


def test_approve_applies_override(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    called = {}
    monkeypatch.setattr(profile_overrides, "set_overrides",
                        lambda uid, params: called.setdefault("v", (uid, dict(params))))
    box.enqueue(7, "ALLOW_US_STOCKS", True, False, "x")
    assert box.approve(7, "ALLOW_US_STOCKS") is True
    assert called["v"] == (7, {"ALLOW_US_STOCKS": True})
    assert box.list_pending(7) == []   # 더 이상 pending 아님


def test_reject_removes_without_applying(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(profile_overrides, "set_overrides",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("적용 금지")))
    box.enqueue(7, "ALLOW_DERIVATIVES", True, False, "x")
    assert box.reject(7, "ALLOW_DERIVATIVES") is True
    assert box.list_pending(7) == []


def test_approve_missing_returns_false(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert box.approve(7, "NOPE") is False
```

- [ ] **Step 2: 실패 확인** — `python3.11 -m pytest tests/test_policy_approval_inbox.py -q` → ModuleNotFound.

- [ ] **Step 3: 구현** — `infra/policy_approval_inbox.py` 생성:

```python
"""정책 플래그 변경 승인 인박스 (per-uid). 토요일 ops 제안 → 사장 승인 시 오버라이드 적용.

Coresight 인박스(infra/coresight_inbox)와 같은 pending 패턴이되, 승인 시 지시문이 아니라
profile_overrides.set_overrides 로 실제 플래그를 적용한다. 저장: data/profiles/<uid>/policy_pending.json.
거버넌스 2026-06-05: 평일 ops 는 정책 키 차단, 토요일만 이 인박스로 회부."""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("POLICY_APPROVAL_INBOX")
KST = timezone(timedelta(hours=9))
_PROFILES_DIR = Path(__file__).parent.parent / "data" / "profiles"
_FILENAME = "policy_pending.json"


def _now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


def _path(uid: int) -> Path:
    d = _PROFILES_DIR / str(int(uid))
    d.mkdir(parents=True, exist_ok=True)
    return d / _FILENAME


def _load(uid: int) -> List[Dict[str, Any]]:
    p = _path(uid)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning("policy_approval_inbox 로드 실패(uid=%s): %s", uid, e)
        return []


def _save(uid: int, items: List[Dict[str, Any]]) -> None:
    try:
        _path(uid).write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("policy_approval_inbox 저장 실패(uid=%s): %s", uid, e)


def enqueue(uid: int, key: str, proposed_value: Any, current_value: Any,
            rationale: str = "") -> Optional[Dict[str, Any]]:
    """정책 키 변경 제안을 승인 대기함에 적재. 같은 key 가 있으면 최신값으로 갱신(pending 으로 리셋)."""
    items = _load(uid)
    item = {
        "id": key, "key": key, "proposed_value": proposed_value,
        "current_value": current_value, "rationale": rationale or "",
        "proposed_at": _now_kst(), "status": "pending",
        "label": "정책 변경 승인 대기(토요일 점검)",
    }
    items = [i for i in items if i.get("key") != key]
    items.append(item)
    _save(uid, items)
    logger.info("정책 변경 제안 적재 (uid=%s key=%s → %r)", uid, key, proposed_value)
    return item


def list_pending(uid: Optional[int]) -> List[Dict[str, Any]]:
    if uid is None:
        return []
    return list(reversed([i for i in _load(uid) if i.get("status") == "pending"]))


def approve(uid: int, key: str) -> bool:
    """대기 항목을 승인 → profile_overrides.set_overrides 로 적용, status=approved."""
    items = _load(uid)
    target = next((i for i in items if i.get("key") == key and i.get("status") == "pending"), None)
    if target is None:
        return False
    try:
        from infra import profile_overrides
        profile_overrides.set_overrides(int(uid), {key: target["proposed_value"]})
    except Exception as e:
        logger.warning("정책 승인 적용 실패(uid=%s key=%s): %s", uid, key, e)
        return False
    target["status"] = "approved"
    target["approved_at"] = _now_kst()
    _save(uid, items)
    logger.info("정책 변경 승인·적용 (uid=%s key=%s)", uid, key)
    return True


def reject(uid: int, key: str) -> bool:
    """대기 항목을 거부 → 큐에서 제거(적용 안 함)."""
    items = _load(uid)
    new_items = [i for i in items if not (i.get("key") == key and i.get("status") == "pending")]
    if len(new_items) == len(items):
        return False
    _save(uid, new_items)
    logger.info("정책 변경 거부 (uid=%s key=%s)", uid, key)
    return True
```

- [ ] **Step 4: 통과 확인** — `python3.11 -m pytest tests/test_policy_approval_inbox.py -q` → PASS.

---

### Task 3: ops 워커 연결 — weekly 정책키 enqueue + 알림

**Files:**
- Modify: `infra/ops_support_worker.py:_handle_param_tuning` (strip_protected → partition_protected 분기)
- Test: `tests/test_ops_protected_keys.py` (와이어링 테스트 weekly 케이스 추가)

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_ops_protected_keys.py` 끝에 추가:

```python
def test_weekly_trigger_enqueues_policy_not_apply(monkeypatch):
    from infra.ops_support_worker import _handle_param_tuning
    import infra.policy_approval_inbox as box
    captured = _patch_sideeffects(monkeypatch)          # set_overrides 캡처(전술키용)
    enq = []
    monkeypatch.setattr(box, "enqueue",
                        lambda uid, key, pv, cv, r="": enq.append((uid, key, pv)))
    plan = {"summary": "주간", "rationale": "근거",
            "param_overrides": {"ALLOW_US_STOCKS": True, "MIN_QUANT_SCORE": 5}}
    _handle_param_tuning(plan, actor_uid=99999, role="ops_support", started="t",
                         trigger="weekly", cycle_id=None, has_cycle_data=True)
    # 전술키만 적용
    assert captured["params"] == {"MIN_QUANT_SCORE": 5}
    # 정책키는 승인 대기로 회부
    assert any(k == "ALLOW_US_STOCKS" for (_u, k, _v) in enq)
```

- [ ] **Step 2: 실패 확인** — `python3.11 -m pytest tests/test_ops_protected_keys.py::test_weekly_trigger_enqueues_policy_not_apply -q` → 정책키가 set_overrides 로 들어가거나 enqueue 안 됨(FAIL).

- [ ] **Step 3: 구현** — `infra/ops_support_worker.py` 의 strip_protected 블록을 다음으로 교체:

```python
    # 거버넌스 2026-06-05 — 정책/구조 플래그(자산군·엔진) 처리는 트리거별로 다르다:
    #   manual(사장)=즉시 / weekly(토)=승인 대기 회부 / cycle(평일)=차단.
    _to_review: Dict[str, Any] = {}
    if raw_ov:
        from infra.ops_param_clamp import partition_protected
        raw_ov, _to_review, _prot_notes = partition_protected(raw_ov, trigger=trigger)
        if _prot_notes:
            rationale = (rationale + " | 정책: " + "; ".join(_prot_notes)).strip()
```
이어서 clamp 블록(`if raw_ov: clamp_overrides`)은 그대로 둔다. clamp 블록 **다음**에 회부 처리를 추가:

```python
    # weekly 정책 키는 승인 대기함에 적재 + 사장에게 알림(자동 적용 금지).
    if _to_review and actor_uid is not None:
        try:
            import runtime
            from infra import policy_approval_inbox
            _review_lines = []
            for _k, _v in _to_review.items():
                _cur = runtime.get(_k, uid=int(actor_uid))
                policy_approval_inbox.enqueue(int(actor_uid), _k, _v, _cur, rationale)
                _review_lines.append(f"  • {_k}: {_cur} → {_v}")
            import main_swarm
            main_swarm.log_response_event({
                "source": "system_event", "type": "agent_msg",
                "agent": display,
                "message": ("🔐 [정책 변경 승인 요청] 토요일 점검에서 아래 정책 플래그 변경을 제안합니다 — "
                            "대시보드 '정책 변경 승인 대기'에서 승인해야 적용됩니다:\n" + "\n".join(_review_lines)),
            }, uid=int(actor_uid))
        except Exception as e:
            logger.warning(f"정책 승인 회부 실패: {e}")
```

- [ ] **Step 4: 통과 확인** — `python3.11 -m pytest tests/test_ops_protected_keys.py -q` → PASS(기존 cycle/manual 와이어링 포함 전부).

---

### Task 4: FastAPI 엔드포인트 3종

**Files:**
- Modify: `server/app.py` (Coresight 엔드포인트 근처 ~914-935 패턴 따라 추가)
- Test: `tests/test_policy_approval_inbox.py` (엔드포인트 함수 단위 호출은 인박스 테스트로 충분 — 추가 없음)

- [ ] **Step 1: 구현** — `server/app.py` 의 `/api/coresight/approve` 핸들러 블록 다음에 추가(로그인 uid 해석은 기존 헬퍼 사용, Coresight 핸들러에서 uid 를 얻는 방식과 동일하게 맞춘다):

```python
# ─── 정책 변경 승인 (토요일 ops 제안) — 로그인 유저 본인 계정 ───
class _PolicyKeyReq(BaseModel):
    key: str


@app.get("/api/policy_changes/pending")
async def policy_changes_pending(request: Request):
    uid = _require_uid(request)
    from infra.policy_approval_inbox import list_pending
    return {"pending": list_pending(uid)}


@app.post("/api/policy_changes/approve")
async def policy_changes_approve(request: Request, req: _PolicyKeyReq):
    uid = _require_uid(request)
    from infra.policy_approval_inbox import approve
    return {"ok": bool(approve(uid, req.key))}


@app.post("/api/policy_changes/reject")
async def policy_changes_reject(request: Request, req: _PolicyKeyReq):
    uid = _require_uid(request)
    from infra.policy_approval_inbox import reject
    return {"ok": bool(reject(uid, req.key))}
```
주: `_require_uid` 는 Coresight 핸들러가 쓰는 동일한 uid 해석 헬퍼명으로 맞출 것 — `server/app.py` 의 coresight_pending(request) 구현을 읽어 같은 패턴(세션→uid)으로 작성한다. `BaseModel` import 가 이미 있는지 확인(없으면 상단 pydantic import 에 추가).

- [ ] **Step 2: 검증** — `python3.11 -c "import server.app"` 로 import 에러 없음 확인.

---

### Task 5: 대시보드 "정책 변경 승인 대기" 섹션

**Files:**
- Modify: `server/static/index.html`

- [ ] **Step 1: 구현** — 전략 탭(또는 운용지원 영역) 적당한 위치에 컨테이너 추가하고, 주기 로드 함수 작성. 기존 fetch 패턴(세션 쿠키 포함)을 따라:

```html
<div id="policy-approval-box" style="display:none; border:1px solid #c0392b; border-radius:8px; padding:12px; margin:12px 0;">
  <h3>🔐 정책 변경 승인 대기</h3>
  <div id="policy-approval-list"></div>
</div>
```

```javascript
async function loadPolicyApprovals() {
  try {
    const r = await fetch('/api/policy_changes/pending', {credentials:'same-origin'});
    const d = await r.json();
    const box = document.getElementById('policy-approval-box');
    const list = document.getElementById('policy-approval-list');
    const items = (d && d.pending) || [];
    if (!items.length) { box.style.display='none'; list.innerHTML=''; return; }
    box.style.display='block';
    list.innerHTML = items.map(it =>
      `<div style="margin:8px 0; padding:8px; background:#2b2b2b; border-radius:6px;">
        <b>${it.key}</b>: ${String(it.current_value)} → <b>${String(it.proposed_value)}</b>
        <div style="font-size:12px; opacity:.8; margin:4px 0;">${(it.rationale||'').slice(0,300)}</div>
        <button onclick="decidePolicy('${it.key}', true)">승인</button>
        <button onclick="decidePolicy('${it.key}', false)">거부</button>
      </div>`).join('');
  } catch(e) {}
}
async function decidePolicy(key, ok) {
  const url = ok ? '/api/policy_changes/approve' : '/api/policy_changes/reject';
  await fetch(url, {method:'POST', credentials:'same-origin',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify({key})});
  loadPolicyApprovals();
}
```
기존 주기 갱신 루프(대시보드가 balance/strategy 를 폴링하는 곳)에 `loadPolicyApprovals()` 호출을 추가한다. 정확한 삽입 지점은 `index.html` 에서 기존 `setInterval`/로딩 함수 모음을 읽어 그 옆에 맞춘다.

- [ ] **Step 2: 검증** — 브라우저 없이도 HTML 문법 깨짐 없는지(태그 매칭) 육안 확인. (실동작은 Task 6 라이브에서.)

---

### Task 6: 전체 검증 + 배포 + 라이브 확인

- [ ] **Step 1: 전체 테스트** — `python3.11 -m pytest` → 전부 통과(신규 포함).
- [ ] **Step 2: 재시작 전 .running 마커 확인/복원** — `ls data/1/.running data/2/.running` 없으면 `python3.11 -c "from infra import user_paths; [user_paths.running_marker(u).write_text('1') for u in (1,2)]"`.
- [ ] **Step 3: 재시작** — `sudo systemctl restart arquant.service` (사장 승인됨) → status active 확인 + 자동재개 로그.
- [ ] **Step 4: 엔드포인트 스모크** — 로그인 세션으로 `GET /api/policy_changes/pending` 200·`{"pending":[]}` 확인(아직 토요일 아님 → 비어 있는 게 정상).
- [ ] **Step 5: weekly 경로 라이브 모의 검증** — 수동으로 enqueue 한 건 넣어(`python3.11 -c "import infra.policy_approval_inbox as b; b.enqueue(1,'ALLOW_US_STOCKS',True,True,'스모크')"`) 대시보드 섹션 노출·승인/거부 동작 확인 후 해당 테스트 항목 정리.

---

## Self-Review
- **스펙 커버리지:** partition_protected(Task1)·inbox(Task2)·워커 회부+알림(Task3)·엔드포인트(Task4)·대시보드(Task5)·검증(Task6) — 스펙 전 항목 매핑됨.
- **플레이스홀더:** `_require_uid`·setInterval 삽입점은 "기존 코드 읽어 동일 패턴" 으로 구체화 지시(실값은 실행 시 server/app.py·index.html 확인). 그 외 코드 전량 기재.
- **타입 일관성:** enqueue(uid,key,proposed_value,current_value,rationale)·approve(uid,key)·reject(uid,key)·list_pending(uid) 전 Task 동일 시그니처. partition_protected 반환 (kept,to_review,notes) 3-튜플 일관.
