# 멀티 자산슬리브 + 매도 종합 + ops tier 구분 — 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 채권 트랙을 채권·원자재 공통 "자산슬리브" 엔진으로 일반화하고, 슬리브 매도를 사후관리실장이 주식과 함께 종합하게 하며, ops 파라미터를 사이클/주간 tier로 코드 강제 구분한다.

**Architecture:** 단일 `SleeveSpec`이 한 트랙을 기술하고(`infra/asset_sleeves.py`), main_swarm의 채권 전용 로직이 spec 인자를 받는 범용 함수로 승격된다. 매도 파이프라인은 슬리브→사후관리 순으로 재배열되어 사후관리실장이 통합 매도결정을 낸다. 커밋은 사장 명시 요청 시에만(자동 Backup 도구가 휩쓸어 반영) — 본 계획의 "Commit" 단계는 **생략**하고 대신 `python3.11 -m pytest` 그린 확인으로 대체한다.

**Tech Stack:** Python 3.11(테스트 필수), pytest, FastAPI(server/app.py), vanilla JS(index.html), Kotlin(모바일, APK 재빌드 보류).

**테스트 명령:** `python3.11 -m pytest`(전체) / `python3.11 -m pytest tests/test_x.py -v`(단일). 기본 `python`은 argon2로 즉사.

**커밋 정책:** 이 저장소는 외부 도구가 주기적으로 `git add -A` + Backup 커밋한다. 본 계획은 명시적 git 커밋을 하지 않는다(사장 요청 시에만). 각 Task 말미는 "전체 pytest 그린 확인"으로 마무리한다.

**스펙:** `docs/superpowers/specs/2026-06-09-multi-asset-sleeves-and-ops-tiers-design.md`

---

## 빌드 순서 (페이즈)

1. **자산슬리브 엔진** — 채권을 슬리브#1로 일반화(동작 보존, 채권 테스트 그린).
2. **원자재 슬리브#2 + config 풀** — 검증 ETF·기본 ON·4분할 매크로.
3. **매도 흐름 재설계** — 슬리브 선행 + 사후관리실장 통합.
4. **보유계획 일괄 상기** — fund_planner → 3매니저.
5. **페르소나 rename** — 운용전략실장 → 주식운용실장.
6. **ops cycle/weekly tier** — 강제 구분.
7. **UI·기본값·최종 검증·배포**.

각 페이즈는 직전 페이즈 그린을 전제로 한다. 페이즈 1은 순수 리팩토링이라 **기존 채권 테스트 9종이 안전망**이다.

---

# 페이즈 1 — 자산슬리브 엔진 (채권 일반화)

선행 조사: 구현 전 `main_swarm.py`에서 현재 채권 함수의 정확한 본문을 읽는다 —
`_parse_macro_bond_pct`(~1095), `current_bond_weight`(~1157), `size_bond_action`(~1176),
`cap_bond_buy_notional`(~1190), `assemble_bond_orders`(~1206), `bond_etf_pool_for_session`(~1110),
`build_exec_list`(~1127), `_parse_bond_decisions`(~1658), `split_bond_holdings`/`all_bond_pool_codes`,
`_cyc_stage_bonds`(~4436). 그리고 `infra/bond_thesis.py` 전체.

## Task 1.1: SleeveSpec + 슬리브 레지스트리

**Files:**
- Create: `infra/asset_sleeves.py`
- Test: `tests/test_asset_sleeves.py`

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_asset_sleeves.py`

```python
from infra.asset_sleeves import SleeveSpec, SLEEVES, get_sleeve, all_sleeve_pool_codes

def test_bond_sleeve_registered():
    s = get_sleeve("bond")
    assert s.manager_name == "채권운용실장"
    assert s.role == "bond_manager"
    assert s.macro_keyword == "채권"
    assert s.decision_keyword == "채권결정"
    assert s.enable_key == "ENABLE_BOND_ETF"
    # 풀 코드에 기존 국고채 3종이 포함
    kr_codes = {c for c, *_ in s.pool_kr}
    assert {"153130", "114260", "148070"} <= kr_codes

def test_two_sleeves_registered():
    keys = {s.key for s in SLEEVES}
    assert keys == {"bond", "commodity"}

def test_all_pool_codes_union_upper():
    codes = all_sleeve_pool_codes()
    assert "153130" in codes and "TLT" in codes  # 채권
    assert "132030" in codes and "GLD" in codes   # 원자재
    # US 티커는 대문자 정규화
    assert "tlt" not in codes
```

- [ ] **Step 2: 실패 확인** — `python3.11 -m pytest tests/test_asset_sleeves.py -v` → `ModuleNotFoundError: infra.asset_sleeves`.

- [ ] **Step 3: 최소 구현** — `infra/asset_sleeves.py`

```python
"""자산슬리브 엔진 — 채권·원자재 등 매크로 자산배분 트랙을 단일 SleeveSpec로 일반화.

채권 트랙(2026-06-08)을 슬리브#1로 승격하고 원자재를 슬리브#2로 추가(2026-06-09).
main_swarm 의 채권 전용 함수가 여기로 옮겨와 spec 인자를 받는 범용 함수가 된다.
풀은 화이트리스트(LLM 티커 환각 방지) — 코드 오류=실주문 실패이므로 검증된 코드만 등재."""
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Tuple, Dict, Optional
import config


@dataclass(frozen=True)
class SleeveSpec:
    key: str                 # "bond" | "commodity"
    manager_name: str        # 라우팅 키(한글)
    role: str                # 모델/토큰 키
    macro_keyword: str       # 매크로% 파싱 단어
    decision_keyword: str    # LLM 마지막 줄 파싱
    pool_kr: Tuple[Tuple, ...]
    pool_us: Tuple[Tuple, ...]
    enable_key: str
    target_max_key: str
    band_key: str
    per_cycle_key: str


BOND_SLEEVE = SleeveSpec(
    key="bond", manager_name="채권운용실장", role="bond_manager",
    macro_keyword="채권", decision_keyword="채권결정",
    pool_kr=tuple(config.BOND_ETF_POOL_KR), pool_us=tuple(config.BOND_ETF_POOL_US),
    enable_key="ENABLE_BOND_ETF", target_max_key="BOND_TARGET_MAX_PCT",
    band_key="BOND_REBALANCE_BAND_PCT", per_cycle_key="BOND_PER_CYCLE_RATIO",
)
COMMODITY_SLEEVE = SleeveSpec(
    key="commodity", manager_name="원자재운용실장", role="commodity_manager",
    macro_keyword="원자재", decision_keyword="원자재결정",
    pool_kr=tuple(config.COMMODITY_ETF_POOL_KR), pool_us=tuple(config.COMMODITY_ETF_POOL_US),
    enable_key="ENABLE_COMMODITY_ETF", target_max_key="COMMODITY_TARGET_MAX_PCT",
    band_key="COMMODITY_REBALANCE_BAND_PCT", per_cycle_key="COMMODITY_PER_CYCLE_RATIO",
)
SLEEVES: List[SleeveSpec] = [BOND_SLEEVE, COMMODITY_SLEEVE]


def get_sleeve(key: str) -> SleeveSpec:
    for s in SLEEVES:
        if s.key == key:
            return s
    raise KeyError(key)


def _pool_codes(spec: SleeveSpec) -> set:
    return {str(c).strip().upper() for c, *_ in (spec.pool_kr + spec.pool_us)}


def all_sleeve_pool_codes() -> set:
    out: set = set()
    for s in SLEEVES:
        out |= _pool_codes(s)
    return out
```

> 주: 이 Task는 config에 `COMMODITY_ETF_POOL_KR/US`·`COMMODITY_TARGET_MAX_PCT` 등이
> 존재해야 import가 된다. **Task 2.1을 먼저 하거나**, 임시로 config에 빈 리스트/기본값을
> 추가한 뒤 Task 2.1에서 실코드로 채운다. 실행 순서상 **Task 2.1(config 원자재 상수)을
> Task 1.1보다 먼저** 진행한다(아래 페이즈 2 참조). 본 계획은 논리 순서로 1을 먼저 적었으나
> 물리 실행은 config 상수 → asset_sleeves 순.

- [ ] **Step 4: 통과 확인** — `python3.11 -m pytest tests/test_asset_sleeves.py -v` → PASS.

## Task 1.2: 범용 사이징/파싱/조립 함수 이식

**Files:**
- Modify: `infra/asset_sleeves.py`
- Modify: `main_swarm.py` (채권 함수 제거 → asset_sleeves 호출로 교체)
- Test: `tests/test_asset_sleeves.py` (확장)

- [ ] **Step 1: 실패 테스트 추가** — 채권 동작을 그대로 핀(기존 `test_bond_*`의 수치를 재사용)

```python
from infra.asset_sleeves import (
    parse_macro_sleeve_pct, current_sleeve_weight, size_sleeve_action,
    cap_sleeve_buy_notional, assemble_sleeve_orders, parse_sleeve_decisions,
    sleeve_pool_for_session, split_sleeve_holdings, BOND_SLEEVE, COMMODITY_SLEEVE,
)

def test_parse_macro_pct_bond_and_commodity():
    txt = "📈 자산 배분 권고: 주식 50% / 채권 25% / 원자재 15% / 현금 10%"
    assert parse_macro_sleeve_pct(txt, "채권") == 0.25
    assert parse_macro_sleeve_pct(txt, "원자재") == 0.15
    assert parse_macro_sleeve_pct(txt, "주식") == 0.50

def test_size_action_skip_inside_band():
    # 현재 24%, 목표 25%, 밴드 3% → 밴드 내 → skip
    action, notional = size_sleeve_action(0.25, 0.24, 1_000_000, 0.40, 0.03)
    assert action == "skip"

def test_size_action_buy_below_band():
    action, notional = size_sleeve_action(0.30, 0.10, 1_000_000, 0.40, 0.03)
    assert action == "buy" and notional > 0

def test_size_action_clamped_to_max():
    # 권고 90%지만 max 40% → 40% 기준
    action, notional = size_sleeve_action(0.90, 0.10, 1_000_000, 0.40, 0.03)
    assert action == "buy"
    assert notional <= 0.40 * 1_000_000 + 1

def test_parse_decisions_whitelist_drop():
    pool = {"148070", "TLT"}
    d = parse_sleeve_decisions("채권결정: 148070=매수, 999999=매수, TLT=보유", "채권결정", pool)
    assert d == {"148070": "매수", "TLT": "보유"}

def test_pool_for_session_kr_vs_us():
    kr = sleeve_pool_for_session(BOND_SLEEVE, "KR_TRADING", us_allowed=True)
    assert all(not str(c).isalpha() for c, *_ in kr)  # KR=6자리 숫자
    us = sleeve_pool_for_session(BOND_SLEEVE, "US_TRADING", us_allowed=True)
    assert any(c == "TLT" for c, *_ in us)
    off = sleeve_pool_for_session(BOND_SLEEVE, "US_TRADING", us_allowed=False)
    assert off == []
```

- [ ] **Step 2: 실패 확인** — import 에러.

- [ ] **Step 3: 구현** — `main_swarm.py`의 채권 함수 본문을 `asset_sleeves.py`로 이식하며 채권 색을 제거하고 `spec`/`keyword` 인자화. 시그니처:

```python
def parse_macro_sleeve_pct(text: Optional[str], keyword: str) -> Optional[float]: ...
def current_sleeve_weight(holdings, total_eval_krw, pool_codes, usdkrw=1.0) -> float: ...
def size_sleeve_action(rec_pct, cur_pct, total_eval_krw, max_pct, band) -> Tuple[str, float]: ...
def cap_sleeve_buy_notional(notional_krw, total_eval_krw, cash_krw, per_cycle_ratio, min_cash_buffer) -> float: ...
def assemble_sleeve_orders(spec, action, notional_krw, directives, holdings, price_lookup, usdkrw) -> List[Dict]: ...
def parse_sleeve_decisions(text, keyword, pool_codes) -> Dict[str, str]: ...
def sleeve_pool_for_session(spec, session, us_allowed) -> List[Tuple]: ...
def split_sleeve_holdings(holdings, sleeve_codes) -> Tuple[list, list]: ...
```

본문 로직은 기존 채권 함수에서 그대로 가져오되 `_parse_macro_bond_pct`의 정규식 `채권`을
`keyword` 파라미터로, `assemble_bond_orders`의 `reason="채권운용실장 자산배분"`을
`f"{spec.manager_name} 자산배분"`으로 바꾼다. `main_swarm.py`의 기존 채권 함수 정의는
삭제하고, 호출부는 `from infra.asset_sleeves import ...`로 교체(이 Task에서 `_cyc_stage_bonds`는
아직 채권만 — 페이즈 3에서 루프화).

- [ ] **Step 4: 통과 + 회귀 확인** — `python3.11 -m pytest tests/test_asset_sleeves.py tests/test_bond_*.py -v` → 전부 PASS. 기존 채권 테스트가 import하던 `main_swarm._parse_bond_decisions` 등이 사라졌으면, 그 테스트들을 `infra.asset_sleeves`의 신규 함수로 import 경로만 교체(동작 동일하므로 assert 불변).

## Task 1.3: bond_thesis → sleeve_thesis 일반화

**Files:**
- Create: `infra/sleeve_thesis.py`
- Delete: `infra/bond_thesis.py`
- Modify: `main_swarm.py`(호출부), `agents/specialists.py`(reminder 포맷터)
- Test: `tests/test_sleeve_thesis_store.py`(신규), `tests/test_bond_thesis_store.py`(경로 교체)

- [ ] **Step 1: 실패 테스트** — `tests/test_sleeve_thesis_store.py`

```python
import infra.sleeve_thesis as st

def test_record_and_get_per_sleeve(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "_base_dir", lambda uid: tmp_path)  # 또는 user_paths 패치
    st.record(99, "bond", "148070", {"entry_price": 10000, "planned_hold_hours": 168})
    st.record(99, "commodity", "GLD", {"entry_price": 200, "planned_hold_hours": 72})
    assert "148070" in st.get_all(99, "bond")
    assert "GLD" in st.get_all(99, "commodity")
    # 슬리브 격리 — 채권 thesis에 원자재 코드 없음
    assert "GLD" not in st.get_all(99, "bond")

def test_sync_removes_unheld(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "_base_dir", lambda uid: tmp_path)
    st.record(99, "bond", "148070", {"entry_price": 10000})
    st.record(99, "bond", "114260", {"entry_price": 10000})
    st.sync_with_holdings(99, "bond", ["148070"])
    assert set(st.get_all(99, "bond")) == {"148070"}
```

- [ ] **Step 2: 실패 확인.**

- [ ] **Step 3: 구현** — `infra/bond_thesis.py`를 복사해 `sleeve_thesis.py`로 만들고 파일명에
`sleeve_key`를 끼운다: 경로 `data/<uid>/<sleeve_key>_thesis.json`. 공개 함수
`record(uid, sleeve_key, code, thesis)`, `get_all(uid, sleeve_key)`,
`sync_with_holdings(uid, sleeve_key, codes)`. 기존 `bond_thesis.py` 삭제. main_swarm·
specialists의 `bond_thesis` import를 `sleeve_thesis`(+ `"bond"` 인자)로 교체.
`tests/test_bond_thesis_store.py`는 신규 API로 경로만 갱신.

- [ ] **Step 4: 통과 확인** — `python3.11 -m pytest tests/test_sleeve_thesis_store.py tests/test_bond_thesis_store.py -v` → PASS.

## Task 1.4: 페이즈 1 회귀 그린

- [ ] **Step 1:** `python3.11 -m pytest` 전체 실행 → 기존 채권 테스트 포함 전부 PASS(원자재 미사용이라 동작 불변). 실패 시 import 경로/시그니처 불일치만 수정.

---

# 페이즈 2 — config 원자재 상수 + 풀 확장 + 4분할 매크로

> **물리 실행상 이 페이즈의 Task 2.1을 페이즈 1보다 먼저** 한다(asset_sleeves가 config 상수에 의존).

## Task 2.1: config 채권 풀 확장 + 원자재 상수 + 기본 ON

**Files:**
- Modify: `config.py`
- Test: `tests/test_commodity_config.py`(신규), `tests/test_bond_config.py`(확장)

- [ ] **Step 1: 실패 테스트** — `tests/test_commodity_config.py`

```python
import config

def test_commodity_defaults_on():
    assert config.ENABLE_COMMODITY_ETF is True
    assert config.STRATEGY_DEFAULTS["ENABLE_COMMODITY_ETF"] is True

def test_bond_default_on():
    assert config.ENABLE_BOND_ETF is True
    assert config.STRATEGY_DEFAULTS["ENABLE_BOND_ETF"] is True

def test_commodity_pools_have_verified_codes():
    kr = {c for c, *_ in config.COMMODITY_ETF_POOL_KR}
    us = {c for c, *_ in config.COMMODITY_ETF_POOL_US}
    assert {"132030", "261220", "137610"} <= kr   # 금/원유/농산물
    assert {"GLD", "USO", "DBA"} <= us

def test_bond_pool_expanded():
    kr = {c for c, *_ in config.BOND_ETF_POOL_KR}
    assert {"357870", "459580", "273130", "451540", "458250"} <= kr  # CD/회사채/환헤지
    us = {c for c, *_ in config.BOND_ETF_POOL_US}
    assert {"LQD", "HYG", "TIP"} <= us

def test_pool_tags_have_five_fields():
    for c, *rest in config.BOND_ETF_POOL_KR + config.COMMODITY_ETF_POOL_KR:
        assert len(rest) == 4  # (name, duration, kind, fx)

def test_commodity_tunable_keys_registered():
    for k in ("ENABLE_COMMODITY_ETF", "COMMODITY_TARGET_MAX_PCT",
              "COMMODITY_REBALANCE_BAND_PCT", "COMMODITY_PER_CYCLE_RATIO"):
        assert k in config.STRATEGY_TUNABLE_KEYS
        assert k in config.STRATEGY_KEY_META
```

- [ ] **Step 2: 실패 확인.**

- [ ] **Step 3: 구현** — `config.py`:
  - `BOND_ETF_POOL_KR/US`를 5필드 태그 `(code, name, duration, kind, fx)`로 확장(스펙 §6 코드).
  - `COMMODITY_ETF_POOL_KR/US` 신규(스펙 §7 코드).
  - `ENABLE_BOND_ETF = True`(False→True).
  - `ENABLE_COMMODITY_ETF = True`, `COMMODITY_TARGET_MAX_PCT = 0.20`,
    `COMMODITY_REBALANCE_BAND_PCT = 0.03`, `COMMODITY_PER_CYCLE_RATIO = 0.10`.
  - `STRATEGY_TUNABLE_KEYS`에 4개 원자재 키 추가.
  - `STRATEGY_KEY_META`·`STRATEGY_KEY_EFFECT`에 4개 메타(채권 메타 복제·라벨만 원자재로).
  - `STRATEGY_DEFAULTS`의 `ENABLE_BOND_ETF`/`ENABLE_COMMODITY_ETF`=True(루프 보충이 모듈
    상수를 읽으므로 자동 반영되나 명시).

- [ ] **Step 4: 통과 확인** — `python3.11 -m pytest tests/test_commodity_config.py tests/test_bond_config.py -v` → PASS.

## Task 2.2: 4분할 매크로 + 원자재 매크로% 파싱

**Files:**
- Modify: `agents/specialists.py`(macro_analyst 프롬프트)
- Test: `tests/test_macro_four_way_allocation.py`(신규)

- [ ] **Step 1: 실패 테스트**

```python
from infra.asset_sleeves import parse_macro_sleeve_pct

MACRO = ("📊 매크로 환경 요약\n"
         "📈 자산 배분 권고: 주식 45% / 채권 25% / 원자재 20% / 현금 10% "
         "(직전: 주식 50% / 채권 25% / 원자재 15% / 현금 10%)")

def test_four_way_parse():
    assert parse_macro_sleeve_pct(MACRO, "주식") == 0.45
    assert parse_macro_sleeve_pct(MACRO, "채권") == 0.25
    assert parse_macro_sleeve_pct(MACRO, "원자재") == 0.20
```

- [ ] **Step 2: 실패 확인** — 직전 행 정규식이 "원자재"를 못 잡거나 "직전" 값을 잡으면 실패.
  (파서는 "자산 배분 권고" 앵커 우선 → 직전 괄호는 제외. 필요 시 `parse_macro_sleeve_pct`
  앵커 로직을 보강.)

- [ ] **Step 3: 구현** — `agents/specialists.py` `create_macro_analyst` 프롬프트 §5 응답 형식·
  행동규칙을 4분할로 갱신: `주식 X% / 채권 Y% / 원자재 W% / 현금 Z%`(직전 동형), "원자재 비중
  확대/축소" 가이드 문구 추가. 필요 시 asset_sleeves의 앵커 정규식 보강(직전 괄호 배제 확인).

- [ ] **Step 4: 통과 확인** — `python3.11 -m pytest tests/test_macro_four_way_allocation.py -v` → PASS.

---

# 페이즈 3 — 원자재 매니저 + 매도 흐름 재설계

## Task 3.1: commodity_manager 모델/토큰 + 페르소나

**Files:**
- Modify: `config.py`(MODEL_ASSIGNMENTS, AGENT_MAX_TOKENS)
- Modify: `agents/specialists.py`(create_commodity_manager)
- Test: `tests/test_commodity_manager_persona.py`(신규)

- [ ] **Step 1: 실패 테스트**

```python
import config
from agents.specialists import create_commodity_manager

def test_commodity_model_registered():
    assert "commodity_manager" in config.MODEL_ASSIGNMENTS
    assert "commodity_manager" in config.AGENT_MAX_TOKENS

def test_commodity_persona_name_and_keyword():
    a = create_commodity_manager(injection={"uid": 1})
    assert a.name == "원자재운용실장"
    assert "원자재결정" in a.system_prompt  # 마지막 줄 결정표 키워드
```

- [ ] **Step 2: 실패 확인.**

- [ ] **Step 3: 구현** — `config.MODEL_ASSIGNMENTS["commodity_manager"]="Qwen3.6-35B-A3B-Uncensored-Claude-Genesis-Q8_0.gguf"`,
  `AGENT_MAX_TOKENS["commodity_manager"]=2000`. `create_commodity_manager` = 채권 매니저 복제,
  성격="실물자산 매크로 전략가"(인플레·달러·지정학·수급 판단, 주식 퀀트 무관), 결정표 키워드
  `원자재결정: GLD=매수, 132030=보유`, 풀 안내.

- [ ] **Step 4: 통과 확인.**

## Task 3.2: `_cyc_stage_sleeves` 루프화 + 3팀장 리포트 주입

**Files:**
- Modify: `main_swarm.py`(`_cyc_stage_bonds`→`_cyc_stage_sleeves`, 인스턴스화, agent_map, 파이프라인 순서)
- Test: `tests/test_sleeve_sell_synthesis.py`(신규, 일부)

- [ ] **Step 1: 실패 테스트(매니저 입력에 매크로+뉴스 주입)** — 단위 가능한 헬퍼를 분리해 테스트.
  **사장 결정 2026-06-09: 채권·원자재는 주식식 퀀트 부적합 → 계량분석 제외, 매크로+뉴스만.**
  `_build_sleeve_prompt(spec, macro_report, news_report, pool_txt, weight_ctx, thesis_reminder)`
  를 순수 함수로 추출하고:

```python
from main_swarm import _build_sleeve_prompt
from infra.asset_sleeves import BOND_SLEEVE

def test_sleeve_prompt_includes_macro_and_news_not_quant():
    p = _build_sleeve_prompt(BOND_SLEEVE, macro="MACRO_X", news="NEWS_Z",
                             pool_txt="148070 KOSEF", weight_ctx="현재 10%",
                             thesis_reminder="")
    assert "MACRO_X" in p and "NEWS_Z" in p
    assert "밴드" in p or "신호" in p  # "%맞아도 신호로 매도" 지시 존재
```

- [ ] **Step 2: 실패 확인.**

- [ ] **Step 3: 구현** —
  - `main_swarm` 생성자에 `self.commodity_manager = create_commodity_manager(injection=...)`,
    `agent_map`에 `"원자재운용실장": self.commodity_manager` 등록.
  - `_cyc_stage_bonds`를 `_cyc_stage_sleeves`로 교체: `for spec in SLEEVES`(enable 체크), 풀/
    비중/사이징 계산은 asset_sleeves 함수 사용. 매니저 호출 프롬프트를 `_build_sleeve_prompt`
    헬퍼로 추출하고 **macro+news 리포트와 thesis 상기**를 주입(계량분석 제외 — 사장 결정
    2026-06-09). 지시문에 "비중이 밴드 안이어도 신호 악화 시 매도 판단; '%맞으니 보류' 금지" 명시.
  - 산출: `cyc.sleeve_buy_orders`(매수), `cyc.sleeve_sell_proposals`(슬리브별 {code: 결정}),
    `cyc.sleeve_price_map`, `cyc.sleeve_holdings_by_key`. (매도는 아직 집행 안 함 — Task 3.3)
  - 파이프라인에서 `_cyc_stage_sleeves` 호출을 `_cyc_stage_finalize_sell` **앞**으로 이동.

- [ ] **Step 4: 통과 확인** — `python3.11 -m pytest tests/test_sleeve_sell_synthesis.py -v` (해당 테스트만).

## Task 3.3: 사후관리실장 통합 매도결정 + 슬리브 매도 조립

**Files:**
- Modify: `main_swarm.py`(`_cyc_stage_finalize_sell` 입력·프롬프트, `_cyc_stage_build_orders` 매도 조립)
- Test: `tests/test_sleeve_sell_synthesis.py`(확장)

- [ ] **Step 1: 실패 테스트(통합 매도결정 파싱 + 조립)**

```python
from main_swarm import _build_sleeve_sell_orders  # post_manager 결정 → 슬리브 매도 주문

def test_post_manager_sells_sleeve_code():
    # 사후관리실장 매도결정에 채권 코드가 포함되면 슬리브 매도 주문이 생성된다
    decisions = {"005930": "보유", "148070": "전량"}
    holdings = [{"code": "148070", "qty": 7, "cur_price": 50000, "name": "KOSEF10Y"}]
    orders = _build_sleeve_sell_orders(decisions, holdings, price_lookup=lambda c: 50000)
    assert any(o["ticker"] == "148070" and o["side"] == "sell" and o["qty"] == 7 for o in orders)

def test_band_ok_but_signal_sell_not_blocked():
    # 비중 적정(skip)이어도 사후관리 매도결정이 있으면 매도 주문이 살아난다
    decisions = {"148070": "절반"}
    holdings = [{"code": "148070", "qty": 8, "cur_price": 50000, "name": "x"}]
    orders = _build_sleeve_sell_orders(decisions, holdings, price_lookup=lambda c: 50000)
    assert any(o["qty"] == 4 for o in orders)
```

- [ ] **Step 2: 실패 확인.**

- [ ] **Step 3: 구현** —
  - `_cyc_stage_finalize_sell`: 사후관리실장 입력에 **슬리브 보유 + 슬리브 매니저 매도 제안
    요약**을 추가(현재 채권 제외 로직은 유지하되, 제외한 슬리브 보유를 별도 컨텍스트로 주입).
    프롬프트의 `매도결정` 설명을 "주식 + 채권/원자재 코드 모두 포함; 비중%만으로 보류 금지,
    신호로 종합 판단"으로 확장.
  - `_build_sleeve_sell_orders(decisions, sleeve_holdings, price_lookup)` 순수 함수 추출:
    사후관리실장 `매도결정` 중 슬리브 풀 코드만 골라 전량/절반/주수 → 매도 주문 조립
    (assemble_sleeve_orders의 매도 분기 재사용).
  - `_cyc_stage_build_orders`: `cyc.sleeve_buy_orders`(매수) + `_build_sleeve_sell_orders(...)`
    (매도)를 order_obj에 합류, `price_map.update(cyc.sleeve_price_map)`.

- [ ] **Step 4: 통과 확인** — `python3.11 -m pytest tests/test_sleeve_sell_synthesis.py -v` → PASS.

## Task 3.4: guardrails 슬리브 면제 일반화

**Files:**
- Modify: `agents/guardrails.py`
- Test: `tests/test_bond_concentration_gate.py`(확장 또는 신규 `test_sleeve_guardrails.py`)

- [ ] **Step 1: 실패 테스트** — 원자재 코드도 집중도·예산캡 면제 받는지.

```python
from agents.guardrails import validate_order_draft  # 실제 시그니처에 맞춰 호출

def test_commodity_concentration_uses_commodity_max():
    # GLD 매수가 COMMODITY_TARGET_MAX_PCT(0.20)까지 허용되는지(주식 0.15 아님)
    ...  # 기존 test_bond_concentration_gate 패턴을 GLD/132030으로 복제
```

- [ ] **Step 2: 실패 확인.**

- [ ] **Step 3: 구현** — guardrails의 `_bond_pool`/`_is_bond_etf`를 `all_sleeve_pool_codes()`와
  슬리브별 `target_max_key` 조회로 교체. 집중도 상한·수량캡 면제·`MAX_CYCLE_BUDGET_RATIO`
  면제를 전 슬리브 코드로 확장. 슬리브별 상한은 해당 코드가 속한 spec의 `target_max_key`.

- [ ] **Step 4: 통과 확인** — `python3.11 -m pytest tests/test_bond_concentration_gate.py tests/test_sleeve_guardrails.py -v` → PASS.

## Task 3.5: 페이즈 3 회귀 그린

- [ ] **Step 1:** `python3.11 -m pytest` 전체 → PASS.

---

# 페이즈 4 — 보유계획 일괄 상기 (fund_planner → 3매니저)

## Task 4.1: 통합 thesis reminder 포맷터

**Files:**
- Modify: `agents/specialists.py`(`format_thesis_reminder`·`format_bond_thesis_reminder` → `format_sleeve_thesis_reminder`)
- Test: `tests/test_thesis_reminder_broadcast.py`(신규), `tests/test_bond_thesis_reminder.py`(경로 교체)

- [ ] **Step 1: 실패 테스트**

```python
from agents.specialists import format_sleeve_thesis_reminder

def test_stock_reminder_has_target_stop():
    theses = {"005930": {"entry_price": 70000, "target_price": 80000, "stop_price": 65000,
                          "planned_hold_hours": 100, "entry_ts": "2026-06-01 10:00:00"}}
    holds = [{"code": "005930", "name": "삼성", "cur_price": 75000}]
    txt = format_sleeve_thesis_reminder(theses, holds, "2026-06-02 10:00:00",
                                        manager_name="사후관리실장", kind="stock")
    assert "목표" in txt and "손절" in txt

def test_sleeve_reminder_hold_hours_only():
    theses = {"148070": {"entry_price": 50000, "planned_hold_hours": 168,
                         "entry_ts": "2026-06-01 10:00:00"}}
    holds = [{"code": "148070", "name": "KOSEF10Y", "cur_price": 50500}]
    txt = format_sleeve_thesis_reminder(theses, holds, "2026-06-02 10:00:00",
                                        manager_name="채권운용실장", kind="sleeve")
    assert "채권운용실장" in txt and "계획 보유" in txt
```

- [ ] **Step 2: 실패 확인.**

- [ ] **Step 3: 구현** — 기존 두 포맷터를 `format_sleeve_thesis_reminder(theses, holdings, now_iso,
  *, manager_name, kind)`로 통합. `kind="stock"`=목표/손절 포함(기존 format_thesis_reminder
  본문), `kind="sleeve"`=보유기간 버전(기존 format_bond_thesis_reminder 본문). 첫 줄 머리말의
  화자명을 `manager_name`으로. 기존 호출부·테스트 import 교체.

- [ ] **Step 4: 통과 확인.**

## Task 4.2: fund_planner 3매니저 일괄 주입 + 단일 발화

**Files:**
- Modify: `main_swarm.py`(매도 스테이지 직전 reminder 주입 지점)
- Test: `tests/test_thesis_reminder_broadcast.py`(확장)

- [ ] **Step 1: 실패 테스트** — 매도 스테이지에서 3매니저 프롬프트에 각 thesis가 들어가고,
  대시보드에 포트폴리오기획팀장 발화 1건이 emit되는지(헬퍼 `_collect_thesis_reminders(cyc)`를
  추출해 단위 테스트).

```python
from main_swarm import _collect_thesis_reminders

def test_collect_reminders_three_managers(fake_cyc):
    rem = _collect_thesis_reminders(fake_cyc)   # {"사후관리실장":..,"채권운용실장":..,"원자재운용실장":..}
    assert set(rem) <= {"사후관리실장", "채권운용실장", "원자재운용실장"}
```

- [ ] **Step 2: 실패 확인.**

- [ ] **Step 3: 구현** — `_collect_thesis_reminders(cyc)`가 주식(position_thesis)·채권·원자재
  (sleeve_thesis)별 reminder 텍스트를 모아 매니저명→텍스트 dict 반환. `_cyc_stage_sleeves`는
  각 매니저 프롬프트에 해당 reminder를, `_cyc_stage_finalize_sell`은 사후관리실장 reminder를
  주입. 매도 스테이지 진입 시 포트폴리오기획팀장 "보유계획 상기" 발화 1건 emit
  (`{"type":"agent_msg","agent":"포트폴리오기획팀장","message": 요약}`).

- [ ] **Step 4: 통과 + 회귀** — `python3.11 -m pytest tests/test_thesis_reminder_broadcast.py tests/test_bond_thesis_reminder.py tests/test_fund_planner_persona.py -v` → PASS.

---

# 페이즈 5 — 페르소나 rename: 운용전략실장 → 주식운용실장

## Task 5.1: 라우팅 키 일괄 치환

**Files:**
- Modify: `main_swarm.py`, `agents/specialists.py`, `server/app.py`,
  `server/static/index.html`, `infra/standing_directives.py`, `infra/error_log.py`,
  `tools/market_data.py`, `tools/gen_manual.js`,
  `arquant_mobile/app/src/main/java/com/arquant/mobile/viewmodel/DashViewModel.kt`
- Test: `tests/test_persona_rename_stock_manager.py`(신규) + 기존 테스트 9종 갱신

- [ ] **Step 1: 실패 테스트**

```python
def test_orchestrator_named_stock_manager():
    import main_swarm
    # agent_map/생성 시 이름이 주식운용실장
    # (Swarm 인스턴스 생성이 무거우면 소스 문자열 존재로 핀)
    src = open("main_swarm.py", encoding="utf-8").read()
    assert "주식운용실장" in src and "운용전략실장" not in src

def test_app_roster_uses_stock_manager():
    src = open("server/app.py", encoding="utf-8").read()
    assert "운용전략실장" not in src
```

- [ ] **Step 2: 실패 확인.**

- [ ] **Step 3: 구현** — 각 파일에서 `운용전략실장` → `주식운용실장` 일괄 치환(라우팅 키·
  emit agent·agent_map 키·@멘션 placeholder·standing_directive 주입·error_log component·
  주석·매뉴얼 예시). **내부 role 키 `chief_orchestrator`는 불변**. DashViewModel.kt는
  `"운용전략실장"`→`"주식운용실장"` + 이참에 stale 이름(전략리서치팀장/뉴스분석팀장/
  트레이딩팀장)도 웹 기준(글로벌리서치팀장/마켓센티먼트팀장/프롭트레이딩팀장)으로 교정하고
  채권운용실장/원자재운용실장/포트폴리오기획팀장 색상 추가. **APK 재빌드는 보류.**

- [ ] **Step 4: 통과 + 회귀** — 기존 테스트(`test_ceo_directive_routing`, `test_ceo_directive_persist`,
  `test_standing_directives`, `test_per_uid_event_log`, `test_quant_prompt_format`,
  `test_candidate_code_resolve`, `test_order_disposition`, `test_order_maxqty_clamp`,
  `test_cheap_fallback_guard`, `test_macro_buy_gate` 등)의 `운용전략실장` 문자열을
  `주식운용실장`으로 갱신. `python3.11 -m pytest` 전체 → PASS.

---

# 페이즈 6 — ops cycle/weekly tier 강제 구분

## Task 6.1: STRATEGY_KEY_META tier 필드 + 카탈로그 표기

**Files:**
- Modify: `config.py`(STRATEGY_KEY_META에 tier, strategy_param_catalog_text에 표기)
- Test: `tests/test_ops_tier_partition.py`(일부), `tests/test_ops_param_catalog.py`(확장)

- [ ] **Step 1: 실패 테스트**

```python
import config

def test_every_tunable_key_has_tier():
    for k in config.STRATEGY_TUNABLE_KEYS:
        m = config.STRATEGY_KEY_META.get(k)
        assert m is not None and m.get("tier") in ("cycle", "weekly"), k

def test_scoring_weights_are_weekly():
    for k in ("QIW_RSI", "DW_QUANT", "POSITION_SIZING_MODE", "MAX_BUY_NAMES",
              "BOND_TARGET_MAX_PCT", "COMMODITY_TARGET_MAX_PCT"):
        assert config.STRATEGY_KEY_META[k]["tier"] == "weekly"

def test_tactical_are_cycle():
    for k in ("TAKE_PROFIT_PCT", "STOP_LOSS_PCT", "PER_ORDER_BUDGET_RATIO",
              "MIN_QUANT_SCORE", "CONSERVATIVE_MDD"):
        assert config.STRATEGY_KEY_META[k]["tier"] == "cycle"

def test_catalog_shows_tier():
    txt = config.strategy_param_catalog_text()
    assert "토요일" in txt or "주간" in txt  # weekly 표기
```

- [ ] **Step 2: 실패 확인.**

- [ ] **Step 3: 구현** — `STRATEGY_KEY_META`의 각 키에 `"tier"` 추가(스펙 §9.1 분류). 누락 방지
  위해 메타 작성 후 `for k in STRATEGY_TUNABLE_KEYS: assert "tier" in META[k]`를 모듈 로드시
  강제(또는 setdefault "cycle" + 명시). `strategy_param_catalog_text()`에 tier 표기
  `[사이클 조정 가능]`/`[토요일 검증 후]` 삽입.

- [ ] **Step 4: 통과 확인.**

## Task 6.2: partition_by_tier + weekly_defer_queue

**Files:**
- Create: `infra/weekly_defer_queue.py`
- Modify: `infra/ops_param_clamp.py`(partition_by_tier), `infra/ops_support_worker.py`(_handle_param_tuning)
- Test: `tests/test_ops_tier_partition.py`(확장)

- [ ] **Step 1: 실패 테스트**

```python
from infra.ops_param_clamp import partition_by_tier

def test_cycle_defers_weekly_tier():
    ov = {"TAKE_PROFIT_PCT": 8.0, "QIW_RSI": 3, "POSITION_SIZING_MODE": "equal"}
    apply, defer, notes = partition_by_tier(ov, trigger="cycle")
    assert apply == {"TAKE_PROFIT_PCT": 8.0}
    assert set(defer) == {"QIW_RSI", "POSITION_SIZING_MODE"}

def test_weekly_applies_all():
    ov = {"TAKE_PROFIT_PCT": 8.0, "QIW_RSI": 3}
    apply, defer, notes = partition_by_tier(ov, trigger="weekly")
    assert defer == {} and set(apply) == {"TAKE_PROFIT_PCT", "QIW_RSI"}

def test_manual_applies_all():
    apply, defer, notes = partition_by_tier({"QIW_RSI": 3}, trigger="manual")
    assert apply == {"QIW_RSI": 3} and defer == {}
```

- [ ] **Step 2: 실패 확인.**

- [ ] **Step 3: 구현** —
  - `infra/weekly_defer_queue.py`: `enqueue(uid, key, value, rationale)`, `list(uid)`,
    `clear(uid)` → `data/profiles/<uid>/weekly_deferred.json`.
  - `partition_by_tier(overrides, trigger)`: cycle→weekly-tier는 defer, weekly/manual→전부 apply.
    tier는 `config.STRATEGY_KEY_META[k].get("tier","cycle")`.
  - `_handle_param_tuning`: `partition_protected` **다음에** `partition_by_tier` 적용. cycle
    트리거의 defer 항목은 `weekly_defer_queue.enqueue`로 적재 + rationale에 회부 메모.

- [ ] **Step 4: 통과 확인.**

## Task 6.3: 토요일 워커가 defer 항목 재평가

**Files:**
- Modify: `infra/weekly_review.py`(directive에 deferred 목록 주입)
- Test: `tests/test_weekly_defer_replay.py`(신규)

- [ ] **Step 1: 실패 테스트** — `build_review_summary`(또는 directive 빌더)가 `weekly_deferred`
  목록을 포함하는지.

```python
def test_weekly_directive_includes_deferred(monkeypatch, tmp_path):
    import infra.weekly_defer_queue as q
    monkeypatch.setattr(q, "_path", lambda uid: tmp_path / "wd.json")
    q.enqueue(7, "QIW_RSI", 3, "추세 강화")
    # weekly_review가 summary/directive에 deferred를 실음
    ...
```

- [ ] **Step 2: 실패 확인.**

- [ ] **Step 3: 구현** — `weekly_review`가 주간 directive에 "지난 주 보류된 구조 파라미터 제안"
  (`weekly_defer_queue.list(uid)`)을 백테스트 결과와 함께 주입. 토요일 워커가 weekly 트리거라
  partition_by_tier가 전부 apply 하므로, 워커가 정당하다 판단하면 실제 반영. 적용 후
  `weekly_defer_queue.clear(uid)`.

- [ ] **Step 4: 통과 + 회귀** — `python3.11 -m pytest tests/test_ops_*.py tests/test_weekly_*.py -v` → PASS.

---

# 페이즈 7 — UI + 최종 검증 + 배포

## Task 7.1: 대시보드 사이드바 + roster 원자재·rename 반영

**Files:**
- Modify: `server/static/index.html`(agentGroups), `server/app.py`(roster, admin label)
- Test: 수동(서버 기동 후 사이드바 확인) + `tests/test_app_roster.py`(있으면 확장, 없으면 문자열 핀)

- [ ] **Step 1: 실패 테스트(문자열 핀)**

```python
def test_sidebar_has_commodity_and_stock_manager():
    html = open("server/static/index.html", encoding="utf-8").read()
    assert "주식운용실장" in html and "원자재운용실장" in html
    assert "운용전략실장" not in html

def test_app_roster_has_commodity():
    src = open("server/app.py", encoding="utf-8").read()
    assert "원자재운용실장" in src and "commodity_manager" in src
```

- [ ] **Step 2: 실패 확인.**

- [ ] **Step 3: 구현** — index.html `agentGroups` FRONT에 원자재운용실장(`color:"#d97706"`,level:0)
  추가 + 운용전략실장→주식운용실장. `server/app.py` roster에 원자재운용실장 항목 + admin
  label에 `commodity_manager` 표기 + chief_orchestrator label 텍스트 갱신.

- [ ] **Step 4: 통과 확인.**

## Task 7.2: 전체 회귀 + 수동 검증

- [ ] **Step 1:** `python3.11 -m pytest` 전체 → 전부 PASS. 실패는 모두 해결.
- [ ] **Step 2:** 로컬 import 스모크 — `python3.11 -c "import main_swarm, server.app"`(import 에러 0).
- [ ] **Step 3:** 사이드바·roster 육안 확인(필요 시 `./start_server.sh` 또는 webapp-testing).

## Task 7.3: 배포 (사장 확인 후)

- [ ] **Step 1:** 변경 요약 보고 + 사장 배포 승인 요청(위험·되돌리기 어려운 작업).
- [ ] **Step 2:** 승인 시 `sudo systemctl restart arquant.service` → `systemctl status`(port 8500).
- [ ] **Step 3:** 재시작 시 루프 OFF(.running 부재) → 대시보드 '시작' 안내. 채권·원자재 기본 ON
  이므로 장중 슬리브 분석/매매 라이브 관측.

---

## 실행 메모
- **물리 실행 순서**: Task 2.1(config 상수) → 페이즈 1(슬리브 엔진) → 2.2 → 페이즈 3~7.
  asset_sleeves가 config 원자재 상수에 의존하기 때문.
- **안전망**: 기존 채권 테스트 9종이 페이즈 1 리팩토링의 회귀를 잡는다.
- **KR/US 비대칭 주의**(CLAUDE.md): 슬리브 매수/매도/세션풀 전 경로에서 KR(6자리)·US(티커)
  양쪽을 동등 처리. KRW 한도와 USD 평가 혼동 금지(current_sleeve_weight의 usdkrw 환산).
- **주문 절대 스킵 금지**: 슬리브 매도 주문도 다중 폴백 전송 경로(기존 execute) 사용.
