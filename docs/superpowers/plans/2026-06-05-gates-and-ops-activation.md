# 게이트 결함 5종 + 운용지원실장 적극화 — 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 두 계정 모두 실거래가 막힌 5개 결정론 게이트/데이터 결함을 수정하고, 운용지원실장이 시간당·주간으로 파라미터를 적극 튜닝하게 한다(범위 클램프로 안전).

**Architecture:** 순수함수(주식비중·클램프·쓰로틀)는 분리해 단위테스트하고, 게이트/프롬프트/워커 수정은 기존 패턴(runtime override·STRATEGY_KEY_META·`_get_json` 재시도)을 따른다. 스키마 변경 없음.

**Tech Stack:** Python 3.11, pytest, asyncio, KIS REST, DeepSeek(ops LLM).

**커밋 정책:** 이 저장소는 외부 자동 Backup 도구가 `git add -A` + `Backup:` 커밋을 주기적으로 수행한다(CLAUDE.md). **수동 커밋 금지** — 각 태스크 체크포인트는 *테스트 통과*다. 전부 구현 후 사장 확인 하에 재시작 1회로 배포.

**테스트 명령:** `python3.11 -m pytest` (기본 `python`은 argon2 import 실패).

---

## File Structure

- `tools/account_weight.py` (Create) — `compute_stock_weight(...)` 순수함수 (Fix 1).
- `infra/ops_param_clamp.py` (Create) — `clamp_overrides(overrides) -> (clamped, notes)` 순수함수 (가드레일).
- `infra/ops_throttle.py` (Create) — `ops_due(last_ts, now, throttle_sec)` 순수함수 (B1).
- `infra/ops_support_worker.py` (Modify) — `_summarize_exec_results` 크래시 수정(Fix 5), 게이트 manual 면제(Fix 2), 클램프 적용(가드레일), build_prompt 모드별 적극화(B1·B2), run() 실패 기록(Fix 5b).
- `main_swarm.py` (Modify) — 매크로 게이트 수식 교체(Fix 1), 후보 사전필터 사이클예산(Fix 4), 보고에 sizing_notes 주입(Fix 3b), ops 시간당 쓰로틀(B1).
- `infra/kis_broker.py` (Modify) — US 시세/일봉 rate-limit 재시도 보강(Fix 3a).
- `config.py` (Modify) — `OPS_THROTTLE_SEC` 추가(B1).
- `tests/` (Create) — 아래 각 태스크의 테스트.

---

## Task 1: Fix 5 — ops 워커 크래시 수정 (`_summarize_exec_results`)

**근본원인:** `orders_executed`는 cycle_store가 JSON **문자열**로 저장(`list_cycles`는 un-parsed 반환). `_summarize_exec_results`가 문자열을 dict 리스트로 순회 → 문자 `'['`.get → `AttributeError`. 매 사이클 ops 사망의 정체.

**Files:**
- Modify: `infra/ops_support_worker.py:278-296` (`_summarize_exec_results`)
- Test: `tests/test_ops_summarize_exec.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_ops_summarize_exec.py
from infra.ops_support_worker import _summarize_exec_results

def test_handles_json_string_input():
    # cycle_store 는 orders_executed 를 JSON 문자열로 저장한다 — 문자열도 처리해야 한다.
    assert _summarize_exec_results("[]") == "없음"
    s = '[{"ticker":"CVX","side":"buy","qty":1,"filled":true}]'
    assert "CVX buy x1: 체결확인" in _summarize_exec_results(s)

def test_handles_none_and_empty():
    assert _summarize_exec_results(None) == "없음"
    assert _summarize_exec_results([]) == "없음"

def test_skips_non_dict_elements():
    # 깨진 데이터(문자열 원소)가 섞여도 죽지 않는다.
    assert _summarize_exec_results(["garbage", {"ticker":"AAPL","side":"buy","qty":2,"accepted":True}]) \
        == "AAPL buy x2: 접수—체결폴링중(실패아님)"

def test_list_of_dicts_unchanged():
    out = _summarize_exec_results([{"ticker":"NVDA","side":"sell","qty":3}])
    assert "NVDA sell x3: 미접수·반려" in out
```

- [ ] **Step 2: 실패 확인**

Run: `python3.11 -m pytest tests/test_ops_summarize_exec.py -v`
Expected: FAIL — `test_handles_json_string_input`에서 `AttributeError: 'str' object has no attribute 'get'`.

- [ ] **Step 3: 최소 구현**

`infra/ops_support_worker.py`의 `_summarize_exec_results` 시작부를 수정:

```python
def _summarize_exec_results(orders_executed) -> str:
    """직전 사이클 주문 실행결과를 '체결확인 / 접수—체결폴링중(실패아님) / 미접수·반려' 로 명확히 구분.
    cycle_store 는 orders_executed 를 JSON 문자열로 저장하므로 문자열 입력도 파싱한다(2026-06-05 버그수정)."""
    if isinstance(orders_executed, str):
        try:
            orders_executed = json.loads(orders_executed)
        except Exception:
            return "없음"
    if not orders_executed:
        return "없음"
    out = []
    for e in orders_executed:
        if not isinstance(e, dict):
            continue
        tk = e.get("ticker", "?"); side = e.get("side", "?"); qty = e.get("qty", "?")
        if e.get("filled"):
            st = "체결확인"
        elif e.get("accepted"):
            st = "접수—체결폴링중(실패아님)"
        else:
            st = "미접수·반려"
        out.append(f"{tk} {side} x{qty}: {st}")
    return " | ".join(out) if out else "없음"
```

(`json`은 파일 상단에 이미 import 되어 있음 — 없으면 `import json` 추가.)

- [ ] **Step 4: 통과 확인**

Run: `python3.11 -m pytest tests/test_ops_summarize_exec.py -v`
Expected: PASS (4 passed).

---

## Task 2: 가드레일 — `clamp_overrides` 순수함수

**근거:** `config.STRATEGY_KEY_META[key]`에 이미 `min`/`max`/`step`/`type`이 있다. 적용 직전 범위 클램프 + 타입 정규화 + 미등록 키 드롭.

**Files:**
- Create: `infra/ops_param_clamp.py`
- Test: `tests/test_ops_param_clamp.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_ops_param_clamp.py
from infra.ops_param_clamp import clamp_overrides

def test_numeric_clamped_to_meta_range():
    # TAKE_PROFIT_PCT 메타 max 미만으로 클램프 (config 메타 기준)
    clamped, notes = clamp_overrides({"TAKE_PROFIT_PCT": 999})
    assert clamped["TAKE_PROFIT_PCT"] <= 999  # 메타 max 로 줄어듦
    import config
    assert clamped["TAKE_PROFIT_PCT"] == config.STRATEGY_KEY_META["TAKE_PROFIT_PCT"]["max"]
    assert any("TAKE_PROFIT_PCT" in n for n in notes)

def test_numeric_within_range_unchanged():
    clamped, notes = clamp_overrides({"TAKE_PROFIT_PCT": 8})
    assert clamped["TAKE_PROFIT_PCT"] == 8
    assert notes == []

def test_bool_normalized():
    clamped, _ = clamp_overrides({"MACRO_STOCK_GATE_ENABLED": "true"})
    assert clamped["MACRO_STOCK_GATE_ENABLED"] is True
    clamped, _ = clamp_overrides({"MACRO_STOCK_GATE_ENABLED": 0})
    assert clamped["MACRO_STOCK_GATE_ENABLED"] is False

def test_unknown_key_dropped():
    clamped, notes = clamp_overrides({"NOT_A_REAL_KEY": 5})
    assert "NOT_A_REAL_KEY" not in clamped
    assert any("NOT_A_REAL_KEY" in n for n in notes)

def test_below_min_clamped_up():
    import config
    lo = config.STRATEGY_KEY_META["TAKE_PROFIT_PCT"]["min"]
    clamped, _ = clamp_overrides({"TAKE_PROFIT_PCT": -100})
    assert clamped["TAKE_PROFIT_PCT"] == lo
```

- [ ] **Step 2: 실패 확인**

Run: `python3.11 -m pytest tests/test_ops_param_clamp.py -v`
Expected: FAIL — `ModuleNotFoundError: infra.ops_param_clamp`.

- [ ] **Step 3: 구현**

```python
# infra/ops_param_clamp.py
"""운용지원실장 파라미터 오버라이드 가드레일 — 적용 직전 범위 클램프/타입 정규화.
반려가 아니라 보정(HARD_MAX_ORDER_QTY 패턴과 동일 철학). 미등록(튜넌 화이트리스트 밖) 키는 드롭."""
from __future__ import annotations
from typing import Any, Dict, List, Tuple

def _to_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    return str(v).strip().lower() in ("1", "true", "yes", "on", "y", "t")

def clamp_overrides(overrides: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """STRATEGY_KEY_META 기준으로 overrides 를 보정. 반환 (clamped, notes)."""
    import config
    meta = getattr(config, "STRATEGY_KEY_META", {})
    tunable = set(getattr(config, "STRATEGY_TUNABLE_KEYS", list(meta.keys())))
    out: Dict[str, Any] = {}
    notes: List[str] = []
    for k, v in (overrides or {}).items():
        if k not in tunable or k not in meta:
            notes.append(f"{k}: 튜닝 허용 키가 아님 → 무시")
            continue
        m = meta[k]
        typ = m.get("type")
        if typ == "bool":
            out[k] = _to_bool(v)
            continue
        # 수치형(int/pct_raw/float 등)
        try:
            num = float(v)
        except Exception:
            notes.append(f"{k}: 숫자 아님({v!r}) → 무시")
            continue
        lo = m.get("min"); hi = m.get("max")
        orig = num
        if lo is not None and num < lo:
            num = lo
        if hi is not None and num > hi:
            num = hi
        if typ == "int":
            num = int(round(num))
        if num != orig:
            notes.append(f"{k}: {orig} → {num} (범위 [{lo},{hi}] 클램프)")
        out[k] = num
    return out, notes
```

- [ ] **Step 4: 통과 확인**

Run: `python3.11 -m pytest tests/test_ops_param_clamp.py -v`
Expected: PASS (5 passed).

---

## Task 3: Fix 2 — 사장 직접 지시(manual) 게이트 면제 + 클램프 적용

**Files:**
- Modify: `infra/ops_support_worker.py:363-370` (`_gate_overrides_by_data`), `:373-401` (`_handle_param_tuning`)
- Test: `tests/test_ops_manual_directive_applies.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_ops_manual_directive_applies.py
from infra.ops_support_worker import _gate_overrides_by_data

def test_manual_bypasses_data_gate():
    # 사장 직접 지시(manual)는 cycle 데이터 없어도 통과한다.
    ov, reason = _gate_overrides_by_data({"TAKE_PROFIT_PCT": 8}, has_cycle_data=False, is_manual=True)
    assert ov == {"TAKE_PROFIT_PCT": 8}
    assert reason == ""

def test_autonomous_still_gated_without_data():
    ov, reason = _gate_overrides_by_data({"TAKE_PROFIT_PCT": 8}, has_cycle_data=False, is_manual=False)
    assert ov == {}
    assert "보류" in reason

def test_autonomous_with_data_passes():
    ov, reason = _gate_overrides_by_data({"TAKE_PROFIT_PCT": 8}, has_cycle_data=True, is_manual=False)
    assert ov == {"TAKE_PROFIT_PCT": 8}
    assert reason == ""
```

- [ ] **Step 2: 실패 확인**

Run: `python3.11 -m pytest tests/test_ops_manual_directive_applies.py -v`
Expected: FAIL — `_gate_overrides_by_data() got unexpected keyword 'is_manual'`.

- [ ] **Step 3: 구현**

`_gate_overrides_by_data` 교체:

```python
def _gate_overrides_by_data(raw_overrides: Dict[str, Any], has_cycle_data: bool,
                            is_manual: bool = False):
    """데이터 없는 *자율* 제안에서 LLM 이 가짜 거래를 지어내 파라미터를 바꾸는 것을 차단.
    단 사장 직접 지시(is_manual=True)는 권위이므로 데이터 게이트를 면제한다(클램프는 별도 적용).
    반환: (적용 후보 overrides, 거부사유)."""
    if is_manual:
        return (raw_overrides or {}), ""
    if not has_cycle_data:
        return {}, ("실제 직전 사이클 데이터가 없어 파라미터 변경을 보류합니다 — "
                    "근거 데이터 없이는 조정하지 않습니다(추측·날조 방지).")
    return (raw_overrides or {}), ""
```

`_handle_param_tuning` 안에서 호출부(현재 line 383)를 수정 — manual 판정 전달 + 클램프 적용:

```python
    is_manual = (trigger == "manual")
    raw_ov, _gate_reason = _gate_overrides_by_data(
        plan.get("param_overrides") or {}, has_cycle_data, is_manual=is_manual)
    if _gate_reason:
        rationale = (f"{_gate_reason} " + rationale).strip()
    # 가드레일 — 적용 직전 범위 클램프(반려 아닌 보정)
    if raw_ov:
        from infra.ops_param_clamp import clamp_overrides
        raw_ov, _clamp_notes = clamp_overrides(raw_ov)
        if _clamp_notes:
            rationale = (rationale + " | 클램프: " + "; ".join(_clamp_notes)).strip()
```

(이후 기존 `applied_ov = profile_overrides.set_overrides(...)` 로직은 그대로.)

- [ ] **Step 4: 통과 확인**

Run: `python3.11 -m pytest tests/test_ops_manual_directive_applies.py -v`
Expected: PASS (3 passed).

---

## Task 4: Fix 1 — 매크로 게이트 주식비중 정정

**Files:**
- Create: `tools/account_weight.py`
- Modify: `main_swarm.py:3357` (게이트 수식), import 추가
- Test: `tests/test_macro_gate_stock_weight.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_macro_gate_stock_weight.py
from tools.account_weight import compute_stock_weight

def test_mock_usd_deposit_not_counted_as_stock():
    # 모의: total 485M(=KR현금100M + 해외USD예수금385M), 해외 주식분 0 → 주식비중 0
    w = compute_stock_weight(total_eval=485_000_000, kr_cash=100_000_000,
                             total_eval_kr=100_000_000, overseas_stock_krw=0)
    assert w == 0.0

def test_kr_stock_counted():
    w = compute_stock_weight(total_eval=100_000_000, kr_cash=50_000_000,
                             total_eval_kr=100_000_000, overseas_stock_krw=0)
    assert abs(w - 0.5) < 1e-9

def test_overseas_stock_counted():
    # total 100M, KR현금10M, KR총평가10M(해외분 90M 중 주식 60M·USD예수금 30M) → 주식 60M/100M
    w = compute_stock_weight(total_eval=100_000_000, kr_cash=10_000_000,
                             total_eval_kr=10_000_000, overseas_stock_krw=60_000_000)
    assert abs(w - 0.6) < 1e-9

def test_zero_total_fail_open():
    assert compute_stock_weight(total_eval=0, kr_cash=0, total_eval_kr=0, overseas_stock_krw=0) == 0.0
    assert compute_stock_weight(total_eval=None, kr_cash=None, total_eval_kr=None, overseas_stock_krw=None) == 0.0
```

- [ ] **Step 2: 실패 확인**

Run: `python3.11 -m pytest tests/test_macro_gate_stock_weight.py -v`
Expected: FAIL — `ModuleNotFoundError: tools.account_weight`.

- [ ] **Step 3: 구현**

```python
# tools/account_weight.py
"""계정 주식 평가비중 계산 — 매크로 매수게이트용.
핵심: 해외 USD '예수금'은 주식이 아니므로 주식비중에서 제외한다(2026-06-05 버그수정).
기존 (total_eval − KR현금)/total_eval 은 해외 USD 예수금을 전부 주식으로 오분류해 모의계정을 영구 동결시켰다."""
from __future__ import annotations
from typing import Optional

def compute_stock_weight(total_eval: Optional[float], kr_cash: Optional[float],
                         total_eval_kr: Optional[float], overseas_stock_krw: Optional[float]) -> float:
    """실제 주식가치 / 총평가.
    주식가치 = (KR 총평가 − KR 현금)  +  해외 주식분(원화환산). 범위 [0,1]."""
    te = float(total_eval or 0.0)
    if te <= 0:
        return 0.0
    tek = float(total_eval_kr or 0.0) or te   # KR 총평가 없으면 total 로 폴백(해외 미보유 가정, 보수)
    kr_stock = max(0.0, tek - float(kr_cash or 0.0))
    os_stock = max(0.0, float(overseas_stock_krw or 0.0))
    return max(0.0, min(1.0, (kr_stock + os_stock) / te))
```

`main_swarm.py` — 파일 상단 import 영역에 추가:
```python
from tools.account_weight import compute_stock_weight
```

`main_swarm.py:3357` 교체 (현재 `_equity_weight = max(0.0, (_total0 - _cash0)) / _total0`):
```python
                    _total_kr0 = float(_bp0.get("total_eval_kr") or _total0)
                    _os_stock0 = 0.0
                    try:
                        self.broker._get_overseas_cache()   # _overseas_stock_krw 채움
                        _os_stock0 = float(getattr(self.broker, "_overseas_stock_krw", 0.0) or 0.0)
                    except Exception:
                        _os_stock0 = 0.0
                    _equity_weight = compute_stock_weight(_total0, _cash0, _total_kr0, _os_stock0)
```

- [ ] **Step 4: 통과 확인**

Run: `python3.11 -m pytest tests/test_macro_gate_stock_weight.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: import 무결성 확인**

Run: `python3.11 -c "import main_swarm"`
Expected: 에러 없이 import.

---

## Task 5: Fix 4 — 후보 사전필터 사이클예산 기준 정렬

**근거:** 조립부는 "1주 from cash" 허용(`main_swarm.py:2082`)하나 리스크부는 `cash*MAX_CYCLE_BUDGET_RATIO` 초과 시 반려(`agents/guardrails.py:151`). 595K~5.95M 가격대 데드존. 선정 전 사전필터를 리스크부 기준으로 강화.

**Files:**
- Create: `tools/affordable_prefilter.py`
- Modify: `main_swarm.py:3518-3543` (후보 사전필터 루프의 affordability 판정)
- Test: `tests/test_candidate_prefilter_cycle_budget.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_candidate_prefilter_cycle_budget.py
from tools.affordable_prefilter import affordable_within_cycle_budget as ok

def test_high_price_dropped():
    # cash 5.95M, ratio 0.10, overshoot 1.2 → 사이클예산 ~595K*1.2=714K. 1.13M 종목 배제
    assert ok(price=1_130_000, cash=5_952_763, cycle_ratio=0.10, overshoot=1.2) is False

def test_within_budget_kept():
    assert ok(price=500_000, cash=5_952_763, cycle_ratio=0.10, overshoot=1.2) is True

def test_price_fetch_failure_kept():
    # 시세 조회 실패(0/음수)는 통과(보수) — 데이터 결손으로 누락 방지
    assert ok(price=0, cash=5_952_763, cycle_ratio=0.10, overshoot=1.2) is True

def test_no_cash_keeps():
    # 현금 정보 없음(0) 이면 판단 불가 → 통과(보수)
    assert ok(price=1_130_000, cash=0, cycle_ratio=0.10, overshoot=1.2) is True
```

- [ ] **Step 2: 실패 확인**

Run: `python3.11 -m pytest tests/test_candidate_prefilter_cycle_budget.py -v`
Expected: FAIL — `ModuleNotFoundError: tools.affordable_prefilter`.

- [ ] **Step 3: 구현**

```python
# tools/affordable_prefilter.py
"""후보 사전필터 — 1주 가격이 '사이클 매수예산'(리스크부 기준) 내인가.
리스크부(agents/guardrails.py)가 cash*MAX_CYCLE_BUDGET_RATIO 로 반려하므로, 선정 전에 동일 기준으로
초고가주를 배제해 '선정→무조건 반려' 헛사이클을 막는다. 시세실패/현금없음은 보수적으로 통과."""
from __future__ import annotations

def affordable_within_cycle_budget(price: float, cash: float, cycle_ratio: float,
                                   overshoot: float = 1.2) -> bool:
    p = float(price or 0.0); c = float(cash or 0.0)
    if p <= 0:        # 시세 조회 실패 → 통과(보수)
        return True
    if c <= 0:        # 현금 판단 불가 → 통과(보수)
        return True
    budget = c * float(cycle_ratio or 0.0) * float(overshoot or 1.0)
    if budget <= 0:
        return True
    return p <= budget
```

`main_swarm.py` 후보 사전필터 루프(현재 `_affordable_one_share(px, _cash_pre, _total_pre)` 판정부)를 사이클예산 기준으로 교체. KR/US 모두 적용:
```python
                from tools.affordable_prefilter import affordable_within_cycle_budget
                _cyc_ratio = float(runtime.get("MAX_CYCLE_BUDGET_RATIO", uid=self.uid) or 0.25)
                _overshoot = float(runtime.get("PER_ORDER_BUDGET_OVERSHOOT", uid=self.uid) or 1.2)
                kept, dropped = [], []
                for c in candidate_codes:
                    try:
                        if _is_kr_code(c):
                            px = await self.broker.kr_last_price(c); await asyncio.sleep(0.1)
                            if affordable_within_cycle_budget(px, _cash_pre, _cyc_ratio, _overshoot):
                                kept.append(c)
                            else:
                                dropped.append(f"{c}({px:,.0f}원)")
                        else:
                            px = await self.broker.us_last_price(c.upper()); await asyncio.sleep(0.1)
                            _px_krw = px * _krw_usd_pre
                            if affordable_within_cycle_budget(_px_krw, _cash_pre, _cyc_ratio, _overshoot):
                                kept.append(c)
                            else:
                                dropped.append(f"{c.upper()}(${px:,.2f})")
                    except Exception:
                        kept.append(c)
                if dropped:
                    await self._emit({"type":"agent_msg","agent":"운용전략실장",
                        "message": (f"후보 사전 필터 — 1주 가격이 사이클 매수예산(현금×{_cyc_ratio:.0%}×{_overshoot:.1f}) "
                                    f"초과라 제외: {', '.join(dropped)}\n최종 후보 종목: {', '.join(kept) or '없음'}")})
                    candidate_codes = kept
```

> 주의: 구현 전 `agents/guardrails.py:151` 의 실제 비율 키(`MAX_CYCLE_BUDGET_RATIO`)와 일치 확인. 다르면 그 키로 맞춘다. 메시지 문구의 "예수금"→"사이클 매수예산" 정정 포함.

- [ ] **Step 4: 통과 + import 확인**

Run: `python3.11 -m pytest tests/test_candidate_prefilter_cycle_budget.py -v && python3.11 -c "import main_swarm"`
Expected: PASS (4 passed), import OK.

---

## Task 6: Fix 3 — US rate-limit 재시도 보강 + 미체결 사유 보고 노출

### 6a. US 시세/일봉 rate-limit 재시도 (`infra/kis_broker.py`)

- [ ] **Step 1: 현재 코드 확인**

Run: `python3.11 -c "import infra.kis_broker"` 후 `us_last_price`(≈1295)·`us_daily_chart`(NAS/NYS/AMS 프로브)를 Read.
판정: 두 함수가 `_get_json`(line 198, rate-limit 재시도 내장)을 경유하는지 확인.

- [ ] **Step 2: 보강**

US 거래소 프로브 루프에서 한 거래소가 rate-limit(`_resp_rate_limited(d)` True)이면 **다음 거래소로 폴스루하지 말고 그 거래소를 백오프 후 재시도**한다(올바른 거래소를 rate-limit 때문에 놓치지 않게). `_get_json` 경유가 아니면 호출부에 `_RATE_LIMIT_*` 재시도 래퍼를 적용. 프로브 간 `asyncio.sleep` 간격(이미 일부 존재)을 TPS 보호로 유지/추가.

- [ ] **Step 3: 회귀 확인**

Run: `python3.11 -m pytest -k "broker or kis or price" -v` (관련 기존 테스트가 있으면 통과 유지). 없으면 `python3.11 -c "import infra.kis_broker"` 로 import 무결성만.

> US 라이브 시세는 단위테스트가 어렵다 — 배포 후 라이브 1사이클로 CVX류 재현/검증(아래 배포 검증).

### 6b. sizing_notes(미체결 사유)를 최종보고에 주입 (`main_swarm.py`)

**근거:** `_build_orders`가 스킵 사유를 `order_obj["sizing_notes"]`로 이미 반환(`main_swarm.py:2256`). 보고 단계는 이를 안 쓰고 "사유 추측 금지"만 지시 → 설명 불가.

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_build_order_skip_notes.py
import json, sqlite3, asyncio
# 순수 검증: sizing_notes 가 보고 컨텍스트 문자열에 포함되는 헬퍼를 테스트한다.
from main_swarm import _format_sizing_notes_for_report

def test_skip_notes_formatted():
    notes = ["CVX: 해외 시세 조회 실패(거래소 미확인) → 제외", "AAPL: 1주 매수"]
    out = _format_sizing_notes_for_report(notes)
    assert "CVX" in out and "시세 조회 실패" in out

def test_empty_notes():
    assert _format_sizing_notes_for_report([]) == ""
    assert _format_sizing_notes_for_report(None) == ""
```

- [ ] **Step 2: 실패 확인**

Run: `python3.11 -m pytest tests/test_build_order_skip_notes.py -v`
Expected: FAIL — `cannot import name '_format_sizing_notes_for_report'`.

- [ ] **Step 3: 구현**

`main_swarm.py`에 모듈 레벨 헬퍼 추가(다른 `_format_*` 헬퍼 근처):
```python
def _format_sizing_notes_for_report(notes) -> str:
    """주문 조립 단계의 스킵/사이징 사유(sizing_notes)를 보고용 한 줄 블록으로. 비면 ''."""
    if not notes:
        return ""
    return "주문 조립 메모(미체결·제외 사유): " + " | ".join(str(n) for n in notes if n)
```

`_cyc_stage_report`의 `report = await self.orchestrator.think(...)` 프롬프트에 주입. 기존 "체결 실패·미체결 사유를 추측하거나 지어내지 말 것" 문장 **뒤에** 실제 사유를 제공:
```python
            _sizing_notes = _format_sizing_notes_for_report((cyc.order_obj or {}).get("sizing_notes"))
            ...
            report = await self.orchestrator.think(
                f"사이클 완료.\n지수: {index_report[:200]}\n매크로: {macro_report[:200]}\n"
                ...
                f"실매매: {exec_summary} / 누적 체결 {self._trades_executed}건\n"
                + (f"{_sizing_notes}\n" if _sizing_notes else "")
                + f"주의: 위 '주문 조립 메모'에 제외/미체결 사유가 있으면 그 사실을 그대로 보고에 반영하라"
                  f"(예: 'CVX는 해외 시세 조회 실패로 매수 보류'). 사유가 메모에 없으면 추측하지 말 것.\n"
                ... (이하 기존 문장 유지) ...
```

- [ ] **Step 4: 통과 확인**

Run: `python3.11 -m pytest tests/test_build_order_skip_notes.py -v && python3.11 -c "import main_swarm"`
Expected: PASS (2 passed), import OK.

---

## Task 7: B1 — ops 시간당 쓰로틀

**Files:**
- Create: `infra/ops_throttle.py`
- Modify: `config.py` (`OPS_THROTTLE_SEC`), `main_swarm.py:4520` (사이클 spawn 호출부)
- Test: `tests/test_ops_hourly_throttle.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_ops_hourly_throttle.py
from infra.ops_throttle import ops_due

def test_due_when_never_run():
    assert ops_due(last_ts=0.0, now=10_000.0, throttle_sec=3600) is True

def test_not_due_within_window():
    assert ops_due(last_ts=10_000.0, now=10_000.0 + 1800, throttle_sec=3600) is False

def test_due_after_window():
    assert ops_due(last_ts=10_000.0, now=10_000.0 + 3601, throttle_sec=3600) is True

def test_none_last_is_due():
    assert ops_due(last_ts=None, now=10_000.0, throttle_sec=3600) is True
```

- [ ] **Step 2: 실패 확인**

Run: `python3.11 -m pytest tests/test_ops_hourly_throttle.py -v`
Expected: FAIL — `ModuleNotFoundError: infra.ops_throttle`.

- [ ] **Step 3: 구현**

```python
# infra/ops_throttle.py
"""운용지원실장 시간당 쓰로틀 — per-uid 마커로 사이클 spawn 빈도를 제어.
매 사이클 spawn(낭비·churn) 대신 throttle_sec(기본 1시간) 이상 경과 시에만 spawn."""
from __future__ import annotations
from pathlib import Path
from typing import Optional

def ops_due(last_ts: Optional[float], now: float, throttle_sec: float) -> bool:
    """직전 ops 실행 epoch(last_ts) 로부터 throttle_sec 이상 지났으면 True. 미실행(None/0)도 True."""
    if not last_ts:
        return True
    return (float(now) - float(last_ts)) >= float(throttle_sec)

def read_last_run(marker: Path) -> float:
    try:
        return float(marker.read_text().strip())
    except Exception:
        return 0.0

def write_last_run(marker: Path, now: float) -> None:
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(float(now)))
    except Exception:
        pass
```

`config.py`에 추가(상수 영역):
```python
OPS_THROTTLE_SEC = 3600   # 운용지원실장 사이클 자동튜닝 최소 간격(초) — 시간당 1회
```

`main_swarm.py:4520`의 사이클 spawn(`self._spawn_ops_support_worker(new_cycle_id, role="ops_support")`)을 쓰로틀로 감싼다. 마커는 per-uid 디렉터리 사용(기존 `user_paths`/equity_path 패턴 따라):
```python
            try:
                from infra.ops_throttle import ops_due, read_last_run, write_last_run
                from config import OPS_THROTTLE_SEC
                _marker = _Path(self.equity_path).parent / ".ops_last_run"
                _now_ts = time.time()
                if ops_due(read_last_run(_marker), _now_ts, OPS_THROTTLE_SEC):
                    self._spawn_ops_support_worker(new_cycle_id, role="ops_support")
                    write_last_run(_marker, _now_ts)
                else:
                    logger.info(f"운용지원 워커 스킵 — 쓰로틀(시간당 1회) 미경과 (uid={self.uid})")
            except Exception as _e:
                logger.warning(f"ops 쓰로틀 처리 실패 — 그대로 spawn: {_e}")
                self._spawn_ops_support_worker(new_cycle_id, role="ops_support")
```

> 주의: `time`·`_Path` import 가 main_swarm 에 이미 있는지 확인(있음). `self.equity_path`가 per-uid data 디렉터리를 가리키는지 확인.

- [ ] **Step 4: 통과 + import 확인**

Run: `python3.11 -m pytest tests/test_ops_hourly_throttle.py -v && python3.11 -c "import main_swarm, config"`
Expected: PASS (4 passed), import OK.

---

## Task 8: B1·B2 — ops 프롬프트 적극화(트리거별 모드)

**Files:**
- Modify: `infra/ops_support_worker.py:357-360` (build_prompt 의 [과제] 블록), `build_prompt` 시그니처에 `trigger` 전달, `run()` 에서 trigger 결정 후 전달
- Modify: `infra/weekly_review.py` (weekly 트리거 프롬프트 문구 — 이미 "주간 피드백 루프" 키워드로 trigger="weekly" 분류됨; 본문은 ops 워커 [과제]가 모드별로 분기)
- Test: `tests/test_ops_prompt_modes.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_ops_prompt_modes.py
from infra.ops_support_worker import _task_block_for_trigger

def test_weekly_is_aggressive():
    t = _task_block_for_trigger("weekly")
    assert "매우 적극" in t or "큰 폭" in t
    assert "전" in t  # 전 튜넌키 점검 뉘앙스

def test_cycle_is_active_but_bounded():
    t = _task_block_for_trigger("cycle")
    assert "적극" in t

def test_manual_focuses_directive():
    t = _task_block_for_trigger("manual")
    assert "지시" in t
```

- [ ] **Step 2: 실패 확인**

Run: `python3.11 -m pytest tests/test_ops_prompt_modes.py -v`
Expected: FAIL — `cannot import name '_task_block_for_trigger'`.

- [ ] **Step 3: 구현**

`infra/ops_support_worker.py`에 헬퍼 추가:
```python
def _task_block_for_trigger(trigger: str) -> str:
    """트리거별 [과제] 블록 — cycle(시간당)=적극, weekly=매우 적극(전 튜넌키 큰 폭), manual=지시 우선."""
    if trigger == "weekly":
        return ("\n[과제 — 주간 정밀 점검] 최근 7일 실데이터를 근거로 **전 튜닝 파라미터**를 하나씩 점검하고, "
                "데이터로 정당화되면 **매우 적극적으로(큰 폭도 허용)** param_overrides 에 담으십시오. "
                "범위는 시스템이 자동 클램프합니다. 손익·체결·반려·에러 추세에서 개선 여지를 적극 발굴하되, "
                "각 변경의 정량 근거를 rationale 에 적으십시오. 소스/구조 버그는 '제안'으로만.")
    if trigger == "manual":
        return ("\n[과제 — 사장 지시 처리] 위 사장 지시를 최우선으로 반영하십시오. 지시가 특정 파라미터·값을 "
                "지정하면 그 값을 param_overrides 에 담으십시오(범위는 자동 클램프). 지시 외 파라미터는 "
                "데이터 근거가 있을 때만 함께 조정하고, 근거 없으면 건드리지 마십시오.")
    # cycle (시간당 자동)
    return ("\n[과제 — 시간당 점검] 최근 사이클(들)을 근거로 개선 가능한 파라미터를 **적극적으로 제안**하십시오. "
            "근거가 있으면 '변경 없음'에 머무르지 말고 구체 조정을 param_overrides 에 담되, 한 번에 과도한 개수는 "
            "피하십시오(범위는 자동 클램프). 정말 손볼 게 없으면 빈 객체로 두고 이유를 rationale 에 적으십시오. "
            "파라미터로 못 고치는 버그는 '제안'으로만.")
```

`build_prompt(ctx, manual_directive=None, trigger="cycle")` 시그니처에 `trigger` 추가하고, 마지막 `parts.append("\n[과제] ...")`(현재 line 357-360)를 `parts.append(_task_block_for_trigger(trigger))` 로 교체.

`run()` (line 469~)에서 trigger 결정 후 build_prompt 에 전달:
```python
    trigger = "weekly" if (manual and "주간 피드백 루프" in manual) else ("manual" if manual else "cycle")
    prompt = build_prompt(ctx, manual_directive=manual, trigger=trigger)
```
(기존 `build_prompt(ctx, manual)` 호출을 위로 교체. trigger 변수는 이미 line 483에 있음 — 중복 정의 제거하고 build_prompt 전에 한 번만 계산.)

- [ ] **Step 4: 통과 확인**

Run: `python3.11 -m pytest tests/test_ops_prompt_modes.py -v && python3.11 -c "import infra.ops_support_worker"`
Expected: PASS (3 passed), import OK.

---

## Task 9: Fix 5b — ops 워커 실패를 ops_history 에 기록(무음 방지)

**Files:**
- Modify: `infra/ops_support_worker.py` `run()` (line 469~501) — 전역 try/except
- Test: 수동(라이브) — 단위테스트 생략(서브프로세스/LLM 의존)

- [ ] **Step 1: 구현**

`run()` 본문을 try/except 로 감싸 예외 시 ops_history 에 실패 엔트리 기록:
```python
async def run(cycle_id, manual, role="ops_support", actor_uid=None, actor_admin=False):
    started = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    trigger = "weekly" if (manual and "주간 피드백 루프" in manual) else ("manual" if manual else "cycle")
    try:
        ... (기존 본문) ...
    except Exception as e:
        import traceback
        logger.error(f"ops run 실패: {type(e).__name__}: {e!r}", exc_info=True)
        try:
            from infra import ops_history
            ops_history.append_run({
                "role": role, "role_display": "운용지원실장", "trigger": trigger, "cycle_id": cycle_id,
                "summary": f"ops 진단 실패: {type(e).__name__}: {e}",
                "rationale": traceback.format_exc()[-800:],
                "applied": [], "rejected": [], "compile_errors": [], "proposed": [], "restarted": False,
            })
        except Exception:
            pass
        raise
```

- [ ] **Step 2: 검증**

Run: `python3.11 -c "import infra.ops_support_worker"` (import 무결성). 라이브 검증은 배포 후.

---

## Task 10: 전체 회귀 + 배포 검증

- [ ] **Step 1: 전체 테스트**

Run: `python3.11 -m pytest -q`
Expected: 신규 테스트 포함 전부 PASS, 기존 회귀 무손상(특히 `test_strategy_params_runtime.py`, `test_ops_param_catalog.py`, `test_model_default_hermes.py`).

- [ ] **Step 2: 수동 ops 워커 1회 실행(크래시 해소 확인)**

Run: `python3.11 infra/ops_support_worker.py --role ops_support --cycle-id <최근 uid=2 사이클id> --actor-user 2 --actor-admin 0`
Expected: 크래시 없이 종료, `data/ops_history.json`에 새 엔트리(또는 실패 엔트리) 기록, `data/ops_support.spawn.log`에 AttributeError 없음.

- [ ] **Step 3: 배포(사장 확인 후)**

Run: `sudo systemctl restart arquant.service && sleep 5 && sudo systemctl status arquant.service --no-pager | head -5`

- [ ] **Step 4: 라이브 1사이클 검증(두 계정)**

- 모의(uid=2): 후보>0·`quant_report` 생성·매크로 게이트 미발동(주식비중 0%) 확인 — `data/cycles.db` 최신 사이클 `candidate_codes != []`.
- 실거래(uid=1): US 시세 재시도 동작·미체결 시 최종보고에 사유 노출·고가주 사전배제 확인.
- ops: 시간당 1회 spawn(쓰로틀)·`ops_history` 기록 재개. manual 지시(`@운용지원실장 TAKE_PROFIT 8%로`)→즉시 반영(클램프) 확인.

---

## Self-Review 체크

- **Spec 커버리지:** Fix1=Task4, Fix2=Task3, Fix3=Task6, Fix4=Task5, Fix5=Task1+9, 가드레일=Task2, B1=Task7+8, B2=Task8. 전부 매핑됨.
- **타입 일관성:** `compute_stock_weight`/`clamp_overrides`/`ops_due`/`affordable_within_cycle_budget`/`_format_sizing_notes_for_report`/`_task_block_for_trigger` 시그니처가 테스트·호출부와 일치.
- **플레이스홀더:** 없음(코드 전량 기재). Task6a·Task9는 라이브/서브프로세스 의존이라 단위테스트 대신 import+수동검증으로 명시.
