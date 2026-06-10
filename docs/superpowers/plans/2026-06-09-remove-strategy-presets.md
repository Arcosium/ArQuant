# 전략 프리셋 제거 + 현재 설정값 가시화 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 라이브 "전략 프리셋"(빌트인 5종 + 사용자 프리셋 + 저장/삭제)을 단일 기본값 세트로 대체하고, '전략' 탭을 그룹별 편집 패널로 단순화하며, 백테스트를 단일 성과 측정으로 축소해 토요일 주간 피드백에 연결한다.

**Architecture:** `config.STRATEGY_DEFAULTS`(단일 dict) → `runtime`(프리셋 머신러리 제거, custom-only) → `server/app.py`(presets 필드·엔드포인트 제거) → `index.html`(WebView가 로드, 항상 펼쳐진 편집 패널). 백테스트는 `run_backtest(params)`로 일반화 후 `weekly_review` summary에 주입.

**Tech Stack:** Python 3.11 (pytest), FastAPI, vanilla JS/HTML 대시보드. 테스트는 반드시 `python3.11 -m pytest`.

스펙: `docs/superpowers/specs/2026-06-09-remove-strategy-presets-design.md`

---

## File Structure

- `config.py` — `STRATEGY_PRESETS`/`DEFAULT_STRATEGY` 삭제, `STRATEGY_DEFAULTS` 신설. `STRATEGY_TUNABLE_KEYS`/`STRATEGY_KEY_META`/`STRATEGY_KEY_EFFECT` 유지.
- `runtime.py` — 프리셋 함수 7개 삭제, `_default_state`/`set_strategy`/`active` 단순화.
- `backtest/engine.py` — `run_backtest(params)` 시그니처.
- `backtest/report.py` — 삭제. `backtest/__init__.py` — docstring 수정.
- `infra/weekly_review.py` — summary에 backtest 섹션 + 메시지 한 줄.
- `server/app.py` — `/api/strategy` GET `presets` 제거, POST custom-only, `/preset` 엔드포인트 2개 삭제.
- `server/static/index.html` — 프리셋 카드 제거, 단일 편집 패널.
- `main_swarm.py`·`agents/specialists.py`·`tools/gen_manual.js` — "프리셋"→"설정" 문구.
- 테스트: `tests/test_strategy_params_config.py`, `tests/test_deterministic_score_config.py`, `tests/test_institutional_params_config.py`, `tests/test_thesis_advisory_only.py`, `tests/test_runtime_per_uid.py`, `tests/test_backtest.py`, + 신규 `tests/test_weekly_backtest.py`.

---

## Task 1: config.py — STRATEGY_DEFAULTS 신설, 프리셋 제거

**Files:**
- Modify: `config.py` (STRATEGY_PRESETS L541~, DEFAULT_STRATEGY L603, STRATEGY_TUNABLE_KEYS 뒤 L318 부근)
- Test: `tests/test_strategy_params_config.py`, `tests/test_deterministic_score_config.py`, `tests/test_institutional_params_config.py`, `tests/test_thesis_advisory_only.py`

- [ ] **Step 1: 4개 config 테스트를 STRATEGY_DEFAULTS 계약으로 갱신 (실패 유도)**

`tests/test_strategy_params_config.py` — `test_surviving_keys_in_all_presets` 와 `test_qw_category_weights_deprecated` 의 프리셋 순회를 단일 dict로 교체:

```python
def test_surviving_keys_in_defaults():
    for k in SURVIVING_KEYS:
        assert k in config.STRATEGY_DEFAULTS, f"기본값에 {k} 누락"


def test_qw_category_weights_deprecated():
    # LLM 채점 가중치 QW_* 는 더 이상 튜너블 아님(결정론 QIW_*/DW_* 로 대체).
    for k in DEPRECATED_QW:
        assert k not in config.STRATEGY_TUNABLE_KEYS, f"{k} 가 아직 튜너블(폐기 안 됨)"
        assert k not in config.STRATEGY_DEFAULTS, f"기본값에 폐기 키 {k} 잔존"
```

`tests/test_deterministic_score_config.py` — `test_new_keys_in_all_presets`, `test_deterministic_scoring_default_on`, `test_qiw_signed_allowed_and_dw_present` 교체:

```python
def test_new_keys_in_defaults():
    for k in NEW:
        assert k in config.STRATEGY_DEFAULTS, f"기본값에 {k} 누락"


def test_deterministic_scoring_default_on():
    assert config.DETERMINISTIC_SCORING is True
    assert config.STRATEGY_DEFAULTS["DETERMINISTIC_SCORING"] is True


def test_qiw_signed_allowed_and_dw_present():
    for k in QIW + DW:
        assert isinstance(config.STRATEGY_DEFAULTS[k], (int, float))
```

`tests/test_institutional_params_config.py` — `test_all_presets_define_new_keys` 교체:

```python
def test_defaults_define_new_keys():
    for k in NEW_KEYS:
        assert k in config.STRATEGY_DEFAULTS, f"기본값에 {k} 누락"
```

`tests/test_thesis_advisory_only.py` — `test_veto_config_keys_removed` 의 프리셋 순회 블록(L29~32)을 교체:

```python
    # 기본값에도 잔재가 없어야 한다.
    assert "THESIS_VETO_ENABLED" not in config.STRATEGY_DEFAULTS
    assert "THESIS_NOISE_BAND_PCT" not in config.STRATEGY_DEFAULTS
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python3.11 -m pytest tests/test_strategy_params_config.py tests/test_deterministic_score_config.py tests/test_institutional_params_config.py tests/test_thesis_advisory_only.py -q`
Expected: FAIL — `AttributeError: module 'config' has no attribute 'STRATEGY_DEFAULTS'`

- [ ] **Step 3: config.py 구현 — STRATEGY_DEFAULTS 추가 (STRATEGY_TUNABLE_KEYS 직후, 즉 L318 "]" 다음 줄에 삽입)**

```python
# ─── 단일 기본 전략값 (사장 지시 2026-06-09: 프리셋 폐지) ──────────────────────
# 과거 STRATEGY_PRESETS["balanced"] 가 모든 프로필의 기본 베이스였다. 프리셋을 없애고
# 그 균형형 값을 유일한 기본값으로 승격한다. 균형형에 없던 후발 추가 키(NXT/인텔리전스/
# 채권)는 모듈 상수에서 채워 전 튜닝키를 빠짐없이 커버한다(기존 폴백 동작 보존).
STRATEGY_DEFAULTS = {
    "PER_ORDER_BUDGET_RATIO": 0.10, "PER_ORDER_BUDGET_OVERSHOOT": 1.20,
    "MAX_CYCLE_BUDGET_RATIO": 0.25, "MIN_CASH_BUFFER": 1.10,
    "CONSERVATIVE_MDD": 0.05, "CONSERVATIVE_STOCK_RATIO": 0.15,
    "MAX_TRADES_PER_CYCLE": 2, "MAX_ORDER_QTY": 0,
    "MIN_QUANT_SCORE": 6, "MAX_BUY_VOLATILITY_PCT": 0, "RSI_OVERBOUGHT_SKIP": 0, "MIN_ADX_FOR_BUY": 0,
    "REQUIRE_FOREIGN_NET_BUY": False, "MAX_PRICE_EXTENSION_PCT": 0,
    "QIW_RSI": 5, "QIW_MACD": 10, "QIW_ADX": 8, "QIW_VWAP": 8, "QIW_VOL": 8,
    "QIW_MOM": 12, "QIW_CMF": 8, "QIW_FLOW": 12, "QIW_HIGH52": 8,
    "DW_QUANT": 60, "DW_NEWS": 25, "DW_MACRO": 15,
    "DETERMINISTIC_SCORING": True, "MACRO_STOCK_GATE_ENABLED": True,
    "MAX_BUY_NAMES": 8, "POSITION_SIZING_MODE": "risk_weighted",
    "SIZING_TILT_STRENGTH": 0.5, "SIZING_MAX_TILT": 2.0,
    "UNIVERSE_MIN_PRICE": 0, "UNIVERSE_MIN_TURNOVER": 0,
    "UNIVERSE_EXCLUDE_LEVERAGED": True, "SCORECARD_WINDOW_DAYS": 30,
    "ENABLE_SELL_REBALANCE": True, "TAKE_PROFIT_PCT": 12.0, "STOP_LOSS_PCT": 5.0, "TRIM_OVER_RATIO": True,
    "ALLOW_DAY_TRADING": True, "MIN_HOLDING_DAYS_FOR_SELL": 0.5,
    "ENABLE_CHEAP_FALLBACK": False, "ALLOW_US_STOCKS": True, "ALLOW_DERIVATIVES": False,
}
# 균형형에 없던 후발 키(NXT/인텔리전스/채권 등)는 모듈 상수에서 보충 → 단일 기본값 완성.
for _k in STRATEGY_TUNABLE_KEYS:
    STRATEGY_DEFAULTS.setdefault(_k, globals().get(_k))
```

- [ ] **Step 4: config.py — STRATEGY_PRESETS dict(L541~601) 와 DEFAULT_STRATEGY(L603) 완전 삭제.** (이 두 정의를 지운다. 주변 주석 L267~270 의 "preset" 서술도 STRATEGY_DEFAULTS 기준으로 정리.)

- [ ] **Step 5: 테스트 통과 확인**

Run: `python3.11 -m pytest tests/test_strategy_params_config.py tests/test_deterministic_score_config.py tests/test_institutional_params_config.py tests/test_thesis_advisory_only.py -q`
Expected: PASS

---

## Task 2: runtime.py — 프리셋 머신러리 제거, custom-only

**Files:**
- Modify: `runtime.py` (L22, L32-35, L298-398 함수들, L324-329 active, L411-438 set_strategy)
- Test: `tests/test_runtime_per_uid.py`

- [ ] **Step 1: test_runtime_per_uid.py 를 custom params 계약으로 재작성 (실패 유도)**

```python
"""런타임 전략 상태 프로필(uid)별 격리 — 프리셋 폐지 후(2026-06-09) custom params 기반."""
import json
import pytest
import config
import runtime

KEY = "PER_ORDER_BUDGET_RATIO"


@pytest.fixture
def rt(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "_STATE", tmp_path / "strategy_state.json")
    monkeypatch.setattr(runtime, "_HIST", tmp_path / "strategy_history.json")
    default = {"name": "default", "params": dict(config.STRATEGY_DEFAULTS)}
    monkeypatch.setattr(runtime, "_states", {"_default": default}, raising=False)
    from infra import profile_overrides as po
    monkeypatch.setattr(po, "_PROFILES_DIR", tmp_path / "profiles")
    return runtime


def test_set_strategy_per_uid_isolated(rt):
    rt.set_strategy(custom={KEY: 0.05}, uid=1)
    rt.set_strategy(custom={KEY: 0.20}, uid=2)
    assert abs(rt.get(KEY, uid=1) - 0.05) < 1e-9
    assert abs(rt.get(KEY, uid=2) - 0.20) < 1e-9


def test_uid_none_unaffected_by_per_uid_changes(rt):
    default_val = rt.get(KEY, uid=None)
    rt.set_strategy(custom={KEY: 0.05}, uid=1)
    rt.set_strategy(custom={KEY: 0.20}, uid=2)
    assert rt.get(KEY, uid=None) == default_val
    assert default_val == config.STRATEGY_DEFAULTS[KEY]


def test_active_reflects_custom_params(rt):
    rt.set_strategy(custom={KEY: 0.05}, uid=1)
    a = rt.active(uid=1)
    assert abs(a["params"][KEY] - 0.05) < 1e-9
    assert a["label"] == "사용자 설정"


def test_state_persists_keyed(rt):
    rt.set_strategy(custom={KEY: 0.05}, uid=5)
    disk = json.loads((runtime._STATE).read_text(encoding="utf-8"))
    assert "5" in disk
    assert "_default" in disk


def test_legacy_flat_state_migrates_to_default(tmp_path, monkeypatch):
    f = tmp_path / "strategy_state.json"
    flat = {"name": "balanced", "params": {KEY: 0.33}, "since": "2026-01-01T00:00:00"}
    f.write_text(json.dumps(flat), encoding="utf-8")
    monkeypatch.setattr(runtime, "_STATE", f)
    monkeypatch.setattr(runtime, "_states", {"_default": {
        "name": "default", "params": dict(config.STRATEGY_DEFAULTS)}}, raising=False)
    runtime._load()
    assert abs(runtime.get(KEY, uid=None) - 0.33) < 1e-9


def test_profile_overrides_only_apply_to_that_uid(rt, tmp_path, monkeypatch):
    from infra import profile_overrides as po
    monkeypatch.setattr(po, "_PROFILES_DIR", tmp_path / "profiles")
    po.set_overrides(3, {KEY: 0.07})
    assert abs(rt.get(KEY, uid=3) - 0.07) < 1e-9
    assert rt.get(KEY, uid=None) == config.STRATEGY_DEFAULTS[KEY]
    assert rt.get(KEY, uid=4) == config.STRATEGY_DEFAULTS[KEY]
```

- [ ] **Step 2: 실패 확인**

Run: `python3.11 -m pytest tests/test_runtime_per_uid.py -q`
Expected: FAIL (set_strategy 시그니처/STRATEGY_DEFAULTS 미일치)

- [ ] **Step 3: runtime.py 구현 변경**

(a) L22 `_USER_PRESETS = _DIR / "user_presets.json"` 라인 **삭제**.

(b) `_default_state()` (L32-35) 교체:

```python
def _default_state() -> dict:
    return {"name": "default",
            "params": dict(config.STRATEGY_DEFAULTS),
            "since": datetime.now().isoformat()}
```

(c) 함수 **삭제**: `_load_user_presets`(L298), `_save_user_presets`(L309), `_preset_label`(L316), `list_presets`(L332), `save_user_preset`(L357), `delete_user_preset`(L381), 그리고 `_NAME_RE`(L354).

(d) `active()` (L324-329) 교체:

```python
def active(uid=None) -> dict:
    """그 uid 의 활성 전략(라벨·since + 효과적 params). 프리셋 폐지 → 항상 '사용자 설정'."""
    st = _get_state(uid)
    keys = config.STRATEGY_TUNABLE_KEYS
    return {"name": st.get("name", "default"), "label": "사용자 설정",
            "since": st.get("since"), "params": {k: get(k, uid=uid) for k in keys}}
```

(e) `set_strategy()` (L411-438) 교체:

```python
def set_strategy(custom: dict = None, by: str = "user", uid=None) -> dict:
    """현재 적용 전략 파라미터를 갱신 — 프로필(uid)별. uid=None → _default.
    프리셋 폐지(2026-06-09): STRATEGY_DEFAULTS 베이스 위에 custom(알려진 키만)만 얹는다.
    """
    keys = config.STRATEGY_TUNABLE_KEYS
    base = dict(config.STRATEGY_DEFAULTS)
    if custom:
        for k, v in custom.items():
            if k in set(keys):
                base[k] = v
    new_state = {"name": "custom", "params": {k: base.get(k) for k in keys},
                 "since": datetime.now().isoformat()}
    _states[_strat_key(uid)] = new_state
    _persist()
    _append_history({"name": "custom", "label": "사용자 설정",
                     "params": new_state["params"], "by": by,
                     "uid": (None if uid is None else int(uid))})
    return active(uid)
```

- [ ] **Step 4: 통과 확인**

Run: `python3.11 -m pytest tests/test_runtime_per_uid.py -q`
Expected: PASS

- [ ] **Step 5: 잔재 grep — 0건이어야**

Run: `grep -rn "STRATEGY_PRESETS\|DEFAULT_STRATEGY\|list_presets\|user_preset\|_preset_label" runtime.py`
Expected: 출력 없음

---

## Task 3: backtest/engine.py — run_backtest(params)

**Files:**
- Modify: `backtest/engine.py` (L1-17 docstring, L68-83 시그니처, L150-151 반환, L154 `_metrics`)
- Test: `tests/test_backtest.py`

- [ ] **Step 1: test_backtest.py 재작성 (실패 유도)**

```python
"""백테스트 엔진 — 결정론 + 룩어헤드 없음 + 리스크규칙 단조성 (프리셋 폐지 후 params 기반)."""
import config
from backtest.engine import load_prices, run_backtest, sma_breakout_signal


def test_signal_no_lookahead():
    closes = [10] * 25 + [9, 8, 12]
    assert sma_breakout_signal(closes, 5) is False
    assert isinstance(sma_breakout_signal(closes, 27), bool)


def test_backtest_is_deterministic():
    prices = load_prices()
    assert prices, "data/daily_*.csv 필요"
    a = run_backtest(config.STRATEGY_DEFAULTS, prices)
    b = run_backtest(config.STRATEGY_DEFAULTS, prices)
    assert a == b


def test_risk_rules_monotonic():
    """손절 타이트·단일종목 한도 작은 설정이 큰 베팅 설정보다 MDD(낙폭)가 작아야 한다."""
    prices = load_prices()
    tight = dict(config.STRATEGY_DEFAULTS)
    tight.update({"STOP_LOSS_PCT": 3.5, "CONSERVATIVE_STOCK_RATIO": 0.07,
                  "CONSERVATIVE_MDD": 0.025, "PER_ORDER_BUDGET_RATIO": 0.03,
                  "MAX_CYCLE_BUDGET_RATIO": 0.10})
    loose = dict(config.STRATEGY_DEFAULTS)
    loose.update({"STOP_LOSS_PCT": 15.0, "CONSERVATIVE_STOCK_RATIO": 0.40,
                  "CONSERVATIVE_MDD": 0.15, "PER_ORDER_BUDGET_RATIO": 0.35,
                  "MAX_CYCLE_BUDGET_RATIO": 0.70})
    d = run_backtest(tight, prices)
    u = run_backtest(loose, prices)
    assert d["max_drawdown_pct"] >= u["max_drawdown_pct"]
    assert set(d) >= {"total_return_pct", "max_drawdown_pct", "sharpe_like",
                      "trades", "win_rate_pct", "final_eval"}
```

- [ ] **Step 2: 실패 확인**

Run: `python3.11 -m pytest tests/test_backtest.py -q`
Expected: FAIL (run_backtest 가 preset_name 문자열 기대)

- [ ] **Step 3: engine.py 구현 변경**

L68-83 의 시그니처/규칙 매핑 교체:

```python
def run_backtest(params: dict,
                 prices: Dict[str, List[dict]],
                 start_cash: float = 10_000_000.0,
                 lookback: int = 20,
                 name: str = "현재 설정") -> dict:
    """전략 파라미터 1세트를 전 종목 공통 신호로 시뮬레이션. 반환: 성과 지표 dict.

    규칙 매핑(라이브 시맨틱 그대로):
      PER_ORDER_BUDGET_RATIO     → 1주문 = 가용현금 × 비율
      CONSERVATIVE_STOCK_RATIO   → 단일 종목 평가액 한도 (초과 시 진입 스킵)
      MAX_CYCLE_BUDGET_RATIO     → 하루(=1사이클) 총 매수 한도
      MIN_CASH_BUFFER            → 노티오날 × 버퍼 ≤ 현금이어야 진입
      CONSERVATIVE_MDD           → 계좌 평가손익 ≤ -MDD 면 신규 매수 전면 차단
      TAKE_PROFIT_PCT/STOP_LOSS_PCT → 보유 수익률 기준 전량 청산
    """
    p = params
```

L150-151 의 반환 교체:

```python
    final = equity_curve[-1] if equity_curve else start_cash
    return _metrics(name, start_cash, final, equity_curve, trades, wins)
```

L154 의 `_metrics` 시그니처/반환 교체 (label 인자 제거, `preset`→`name`):

```python
def _metrics(name, start, final, curve, trades, wins) -> dict:
    total_ret = (final / start - 1.0) * 100.0
    peak = -math.inf
    mdd = 0.0
    for v in curve:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1.0)
    rets = [curve[i] / curve[i - 1] - 1.0 for i in range(1, len(curve)) if curve[i - 1]]
    mean = sum(rets) / len(rets) if rets else 0.0
    var = sum((r - mean) ** 2 for r in rets) / len(rets) if rets else 0.0
    vol = math.sqrt(var)
    sharpe_like = (mean / vol * math.sqrt(252)) if vol else 0.0
    return {"name": name,
            "total_return_pct": round(total_ret, 2),
            "max_drawdown_pct": round(mdd * 100.0, 2),
            "sharpe_like": round(sharpe_like, 2),
            "trades": trades,
            "win_rate_pct": round(wins / trades * 100.0, 1) if trades else 0.0,
            "final_eval": round(final)}
```

L1-17 docstring 의 "프리셋 비교" 서술을 "단일 설정 성과 측정 — config.STRATEGY_PRESETS 의존 제거"로 수정.

- [ ] **Step 4: 통과 확인**

Run: `python3.11 -m pytest tests/test_backtest.py -q`
Expected: PASS

---

## Task 4: backtest/report.py 삭제 + __init__ docstring

**Files:**
- Delete: `backtest/report.py`
- Modify: `backtest/__init__.py`

- [ ] **Step 1: report.py 삭제**

```bash
git rm backtest/report.py
```

- [ ] **Step 2: __init__.py docstring 교체**

```python
"""ArQuant 백테스트 하네스 — 현재 전략 파라미터의 결정론 리스크/청산 규칙 성과 측정."""
```

- [ ] **Step 3: 잔재 grep**

Run: `grep -rn "backtest.report\|STRATEGY_PRESETS" backtest/`
Expected: 출력 없음

---

## Task 5: infra/weekly_review.py — 토요일 백테스트 연결

**Files:**
- Modify: `infra/weekly_review.py` (build_review_summary 반환부 L122-134, build_review_message L150-154)
- Test: `tests/test_weekly_backtest.py` (신규)

- [ ] **Step 1: 신규 테스트 작성 (실패 유도)**

`tests/test_weekly_backtest.py`:

```python
"""주간 피드백 summary 에 현재-파라미터 단일 백테스트가 포함되는지 (2026-06-09)."""
import infra.weekly_review as wr


def test_summary_has_backtest_section(monkeypatch):
    # cycle/equity 의존부를 가볍게 우회 — backtest 섹션 존재만 검증.
    summary = wr.build_review_summary(uid=None)
    assert "backtest" in summary
    bt = summary["backtest"]
    assert "available" in bt
    if bt["available"]:
        assert set(bt) >= {"available", "total_return_pct", "max_drawdown_pct",
                           "sharpe_like", "trades", "win_rate_pct"}


def test_message_includes_backtest_line():
    summary = {"period": "최근 7일", "cycles": 0, "with_orders": 0, "risk_approved": 0,
               "market_open_cycles": 0, "candidate_to_target_pct": 0,
               "candidates_picked": 0, "targets_final": 0,
               "trades_executed": 0, "trades_failed": 0, "equity_return_pct_adj": None,
               "backtest": {"available": True, "total_return_pct": 3.2,
                            "max_drawdown_pct": -4.1, "sharpe_like": 0.8,
                            "trades": 5, "win_rate_pct": 60.0}}
    msg = wr.build_review_message(summary)
    assert "백테스트" in msg
```

- [ ] **Step 2: 실패 확인**

Run: `python3.11 -m pytest tests/test_weekly_backtest.py -q`
Expected: FAIL (summary 에 backtest 없음)

- [ ] **Step 3: weekly_review.py — backtest 헬퍼 + summary/message 주입**

`build_review_summary` 위에 헬퍼 추가:

```python
def _run_current_backtest(uid=None) -> Dict[str, Any]:
    """현재 적용 파라미터(프로필 오버라이드 반영)로 단일 성과 백테스트. CSV 없으면 available=False."""
    try:
        import config, runtime
        from backtest.engine import load_prices, run_backtest
        prices = load_prices()
        if not prices:
            return {"available": False, "reason": "no_daily_csv"}
        params = {k: runtime.get(k, uid=uid) for k in config.STRATEGY_TUNABLE_KEYS}
        m = run_backtest(params, prices)
        return {"available": True,
                "total_return_pct": m["total_return_pct"],
                "max_drawdown_pct": m["max_drawdown_pct"],
                "sharpe_like": m["sharpe_like"],
                "trades": m["trades"],
                "win_rate_pct": m["win_rate_pct"]}
    except Exception as e:
        return {"available": False, "reason": str(e)}
```

`build_review_summary` 의 `return {...}` dict 에 한 줄 추가(equity_return 다음):

```python
        "equity_return_pct_adj": equity_return,
        "backtest": _run_current_backtest(uid=uid),
```

`build_review_message` 의 `er` 라인 다음, `→ 운용지원실장...` 라인 앞에 삽입:

```python
    bt = summary.get("backtest") or {}
    if bt.get("available"):
        lines.append(
            f"• 현재 설정 백테스트: 수익률 {bt.get('total_return_pct', 0):+.1f}% · "
            f"MDD {bt.get('max_drawdown_pct', 0):.1f}% · 샤프* {bt.get('sharpe_like', 0):.2f} · "
            f"매매 {bt.get('trades', 0)}건 · 승률 {bt.get('win_rate_pct', 0):.0f}%")
    else:
        lines.append("• 현재 설정 백테스트: 데이터 부족(일봉 CSV 없음)")
```

- [ ] **Step 4: 통과 확인**

Run: `python3.11 -m pytest tests/test_weekly_backtest.py -q`
Expected: PASS

---

## Task 6: server/app.py — /api/strategy presets 제거, preset 엔드포인트 삭제

**Files:**
- Modify: `server/app.py` (strategy_get L1163-1176, strategy_set L1178-1192, L1194-1224 두 엔드포인트)

- [ ] **Step 1: strategy_get — `presets` 필드 제거 (L1174)**

```python
    return {"active": active,
            "history": runtime.history(),
            "key_meta": STRATEGY_KEY_META, "key_order": STRATEGY_TUNABLE_KEYS}
```

- [ ] **Step 2: strategy_set — custom-only (L1178-1192)**

```python
@app.post("/api/strategy")
async def strategy_set(req: dict, request: Request):
    import runtime
    from main_swarm import _broadcast
    uid = getattr(request.state, "user_id", None)
    custom = (req or {}).get("params")
    if not custom:
        raise HTTPException(400, "params 필요")
    active = runtime.set_strategy(custom=custom, by="dashboard", uid=uid)
    try:
        await _broadcast({"type": "status", "state": "IDLE",
                          "message": f"⚙️ 전략 설정 변경 → {active['label']}"})
    except Exception: pass
    return {"active": active}
```

- [ ] **Step 3: `/api/strategy/preset` POST·DELETE 엔드포인트(L1194-1224) 완전 삭제**

- [ ] **Step 4: 잔재 grep**

Run: `grep -n "list_presets\|save_user_preset\|delete_user_preset\|strategy/preset" server/app.py`
Expected: 출력 없음

- [ ] **Step 5: import 정합 확인 (앱 import 가능)**

Run: `python3.11 -c "import server.app"`
Expected: 에러 없음

---

## Task 7: index.html — 단일 편집 패널

**Files:**
- Modify: `server/static/index.html` (전략 탭 HTML L490-521, strategy JS L1624-1778)

- [ ] **Step 1: 전략 탭 HTML 단순화 (L497-520)**

"🎛️ 적용 가능 전략" 카드(L497-499)와 커스터마이즈 카드(L500-520)를 아래 단일 카드로 교체:

```html
      <div class="card">
        <div class="ch"><span class="ct">⚙️ 현재 적용 설정</span>
          <button id="opsToggle" onclick="toggleOpsFeedback()" title="이 계정의 운용지원 피드백(전략 파라미터 자동 조정) on/off — 프로필별" style="display:none;margin-left:auto;font-size:11px;color:var(--dim);background:var(--s2);border-radius:6px;padding:3px 10px;border:1px solid var(--border);cursor:pointer">🛠 운용지원 …</button>
        </div>
        <div id="stratMeta" style="font-size:10px;color:var(--dim);margin:2px 0 10px"></div>
        <div id="customLoader" style="font-size:11px;color:var(--dim);padding:8px">파라미터 불러오는 중...</div>
        <div id="customFields" style="display:none"></div>
        <div id="customActions" style="display:none;margin-top:14px;gap:8px;align-items:center;flex-wrap:wrap">
          <button class="btn-apply" onclick="applyCustom()">▶ 변경값 적용</button>
          <button class="btn-apply" style="background:var(--s2);border:1px solid var(--border)" onclick="loadCustomFromActive()">↺ 되돌리기</button>
        </div>
        <div id="customMsg" style="margin-top:8px;font-size:11px;color:var(--dim);min-height:14px"></div>
      </div>
```

기존 `#stratNow`(L491) 와 `#customCard` 박스는 위 카드로 대체되므로 제거. (`#stratNow` 줄 삭제.)

- [ ] **Step 2: strategy JS 교체 (L1624-1672 의 _ppHtml/_ppFullHtml/loadStrategy + L1768-1778 의 saveCustomPreset/deleteUserPreset/applyStrategy 제거)**

`loadStrategy` 를 아래로 교체하고, `_ppHtml`/`_ppFullHtml`/`toggleCustomCard` 및 프리셋 관련 함수(`saveCustomPreset`,`deleteUserPreset`,`applyStrategy`)는 삭제:

```javascript
function loadStrategy(){fetch(API+'/api/strategy').then(r=>r.json()).then(d=>{
  _stratData=d; const meta=d.key_meta||{};
  const a=d.active||{};
  const _since=(a.ops_since||a.since||'').replace('T',' ').slice(0,19);
  document.getElementById('stratMeta').textContent=
    `적용 시각: ${_since||'-'}${a.ops_since?' · 운용지원실장 조정 반영':''}`;
  _buildCustomFields(d.key_order||[],meta,(a.params||{}));
}).catch(e=>{document.getElementById('customMsg').textContent='⚠️ 전략 로드 실패: '+e.message})}
```

`_buildCustomFields`/`_fillCustomFromParams`/`_readCustomParams`/`loadCustomFromActive`/`applyCustom` (L1674-1751)는 그대로 유지(편집 패널 코어). 단 `_buildCustomFields` 끝의 `document.getElementById('customActions').style.display='flex';` 유지.

- [ ] **Step 3: 수동 검증 (서버 재시작 후)**

브라우저에서 '전략' 탭 → "⚙️ 현재 적용 설정" 패널에 그룹별(사이징/리스크/매도 규칙/…) 현재값이 입력칸에 표시되고, 값 수정 → "▶ 변경값 적용" → 토스트 "✅ 즉시 적용". 프리셋 리스트/저장 UI가 사라졌는지 확인.

(자동 테스트 없음 — 프론트엔드. JS 문법 점검: `node --check server/static/index.html` 불가하므로 브라우저 콘솔 무에러 확인.)

---

## Task 8: 프롬프트·매뉴얼 문구 정리

**Files:**
- Modify: `main_swarm.py` (L2956, L4246, L4264, L4331), `agents/specialists.py` (L308), `tools/gen_manual.js` (L99, L174, L263, L279, L417)

- [ ] **Step 1: main_swarm.py 문구 교체**

- L2956: `'최대 매수 개수 N'(전략 프리셋: 보수형 1 / 균형형 2 / 공격형 3)` → `'최대 매수 개수 N'(전략 설정값)`
- L4246 주석: `(전략 프리셋에 따라 1~N개)` → `(전략 설정에 따라 1~N개)`
- L4264: `최대 매수 개수 N = {_N} (전략 프리셋)` → `최대 매수 개수 N = {_N} (전략 설정)`
- L4331 주석: `데이트레이딩 회피 룰은 전략 프리셋에서 토글.` → `데이트레이딩 회피 룰은 전략 설정에서 토글.`

- [ ] **Step 2: agents/specialists.py L308 교체**

`- 전략 프리셋의 `ALLOW_DAY_TRADING` 토글이 결정합니다` → `- 전략 설정의 `ALLOW_DAY_TRADING` 토글이 결정합니다`

- [ ] **Step 3: tools/gen_manual.js 교체**

- L99: `전략 프리셋과 위험 한도를 정해두면` → `전략 설정과 위험 한도를 정해두면`. `기본 전략은 '균형형'이며` → `기본 설정으로 즉시 동작하며`
- L174: `"전략 프리셋 선택·적용 + 커스터마이즈 + 운용지원 ON/OFF"` → `"현재 전략 설정값 확인·편집 + 운용지원 ON/OFF"`
- L263: 통째 교체 → `children.push(P("'전략' 탭에서 현재 적용 중인 모든 파라미터를 그룹별로 보고, 한국어 라벨·단위(%, 배, 일, 건)로 직접 수정해 '변경값 적용'을 누르면 즉시 라이브 반영됩니다."));`
- L279: 통째 교체 → `children.push(P("각 항목은 현재 적용값이 입력칸에 그대로 표시됩니다. 수정 후 '변경값 적용'으로 라이브 오버라이드하거나, '되돌리기'로 현재 적용값을 다시 불러올 수 있습니다."));`
- L417: `방어형/보수형 프리셋과 작은 예산 비율로` → `보수적인 설정값(작은 주문 비율·빠른 손절)과 작은 예산 비율로`

- [ ] **Step 4: 잔재 grep**

Run: `grep -rn "프리셋\|preset" main_swarm.py agents/specialists.py tools/gen_manual.js`
Expected: 출력 없음

---

## Task 9: 전체 테스트 + 배포

- [ ] **Step 1: 전역 프리셋 잔재 최종 grep**

Run: `grep -rn "STRATEGY_PRESETS\|DEFAULT_STRATEGY\|list_presets\|save_user_preset\|delete_user_preset" --include=*.py .`
Expected: 출력 없음 (tests 포함)

- [ ] **Step 2: 전체 테스트**

Run: `python3.11 -m pytest -q`
Expected: 전부 PASS (이전 786 기준 ±, 신규 test_weekly_backtest 포함)

- [ ] **Step 3: 사장에게 재시작 확인 요청 후 배포**

```bash
sudo systemctl restart arquant.service
sudo systemctl status arquant.service   # port 8500 healthy
```
재시작은 되돌리기 어려운 작업 → **사장 확인 후 실행**. 루프 OFF면 대시보드 '시작' 안내.

- [ ] **Step 4: 배포 후 수동 검증**

- `curl -s localhost:8500/api/strategy | python3.11 -m json.tool` → `presets` 키 없음, `active.label="사용자 설정"`.
- 대시보드 '전략' 탭 패널 동작(Task 7 Step 3).
