# 제도권 투자 파이프라인 4단계 이식 — 구현계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ArQuant에 제도권 운용 파이프라인의 4요소(퀀트가 선정에 영향·리스크기반 사이징·유니버스 결정론 스크리닝·에이전트 성과귀인)를 라이브 안전하게 이식한다.

**Architecture:** 신규 동작은 전부 튜너블 노브 뒤에 두고 디폴트를 기존 동작에 가깝게 잡는다(회귀 0). 계산 로직은 순수 함수 모듈(`tools/position_sizing.py`·`tools/universe_screen.py`·`tools/agent_scorecard.py`)로 분리해 TDD. 성과귀인 예측은 신규 `infra/scorecard_store.py`(`data/scorecard.db`)에 전향적으로 구조화 적재 후, 체결결과(`trade_log.json`)·자산곡선과 조인. KR/US 양 경로는 공통 헬퍼로 패리티 유지.

**Tech Stack:** Python 3.11 (테스트는 반드시 `python3.11 -m pytest`), sqlite3, FastAPI(server/app.py), runtime override 시스템(`runtime.get(key, uid)`), 기존 결정론 점수 엔진(`tools/quant_score.py`).

**규칙(사장/CLAUDE.md):**
- 테스트는 `python3.11`. 기본 `python`은 argon2 import에서 죽는다.
- **git 커밋 직접 안 함** — 외부 자동 Backup 도구가 `git add -A` + `Backup:` 커밋/푸시. 각 Task 끝의 체크포인트는 "전체 테스트 통과 확인"으로 갈음.
- 주문 절대 스킵 금지: 신규 필터는 *후보(아이디어 풀)*만 거른다. 무음 컷 금지(거른 내역 로그).
- 4개 전부 구현·테스트 통과 후 **1회 재시작** 배포. 재시작 전 `.running` 마커 선생성.

spec: `docs/superpowers/specs/2026-06-04-institutional-pipeline-mimicry-design.md`

---

## File Structure

**Create:**
- `tools/position_sizing.py` — `compute_sizing_weights()` 순수 함수(리스크기반 비중).
- `tools/universe_screen.py` — `screen_universe()` 순수 함수(레버리지/저가/거래대금 배제).
- `tools/agent_scorecard.py` — `information_coefficient()`·`slippage_stats()`·`alpha_beta()`·`compute_scorecard()` 순수.
- `infra/scorecard_store.py` — `data/scorecard.db`의 `agent_signals` 테이블 적재/조회.
- `tests/test_position_sizing.py`, `tests/test_universe_screen.py`, `tests/test_agent_scorecard.py`, `tests/test_scorecard_store.py`, `tests/test_filter_targets_rank.py`, `tests/test_institutional_params_config.py`, `tests/test_scorecard_endpoint.py`.

**Modify:**
- `config.py` — 신규 노브 8개 + META/EFFECT/5개 프리셋/TUNABLE_KEYS.
- `main_swarm.py` — ① `format_scoring_rubric_block()` 신규 + `filter_targets_by_score()` 랭크/캡 확장 + select 프롬프트 주입; ② `_build_orders` 사이징 배선; ③ 후보 스크리닝 배선; ④ data_quant 신호 캡처 + report 요약.
- `infra/ops_support_worker.py` — 플레이북에 신규 노브 예시 + 스코어카드 요약 주입.
- `server/app.py` — `GET /api/scorecard` 엔드포인트.

---

## Phase A — 신규 전략 파라미터 등록

### Task A1: config.py 신규 노브 8개 등록

**Files:**
- Modify: `config.py:165-194` (상수), `config.py:224-239` (TUNABLE_KEYS), `config.py:355` (META 끝), `config.py:399` (EFFECT 끝), `config.py:401-452` (프리셋 5개)
- Test: `tests/test_institutional_params_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_institutional_params_config.py
"""사장 지시 2026-06-04: 제도권 파이프라인 4기능 — 신규 튜너블 노브 등록 검증.
spec: docs/superpowers/specs/2026-06-04-institutional-pipeline-mimicry-design.md"""
import config

NEW_KEYS = [
    "MAX_BUY_NAMES", "POSITION_SIZING_MODE", "SIZING_TILT_STRENGTH", "SIZING_MAX_TILT",
    "UNIVERSE_MIN_PRICE", "UNIVERSE_MIN_TURNOVER", "UNIVERSE_EXCLUDE_LEVERAGED",
    "SCORECARD_WINDOW_DAYS",
]

def test_constants_exist():
    for k in NEW_KEYS:
        assert hasattr(config, k), f"config.{k} 상수 누락"

def test_registered_in_tunable_keys():
    for k in NEW_KEYS:
        assert k in config.STRATEGY_TUNABLE_KEYS, f"{k} TUNABLE_KEYS 누락"

def test_meta_and_effect_present():
    for k in NEW_KEYS:
        assert k in config.STRATEGY_KEY_META, f"{k} META 누락"
        assert config.STRATEGY_KEY_META[k].get("label"), f"{k} label 누락"
        assert k in config.STRATEGY_KEY_EFFECT, f"{k} EFFECT 누락"

def test_all_presets_define_new_keys():
    for name, preset in config.STRATEGY_PRESETS.items():
        for k in NEW_KEYS:
            assert k in preset, f"프리셋 {name}에 {k} 누락"

def test_defaults_are_backward_safe():
    # 디폴트는 기존 동작에 가깝게: 사이징 약한 기울임, 유니버스 레버리지만 배제(가격/거래대금 0=off)
    assert config.POSITION_SIZING_MODE in ("equal", "risk_weighted")
    assert 0.0 <= config.SIZING_TILT_STRENGTH <= 1.0
    assert config.SIZING_MAX_TILT >= 1.0
    assert config.MAX_BUY_NAMES >= 1

def test_catalog_text_includes_new_keys():
    txt = config.strategy_param_catalog_text()
    assert "POSITION_SIZING_MODE" in txt and "UNIVERSE_EXCLUDE_LEVERAGED" in txt
    assert "효과:" in txt  # EFFECT 주입 확인
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_institutional_params_config.py -v`
Expected: FAIL — `config.MAX_BUY_NAMES` AttributeError.

- [ ] **Step 3: Add constants** — insert after `config.py:194` (`DETERMINISTIC_SCORING = True` 줄 뒤, `def strategy_param_catalog_text` 앞)

```python

# ─── 제도권 파이프라인 4기능 (사장 지시 2026-06-04) ───────────────────────────
# spec: docs/superpowers/specs/2026-06-04-institutional-pipeline-mimicry-design.md
# ① 퀀트 점수가 선정에 영향 — 한 사이클 최대 매수 종목 수(랭크 상위부터 자금배정)
MAX_BUY_NAMES          = 8            # 통과 후보가 이보다 많으면 퀀트점수 상위 N개만 매수(큰 값=무변화)
# ② 리스크기반 포지션 사이징
POSITION_SIZING_MODE   = "risk_weighted"  # 'equal'=균등(기존) / 'risk_weighted'=점수·역변동성 가중
SIZING_TILT_STRENGTH   = 0.5          # 0=완전균등 … 1=완전기울임 (균등분배와 raw가중 사이 보간)
SIZING_MAX_TILT        = 2.0          # 균등 대비 한 종목 최대/최소 배수(과집중 방지)
# ③ 유니버스 스크리닝 결정론화 (후보 풀 사전 배제 — 무음 금지, 로그 남김)
UNIVERSE_MIN_PRICE     = 0.0          # 현재가 < 이 값(원, US는 USD 별도 임계 미적용) 후보 배제 (0=off)
UNIVERSE_MIN_TURNOVER  = 0.0          # 일거래대금 < 이 값 후보 배제 (0=off)
UNIVERSE_EXCLUDE_LEVERAGED = True     # 레버리지/인버스/곱버스/ETN 후보 배제
# ④ 성과귀인 스코어카드 — 귀인 트레일링 윈도우(일)
SCORECARD_WINDOW_DAYS  = 30
```

- [ ] **Step 4: Register in STRATEGY_TUNABLE_KEYS** — modify `config.py:234-238`, add a new block before the `# (C) 레짐 대응` line (line 234):

Replace the `# (C) 레짐 대응` section header block start by inserting these lines right after line 233 (`"DW_QUANT", "DW_NEWS", "DW_MACRO", "DETERMINISTIC_SCORING",`):

```python
    # 제도권 파이프라인 4기능 (사장 지시 2026-06-04)
    "MAX_BUY_NAMES", "POSITION_SIZING_MODE", "SIZING_TILT_STRENGTH", "SIZING_MAX_TILT",
    "UNIVERSE_MIN_PRICE", "UNIVERSE_MIN_TURNOVER", "UNIVERSE_EXCLUDE_LEVERAGED",
    "SCORECARD_WINDOW_DAYS",
```

- [ ] **Step 5: Add META entries** — insert into `STRATEGY_KEY_META` dict before its closing brace (`config.py:355`, the `}` after `ALLOW_DERIVATIVES`):

```python
    # 제도권 파이프라인 4기능 (사장 지시 2026-06-04)
    "MAX_BUY_NAMES":              {"label": "사이클당 최대 매수 종목 수", "type": "int", "unit": "개",
                                   "help": "퀀트점수 통과 후보가 이보다 많으면 점수 상위 N개만 매수(랭크 우선 자금배정)",
                                   "min": 1, "max": 20, "step": 1, "group": "종목 필터"},
    "POSITION_SIZING_MODE":       {"label": "포지션 사이징 방식", "type": "choice", "choices": ["equal", "risk_weighted"],
                                   "help": "equal=종목 균등분배(기존). risk_weighted=퀀트점수↑·변동성↓ 종목에 더 큰 비중",
                                   "group": "사이징"},
    "SIZING_TILT_STRENGTH":       {"label": "사이징 기울임 강도", "type": "pct_raw", "unit": "",
                                   "help": "0=완전 균등, 1=완전 기울임. risk_weighted일 때만 효과(0.5=절반 적용)",
                                   "min": 0.0, "max": 1.0, "step": 0.1, "group": "사이징"},
    "SIZING_MAX_TILT":            {"label": "사이징 최대 배수(과집중 방지)", "type": "multiplier", "unit": "×",
                                   "help": "한 종목이 균등분배 대비 받을 수 있는 최대/최소 배수(2.0=최대2배·최소0.5배)",
                                   "min": 1.0, "max": 4.0, "step": 0.5, "group": "사이징"},
    "UNIVERSE_MIN_PRICE":         {"label": "유니버스 최소 주가 (저가주 배제)", "type": "pct_raw", "unit": "원",
                                   "help": "현재가가 이 값 미만인 후보를 사전 배제(동전주 회피). 0=제한없음",
                                   "min": 0, "max": 10000, "step": 100, "group": "종목 필터"},
    "UNIVERSE_MIN_TURNOVER":      {"label": "유니버스 최소 일거래대금", "type": "pct_raw", "unit": "원",
                                   "help": "일거래대금이 이 값 미만인 후보를 사전 배제(유동성 확보). 0=제한없음",
                                   "min": 0, "max": 100000000000, "step": 100000000, "group": "종목 필터"},
    "UNIVERSE_EXCLUDE_LEVERAGED": {"label": "레버리지/인버스 ETF·ETN 배제", "type": "bool",
                                   "help": "ON이면 레버리지·인버스·곱버스·ETN 후보를 사전 배제",
                                   "group": "종목 필터"},
    "SCORECARD_WINDOW_DAYS":      {"label": "성과귀인 분석 기간(일)", "type": "int", "unit": "일",
                                   "help": "에이전트 스코어카드(IC·알파/베타)를 계산할 트레일링 윈도우",
                                   "min": 7, "max": 180, "step": 1, "group": "기타"},
```

- [ ] **Step 6: Add EFFECT entries** — insert into `STRATEGY_KEY_EFFECT` dict before its closing brace (`config.py:399`):

```python
    "MAX_BUY_NAMES": "내리면 고확신 소수 종목에 집중(분산↓), 올리면 폭넓게 분산 매수.",
    "POSITION_SIZING_MODE": "risk_weighted=점수높고 변동성낮은 종목에 큰 비중(샤프 개선). equal=균등(기존).",
    "SIZING_TILT_STRENGTH": "올리면 점수·변동성 차이를 비중에 강하게 반영(공격), 0이면 균등.",
    "SIZING_MAX_TILT": "올리면 한 종목 비중 편차 허용(집중), 내리면 균등에 가깝게(분산·방어).",
    "UNIVERSE_MIN_PRICE": "올리면 저가·동전주 배제(품질 방어), 0=제한없음.",
    "UNIVERSE_MIN_TURNOVER": "올리면 거래 활발한 종목만(유동성·급락장 방어), 0=제한없음.",
    "UNIVERSE_EXCLUDE_LEVERAGED": "켜면 레버리지/인버스/ETN 배제(변동성 방어), 끄면 허용.",
    "SCORECARD_WINDOW_DAYS": "올리면 더 긴 기간으로 에이전트 성과 평가(안정), 내리면 최근 위주(민감).",
```

- [ ] **Step 7: Add keys to all 5 presets** — modify each preset dict (`config.py:402-451`). Add to each preset, on a new line right before each preset's closing `}`:

defensive (before `config.py:411` closing — add after the `"ALLOW_DAY_TRADING": False, "MIN_HOLDING_DAYS_FOR_SELL": 1.0, "THESIS_VETO_ENABLED": True,` line):
```python
        "MAX_BUY_NAMES": 3, "POSITION_SIZING_MODE": "risk_weighted", "SIZING_TILT_STRENGTH": 0.6, "SIZING_MAX_TILT": 2.0,
        "UNIVERSE_MIN_PRICE": 2000, "UNIVERSE_MIN_TURNOVER": 1000000000, "UNIVERSE_EXCLUDE_LEVERAGED": True, "SCORECARD_WINDOW_DAYS": 30,
```
conservative:
```python
        "MAX_BUY_NAMES": 4, "POSITION_SIZING_MODE": "risk_weighted", "SIZING_TILT_STRENGTH": 0.5, "SIZING_MAX_TILT": 2.0,
        "UNIVERSE_MIN_PRICE": 1000, "UNIVERSE_MIN_TURNOVER": 500000000, "UNIVERSE_EXCLUDE_LEVERAGED": True, "SCORECARD_WINDOW_DAYS": 30,
```
balanced:
```python
        "MAX_BUY_NAMES": 8, "POSITION_SIZING_MODE": "risk_weighted", "SIZING_TILT_STRENGTH": 0.5, "SIZING_MAX_TILT": 2.0,
        "UNIVERSE_MIN_PRICE": 0, "UNIVERSE_MIN_TURNOVER": 0, "UNIVERSE_EXCLUDE_LEVERAGED": True, "SCORECARD_WINDOW_DAYS": 30,
```
aggressive:
```python
        "MAX_BUY_NAMES": 10, "POSITION_SIZING_MODE": "risk_weighted", "SIZING_TILT_STRENGTH": 0.7, "SIZING_MAX_TILT": 3.0,
        "UNIVERSE_MIN_PRICE": 0, "UNIVERSE_MIN_TURNOVER": 0, "UNIVERSE_EXCLUDE_LEVERAGED": False, "SCORECARD_WINDOW_DAYS": 30,
```
ultra_aggressive:
```python
        "MAX_BUY_NAMES": 12, "POSITION_SIZING_MODE": "risk_weighted", "SIZING_TILT_STRENGTH": 1.0, "SIZING_MAX_TILT": 4.0,
        "UNIVERSE_MIN_PRICE": 0, "UNIVERSE_MIN_TURNOVER": 0, "UNIVERSE_EXCLUDE_LEVERAGED": False, "SCORECARD_WINDOW_DAYS": 14,
```

- [ ] **Step 8: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_institutional_params_config.py -v`
Expected: PASS (6 tests).

- [ ] **Step 9: Checkpoint — full suite green**

Run: `python3.11 -m pytest -q`
Expected: all pass (no regression from new keys). 자동 Backup이 커밋 담당 — 직접 커밋 금지.

---

## Phase B — ① 퀀트 점수가 *선정*에 영향 (랭크-인지)

### Task B1: `format_scoring_rubric_block()` 순수 헬퍼 + filter_targets_by_score 랭크/캡 확장

**Files:**
- Modify: `main_swarm.py:1092-1108` (`filter_targets_by_score`), 그 함수 위에 `format_scoring_rubric_block` 신규.
- Test: `tests/test_filter_targets_rank.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_filter_targets_rank.py
"""사장 지시 2026-06-04: ① 퀀트 점수가 선정에 영향 — 미달 제거 + 점수 내림차순 + 최대 종목수 캡.
점수 없는 종목 보존(주문 스킵 금지). 루브릭 블록은 PASS1 선정에 채점 기준 주입."""
from main_swarm import filter_targets_by_score, format_scoring_rubric_block


def test_drops_below_min_and_sorts_desc():
    kept, dropped = filter_targets_by_score(["A", "B", "C"], {"A": 7, "B": 9, "C": 4}, 6, max_names=8)
    assert kept == ["B", "A"]          # 9 > 7, C(4) 제거
    assert dropped == ["C"]


def test_caps_to_max_names_keeping_top():
    kept, dropped = filter_targets_by_score(
        ["A", "B", "C", "D"], {"A": 6, "B": 9, "C": 7, "D": 8}, 6, max_names=2)
    assert kept == ["B", "D"]          # 상위 2개(9,8)
    assert set(dropped) == {"A", "C"}  # 캡 초과분도 dropped 에 보고


def test_missing_score_preserved_first():
    # 점수 없는 종목은 평가불가 → 보존(드롭 금지). 정렬에서 맨 앞(우선 자금배정 안전).
    kept, dropped = filter_targets_by_score(["A", "X"], {"A": 7}, 6, max_names=8)
    assert "X" in kept and "A" in kept and dropped == []


def test_max_names_zero_means_no_cap():
    kept, _ = filter_targets_by_score(["A", "B", "C"], {"A": 7, "B": 8, "C": 9}, 6, max_names=0)
    assert set(kept) == {"A", "B", "C"}


def test_backward_compatible_default_args():
    # max_names 미지정 시 기존 동작(캡 없음, 미달만 제거) — 정렬은 적용.
    kept, dropped = filter_targets_by_score(["A", "B"], {"A": 5, "B": 7}, 6)
    assert kept == ["B"] and dropped == ["A"]


def test_rubric_block_describes_weights_and_gate():
    block = format_scoring_rubric_block(
        {"rsi": 5, "macd": 10, "mom": 12}, {"QUANT": 60, "NEWS": 25, "MACRO": 15}, 6)
    assert "퀀트점수" in block
    assert "6" in block                # MIN_QUANT_SCORE
    assert "모멘텀" in block or "MOM" in block  # 최상위 가중 지표 언급
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_filter_targets_rank.py -v`
Expected: FAIL — `format_scoring_rubric_block` ImportError / `filter_targets_by_score()` got unexpected kwarg `max_names`.

- [ ] **Step 3: Replace `filter_targets_by_score` (main_swarm.py:1092-1108) with rank-aware version**

```python
def filter_targets_by_score(target_codes, quant_scores: Dict[str, int], min_score: int, max_names: int = 0):
    """MIN_QUANT_SCORE 결정론 게이트 + 랭크-인지 선정(사장 지시 2026-06-04 ①).
    1) 점수<min_score 제거 (점수 매핑에 '없는' 종목은 평가불가 → 보존, 주문 스킵 금지).
    2) 통과분을 퀀트점수 내림차순 정렬(점수 없는 보존 종목은 맨 앞 — 우선 자금배정 안전).
    3) max_names>0 이면 상위 N개만 kept, 나머지는 dropped 로 보고(무음 컷 금지).
    min_score<=0 이면 게이트 비활성(정렬·캡만). Returns (kept, dropped)."""
    ms = int(min_score or 0)
    survivors, dropped = [], []
    for c in (target_codes or []):
        c = str(c).strip()
        if not c:
            continue
        if ms <= 0 or c not in (quant_scores or {}):
            survivors.append(c)                       # 게이트 off 또는 미점수 → 통과(보존)
        elif int(quant_scores.get(c) or 0) >= ms:
            survivors.append(c)
        else:
            dropped.append(c)                         # 점수 미달 제거
    # 점수 없는 종목(평가불가)은 +inf 로 둬 맨 앞 — 캡에서 우선 보존
    def _key(c):
        return quant_scores.get(c) if c in (quant_scores or {}) else float("inf")
    survivors.sort(key=lambda c: -float(_key(c)))
    mn = int(max_names or 0)
    if mn > 0 and len(survivors) > mn:
        dropped.extend(survivors[mn:])
        survivors = survivors[:mn]
    return survivors, dropped


def format_scoring_rubric_block(qiw: Dict[str, float], dw: Dict[str, float], min_score: int) -> str:
    """운용전략실장 PASS1 선정 프롬프트에 주입할 채점 루브릭 요약(사장 지시 2026-06-04 ①).
    어떤 지표가 가점/감점되는지(상위 가중 3개)와 최소 퀀트점수 게이트를 알려, LLM이 루브릭 정렬된
    후보를 제안하게 한다. 점수는 시스템(파이썬)이 확정하므로 LLM은 '선정 기준'으로만 참고."""
    _names = {"rsi": "RSI(과매도 가점)", "macd": "MACD 모멘텀", "adx": "ADX 추세강도",
              "vwap": "VWAP 이격(추격 감점)", "vol": "저변동", "mom": "모멘텀(1·3M)",
              "cmf": "CMF 매집", "flow": "외인·기관 수급", "high52": "52주 신고가 근접"}
    pos = sorted(((k, float(v)) for k, v in (qiw or {}).items() if float(v) > 0), key=lambda kv: -kv[1])[:3]
    drivers = ", ".join(_names.get(k, k) for k, _ in pos) or "(가중치 미설정)"
    lines = ["[채점 루브릭 — 운용전략실장 선정 참고]",
             f"시스템 퀀트점수(0~10)는 다음을 가장 크게 반영: {drivers}.",
             f"최종 매수는 퀀트점수 {int(min_score or 0)}점 이상만 통과하니, 이 기준에 부합할 종목을 우선 고르십시오.",
             "점수는 시스템이 확정합니다(이 블록은 선정 기준 참고용)."]
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_filter_targets_rank.py tests/test_strategy_params_runtime.py -v`
Expected: PASS (new 6 + existing filter tests still green — old call sites use 3 args, default `max_names=0`).

- [ ] **Step 5: Checkpoint**

Run: `python3.11 -m pytest -q`
Expected: all pass.

### Task B2: 배선 — PASS1 루브릭 주입 + finalize_sell 랭크/캡 적용

**Files:**
- Modify: `main_swarm.py` `_cyc_stage_select` (PASS1 프롬프트, ~3402 부근) — 루브릭 블록 주입.
- Modify: `main_swarm.py` `_cyc_stage_finalize_sell` — `filter_targets_by_score` 호출에 `max_names` 전달.

- [ ] **Step 1: Inject rubric into PASS1 select prompt**

`_cyc_stage_select` 내 운용전략실장(orchestrator) PASS1 `think()` 호출 프롬프트를 조립하는 곳을 찾는다(후보 5개 선정, ~3402). 프롬프트 문자열에 다음을 추가(보유 종목 입력·매크로 입력 사이 등 자연스러운 위치):

먼저 프롬프트 조립 직전에 루브릭 블록을 만든다(`self.uid` 사용):
```python
            _rubric_qiw = {sig: runtime.get(key, uid=self.uid) for sig, key in (
                ("rsi", "QIW_RSI"), ("macd", "QIW_MACD"), ("adx", "QIW_ADX"), ("vwap", "QIW_VWAP"),
                ("vol", "QIW_VOL"), ("mom", "QIW_MOM"), ("cmf", "QIW_CMF"), ("flow", "QIW_FLOW"),
                ("high52", "QIW_HIGH52"))}
            _rubric_dw = {"QUANT": runtime.get("DW_QUANT", uid=self.uid), "NEWS": runtime.get("DW_NEWS", uid=self.uid),
                          "MACRO": runtime.get("DW_MACRO", uid=self.uid)}
            _rubric_block = format_scoring_rubric_block(
                _rubric_qiw, _rubric_dw, int(runtime.get("MIN_QUANT_SCORE", uid=self.uid) or 0))
```
그리고 PASS1 프롬프트 f-string에 `\n\n{_rubric_block}\n` 한 줄을 후보 선정 지시 근처에 삽입한다. (결정론 점수 OFF여도 루브릭은 무해 — 단순 선정 가이드.)

- [ ] **Step 2: Apply max_names in finalize_sell**

`_cyc_stage_finalize_sell`에서 `filter_targets_by_score(...)`를 호출하는 곳을 찾는다(현재 3-arg). 호출을 다음으로 교체:
```python
                _max_names = int(runtime.get("MAX_BUY_NAMES", uid=self.uid) or 0)
                _kept_targets, _dropped_targets = filter_targets_by_score(
                    _final_targets, cyc._quant_scores or {},
                    int(runtime.get("MIN_QUANT_SCORE", uid=self.uid) or 0), max_names=_max_names)
```
(`_final_targets`는 기존 변수명에 맞춘다 — 기존 호출의 첫 인자.) drop 내역을 기존 notes/로그 패턴대로 남긴다:
```python
                if _dropped_targets:
                    logger.info(f"[랭크선정 uid={self.uid}] 매수대상 정렬·캡: 유지 {_kept_targets} / 제외 {_dropped_targets}")
```

- [ ] **Step 3: Smoke — import & rubric**

Run:
```bash
python3.11 -c "from main_swarm import format_scoring_rubric_block as f; print(f({'mom':12,'macd':10}, {'QUANT':60}, 6))"
```
Expected: 루브릭 텍스트 출력(모멘텀 언급·6점 게이트).

- [ ] **Step 4: Checkpoint**

Run: `python3.11 -m pytest -q`
Expected: all pass.

---

## Phase C — ② 리스크기반 포지션 사이징

### Task C1: `tools/position_sizing.py` 순수 모듈

**Files:**
- Create: `tools/position_sizing.py`
- Test: `tests/test_position_sizing.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_position_sizing.py
"""사장 지시 2026-06-04 ②: 리스크기반 포지션 사이징(순수). weight=1.0 → 균등 1몫.
점수↑→비중↑, σ↑→비중↓. equal/strength0→전원 1.0. Σw=종목수. 결측=중립."""
import math
from tools.position_sizing import compute_sizing_weights


def test_equal_mode_all_one():
    w = compute_sizing_weights(["A", "B"], {"A": 9, "B": 3}, {"A": 10, "B": 80}, mode="equal")
    assert w == {"A": 1.0, "B": 1.0}


def test_strength_zero_all_one():
    w = compute_sizing_weights(["A", "B"], {"A": 9, "B": 3}, {"A": 10, "B": 80},
                               mode="risk_weighted", strength=0.0)
    assert all(abs(v - 1.0) < 1e-9 for v in w.values())


def test_weights_sum_to_n():
    codes = ["A", "B", "C"]
    w = compute_sizing_weights(codes, {"A": 8, "B": 5, "C": 6}, {"A": 20, "B": 30, "C": 25},
                               mode="risk_weighted", strength=0.5, max_tilt=2.0)
    assert abs(sum(w.values()) - len(codes)) < 1e-6
    assert all(v > 0 for v in w.values())


def test_higher_score_gets_more():
    w = compute_sizing_weights(["HI", "LO"], {"HI": 9, "LO": 4}, {"HI": 25, "LO": 25},
                               mode="risk_weighted", strength=1.0, max_tilt=3.0)
    assert w["HI"] > w["LO"]


def test_higher_vol_gets_less():
    w = compute_sizing_weights(["CALM", "WILD"], {"CALM": 6, "WILD": 6}, {"CALM": 15, "WILD": 60},
                               mode="risk_weighted", strength=1.0, max_tilt=3.0)
    assert w["CALM"] > w["WILD"]


def test_max_tilt_bounds_each_weight():
    w = compute_sizing_weights(["A", "B", "C"], {"A": 10, "B": 0, "C": 5}, {"A": 5, "B": 90, "C": 30},
                               mode="risk_weighted", strength=1.0, max_tilt=2.0)
    # 합=종목수 보장 하에서 극단치가 1/max_tilt~max_tilt 근방으로 억제(과집중 방지) — 최대가 종목수 미만
    assert max(w.values()) < len(w)


def test_missing_score_and_sigma_neutral():
    # 점수/σ 둘 다 결측 → 균등(중립)
    w = compute_sizing_weights(["A", "B"], {}, {}, mode="risk_weighted", strength=1.0)
    assert all(abs(v - 1.0) < 1e-9 for v in w.values())


def test_empty_codes():
    assert compute_sizing_weights([], {}, {}) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_position_sizing.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `tools/position_sizing.py`**

```python
"""리스크기반 포지션 사이징 — 순수 함수 (사장 지시 2026-06-04 ②).
종목별 사이징 가중치 w∈(0,∞), Σw=종목수. budget_i = (cycle_budget/n) * w[code_i].
점수↑(우호)·변동성↓(안전)일수록 큰 비중. equal 모드/strength=0 이면 전원 1.0(기존 균등분배).
결측 점수/σ 는 중립(1.0 요인) 처리. 하드 한도(per_stock_cap)는 호출부에서 별도 적용."""
from typing import Dict, List


def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def compute_sizing_weights(codes: List[str], scores: Dict[str, float], sigmas: Dict[str, float],
                           *, mode: str = "risk_weighted", strength: float = 0.5,
                           max_tilt: float = 2.0) -> Dict[str, float]:
    codes = [str(c).strip() for c in (codes or []) if str(c).strip()]
    if not codes:
        return {}
    if mode != "risk_weighted":
        return {c: 1.0 for c in codes}
    strength = max(0.0, min(1.0, float(strength)))
    max_tilt = max(1.0, float(max_tilt))

    # 점수 요인: score/5 → 5점=1.0, 10점=2.0, 0점=0(하한 0.2로 막아 0 방지)
    score_factor = {}
    for c in codes:
        s = scores.get(c)
        score_factor[c] = max(0.2, float(s) / 5.0) if s is not None else 1.0
    # 변동성 요인: median_sigma / sigma → 저변동 종목이 >1 (역변동성). σ 결측/<=0 → 1.0
    valid_sig = [float(sigmas[c]) for c in codes if sigmas.get(c) and float(sigmas[c]) > 0]
    med = sorted(valid_sig)[len(valid_sig) // 2] if valid_sig else 0.0
    vol_factor = {}
    for c in codes:
        sg = sigmas.get(c)
        vol_factor[c] = (med / float(sg)) if (sg and float(sg) > 0 and med > 0) else 1.0

    raw = {c: score_factor[c] * vol_factor[c] for c in codes}
    m = _mean([raw[c] for c in codes]) or 1.0
    # 균등(1.0)과 정규화 raw 사이 strength 보간 → 클램프 → 합=n 재정규화
    tilt = {}
    for c in codes:
        norm = raw[c] / m
        t = 1.0 + strength * (norm - 1.0)
        tilt[c] = max(1.0 / max_tilt, min(max_tilt, t))
    tm = _mean([tilt[c] for c in codes]) or 1.0
    return {c: tilt[c] / tm for c in codes}      # Σw = n
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_position_sizing.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Checkpoint**

Run: `python3.11 -m pytest -q`
Expected: all pass.

### Task C2: 배선 — `_build_orders`에 사이징 가중치 적용 (KR+US 패리티)

**Files:**
- Modify: `main_swarm.py:3621` 부근 (data_quant — σ 저장), `cyc._quant_sigmas` 세팅.
- Modify: `main_swarm.py:1944-2087` (`_build_orders` — per-name budget에 weight 곱).
- Modify: `_cyc_stage_build_orders` 호출부 — `cyc._quant_sigmas` 전달.

- [ ] **Step 1: Capture sigma in data_quant**

`main_swarm.py:3618-3623`의 결정론 블록에서 `_ind` 계산 직후, σ를 사이클에 모은다. `_cyc_stage_data_quant` 상단(루프 전, `_quant_scores = ...` 초기화 근처)에 `_quant_sigmas: Dict[str, float] = {}` 추가하고, 루프 안 `_ind` 계산 직후 삽입:
```python
                        if _ind and _ind.get("sigma20") is not None:
                            _quant_sigmas[_qcode] = float(_ind["sigma20"])
```
그리고 스테이지 끝(`cyc._quant_scores = _quant_scores` 다음, 3724 부근)에 추가:
```python
            cyc._quant_sigmas = _quant_sigmas   # 사장 지시 2026-06-04 ②: 리스크기반 사이징용 변동성
```
(주의: `_quant_sigmas`는 `_det_scoring` 여부와 무관히 초기화돼야 한다. det OFF면 비어 있고 → 사이징은 점수만/중립으로 동작.)

- [ ] **Step 2: Pass sigmas into `_build_orders`**

`_build_orders` 시그니처(`main_swarm.py:1944-1948`)에 `quant_scores=None, quant_sigmas=None` 추가:
```python
    async def _build_orders(self, target_codes: List[str], candidate_codes: List[str], quant_report: str, news_report: str,
                            holdings: List[Dict], sell_directives: Optional[Dict[str, str]] = None,
                            market_open: bool = False,
                            entry_dirs: Optional[Dict[str, Dict[str, Any]]] = None,
                            sell_prices: Optional[Dict[str, Dict[str, Any]]] = None,
                            quant_scores: Optional[Dict[str, int]] = None,
                            quant_sigmas: Optional[Dict[str, float]] = None):
```
`_cyc_stage_build_orders`(3929)에서 `_build_orders(...)` 호출에 인자 추가:
```python
                                       quant_scores=getattr(cyc, "_quant_scores", None),
                                       quant_sigmas=getattr(cyc, "_quant_sigmas", None))
```

- [ ] **Step 3: Compute weights and tilt per-name budget**

`_build_orders` 내 `per_name_budget` 계산(`main_swarm.py:1996-1998`) 직후에 가중치를 만든다:
```python
        # 사장 지시 2026-06-04 ②: 리스크기반 사이징 — 점수·역변동성으로 종목별 예산 기울임(KR/US 공통).
        from tools.position_sizing import compute_sizing_weights
        _buy_names = [str(c).strip() for c in (target_codes or [])
                      if str(c).strip() and str(c).strip() not in held_codes]
        _sizing_w = compute_sizing_weights(
            _buy_names, quant_scores or {}, quant_sigmas or {},
            mode=str(runtime.get("POSITION_SIZING_MODE", uid=self.uid) or "equal"),
            strength=float(runtime.get("SIZING_TILT_STRENGTH", uid=self.uid) or 0.0),
            max_tilt=float(runtime.get("SIZING_MAX_TILT", uid=self.uid) or 2.0))
```
그리고 KR 매수 사이징(`main_swarm.py:2031-2034`)의 `per_order_budget=min(per_order_budget, per_name_budget)`를 가중 적용으로 교체:
```python
                _w = _sizing_w.get(code, 1.0)
                qty = _affordable_buy_qty(
                    price, per_order_budget=min(per_order_budget, per_name_budget * _w),
                    per_stock_cap=(per_stock_cap if per_stock_cap > 0 else float("inf")),
                    cycle_remaining=max(0.0, cycle_budget - spent_krw))
```
US 매수 사이징(`main_swarm.py:2065-2068`)도 동일하게 `per_name_budget` → `per_name_budget * _w` (USD 환산 전 KRW 예산에 곱):
```python
                _w = _sizing_w.get(tk, 1.0)
                qty_us = _affordable_buy_qty(
                    us_px, per_order_budget=min(per_order_budget, per_name_budget * _w) / _krw_per_usd,
                    per_stock_cap=((per_stock_cap / _krw_per_usd) if per_stock_cap > 0 else float("inf")),
                    cycle_remaining=max(0.0, cycle_budget - spent_krw) / _krw_per_usd)
```
(주의 KR/US 패리티: 양쪽 모두 `per_name_budget * _w`로 동일하게 곱한다. `per_stock_cap`·`cycle_remaining` 하드 한도는 그대로 — 과집중 이중 방어.)

- [ ] **Step 4: Smoke — equal mode 회귀(기존 수량 동일)**

Run:
```bash
python3.11 -c "
from tools.position_sizing import compute_sizing_weights as w
print('equal', w(['A','B'],{'A':9},{'A':10}, mode='equal'))
print('rw', w(['A','B'],{'A':9,'B':4},{'A':15,'B':60}, mode='risk_weighted', strength=0.5))
"
```
Expected: equal → 모두 1.0; rw → A>B(점수↑·σ↓), 합=2.

- [ ] **Step 5: Checkpoint**

Run: `python3.11 -m pytest -q`
Expected: all pass.

---

## Phase D — ③ 유니버스 스크리닝 결정론화

### Task D1: `tools/universe_screen.py` 순수 모듈

**Files:**
- Create: `tools/universe_screen.py`
- Test: `tests/test_universe_screen.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_universe_screen.py
"""사장 지시 2026-06-04 ③: 유니버스 스크리닝(순수). 레버리지/인버스·저가·거래대금 미달 후보 배제.
임계 0/False=해당 기준 off. 거른 내역은 사유 동반(무음 금지). 후보 풀만 거름(최종 주문 불간섭)."""
from tools.universe_screen import screen_universe


def _items():
    return [
        {"code": "005930", "name": "삼성전자", "price": 70000, "turnover": 5_000_000_000},
        {"code": "251340", "name": "KODEX 코스닥150 인버스", "price": 3000, "turnover": 9_000_000_000},
        {"code": "900001", "name": "동전주식", "price": 300, "turnover": 8_000_000_000},
        {"code": "000001", "name": "저거래기업", "price": 50000, "turnover": 1_000_000},
    ]


def test_excludes_leveraged_by_name():
    kept, dropped = screen_universe(_items(), exclude_leveraged=True)
    assert "251340" not in kept
    assert any(c == "251340" for c, _ in dropped)


def test_excludes_low_price():
    kept, dropped = screen_universe(_items(), min_price=1000, exclude_leveraged=False)
    assert "900001" not in kept and "005930" in kept


def test_excludes_low_turnover():
    kept, dropped = screen_universe(_items(), min_turnover=100_000_000, exclude_leveraged=False)
    assert "000001" not in kept


def test_thresholds_off_keep_all_nonleveraged():
    kept, _ = screen_universe(_items(), min_price=0, min_turnover=0, exclude_leveraged=False)
    assert set(kept) == {"005930", "251340", "900001", "000001"}


def test_dropped_carries_reason():
    _, dropped = screen_universe(_items(), min_price=1000, min_turnover=100_000_000, exclude_leveraged=True)
    reasons = dict(dropped)
    assert "레버리지" in reasons.get("251340", "") or "인버스" in reasons.get("251340", "")
    assert "저가" in reasons.get("900001", "")
    assert "거래대금" in reasons.get("000001", "")


def test_missing_fields_kept():
    # price/turnover 결측 종목은 데이터 없음 → 보존(평가불가 드롭 금지)
    kept, _ = screen_universe([{"code": "111111", "name": "데이터없음"}], min_price=1000, min_turnover=100)
    assert kept == ["111111"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_universe_screen.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `tools/universe_screen.py`**

```python
"""유니버스 스크리닝 — 순수 함수 (사장 지시 2026-06-04 ③).
후보(아이디어 풀)에서 레버리지/인버스·저가·거래대금 미달 종목을 사전 배제한다.
임계 0/False = 해당 기준 비활성. price/turnover 결측 종목은 평가불가 → 보존(드롭 금지).
*최종 주문은 거르지 않는다* — 후보 풀에만 적용해 주문 스킵을 피한다. 거른 내역은 사유 동반."""
from typing import Dict, List, Tuple

LEVERAGE_KEYWORDS = ("레버리지", "인버스", "곱버스", "2X", "3X", "ETN", "LEVERAGE", "INVERSE")


def _is_leveraged(name: str) -> bool:
    u = (name or "").upper()
    return any(k.upper() in u for k in LEVERAGE_KEYWORDS)


def screen_universe(items: List[Dict], *, min_price: float = 0.0, min_turnover: float = 0.0,
                    exclude_leveraged: bool = True) -> Tuple[List[str], List[Tuple[str, str]]]:
    """items: [{code, name, price?, turnover?}]. Returns (kept_codes, dropped[(code, reason)])."""
    kept: List[str] = []
    dropped: List[Tuple[str, str]] = []
    for it in (items or []):
        code = str(it.get("code", "")).strip()
        if not code:
            continue
        name = it.get("name") or ""
        price = it.get("price")
        turnover = it.get("turnover")
        if exclude_leveraged and _is_leveraged(name):
            dropped.append((code, f"레버리지/인버스/ETN 배제: {name}")); continue
        if min_price and price is not None and float(price) > 0 and float(price) < float(min_price):
            dropped.append((code, f"저가주 배제: {float(price):,.0f} < {float(min_price):,.0f}")); continue
        if min_turnover and turnover is not None and float(turnover) < float(min_turnover):
            dropped.append((code, f"거래대금 미달 배제: {float(turnover):,.0f} < {float(min_turnover):,.0f}")); continue
        kept.append(code)
    return kept, dropped
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_universe_screen.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Checkpoint**

Run: `python3.11 -m pytest -q`
Expected: all pass.

### Task D2: 배선 — 후보 확정 직후 스크리닝 적용

**Files:**
- Modify: `main_swarm.py` `_cyc_stage_data_quant` 또는 `_cyc_stage_select` — 후보 코드 확정 후(`candidate_codes` 결정 직후) `screen_universe` 적용.

- [ ] **Step 1: Apply screen to candidates**

후보 코드가 확정되는 지점(`_resolve_candidate_codes`/`seed_candidates_from_news` 결과를 `candidate_codes`로 합친 직후, select 또는 data_quant 초입)에서 가격/거래대금을 모아 스크린한다. KR 코드만 가격/거래대금 조회 가능하므로, US 티커는 레버리지 이름 필터만 적용(가격/거래대금 임계는 KR 전용). 다음을 후보 확정부에 삽입:

```python
            # 사장 지시 2026-06-04 ③: 유니버스 결정론 스크리닝 — 후보 풀에서 레버리지/저가/거래대금미달 배제.
            _excl_lev = bool(runtime.get("UNIVERSE_EXCLUDE_LEVERAGED", uid=self.uid))
            _min_price = float(runtime.get("UNIVERSE_MIN_PRICE", uid=self.uid) or 0)
            _min_turn = float(runtime.get("UNIVERSE_MIN_TURNOVER", uid=self.uid) or 0)
            if candidate_codes and (_excl_lev or _min_price > 0 or _min_turn > 0):
                from tools.universe_screen import screen_universe
                _items = []
                for _c in candidate_codes:
                    _c = str(_c).strip()
                    _nm_c = name_map.get(_c) or _c
                    _it = {"code": _c, "name": _nm_c}
                    if _is_kr_code(_c):
                        try:
                            _px = await self.broker.kr_last_price(_c); await asyncio.sleep(0.15)
                            if _px and _px > 0:
                                _it["price"] = _px
                        except Exception:
                            pass
                    _items.append(_it)   # US/조회실패는 price/turnover 결측 → 이름 필터만
                _kept, _dropped = screen_universe(
                    _items, min_price=_min_price, min_turnover=_min_turn, exclude_leveraged=_excl_lev)
                if _dropped:
                    logger.info(f"[유니버스 스크린 uid={self.uid}] 배제 {len(_dropped)}건: " +
                                "; ".join(f"{c}({r})" for c, r in _dropped))
                    await self._emit({"type": "agent_msg", "agent": "리스크관리실장",
                        "message": "🧹 유니버스 스크리닝 — 후보 배제: " +
                                   ", ".join(f"{c}: {r}" for c, r in _dropped)})
                    candidate_codes = _kept
```
(주의: `name_map`·`_is_kr_code`·`self.broker.kr_last_price`는 동일 스코프에 이미 존재. 거래대금(turnover) 소스가 시세 응답에 없으면 `_min_turn`은 사실상 가격만으로 동작 — `UNIVERSE_MIN_TURNOVER` 기본 0이라 무해. 추후 거래대금 필드 확보 시 `_it["turnover"]` 채우면 자동 활성.)

- [ ] **Step 2: Smoke**

Run:
```bash
python3.11 -c "
from tools.universe_screen import screen_universe
print(screen_universe([{'code':'251340','name':'KODEX 인버스','price':3000},
                        {'code':'005930','name':'삼성전자','price':70000}], min_price=1000))
"
```
Expected: kept=['005930'], dropped 에 251340(레버리지).

- [ ] **Step 3: Checkpoint**

Run: `python3.11 -m pytest -q`
Expected: all pass.

---

## Phase E — ④ 성과 귀인 + 에이전트 스코어카드

### Task E1: `infra/scorecard_store.py` — agent_signals 적재

**Files:**
- Create: `infra/scorecard_store.py`
- Test: `tests/test_scorecard_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scorecard_store.py
"""사장 지시 2026-06-04 ④: 에이전트 예측 구조화 적재(전향적). uid 분리·결측 컬럼 허용."""
import importlib


def _fresh_store(tmp_path, monkeypatch):
    import infra.scorecard_store as ss
    importlib.reload(ss)
    monkeypatch.setattr(ss, "DB_PATH", tmp_path / "sc.db")
    ss._conn = None  # 강제 재연결
    return ss


def test_record_and_list_roundtrip(tmp_path, monkeypatch):
    ss = _fresh_store(tmp_path, monkeypatch)
    ss.record_signal({"uid": 1, "cycle_started_at": "2026-06-04 10:00:00", "ts": "2026-06-04 10:00:01",
                      "code": "005930", "name": "삼성전자", "news_sentiment": 0.8, "quant_score": 7,
                      "det_breakdown": {"S_quant": 6.2}})
    rows = ss.list_signals(uid=1)
    assert len(rows) == 1
    assert rows[0]["code"] == "005930" and rows[0]["quant_score"] == 7
    assert rows[0]["det_breakdown"]["S_quant"] == 6.2  # JSON 파싱돼 반환


def test_uid_isolation(tmp_path, monkeypatch):
    ss = _fresh_store(tmp_path, monkeypatch)
    ss.record_signal({"uid": 1, "cycle_started_at": "t", "ts": "t", "code": "A"})
    ss.record_signal({"uid": 2, "cycle_started_at": "t", "ts": "t", "code": "B"})
    assert {r["code"] for r in ss.list_signals(uid=1)} == {"A"}
    assert {r["code"] for r in ss.list_signals(uid=2)} == {"B"}


def test_missing_optional_columns_ok(tmp_path, monkeypatch):
    ss = _fresh_store(tmp_path, monkeypatch)
    rid = ss.record_signal({"uid": 1, "cycle_started_at": "t", "ts": "t", "code": "A"})
    assert rid is not None
    r = ss.list_signals(uid=1)[0]
    assert r["news_sentiment"] is None and r["quant_score"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_scorecard_store.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `infra/scorecard_store.py`** (cycle_store.py 패턴 미러)

```python
"""에이전트 예측 신호 영속화 (사장 지시 2026-06-04 ④ — 성과 귀인용).
data/scorecard.db 의 agent_signals 테이블에 사이클별·종목별 예측을 전향적으로 적재.
cycle_store.py 의 단일-라이터 sqlite 패턴을 따른다. thesis/sell/risk 컬럼은 예약(nullable)."""
from __future__ import annotations
import json, sqlite3, threading, logging
from pathlib import Path
from typing import Optional, Dict, List, Any

logger = logging.getLogger("SCORECARD_STORE")
DB_PATH = Path(__file__).parent.parent / "data" / "scorecard.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_signals (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    uid              INTEGER,
    cycle_started_at TEXT,
    ts               TEXT,
    code             TEXT NOT NULL,
    name             TEXT,
    news_sentiment   REAL,
    quant_score      INTEGER,
    det_breakdown    TEXT,          -- JSON
    thesis_verdict   TEXT,          -- 예약(향후)
    sell_decision    TEXT,          -- 예약
    risk_verdict     TEXT           -- 예약
);
CREATE INDEX IF NOT EXISTS idx_sig_uid ON agent_signals(uid);
CREATE INDEX IF NOT EXISTS idx_sig_code ON agent_signals(code);
CREATE INDEX IF NOT EXISTS idx_sig_ts ON agent_signals(ts);
"""

_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, isolation_level=None)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(_SCHEMA)
    return _conn


def record_signal(sig: Dict[str, Any]) -> Optional[int]:
    """한 종목·한 사이클의 에이전트 예측을 적재. 누락 키 → NULL. Returns row id or None."""
    cols = ("uid", "cycle_started_at", "ts", "code", "name", "news_sentiment",
            "quant_score", "det_breakdown", "thesis_verdict", "sell_decision", "risk_verdict")
    vals = []
    for c in cols:
        v = sig.get(c)
        if c == "det_breakdown" and v is not None:
            try: v = json.dumps(v, ensure_ascii=False)
            except Exception: v = None
        vals.append(v)
    try:
        with _lock:
            cur = _get_conn().execute(
                f"INSERT INTO agent_signals ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})", vals)
            return cur.lastrowid
    except Exception as e:
        logger.warning(f"record_signal 실패: {e}")
        return None


def list_signals(uid: Optional[int] = None, since: Optional[str] = None, limit: int = 5000) -> List[Dict]:
    """최신순 신호. uid/since(ts >=) 필터. det_breakdown 은 dict 로 파싱해 반환."""
    try:
        where, args = [], []
        if uid is not None:
            where.append("uid=?"); args.append(int(uid))
        if since:
            where.append("ts>=?"); args.append(since)
        wsql = ("WHERE " + " AND ".join(where)) if where else ""
        args.append(int(limit))
        with _lock:
            rows = _get_conn().execute(
                f"SELECT * FROM agent_signals {wsql} ORDER BY id DESC LIMIT ?", tuple(args)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if d.get("det_breakdown"):
                try: d["det_breakdown"] = json.loads(d["det_breakdown"])
                except Exception: pass
            out.append(d)
        return out
    except Exception as e:
        logger.warning(f"list_signals 실패: {e}")
        return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_scorecard_store.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Checkpoint**

Run: `python3.11 -m pytest -q`
Expected: all pass.

### Task E2: `tools/agent_scorecard.py` — 귀인 엔진(순수)

**Files:**
- Create: `tools/agent_scorecard.py`
- Test: `tests/test_agent_scorecard.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_agent_scorecard.py
"""사장 지시 2026-06-04 ④: 성과귀인 순수함수 — IC(스피어만)·슬리피지·알파베타."""
import math
from tools.agent_scorecard import information_coefficient, slippage_stats, alpha_beta, compute_scorecard


def test_ic_perfect_positive():
    pairs = [(1, 0.1), (2, 0.2), (3, 0.3), (4, 0.4)]
    assert abs(information_coefficient(pairs) - 1.0) < 1e-9


def test_ic_perfect_negative():
    pairs = [(1, 0.4), (2, 0.3), (3, 0.2), (4, 0.1)]
    assert abs(information_coefficient(pairs) + 1.0) < 1e-9


def test_ic_too_few_points_none():
    assert information_coefficient([(1, 0.1), (2, 0.2)]) is None


def test_slippage_bps_sign():
    # 매수가 결정가보다 비싸게 체결 → 양(+) 불리한 슬리피지
    fills = [{"side": "buy", "decision_price": 100.0, "fill_price": 101.0}]
    st = slippage_stats(fills)
    assert st["n"] == 1 and st["mean_bps"] > 0


def test_alpha_beta_recovers_known():
    bench = [0.01, -0.02, 0.03, 0.00, 0.015]
    port = [2 * b + 0.001 for b in bench]   # beta≈2, alpha≈0.001
    ab = alpha_beta(port, bench)
    assert abs(ab["beta"] - 2.0) < 1e-6
    assert abs(ab["alpha"] - 0.001) < 1e-6


def test_alpha_beta_too_few_none():
    assert alpha_beta([0.01], [0.02]) is None


def test_compute_scorecard_shape():
    signals = [
        {"code": "A", "ts": "2026-06-01 10:00:00", "quant_score": 8, "news_sentiment": 0.7},
        {"code": "B", "ts": "2026-06-01 10:00:00", "quant_score": 3, "news_sentiment": -0.4},
        {"code": "C", "ts": "2026-06-01 10:00:00", "quant_score": 6, "news_sentiment": 0.1},
    ]
    fwd = {"A": 0.05, "B": -0.03, "C": 0.01}     # 점수·감성과 양의 상관
    card = compute_scorecard(signals, trades=[], equity=[], bench=[],
                             price_lookup=lambda code, ts: fwd.get(code), window_days=30)
    assert "quant" in card and "news" in card
    assert card["quant"]["ic"] is not None and card["quant"]["n"] == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_agent_scorecard.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `tools/agent_scorecard.py`**

```python
"""에이전트 성과 귀인 — 순수 함수 (사장 지시 2026-06-04 ④).
IO 없음: 신호/체결/자산/가격을 인자로 받아 에이전트별 예측력 지표를 계산한다.
- information_coefficient: 예측(점수·감성) vs 후속수익 스피어만 순위상관(-1..1).
- slippage_stats: 결정가 대비 체결가 불리도(bps, +면 불리).
- alpha_beta: 포트 수익 vs 벤치마크 OLS(beta·alpha).
- compute_scorecard: 위를 조립해 {quant, news, slippage, portfolio, ...} dict.
표본 부족(3 미만)·결측은 None/n 으로 정직히 표기(무음 금지)."""
from typing import Callable, Dict, List, Optional, Tuple


def _rank(xs: List[float]) -> List[float]:
    """평균 순위(동점은 평균). 1-based."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(xs: List[float], ys: List[float]) -> Optional[float]:
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n; my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs); vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / (vx ** 0.5 * vy ** 0.5)


def information_coefficient(pairs: List[Tuple[float, float]]) -> Optional[float]:
    """[(signal, forward_return)] → 스피어만 순위상관. 3쌍 미만이면 None."""
    pairs = [(float(s), float(r)) for s, r in pairs if s is not None and r is not None]
    if len(pairs) < 3:
        return None
    sigs = [p[0] for p in pairs]; rets = [p[1] for p in pairs]
    return _pearson(_rank(sigs), _rank(rets))


def slippage_stats(fills: List[Dict]) -> Dict:
    """fills: [{side, decision_price, fill_price}] → {mean_bps, n}. +bps = 불리(매수 비싸게/매도 싸게)."""
    bps = []
    for f in (fills or []):
        dp = f.get("decision_price"); fp = f.get("fill_price")
        if not dp or not fp or float(dp) <= 0:
            continue
        diff = (float(fp) - float(dp)) / float(dp)
        if str(f.get("side", "")).lower().startswith("sell"):
            diff = -diff                      # 매도는 싸게 체결될수록 불리
        bps.append(diff * 10000.0)
    return {"mean_bps": (sum(bps) / len(bps)) if bps else None, "n": len(bps)}


def alpha_beta(port_returns: List[float], bench_returns: List[float]) -> Optional[Dict]:
    """포트/벤치 수익률 시계열 OLS. 3점 미만/벤치 무분산이면 None. {alpha, beta, n}."""
    n = min(len(port_returns or []), len(bench_returns or []))
    if n < 3:
        return None
    p = [float(x) for x in port_returns[:n]]; b = [float(x) for x in bench_returns[:n]]
    mb = sum(b) / n; mp = sum(p) / n
    vb = sum((x - mb) ** 2 for x in b)
    if vb <= 0:
        return None
    beta = sum((b[i] - mb) * (p[i] - mp) for i in range(n)) / vb
    return {"alpha": mp - beta * mb, "beta": beta, "n": n}


def compute_scorecard(signals: List[Dict], trades: List[Dict], equity: List[Dict],
                      bench: List[float], *, price_lookup: Callable[[str, str], Optional[float]],
                      window_days: int = 30) -> Dict:
    """에이전트별 지표 조립. price_lookup(code, signal_ts) → 후속수익률(없으면 None=표본제외).
    quant/news IC 는 signals, slippage 는 trades, portfolio 알파/베타는 equity vs bench."""
    q_pairs, n_pairs = [], []
    for s in (signals or []):
        fwd = price_lookup(s.get("code"), s.get("ts"))
        if fwd is None:
            continue
        if s.get("quant_score") is not None:
            q_pairs.append((float(s["quant_score"]), float(fwd)))
        if s.get("news_sentiment") is not None:
            n_pairs.append((float(s["news_sentiment"]), float(fwd)))
    # 포트 수익률 시계열 — equity[{total_eval}] 연속 차분
    evals = [float(e["total_eval"]) for e in (equity or []) if e.get("total_eval")]
    port_rets = [(evals[i] / evals[i - 1] - 1.0) for i in range(1, len(evals)) if evals[i - 1] > 0]
    return {
        "quant": {"ic": information_coefficient(q_pairs), "n": len(q_pairs)},
        "news": {"ic": information_coefficient(n_pairs), "n": len(n_pairs)},
        "slippage": slippage_stats(trades or []),
        "portfolio": alpha_beta(port_rets, bench or []),
        "window_days": window_days,
        "signal_count": len(signals or []),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_agent_scorecard.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Checkpoint**

Run: `python3.11 -m pytest -q`
Expected: all pass.

### Task E3: 배선 — data_quant 신호 캡처

**Files:**
- Modify: `main_swarm.py` `_cyc_stage_data_quant` — 종목 점수 확정 후 신호 적재.

- [ ] **Step 1: Capture signals after score finalized**

`main_swarm.py:3677-3679`(점수 확정 직후, `_quant_scores[_qcode] = _det_score` 부근)에 종목별 신호를 적재한다. 사이클 시작 시각(`cyc.started_at` 또는 동등 필드)을 키로:
```python
                # 사장 지시 2026-06-04 ④: 에이전트 예측 구조화 적재(성과귀인). 매매동작 불변(부수 적재).
                try:
                    from infra import scorecard_store
                    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
                    _kst_now = _dt.now(_tz(_td(hours=9))).strftime("%Y-%m-%d %H:%M:%S")
                    scorecard_store.record_signal({
                        "uid": self.uid,
                        "cycle_started_at": getattr(cyc, "started_at", None) or _kst_now,
                        "ts": _kst_now, "code": _qcode, "name": _qname,
                        "news_sentiment": (parse_news_sentiment(news_report, _qcode, _qname)),
                        "quant_score": _quant_scores.get(_qcode),
                        "det_breakdown": _det_bd})
                except Exception as _se:
                    logger.debug(f"[스코어카드] 신호 적재 생략 {_qcode}: {_se}")
```
(주의: `cyc.started_at` 필드명이 다르면 사이클 객체의 시작시각 속성을 쓴다 — `record_cycle` meta 의 `started_at`과 동일 키. 적재 실패는 매매에 영향 없게 try/except.)

- [ ] **Step 2: Smoke — signals persisted**

Run:
```bash
python3.11 -c "
import infra.scorecard_store as ss
ss.record_signal({'uid':99,'cycle_started_at':'t','ts':'t','code':'TEST','quant_score':7})
print([r['code'] for r in ss.list_signals(uid=99)])
"
```
Expected: `['TEST']` (data/scorecard.db 생성·적재 확인).

- [ ] **Step 3: Checkpoint**

Run: `python3.11 -m pytest -q`
Expected: all pass.

### Task E4: `GET /api/scorecard` 엔드포인트 + 가격 lookup

**Files:**
- Modify: `server/app.py` — 신규 라우트.
- Test: `tests/test_scorecard_endpoint.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scorecard_endpoint.py
"""사장 지시 2026-06-04 ④: /api/scorecard 가 에이전트 성과 지표 JSON 을 반환."""
from fastapi.testclient import TestClient
import server.app as app_mod


def test_scorecard_endpoint_ok(monkeypatch):
    # 신호/체결/자산을 스텁해 엔드포인트가 스코어카드 dict 를 200 으로 반환하는지 확인
    monkeypatch.setattr(app_mod, "_scorecard_for_uid", lambda uid: {
        "quant": {"ic": 0.3, "n": 12}, "news": {"ic": 0.1, "n": 9},
        "slippage": {"mean_bps": 4.2, "n": 5}, "portfolio": {"alpha": 0.001, "beta": 1.1, "n": 20},
        "window_days": 30, "signal_count": 30})
    client = TestClient(app_mod.app)
    r = client.get("/api/scorecard?uid=1")
    assert r.status_code == 200
    body = r.json()
    assert body["quant"]["ic"] == 0.3 and body["portfolio"]["beta"] == 1.1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_scorecard_endpoint.py -v`
Expected: FAIL — 404 (route missing) / `_scorecard_for_uid` AttributeError.

- [ ] **Step 3: Add `_scorecard_for_uid` helper + route to `server/app.py`**

`server/app.py` 적당한 위치(다른 `/api/...` 라우트 근처)에 추가. 기존 패턴(uid 해석·equity/trade 로더)을 따른다:
```python
def _scorecard_for_uid(uid: int):
    """에이전트 성과 스코어카드 — 신호(scorecard_store)·체결(trade_log)·자산곡선·지수 조인."""
    import runtime as _rt
    from infra import scorecard_store
    from tools.agent_scorecard import compute_scorecard
    from main_swarm import get_trade_log, get_equity_series, equity_path_for_uid  # 기존 헬퍼명에 맞춰 조정
    from tools.market_data import forward_return_after  # Step 4 에서 추가

    window = int(_rt.get("SCORECARD_WINDOW_DAYS", uid=uid) or 30)
    signals = scorecard_store.list_signals(uid=uid, limit=5000)
    try:
        trades = get_trade_log(equity_path_for_uid(uid))  # 체결: decision/fill 가용 시
    except Exception:
        trades = []
    try:
        eq = get_equity_series(equity_path_for_uid(uid), limit=400)
    except Exception:
        eq = []
    def _price_lookup(code, ts):
        try:
            return forward_return_after(code, ts, window_days=window)
        except Exception:
            return None
    return compute_scorecard(signals, trades, eq, bench=[], price_lookup=_price_lookup, window_days=window)


@app.get("/api/scorecard")
async def api_scorecard(uid: int = 1):
    return _scorecard_for_uid(int(uid))
```
(주의: `get_trade_log`/`get_equity_series`/`equity_path_for_uid`는 기존 main_swarm/서버 헬퍼의 실제 이름으로 맞춘다. 없으면 server/app.py 가 이미 쓰는 equity/trade 로더를 재사용. bench(지수 수익률)는 v1 에서 빈 리스트 → portfolio=None 가능, 추후 지수 시계열 주입.)

- [ ] **Step 4: Add `forward_return_after` to `tools/market_data.py`**

신호 시각 이후 `window_days` 영업일 후 수익률을 일봉에서 계산(없으면 None). 기존 일봉 로더를 재사용:
```python
def forward_return_after(code: str, signal_ts: str, window_days: int = 30):
    """signal_ts(='YYYY-MM-DD ...') 이후 종가 대비 window_days 경과 종가 수익률. 데이터 없으면 None.
    성과귀인(IC)용 — 미래 데이터가 아직 없으면(최근 신호) None 반환해 표본에서 제외."""
    try:
        df = _load_daily(code)  # 기존 일봉 로더명에 맞춰 조정(없으면 fetch 함수 재사용)
        if df is None or len(df) < 2:
            return None
        base_day = str(signal_ts)[:10]
        idx = df.index[df.index <= base_day]
        if len(idx) == 0:
            return None
        start_pos = df.index.get_loc(idx[-1])
        end_pos = min(start_pos + int(window_days), len(df) - 1)
        if end_pos <= start_pos:
            return None
        p0 = float(df["close"].iloc[start_pos]); p1 = float(df["close"].iloc[end_pos])
        return (p1 / p0 - 1.0) if p0 > 0 else None
    except Exception:
        return None
```
(주의: `_load_daily`/`df["close"]`/index 형식은 `compute_quant_indicators`가 쓰는 일봉 구조에 맞춘다 — 구현 시 해당 함수가 일봉을 얻는 방식을 그대로 재사용. 데이터 형식 불일치 시 None 반환이 안전 폴백.)

- [ ] **Step 5: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_scorecard_endpoint.py -v`
Expected: PASS.

- [ ] **Step 6: Checkpoint**

Run: `python3.11 -m pytest -q`
Expected: all pass.

### Task E5: ops_support 플레이북 + 스코어카드 요약 주입

**Files:**
- Modify: `infra/ops_support_worker.py` `_param_tuning_addendum()` — 신규 노브 플레이북 예시 + 스코어카드 요약.
- Modify: `tests/test_ops_param_catalog.py` — 신규 노브 노출 검증 추가.

- [ ] **Step 1: Extend test**

`tests/test_ops_param_catalog.py`의 `test_addendum_includes_regime_playbook`에 추가 assert:
```python
    # 제도권 4기능 신규 노브가 플레이북·카탈로그에 노출
    assert "POSITION_SIZING_MODE" in txt and "UNIVERSE_EXCLUDE_LEVERAGED" in txt and "MAX_BUY_NAMES" in txt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_ops_param_catalog.py -v`
Expected: FAIL — 신규 노브 미노출(플레이북은 정적 텍스트일 수 있음).

- [ ] **Step 3: Add playbook examples**

`_param_tuning_addendum()`의 레짐 플레이북 텍스트에 다음 줄 추가(카탈로그는 META 에서 자동 노출되므로 키 자체는 이미 포함되나, 플레이북에서 *지목*):
```
"- 급락장 대비: SIZING_TILT_STRENGTH↑(고확신·저변동 집중)·UNIVERSE_MIN_TURNOVER↑(유동성)·UNIVERSE_EXCLUDE_LEVERAGED=true·MAX_BUY_NAMES↓(집중).\n"
"- 분산 강화: MAX_BUY_NAMES↑·SIZING_TILT_STRENGTH↓·SIZING_MAX_TILT↓(균등에 가깝게).\n"
"- 고품질만: UNIVERSE_MIN_PRICE↑(동전주 배제)·MIN_QUANT_SCORE↑.\n"
```

- [ ] **Step 4: Inject scorecard summary (best-effort)**

`_param_tuning_addendum()` 끝부분에 스코어카드 요약을 동적 주입(증거기반 튜닝). uid 가용 시:
```python
    try:
        from infra import scorecard_store
        sigs = scorecard_store.list_signals(uid=uid, limit=500) if uid is not None else []
        if sigs:
            addendum += (f"\n\n[에이전트 성과 참고] 최근 적재 신호 {len(sigs)}건. "
                         f"성과 상세는 대시보드 /api/scorecard 참조 — IC 가 음수인 에이전트의 가중치를 재검토하십시오.")
    except Exception:
        pass
```
(주의: `_param_tuning_addendum(uid=...)` 시그니처에 uid 가 없으면 호출부에서 uid 를 넘기도록 추가. 없으면 이 블록은 생략 가능 — 카탈로그/플레이북 노출만으로 Step 1 테스트는 통과.)

- [ ] **Step 5: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_ops_param_catalog.py -v`
Expected: PASS.

- [ ] **Step 6: Checkpoint**

Run: `python3.11 -m pytest -q`
Expected: all pass.

---

## Phase F — 통합 검증 & 배포

### Task F1: 전체 테스트 + 실데이터 스모크

- [ ] **Step 1: Full suite**

Run: `python3.11 -m pytest -q`
Expected: 전부 통과(기존 + 신규 ~40여 케이스).

- [ ] **Step 2: Real-data smoke — sizing·screen·scorecard 동작**

Run:
```bash
python3.11 -c "
import config, runtime
from tools.position_sizing import compute_sizing_weights
from tools.universe_screen import screen_universe
print('keys', len(config.STRATEGY_TUNABLE_KEYS))
print('size', compute_sizing_weights(['A','B','C'],{'A':8,'B':5,'C':6},{'A':20,'B':60,'C':30}, mode='risk_weighted'))
print('screen', screen_universe([{'code':'251340','name':'인버스','price':3000},{'code':'005930','name':'삼성전자','price':70000}], min_price=1000))
print('preset balanced MAX_BUY_NAMES', config.STRATEGY_PRESETS['balanced']['MAX_BUY_NAMES'])
"
```
Expected: keys≈47, 사이징 합=3·A최대, screen 인버스 배제, MAX_BUY_NAMES=8.

### Task F2: 배포 (사장 확인 후)

- [ ] **Step 1: 마커 선생성(자동재개)** — 두 계정 data dir 에 `.running` 마커. (실제 마커 파일 규약은 기존 재시작 절차를 따른다 — `arquant-deploy-and-credentials` 메모리 참조.)

- [ ] **Step 2: 재시작** — `sudo systemctl restart arquant.service` (사장 명시 승인 후에만).

- [ ] **Step 3: 헬스 확인** — `sudo systemctl status arquant.service`; `curl -s localhost:8500/healthz` 또는 대시보드 200; `curl -s 'localhost:8500/api/scorecard?uid=1'` JSON 확인.

- [ ] **Step 4: 라이브 1사이클 관찰** — 다음 사이클에서 (a) 유니버스 스크린 로그 (b) 사이징이 점수 상위에 더 큰 수량 (c) scorecard_store 에 신호 적재되는지 확인.

---

## Self-Review (작성자 점검 완료)

**1. Spec coverage:** ①=Phase B(rubric+rank filter+wiring) ✓ / ②=Phase C(pure module+KR/US wiring) ✓ / ③=Phase D(pure module+candidate wiring) ✓ / ④=Phase E(store+engine+capture+endpoint+ops) ✓ / 교차 파라미터=Phase A ✓ / 배포=Phase F ✓. 비목표(분할집행·LLM투심위·대시보드탭·retro파싱)는 계획에서 의도적으로 제외.

**2. Placeholder scan:** "기존 헬퍼명에 맞춰 조정"·"필드명이 다르면" 류는 배선 시 실제 코드 확인이 필요한 곳(엔드포인트의 equity/trade 로더, 일봉 로더, cyc.started_at)으로, 구현 단계에서 grep 1회로 확정 가능한 *명시된* adaptation 포인트다(미정 설계 아님). 모든 순수함수·테스트·config 는 완전 코드.

**3. Type consistency:** `compute_sizing_weights(codes, scores, sigmas, *, mode, strength, max_tilt)` / `screen_universe(items, *, min_price, min_turnover, exclude_leveraged)→(kept, dropped)` / `filter_targets_by_score(target_codes, quant_scores, min_score, max_names=0)→(kept, dropped)` / `record_signal(dict)→id`·`list_signals(uid, since, limit)` / `compute_scorecard(signals, trades, equity, bench, *, price_lookup, window_days)` — 전 Task 에서 일관.

**알려진 구현-시 확인 포인트(설계 미정 아님, 단순 명명 확인):** server/app.py 의 equity·trade 로더 실제 이름, `tools/market_data.py` 일봉 로더 이름/스키마, 사이클 객체의 시작시각 속성명(`cyc.started_at`). 각각 None/빈값 안전 폴백을 두어 실패해도 매매·서버 안정.
