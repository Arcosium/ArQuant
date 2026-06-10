# 채권 ETF 자동매매 — 채권관리실장 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 매크로의 "채권 비중 X%" 권고를 전담 에이전트(채권관리실장)가 채권 ETF 매수/매도로 실현하는 독립 트랙을 추가한다.

**Architecture:** 주식 퀀트 파이프라인과 분리된 채권 트랙. 채권관리실장(능동적 금리 전략가) LLM이 매크로 금리뷰 + 권고 채권%를 받아 ETF 풀 안에서 듀레이션·종목을 재량 결정(목표비중 추종). 퀀트·thesis 거부권·자동손절은 우회, 리스크관리실장 검증만 공유. 매수 시 보유기간을 self-thesis로 기록해 매도 판단 때 강력 권고.

**Tech Stack:** Python 3.11, pytest, 기존 KIS `kr_buy/kr_sell`(6자리)·`us_buy/us_sell`(티커) 경로 재사용.

**참조 스펙:** `docs/superpowers/specs/2026-06-08-bond-etf-bond-manager-design.md`

## 실행 규칙 (이 저장소 고유)
- **테스트는 반드시 `python3.11 -m pytest`** (기본 `python`은 argon2 import 실패).
- **커밋 스텝 없음:** 외부 도구가 주기적으로 `Backup:` 커밋을 자동 수행한다. 각 Task의 체크포인트는 "전체 테스트 통과"이며, 사장 명시 요청 없이 직접 커밋하지 않는다.
- **배포(재시작) 금지(이 계획 내에서):** 모든 Task 완료 후, 이미 구현된 펀드기획실장 거부권 폐지와 **함께** 사장 확인하에 `arquant.service` 재시작 1회로 배포한다.

## 파일 구조
- Modify `config.py` — 채권 설정 키·ETF 풀·튜너블/메타 등록.
- Modify `main_swarm.py` — 매크로 채권% 파서, ETF 풀 세션필터, 현재 채권비중, 사이징, 채권결정 파서, `_cyc_stage_bonds`, 에이전트 등록.
- Modify `agents/specialists.py` — `create_bond_manager` 페르소나, `format_bond_thesis_reminder`.
- Create `infra/bond_thesis.py` — 채권 보유기간 self-thesis 저장소(주식 `position_thesis`와 분리).
- Modify `infra/user_paths.py` — `bond_thesis_path`.
- Create tests — `tests/test_bond_*.py`.

---

### Task 1: 채권 설정 키 + ETF 풀 (`config.py`)

**Files:**
- Modify: `config.py` (상수 추가 + `STRATEGY_TUNABLE_KEYS` + `STRATEGY_KEY_META` + `STRATEGY_KEY_EFFECTS`)
- Test: `tests/test_bond_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bond_config.py
import config

def test_bond_flags_exist_with_defaults():
    assert config.ENABLE_BOND_ETF is False          # 마스터 스위치 기본 OFF
    assert 0.0 < config.BOND_TARGET_MAX_PCT <= 1.0
    assert 0.0 <= config.BOND_REBALANCE_BAND_PCT < 0.2

def test_bond_etf_pools_shape():
    for pool in (config.BOND_ETF_POOL_KR, config.BOND_ETF_POOL_US):
        assert len(pool) == 3
        for code, name, dur in pool:
            assert isinstance(code, str) and isinstance(name, str)
            assert dur in ("short", "mid", "long")
    kr_codes = [c for c, _, _ in config.BOND_ETF_POOL_KR]
    assert kr_codes == ["153130", "114260", "148070"]
    us_codes = [c for c, _, _ in config.BOND_ETF_POOL_US]
    assert us_codes == ["SHY", "IEF", "TLT"]

def test_bond_keys_are_tunable_and_have_meta():
    for k in ("ENABLE_BOND_ETF", "BOND_TARGET_MAX_PCT", "BOND_REBALANCE_BAND_PCT"):
        assert k in config.STRATEGY_TUNABLE_KEYS
        assert k in config.STRATEGY_KEY_META
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_bond_config.py -v`
Expected: FAIL — `AttributeError: module 'config' has no attribute 'ENABLE_BOND_ETF'`.

- [ ] **Step 3: Write minimal implementation**

`config.py` — NXT 시간외 매매 블록(`ENABLE_NXT_EXTENDED_HOURS` 근처) 아래에 추가:
```python
# ─── 채권 ETF 자동매매 (사장 지시 2026-06-08) ─────────────────────────────────
# 매크로 '채권 비중 X%' 권고를 채권관리실장이 채권 ETF 매수/매도로 실현(독립 트랙).
ENABLE_BOND_ETF         = False   # 마스터 스위치 (OFF면 채권 트랙 전체 스킵 — 기존 동작 불변)
BOND_TARGET_MAX_PCT     = 0.40    # 채권 비중 절대 상한(매크로 권고가 넘어도 이 값으로 클램프)
BOND_REBALANCE_BAND_PCT = 0.03    # 목표 대비 ±이 폭 이내면 매매 안 함(채권 churn 방지 데드존)
# 허용 채권 ETF 풀 — (코드, 이름, 듀레이션). 능동적 금리 전략가가 이 안에서만 선택(티커 환각 방지).
BOND_ETF_POOL_KR = [
    ("153130", "KODEX 단기채권",   "short"),
    ("114260", "KODEX 국고채3년",  "mid"),
    ("148070", "KOSEF 국고채10년", "long"),
]
BOND_ETF_POOL_US = [
    ("SHY", "iShares 1-3Y Treasury",  "short"),
    ("IEF", "iShares 7-10Y Treasury", "mid"),
    ("TLT", "iShares 20+Y Treasury",  "long"),
]
```

`STRATEGY_TUNABLE_KEYS` 리스트의 NXT 키들 뒤에 추가:
```python
    "ENABLE_BOND_ETF", "BOND_TARGET_MAX_PCT", "BOND_REBALANCE_BAND_PCT",
```

`STRATEGY_KEY_META` 딕셔너리에 추가:
```python
    "ENABLE_BOND_ETF":         {"label": "채권 ETF 자동매매(채권관리실장)", "type": "bool",
                                "help": "켜면 매크로 채권 비중 권고를 채권 ETF 매수/매도로 실현. 끄면 채권 트랙 전체 스킵.",
                                "group": "매도 규칙"},
    "BOND_TARGET_MAX_PCT":     {"label": "채권 비중 상한", "type": "pct_raw", "unit": "%비율",
                                "help": "채권 평가비중 절대 상한. 매크로 권고가 이를 넘어도 이 값으로 클램프(0.40=40%)",
                                "min": 0.0, "max": 1.0, "step": 0.05, "group": "매도 규칙"},
    "BOND_REBALANCE_BAND_PCT": {"label": "채권 리밸런싱 데드존", "type": "pct_raw", "unit": "%비율",
                                "help": "목표 대비 ±이 폭 이내면 채권 매매 안 함(잦은 교체 방지). 0.03=±3%p",
                                "min": 0.0, "max": 0.2, "step": 0.01, "group": "매도 규칙"},
```

`STRATEGY_KEY_EFFECTS`(효과 카탈로그) 딕셔너리에 추가:
```python
    "ENABLE_BOND_ETF": "켜면 매크로 채권 권고를 채권 ETF로 실현(자산배분 충실), 끄면 채권 매매 안 함.",
    "BOND_TARGET_MAX_PCT": "올리면 채권에 더 많이 배분 허용, 내리면 채권 상한 축소.",
    "BOND_REBALANCE_BAND_PCT": "올리면 채권 교체 둔감(churn↓), 내리면 목표 추종 민감.",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_bond_config.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Checkpoint** — `python3.11 -m pytest -q` 전체 통과 확인.

---

### Task 2: 매크로 채권% 파서 (`main_swarm.py`)

**Files:**
- Modify: `main_swarm.py` (`_parse_macro_stock_pct` 정의 바로 아래, ~line 1053)
- Test: `tests/test_bond_macro_parse.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bond_macro_parse.py
from main_swarm import _parse_macro_bond_pct

def test_parse_bond_pct_from_allocation_line():
    txt = "📈 자산 배분 권고: 주식 40% / 채권 35% / 현금 25% (직전: ...)"
    assert _parse_macro_bond_pct(txt) == 0.35

def test_parse_bond_pct_tolerates_markdown_stars():
    assert _parse_macro_bond_pct("자산 배분 권고: 채권 **20%**") == 0.20

def test_parse_bond_pct_none_when_absent():
    assert _parse_macro_bond_pct("주식 비중 확대 의견") is None
    assert _parse_macro_bond_pct("") is None
    assert _parse_macro_bond_pct(None) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_bond_macro_parse.py -v`
Expected: FAIL — `ImportError: cannot import name '_parse_macro_bond_pct'`.

- [ ] **Step 3: Write minimal implementation**

`main_swarm.py`, `_parse_macro_stock_pct` 함수 바로 아래에 추가(동일 패턴, '주식'→'채권'):
```python
def _parse_macro_bond_pct(text: Optional[str]) -> Optional[float]:
    """전략리서치팀장 매크로 보고에서 권고 '채권 Y%' 비중을 분수(0.01)로 추출한다.
    '자산 배분 권고' 라인 우선, 없으면 전체 첫 매치. 못 찾으면 None(→ 채권 트랙 스킵, fail-safe)."""
    if not text:
        return None
    s = str(text)
    pat = r"채권\s*\*{0,2}\s*(\d+(?:\.\d+)?)\s*%"
    anchor = s.find("자산 배분 권고")
    if anchor >= 0:
        m = re.search(pat, s[anchor:anchor + 200])
        if m:
            return float(m.group(1)) / 100.0
    m = re.search(pat, s)
    return float(m.group(1)) / 100.0 if m else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_bond_macro_parse.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Checkpoint** — `python3.11 -m pytest -q` 전체 통과.

---

### Task 3: ETF 풀 세션 필터 (`main_swarm.py`)

**Files:**
- Modify: `main_swarm.py` (`_parse_macro_bond_pct` 아래)
- Test: `tests/test_bond_etf_pool.py`

US 풀은 미국장 비활성 계정에선 빈 리스트(스펙 §4.3: `ALLOW_US_STOCKS` 제약).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bond_etf_pool.py
from main_swarm import bond_etf_pool_for_session

def test_kr_session_returns_kr_pool():
    pool = bond_etf_pool_for_session("KR_TRADING", us_allowed=False)
    assert [c for c, _, _ in pool] == ["153130", "114260", "148070"]

def test_us_session_returns_us_pool_when_allowed():
    pool = bond_etf_pool_for_session("US_TRADING", us_allowed=True)
    assert [c for c, _, _ in pool] == ["SHY", "IEF", "TLT"]

def test_us_session_empty_when_us_not_allowed():
    assert bond_etf_pool_for_session("US_TRADING", us_allowed=False) == []

def test_off_hours_empty():
    assert bond_etf_pool_for_session("OFF_HOURS", us_allowed=True) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_bond_etf_pool.py -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Write minimal implementation**

`main_swarm.py` (이 파일은 이미 `is_kr_session`, `config` 사용):
```python
def bond_etf_pool_for_session(session: Optional[str], *, us_allowed: bool):
    """현재 세션에 매수 가능한 채권 ETF 풀 (코드,이름,듀레이션) 리스트. 세션 연동(사장 확정).
    KR 세션→KR 국채 ETF, US_TRADING→US 국채 ETF(미국장 활성 시만), 그 외→[]."""
    if is_kr_session(session):
        return list(config.BOND_ETF_POOL_KR)
    if session == "US_TRADING":
        return list(config.BOND_ETF_POOL_US) if us_allowed else []
    return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_bond_etf_pool.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Checkpoint** — `python3.11 -m pytest -q` 전체 통과.

---

### Task 4: 현재 채권 비중 계산 (`main_swarm.py`)

**Files:**
- Modify: `main_swarm.py`
- Test: `tests/test_bond_weight.py`

보유 종목 중 ETF 풀 코드에 해당하는 것만 평가액 합산 ÷ 총평가액. US ETF는 USDKRW 환산.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bond_weight.py
from main_swarm import current_bond_weight

def _h(code, qty, px):
    return {"code": code, "qty": qty, "cur_price": px}

def test_kr_bond_weight():
    holdings = [_h("153130", 100, 10000), _h("005930", 10, 70000)]  # 채권 100만 + 주식 70만
    w = current_bond_weight(holdings, total_eval_krw=2_000_000,
                            pool_codes=["153130", "114260", "148070"])
    assert abs(w - 0.5) < 1e-9   # 1,000,000 / 2,000,000

def test_us_bond_weight_fx_converted():
    holdings = [_h("TLT", 10, 90.0)]  # 900 USD
    w = current_bond_weight(holdings, total_eval_krw=1_350_000,
                            pool_codes=["SHY", "IEF", "TLT"], usdkrw=1500.0)
    assert abs(w - 1.0) < 1e-9   # 900*1500 = 1,350,000

def test_zero_total_returns_zero():
    assert current_bond_weight([_h("153130", 1, 1)], 0, ["153130"]) == 0.0

def test_no_bond_holdings_returns_zero():
    assert current_bond_weight([_h("005930", 10, 70000)], 1_000_000, ["153130"]) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_bond_weight.py -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Write minimal implementation**

`main_swarm.py` (이 파일은 이미 `_is_kr_code` 보유):
```python
def current_bond_weight(holdings, total_eval_krw: float, pool_codes,
                        usdkrw: float = 1.0) -> float:
    """보유 종목 중 채권 ETF 풀에 속한 것의 평가액 합 ÷ 총평가액. US ETF는 USDKRW 환산.
    총평가액 ≤ 0 이면 0.0(평가 불가)."""
    if not total_eval_krw or float(total_eval_krw) <= 0:
        return 0.0
    pool = {str(c).strip().upper() for c in (pool_codes or [])}
    s = 0.0
    for h in (holdings or []):
        code = str(h.get("code", "")).strip().upper()
        if code not in pool:
            continue
        val = float(h.get("qty") or 0.0) * float(h.get("cur_price") or 0.0)
        if not _is_kr_code(code):
            val *= float(usdkrw or 1.0)
        s += val
    return s / float(total_eval_krw)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_bond_weight.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Checkpoint** — `python3.11 -m pytest -q` 전체 통과.

---

### Task 5: 채권 사이징 — 목표비중 추종 (`main_swarm.py`)

**Files:**
- Modify: `main_swarm.py`
- Test: `tests/test_bond_sizing.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bond_sizing.py
from main_swarm import size_bond_action

def test_buy_when_under_target():
    # 권고 40%, 현재 10%, 총평가 1000만 → diff 30% → 300만 매수
    action, notional = size_bond_action(0.40, 0.10, 10_000_000, max_pct=0.40, band=0.03)
    assert action == "buy" and abs(notional - 3_000_000) < 1e-6

def test_sell_when_over_target():
    action, notional = size_bond_action(0.10, 0.30, 10_000_000, max_pct=0.40, band=0.03)
    assert action == "sell" and abs(notional - 2_000_000) < 1e-6

def test_hold_within_band():
    action, notional = size_bond_action(0.32, 0.30, 10_000_000, max_pct=0.40, band=0.03)
    assert action == "hold" and notional == 0.0

def test_clamped_to_max_pct():
    # 권고 80%지만 상한 40% → 현재 10% 기준 diff 30%
    action, notional = size_bond_action(0.80, 0.10, 10_000_000, max_pct=0.40, band=0.03)
    assert action == "buy" and abs(notional - 3_000_000) < 1e-6

def test_skip_when_no_recommendation():
    assert size_bond_action(None, 0.10, 10_000_000, max_pct=0.40, band=0.03) == ("skip", 0.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_bond_sizing.py -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Write minimal implementation**

`main_swarm.py`:
```python
def size_bond_action(rec_bond_pct, cur_bond_pct: float, total_eval_krw: float,
                     *, max_pct: float, band: float):
    """목표비중 추종. 반환 (action, notional_krw).
    action: 'skip'(권고없음) | 'hold'(데드존) | 'buy'(부족) | 'sell'(초과)."""
    if rec_bond_pct is None:
        return ("skip", 0.0)
    target = min(float(rec_bond_pct), float(max_pct))
    diff = target - float(cur_bond_pct or 0.0)
    if abs(diff) <= float(band):
        return ("hold", 0.0)
    notional = abs(diff) * float(total_eval_krw or 0.0)
    return ("buy" if diff > 0 else "sell", notional)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_bond_sizing.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Checkpoint** — `python3.11 -m pytest -q` 전체 통과.

---

### Task 6: 채권결정 파서 (`main_swarm.py`)

**Files:**
- Modify: `main_swarm.py` (`_parse_sell_decisions` 아래, ~line 1460)
- Test: `tests/test_bond_decisions_parse.py`

풀 화이트리스트 밖 코드는 드롭(환각 가드).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bond_decisions_parse.py
from main_swarm import _parse_bond_decisions

POOL = ["153130", "114260", "148070"]

def test_parses_pool_codes():
    out = _parse_bond_decisions("채권결정: 148070=매수, 114260=보유", POOL)
    assert out == {"148070": "매수", "114260": "보유"}

def test_drops_codes_outside_pool():
    out = _parse_bond_decisions("채권결정: 999999=매수, 148070=절반", POOL)
    assert out == {"148070": "절반"}   # 999999 풀 밖 → 드롭

def test_us_tickers_in_pool():
    out = _parse_bond_decisions("채권결정: TLT=매수", ["SHY", "IEF", "TLT"])
    assert out == {"TLT": "매수"}

def test_empty_when_no_line():
    assert _parse_bond_decisions("보유 채권 없음", POOL) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_bond_decisions_parse.py -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Write minimal implementation**

`main_swarm.py`:
```python
def _parse_bond_decisions(text: str, pool_codes) -> Dict[str, str]:
    """채권관리실장의 '채권결정: 148070=매수, TLT=보유' 한 줄 → {code: directive}.
    ETF 풀 화이트리스트 밖 코드는 드롭(티커 환각 가드)."""
    out: Dict[str, str] = {}
    m = re.search(r"채권\s*결정\s*[:：]\s*(.+)", text or "", re.IGNORECASE)
    if not m:
        return out
    pool = {str(c).strip().upper() for c in (pool_codes or [])}
    for part in re.split(r"[,，;]", m.group(1).splitlines()[0]):
        mm = re.match(r"\s*([0-9]{6}|[A-Za-z]{1,5})\s*=\s*([^\s,，;]+)", part)
        if mm and mm.group(1).upper() in pool:
            out[mm.group(1).upper()] = mm.group(2).strip()
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_bond_decisions_parse.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Checkpoint** — `python3.11 -m pytest -q` 전체 통과.

---

### Task 7: 채권관리실장 페르소나 (`agents/specialists.py`)

**Files:**
- Modify: `agents/specialists.py` (`create_post_manager` 근처에 추가)
- Test: `tests/test_bond_manager_persona.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bond_manager_persona.py
from agents.specialists import create_bond_manager

def test_persona_created():
    a = create_bond_manager(injection={"uid": 1})
    assert a.name == "채권관리실장"
    assert a.role == "bond_manager"

def test_persona_is_active_rate_strategist():
    a = create_bond_manager(injection={"uid": 1})
    assert "금리" in a.system_prompt and "듀레이션" in a.system_prompt
    assert "채권결정" in a.system_prompt          # 출력 형식 명시
    assert "퀀트" not in a.system_prompt or "무관" in a.system_prompt  # 퀀트 비의존
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_bond_manager_persona.py -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Write minimal implementation**

`agents/specialists.py`:
```python
def create_bond_manager(injection=None) -> BaseAgent:
    """채권관리실장(Bond Manager) — 채권 ETF 전담, 능동적 금리 전략가 (사장 지시 2026-06-08).

    매크로 금리 전망 + 권고 채권비중을 받아, 허용 ETF 풀 안에서 듀레이션·종목·페이스를
    재량 결정(목표비중 추종). 주식 퀀트 지표는 채권에 무의미하므로 쓰지 않는다.
    매수 시 계획 보유기간을 self-thesis 로 기록, 매도 판단 때 강력 권고로 상기."""
    return BaseAgent(
        name="채권관리실장",
        role="bond_manager",
        model_key="bond_manager",
        injection=injection,
        system_prompt="""당신은 ArQuant v1.0의 '채권관리실장(Bond Manager)'입니다.
성격: **능동적 금리 전략가**. 매크로 금리 전망으로 채권 ETF의 듀레이션을 조절합니다 —
금리 하락 예상이면 장기채(듀레이션 확대), 상승 예상이면 단기채(듀레이션 축소).

## 입력 (매 사이클 주입)
- 현재 세션에서 매수 가능한 **허용 채권 ETF 풀** (코드·이름·듀레이션) — 이 안에서만 선택하십시오.
- 전략리서치팀장 매크로 보고: 금리 전망 + 권고 채권 비중.
- 현재 채권 ETF 보유·평가비중, 목표 대비 부족/초과분(매수/매도 예산).
- 보유 채권의 계획 보유기간(강력 권고로 상기됨).

## 판단 원칙
- 주식 퀀트(RSI·모멘텀 등)는 채권에 **무관** — 금리·매크로로만 판단하십시오.
- 부족분(매수 예산) 한도 안에서 듀레이션·종목을 고르고, 초과 시 비중을 줄이십시오.
- 보유기간 강력 권고가 있으면 존중하되, 금리 전망이 바뀌었으면 사유를 적고 교체할 수 있습니다.

## 응답 형식 (자유 서술 + 마지막 줄에 결정표)
- 줄글 + '-' 불릿. 종목별 금리뷰·듀레이션 판단 1~2줄.
- 마지막 줄은 반드시 (다른 텍스트 없이):
  `채권결정: 148070=매수, TLT=보유`  ← 풀의 ETF 코드. 값: 매수 / 절반 / 보유 / 또는 매도 주수.""",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_bond_manager_persona.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Checkpoint** — `python3.11 -m pytest -q` 전체 통과.

---

### Task 8: 채권 보유기간 self-thesis 저장소 (`infra/bond_thesis.py`)

**Files:**
- Create: `infra/bond_thesis.py`
- Modify: `infra/user_paths.py` (`bond_thesis_path` 추가)
- Test: `tests/test_bond_thesis_store.py`

주식 `position_thesis`와 **분리된** 파일(`data/<uid>/bond_thesis.json`)에 저장 — 주식 매도 트랙에 채권 thesis가 섞이지 않게.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bond_thesis_store.py
from infra import bond_thesis

def test_record_and_get(tmp_path, monkeypatch):
    from infra import user_paths
    monkeypatch.setattr(user_paths, "bond_thesis_path",
                        lambda uid: tmp_path / f"bond_thesis_{uid}.json")
    bond_thesis._reset_cache_for_tests()
    bond_thesis.record(1, "148070", {"entry_ts": "2026-06-08 10:00:00",
                                     "entry_price": 50000.0, "planned_hold_hours": 120,
                                     "entry_reason": "금리 고점 베팅", "source_agent": "채권관리실장"})
    t = bond_thesis.get(1, "148070")
    assert t["planned_hold_hours"] == 120
    assert "148070" in bond_thesis.get_all(1)

def test_remove(tmp_path, monkeypatch):
    from infra import user_paths
    monkeypatch.setattr(user_paths, "bond_thesis_path",
                        lambda uid: tmp_path / f"bond_thesis_{uid}.json")
    bond_thesis._reset_cache_for_tests()
    bond_thesis.record(1, "TLT", {"entry_price": 90.0})
    bond_thesis.remove(1, "TLT")
    assert bond_thesis.get(1, "TLT") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_bond_thesis_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'infra.bond_thesis'`.

- [ ] **Step 3: Write minimal implementation**

`infra/user_paths.py` — `position_thesis_path` 바로 아래에 추가:
```python
def bond_thesis_path(uid: int) -> Path:
    return _user_dir(uid) / "bond_thesis.json"
```
> 주: `position_thesis_path`가 쓰는 디렉터리 헬퍼와 동일한 것을 사용한다(같은 파일 안에서 `position_thesis_path` 구현을 그대로 본떠 파일명만 `bond_thesis.json`으로 바꾼다).

`infra/bond_thesis.py` — `infra/position_thesis.py`를 복제하되 (1) docstring을 채권용으로, (2) 경로를 `user_paths.bond_thesis_path`로, (3) 로거 이름을 `"BOND_THESIS"`로 변경. 공개 API는 동일: `record(uid, code, thesis)`, `get(uid, code)`, `get_all(uid)`, `remove(uid, code)`, `_reset_cache_for_tests()`, `sync_with_holdings(uid, current_codes)`.

핵심 본문(전체를 이 형태로):
```python
"""채권 보유기간 self-thesis 저장소 — per-uid data/<uid>/bond_thesis.json.

채권관리실장이 채권 ETF 매수 시 기록한 진입가·계획 보유기간·진입사유를 보관해,
매도 판단 직전 강력 권고로 상기한다(사장 지시 2026-06-08). 주식 position_thesis 와 분리.
포맷: {code: {entry_ts, entry_price, planned_hold_hours, entry_reason, source_agent}}.
"""
from __future__ import annotations
import json, logging, os
from typing import Dict, Optional, Any
from infra import user_paths

logger = logging.getLogger("BOND_THESIS")
_CACHE: Dict[int, Dict[str, Dict[str, Any]]] = {}

def _reset_cache_for_tests() -> None:
    _CACHE.clear()

def _load(uid: int) -> Dict[str, Dict[str, Any]]:
    if uid in _CACHE:
        return _CACHE[uid]
    p = user_paths.bond_thesis_path(uid)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except Exception as e:
            logger.warning(f"[bond_thesis] uid={uid} 로드 실패({e})")
            data = {}
    else:
        data = {}
    _CACHE[uid] = data
    return data

def _save(uid: int) -> None:
    data = _CACHE.get(uid) or {}
    p = user_paths.bond_thesis_path(uid)
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except Exception as e:
        logger.warning(f"[bond_thesis] uid={uid} 저장 실패: {e}")

def _norm_code(code: Any) -> str:
    return str(code).strip().upper()

def record(uid: int, code: Any, thesis: Dict[str, Any]) -> None:
    d = _load(uid); d[_norm_code(code)] = dict(thesis or {}); _save(uid)

def get(uid: int, code: Any) -> Optional[Dict[str, Any]]:
    return _load(uid).get(_norm_code(code))

def get_all(uid: int) -> Dict[str, Dict[str, Any]]:
    return dict(_load(uid))

def remove(uid: int, code: Any) -> None:
    d = _load(uid)
    if _norm_code(code) in d:
        del d[_norm_code(code)]; _save(uid)

def sync_with_holdings(uid: int, current_codes) -> list:
    """보유에서 사라진 채권 thesis 정리. 반환 제거된 코드 리스트."""
    keep = {_norm_code(c) for c in (current_codes or [])}
    d = _load(uid); removed = [c for c in list(d) if c not in keep]
    for c in removed:
        del d[c]
    if removed:
        _save(uid)
    return removed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_bond_thesis_store.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Checkpoint** — `python3.11 -m pytest -q` 전체 통과.

---

### Task 9: 보유기간 강력 권고 포맷터 (`agents/specialists.py`)

**Files:**
- Modify: `agents/specialists.py` (`format_thesis_reminder` 아래)
- Test: `tests/test_bond_thesis_reminder.py`

주식 `format_thesis_reminder`의 채권판. 강제력 없음 — 강력 권고만.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bond_thesis_reminder.py
from agents.specialists import format_bond_thesis_reminder

def test_reminder_strong_advisory():
    theses = {"148070": {"entry_ts": "2026-06-08 10:00:00", "entry_price": 50000.0,
                         "planned_hold_hours": 120, "entry_reason": "금리 고점 베팅",
                         "source_agent": "채권관리실장"}}
    holdings = [{"code": "148070", "name": "KOSEF 국고채10년", "cur_price": 50500.0}]
    out = format_bond_thesis_reminder(theses, holdings, now_iso="2026-06-08 14:00:00")
    assert "강력 권고" in out
    assert "148070" in out or "국고채" in out
    assert "120" in out          # 계획 보유기간

def test_empty_when_no_match():
    out = format_bond_thesis_reminder({}, [], now_iso="2026-06-08 14:00:00")
    assert out == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_bond_thesis_reminder.py -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Write minimal implementation**

`agents/specialists.py` (이미 `_hours_between` 보유):
```python
def format_bond_thesis_reminder(theses: Dict[str, Dict[str, Any]],
                                holdings: List[Dict[str, Any]],
                                now_iso: str) -> str:
    """채권 보유기간 self-thesis 를 채권관리실장 매도 판단 프롬프트에 강력 권고로 주입.
    매칭 없으면 빈 문자열. 강제력 없음 — 최종 매도 권한은 채권관리실장."""
    if not theses or not holdings:
        return ""
    lines: List[str] = ["📌 채권관리실장 [강력 권고]: 매도 판단 전에 — 매수 때 세운 계획 보유기간을 확인하십시오. 계획기간이 한참 남았는데 미세 손익만으로 청산하는 것은 무계획 단타입니다. 계획 유지를 강력히 권고하되, 금리 전망이 바뀌었으면 사유를 적고 교체할 수 있습니다."]
    for h in holdings:
        code = str(h.get("code", "")).strip().upper()
        if code not in theses:
            continue
        t = theses[code]
        name = h.get("name") or code
        entry_p = float(t.get("entry_price") or 0.0)
        hold_h = float(t.get("planned_hold_hours") or 0.0)
        entry_ts = t.get("entry_ts") or ""
        cur_price = float(h.get("cur_price") or 0.0)
        hours_held = _hours_between(entry_ts, now_iso) if entry_ts else 0.0
        over = (hold_h > 0 and hours_held > hold_h)
        bits = [f"- {name}({code}): {entry_ts} 매수 @{entry_p:,.0f}",
                f"계획 보유 {hold_h:.0f}h, 현재 {hours_held:.1f}h{' (초과)' if over else ''}",
                f"현재가 {cur_price:,.0f}"]
        lines.append(" | ".join(bits))
        reason = (t.get("entry_reason") or "").strip()
        if reason:
            lines.append(f"    진입 사유: {reason}")
    return "\n".join(lines) if len(lines) > 1 else ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_bond_thesis_reminder.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Checkpoint** — `python3.11 -m pytest -q` 전체 통과.

---

### Task 10: 사이클 통합 — `_cyc_stage_bonds` + 에이전트 등록 (`main_swarm.py`)

**Files:**
- Modify: `main_swarm.py` (import·에이전트 등록·스테이지 추가·파이프라인 삽입)
- Test: `tests/test_bond_cycle_integration.py` (순수 헬퍼 위주 — LLM 호출은 통합 스모크에서 제외)

이 Task는 앞 Task들의 순수 함수를 한 스테이지로 엮는다. LLM 호출(`bond_manager.think`)은 모킹이 무거우므로, **테스트는 채권 보유 분리 헬퍼**(`split_bond_holdings`)에 집중하고, 스테이지 자체는 `ENABLE_BOND_ETF=False` 스킵 회귀(Task 11)로 보호한다.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bond_cycle_integration.py
from main_swarm import split_bond_holdings

def test_split_separates_bond_etfs_from_stocks():
    holdings = [{"code": "153130", "name": "KODEX 단기채권"},
                {"code": "005930", "name": "삼성전자"},
                {"code": "TLT", "name": "TLT"}]
    pool_codes = ["153130", "114260", "148070", "SHY", "IEF", "TLT"]
    stocks, bonds = split_bond_holdings(holdings, pool_codes)
    assert [h["code"] for h in stocks] == ["005930"]
    assert sorted(h["code"] for h in bonds) == ["153130", "TLT"]

def test_split_empty_pool_all_stocks():
    holdings = [{"code": "005930"}]
    stocks, bonds = split_bond_holdings(holdings, [])
    assert stocks == holdings and bonds == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_bond_cycle_integration.py -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Write minimal implementation**

3a. `main_swarm.py` 상단 import에 `create_bond_manager` 추가(line 12-13 블록):
```python
from agents.specialists import (create_macro_analyst, create_quant_analyst, create_news_analyst,
                                create_trader, create_post_manager, create_ops_support,
                                create_bond_manager)
```

3b. 헬퍼 추가(`current_bond_weight` 근처):
```python
def split_bond_holdings(holdings, pool_codes):
    """보유를 (주식, 채권ETF) 로 분리. 채권 ETF 는 주식 매도 트랙에서 제외하고 채권 트랙으로."""
    pool = {str(c).strip().upper() for c in (pool_codes or [])}
    stocks, bonds = [], []
    for h in (holdings or []):
        (bonds if str(h.get("code", "")).strip().upper() in pool else stocks).append(h)
    return stocks, bonds
```

3c. 에이전트 등록 — `self.fund_planner = create_fund_planner(...)`(line ~2735) 아래:
```python
        self.bond_manager = create_bond_manager(injection=_inj)
```
그리고 에이전트 레지스트리 딕셔너리(line ~2771, `"펀드기획실장": self.fund_planner,` 옆)에:
```python
            "채권관리실장": self.bond_manager,
```

3d. 새 스테이지 메서드 추가(`_cyc_stage_finalize_sell` 와 `_cyc_stage_build_orders` 사이):
```python
    async def _cyc_stage_bonds(self, cyc):
        """채권 트랙 — 매크로 채권% 권고를 채권 ETF 매수/매도로 실현(독립 트랙).
        ENABLE_BOND_ETF 면만 동작. 주식 퀀트/thesis 거부권/자동손절 우회, 리스크검증은 공통."""
        if not bool(runtime.get("ENABLE_BOND_ETF", uid=self.uid)):
            return
        session = cyc.session
        us_allowed = bool(runtime.get("ALLOW_US_STOCKS", uid=self.uid))
        pool = bond_etf_pool_for_session(session, us_allowed=us_allowed)
        if not pool:
            return
        pool_codes = [c for c, _, _ in pool]
        rec_bond_pct = _parse_macro_bond_pct(getattr(cyc, "macro_report", "") or "")
        holdings = getattr(cyc, "_orig_holdings", None) or cyc.holdings or []
        total_eval = float(getattr(cyc, "total_eval_krw", 0.0) or 0.0)
        usdkrw = float(getattr(cyc, "usdkrw", 1.0) or 1.0)
        cur_w = current_bond_weight(holdings, total_eval, pool_codes, usdkrw)
        action, notional = size_bond_action(
            rec_bond_pct, cur_w, total_eval,
            max_pct=float(runtime.get("BOND_TARGET_MAX_PCT", uid=self.uid) or 0.40),
            band=float(runtime.get("BOND_REBALANCE_BAND_PCT", uid=self.uid) or 0.03))
        if action in ("skip", "hold"):
            await self._emit({"type": "agent_msg", "agent": "채권관리실장",
                "message": f"채권 평가 — 권고 {('%.0f%%'%(rec_bond_pct*100)) if rec_bond_pct is not None else 'N/A'}, "
                           f"현재 {cur_w*100:.0f}% → {'데드존 유지' if action=='hold' else '권고 없음(스킵)'}."})
            cyc.bond_directives = {}
            return
        # 보유기간 강력 권고 주입(채권판)
        from infra import bond_thesis as _bt
        _bt_all = _bt.get_all(self.uid)
        _, bond_holdings = split_bond_holdings(holdings, pool_codes)
        reminder = ""
        if _bt_all and bond_holdings:
            from agents.specialists import format_bond_thesis_reminder
            reminder = format_bond_thesis_reminder(_bt_all, bond_holdings, _now_kst_iso())
            if reminder:
                await self._emit({"type": "agent_msg", "agent": "채권관리실장", "message": reminder})
        pool_str = ", ".join(f"{c}({n},{d})" for c, n, d in pool)
        prompt = (
            (f"{reminder}\n\n" if reminder else "")
            + f"현재 세션: {session}\n허용 채권 ETF 풀: {pool_str}\n\n"
            + f"전략리서치팀장 매크로 보고:\n{getattr(cyc, 'macro_report', '')}\n\n"
            + f"권고 채권비중: {rec_bond_pct*100:.0f}% / 현재 채권비중: {cur_w*100:.0f}%\n"
            + f"→ {action.upper()} 예산 약 {notional:,.0f}원 한도. 듀레이션·종목을 풀에서 골라 결정하십시오.\n"
            + f"마지막 줄은 반드시 `채권결정: 코드=매수/절반/보유, ...` (풀 ETF 전체).")
        view = ""
        for _ in range(3):
            try:
                view = await self.bond_manager.think(prompt)
            except Exception as e:
                view = f"[채권관리실장 에러] {e}"
            if view and view.strip() and "에러" not in view[:80]:
                break
            await asyncio.sleep(1.0)
        self.cycle_log.log("RISK", "채권관리실장", view)
        await self._emit({"type": "agent_msg", "agent": "채권관리실장", "message": view})
        cyc.bond_directives = _parse_bond_decisions(view, pool_codes)
        cyc.bond_action = action
        cyc.bond_notional = notional
```

3e. 파이프라인에 삽입(`_cyc_stage_finalize_sell` 호출 다음 줄, line ~3275):
```python
            await self._cyc_stage_finalize_sell(cyc)
            await self._cyc_stage_bonds(cyc)
            await self._cyc_stage_build_orders(cyc)
```

> **주문 조립 연결:** `_cyc_stage_build_orders`/`_build_orders`에서 `cyc.bond_directives`(매도)와 `cyc.bond_action=='buy'`(매수: notional 한도 내에서 듀레이션 선택 종목으로 `kr_buy`/`us_buy`)를 주식 주문 객체에 합류시킨다. 채권 매수 체결 시 `bond_thesis.record(uid, code, {...planned_hold_hours...})` 호출, 매도 체결 시 `bond_thesis.remove`. 이 연결의 정확한 구현은 `_build_orders` 내부 구조에 맞춰 진행하되, 채권 주문도 기존 리스크검증(`validate_order_draft`)·실행 경로를 그대로 통과시킨다. **별도 채권 주문 경로를 만들지 말 것** — 같은 주문 리스트에 side='buy'/'sell'로 합류.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_bond_cycle_integration.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Checkpoint** — `python3.11 -m pytest -q` 전체 통과.

---

### Task 11: 회귀 — 마스터 스위치 OFF 시 채권 트랙 스킵

**Files:**
- Test: `tests/test_bond_master_switch.py`

기존 동작 불변 보증: `ENABLE_BOND_ETF=False`(기본)면 `_cyc_stage_bonds`가 즉시 반환.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bond_master_switch.py
import asyncio, config

def test_default_master_switch_off():
    assert config.ENABLE_BOND_ETF is False   # 배포 시 기본 OFF — 사장이 대시보드에서 켤 때만 동작

def test_stage_returns_immediately_when_off(monkeypatch):
    import main_swarm, runtime
    # ENABLE_BOND_ETF override 가 없으면 config 기본(False) 사용 → 스테이지 즉시 반환.
    class _Cyc:  # 최소 더미
        session = "KR_TRADING"; holdings = []; macro_report = ""
    # bond_manager.think 가 절대 호출되지 않아야 함(호출되면 AttributeError 로 실패 유도).
    # 스테이지가 즉시 return 하면 think 미참조 → 통과.
    # 실제 Swarm 인스턴스 구성은 무겁기 때문에, 여기서는 게이트 1줄만 단위로 본다:
    assert bool(runtime.get("ENABLE_BOND_ETF", uid=999)) is False
```

> 주: Swarm 인스턴스 전체를 띄우는 통합 테스트는 비용이 크다. 이 회귀는 (1) config 기본 OFF, (2) `runtime.get`이 override 없을 때 config 기본값을 돌려준다는 두 사실로 "기본 배포 시 채권 트랙 미동작"을 보장한다. `runtime.get` 폴백 동작은 기존 `runtime` 테스트로 이미 검증돼 있다.

- [ ] **Step 2: Run test to verify it fails (or passes trivially)**

Run: `python3.11 -m pytest tests/test_bond_master_switch.py -v`
Expected: Task 1 적용 후엔 PASS — 단, Task 1 *이전*에 작성했다면 `config.ENABLE_BOND_ETF` 없어 FAIL. (이미 Task 1 완료 상태이므로 통과 확인용 회귀.)

- [ ] **Step 3: (구현 불필요 — 게이트는 Task 10에서 작성됨)**

- [ ] **Step 4: Run full suite**

Run: `python3.11 -m pytest -q`
Expected: PASS (전체).

- [ ] **Step 5: Checkpoint** — 전체 통과 + 거부권 폐지 테스트(`test_thesis_advisory_only.py`)도 여전히 green 확인.

---

## Self-Review (작성자 점검 결과)

**Spec coverage:**
- §4.1 페르소나 → Task 7 ✓ / §4.2 채권% 파서 → Task 2 ✓ / §4.3 ETF 풀·세션필터 → Task 1·3 ✓
- §4.4 현재 채권비중 → Task 4 ✓ / §4.5 사이징 → Task 5 ✓ / §4.6 보유기간 thesis → Task 8·9 ✓
- §4.7 주문 조립·검증 공유 → Task 10(3d/3e 주문 합류 note) ✓ / §5 사이클 통합 → Task 10 ✓
- §6 설정 키 → Task 1 ✓ / §7 권한 매트릭스(퀀트 우회) → Task 10 스테이지가 퀀트 미참조 ✓ / §8 테스트 → 각 Task ✓

**남은 구현 판단(엔지니어가 `_build_orders` 컨텍스트에서 확정):**
- Task 10 3d note의 채권 매수/매도 주문 합류 + 체결 시 `bond_thesis.record/remove` 연결. `cyc.total_eval_krw`·`cyc.usdkrw`·`cyc._orig_holdings` 속성명이 실제와 다르면 `_cyc_stage_finalize_sell`에서 쓰는 동일 출처로 맞춘다(현재 평가액은 `kr_net_valuation` + 해외 외화평가총액).

**Placeholder scan:** 코드 스텝은 모두 실제 코드 포함. Task 10의 주문 합류만 의도적으로 기존 `_build_orders` 구조에 위임(별도 경로 금지 명시).

**Type consistency:** `_parse_macro_bond_pct`, `bond_etf_pool_for_session`, `current_bond_weight`, `size_bond_action`, `_parse_bond_decisions`, `split_bond_holdings`, `create_bond_manager`, `format_bond_thesis_reminder`, `bond_thesis.{record,get,get_all,remove}` — 정의 Task와 사용 Task 시그니처 일치 확인.
