# 게이트 결함 5종 수정 + 운용지원실장 적극화 — 설계

작성일: 2026-06-05
근거: 업데이트 후 hh09080(실거래)·hh0908(모의) 2사이클 검토에서 발견된 5개 결함 + 사장 지시(ops 시간당·주간 적극화).

## 배경 — 검토에서 확인된 사실

두 계정 모두 실거래가 한 건도 체결되지 않았다. 원인은 에이전트 분석 부실이 **아니라**(대화·분석은 매끄럽고 충실) 결정론 게이트/데이터 결함 5종:

1. **모의계정 영구 동결**: `_equity_weight = (total_eval − KR현금)/total_eval`가 해외 **USD 예수금**(385M, `overseas_krw_cache.json`: `stock:0.0`, `exrt:225.57`)을 전부 "주식"으로 오분류 → 79% 주식비중 → 매크로 게이트가 매 사이클 매수 차단(#159 이후 10+사이클 후보 0).
2. **사장 직접 튜닝지시 전부 무시**: `_gate_overrides_by_data`가 manual 지시(`has_cycle_data=False`)를 싸잡아 차단 → TAKE_PROFIT_PCT 8/10/15/18% 지시 100건 전부 `applied=[]`, 여전히 기본값 12.0.
3. **US 일봉 rate-limit 무음 미체결**: #170 CVX 선정(퀀트 6)됐으나 KIS 일봉 조회가 초당거래건수 초과로 전 거래소 실패 → `us_last_price=0` → 조용히 스킵 → `orders_planned=[]`. 최종보고는 "체결 실패 원인 추정 안 함"(관측성 공백).
4. **KR 고가주 예산 데드존**: #172 LG이노텍(011070) 1주=1.13M인데 사이클예산 595K → 리스크 반려. 조립부("1주 from cash" 허용)와 리스크부("사이클예산 초과") 기준 불일치로 595K~5.95M 가격대는 무조건 반려.
5. **ops 사이클 워커 무음 사망**: fire-and-forget 서브프로세스가 spawn 후 조용히 죽음(재시작 후 ops_history 기록 0, 에러로그도 없음).

## 목표

- 5개 결함 수정 → 정상 게이트 통과 시 실제 매매 재개.
- 운용지원실장이 **시간당 적극 튜닝**(자율) + **주간 매우 적극 튜닝**(실데이터 근거) 수행.
- 적극화의 안전장치로 **파라미터별 범위 클램프**.

## 비목표 (YAGNI)

- 분할집행(TWAP/VWAP), LLM 투심위 — 의도적 제외(기존 결정).
- ops 자율 서비스 재시작 — 하지 않음(런타임 반영만).
- 매크로 게이트 자체 폐지 — 수식만 교정(게이트 기능은 유지).

---

## A. 5개 버그 수정

### Fix 1 — 매크로 게이트 주식비중 정정 (`main_swarm.py`)

현재(`main_swarm.py:3357`):
```python
_equity_weight = max(0.0, (_total0 - _cash0)) / _total0
```

교체 — 순수함수로 분리:
```python
def compute_stock_weight(total_eval, kr_cash, total_eval_kr, overseas_stock_krw):
    """실제 주식가치 / 총평가. 해외 USD 예수금은 주식이 아니므로 제외.
    주식가치 = (KR 총평가 − KR 현금)  +  해외 주식분(원화환산)."""
    if not total_eval or total_eval <= 0:
        return 0.0
    kr_stock = max(0.0, (total_eval_kr or 0.0) - (kr_cash or 0.0))
    os_stock = max(0.0, overseas_stock_krw or 0.0)
    return max(0.0, min(1.0, (kr_stock + os_stock) / total_eval))
```

입력 출처:
- `total_eval`, `kr_cash`: `kr_account_snapshot().buying_power`의 `total_eval`/`cash`.
- `total_eval_kr`: 같은 snapshot의 `total_eval_kr`(없으면 `total_eval`로 폴백 — 해외 미보유로 간주, 보수적).
- `overseas_stock_krw`: 브로커 `_get_overseas_cache()` 호출 후 `self._overseas_stock_krw`(캐시 `stock` 필드).

효과: 모의(KR현금 100M=KR총평가, 해외 stock 0) → `kr_stock=0, os_stock=0 → 0%` → 매크로 20% > 0% → 게이트 미발동. 실거래도 동일하게 정상.

테스트 `test_macro_gate_stock_weight.py`:
- 모의 케이스: total=485M, kr_cash=100M, total_eval_kr=100M, os_stock=0 → 0.0 (게이트 미발동).
- 실제 주식 보유: total=100M, kr_cash=50M, total_eval_kr=100M, os_stock=0 → 0.5.
- 해외 주식 보유: total=100M, kr_cash=10M, total_eval_kr=10M(해외분 90M 중 stock 60M) → (0 + 60M)/100M = 0.6.
- total 0/None → 0.0 (fail-open).

### Fix 2 — 사장 직접 지시 적용 (`infra/ops_support_worker.py`)

`_gate_overrides_by_data(raw_overrides, has_cycle_data, is_manual=False)`:
```python
if is_manual:
    return (raw_overrides or {}), ""          # 사장 직접 지시 = 권위. 데이터 게이트 면제(클램프는 별도 적용)
if not has_cycle_data:
    return {}, "실제 직전 사이클 데이터가 없어 ... 보류 ..."
return (raw_overrides or {}), ""
```
`_handle_param_tuning`은 `is_manual = (trigger == "manual")`를 전달. 자율(cycle/weekly)은 기존 데이터 게이트 유지(LLM 날조 방지).

테스트 `test_ops_manual_directive_applies.py`: trigger="manual", has_cycle_data=False, param_overrides={"TAKE_PROFIT_PCT":8} → 게이트 통과(클램프 후 적용 후보로 남음). trigger="cycle", has_cycle_data=False → 차단.

### Fix 3 — US 시세/일봉 rate-limit 보강 + 스킵 노출 (`infra/kis_broker.py`, `main_swarm.py`)

(a) **재시도**: `us_last_price`(및 US 일봉 조회)가 KIS rate-limit(`rt_cd=1`, "초당 거래건수 초과")에 대해 **TPS 간격 sleep + 재시도**(기존 KR `_get_json` 재전송 패턴과 동일하게). 이미 broker에 rate-limit 재전송 로직이 있으면 US 경로에도 일관 적용.

(b) **스킵 노출**: `_build_orders`가 모으는 `notes`(예: "CVX: 해외 시세 조회 실패 → 제외")를 사이클 객체에 보존(`cyc.build_notes`)하고, 최종보고 생성 프롬프트에 주입 → 운용전략실장이 "왜 안 샀는지" 설명 가능. cycle_store에는 별도 컬럼 추가 없이 `final_report` 컨텍스트로만 전달(스키마 무변경).

테스트 `test_build_order_skip_notes.py`: 시세 0인 US 후보가 `notes`에 스킵 사유로 남고, 그 notes가 보고 컨텍스트에 포함되는지(빌드 함수 반환 또는 cyc 속성 확인).

### Fix 4 — 후보 사전필터 사이클예산 기준 정렬 (`main_swarm.py`)

후보 사전필터(`main_swarm.py:3509~`)의 기준을 **현금 1주(`_affordable_one_share(px, cash, total)`)** 에서 **사이클 예산 기준**으로 강화:
```python
cycle_budget_pre = cash_pre * MAX_CYCLE_BUDGET_RATIO        # 리스크부와 동일 기준
afford = (px <= 0) or (px <= cycle_budget_pre * PER_ORDER_BUDGET_OVERSHOOT)
```
사이클 예산 내(소폭 overshoot 허용) 매수 불가한 초고가주는 선정·평가 전 배제 → 조립부·리스크부와 기준 일치, LG이노텍류 헛사이클 제거. 시세 조회 실패(px≤0)는 기존대로 통과(보수).

> 주의: 리스크부 실제 사용 비율과 정확히 맞춘다(구현 시 리스크 검증부 코드 확인해 `MAX_CYCLE_BUDGET_RATIO` 또는 실제 사용 키로 일치).

테스트 `test_candidate_prefilter_cycle_budget.py`: cash=5.95M, ratio=0.10 → 1.13M 종목 배제, 50만원 종목 유지, 시세실패(0) 유지.

### Fix 5 — ops 워커 실패 가시화 (`main_swarm.py`, `infra/ops_support_worker.py`)

- 부모(`_spawn_ops_support_worker`): 서브프로세스 **종료코드·stderr 캡처**해 비정상 종료 시 `logger.warning`.
- 워커(`run()`): 전역 try/except로 감싸 예외 시 ops_history에 `summary="ops 진단 실패: <에러>"` 기록(무음 방지).
- 실제 1회 수동 실행(`python3.11 infra/ops_support_worker.py --cycle <id>`류)으로 근본 크래시 원인 진단 후 필요한 핀포인트 수정.

---

## B. 운용지원실장 적극화

### B1. 시간당 쓰로틀 (`main_swarm.py`)

- per-uid 마커 `data/<uid>/.ops_last_run`(epoch 텍스트). 사이클 끝(report 단계 후)에서 `_spawn_ops_support_worker` 호출 전:
```python
if now - last_ops_run(uid) >= OPS_THROTTLE_SEC:   # 기본 3600
    spawn(); write_marker(uid, now)
```
- `OPS_THROTTLE_SEC` 상수(config). 매 사이클 spawn 폐지 → 시간당 1회.
- 워커 프롬프트 강화: "최근 사이클(들)을 근거로 **개선 가능한 파라미터를 적극 제안**하라. 근거가 있으면 '변경 없음' 대신 구체 조정을 제시(범위 내)."

테스트 `test_ops_hourly_throttle.py`: 순수 헬퍼 `ops_due(last_ts, now, throttle)` — 1시간 미만 False, 이상 True, 마커 없음(0) True.

### B2. 주간 매우 적극 (`infra/weekly_review.py`, `infra/ops_support_worker.py`)

- 기존 토 06:00 KST 트리거 유지. weekly 트리거 프롬프트를 "**최근 7일 실데이터로 47개 전 튜넌키를 점검하고, 근거가 충분하면 큰 폭으로 조정(범위 클램프 적용)**"으로 강화.
- weekly는 `has_cycle_data=True`(7일 cycles) → 데이터 게이트 통과. 재시작 안 함.

---

## C. 가드레일 — 파라미터별 범위 클램프 (`config.py`, `infra/ops_support_worker.py`)

- `STRATEGY_KEY_META[key]`에 선택적 `"min"`/`"max"` 추가(수치형). bool/enum은 기존 타입 검증.
- 순수함수 `clamp_overrides(overrides) -> (clamped, notes)`:
  - 수치형: `max(min_, min(max_, v))`. 클램프되면 note.
  - bool: 0/1·true/false 정규화.
  - enum(예: POSITION_SIZING_MODE): 허용값 외면 드롭+note.
  - 미등록 키는 드롭(튜넌 화이트리스트 밖).
- `_handle_param_tuning`에서 게이트 통과 후 **적용 직전** 클램프(manual·cycle·weekly 공통). 클램프 note는 rationale·메시지에 노출.

범위 예시(구현 시 STRATEGY_KEY_META 기준 확정):
- `TAKE_PROFIT_PCT` [2, 50], `STOP_LOSS_PCT` [1, 30], `PER_ORDER_BUDGET_RATIO` [0.01, 0.5],
  `MAX_CYCLE_BUDGET_RATIO` [0.05, 1.0], `CONSERVATIVE_STOCK_RATIO` [0.05, 0.6], `MIN_QUANT_SCORE` [0, 10],
  `MAX_BUY_NAMES` [1, 20], `SIZING_TILT_STRENGTH` [0, 1], `SIZING_MAX_TILT` [1, 5] 등.

테스트 `test_ops_param_clamp.py`: 범위 밖 값 클램프, bool 정규화, enum 비허용 드롭, 미등록 키 드롭, note 생성.

---

## 테스트 전략 (TDD, `python3.11 -m pytest`)

신규:
- `test_macro_gate_stock_weight.py`
- `test_ops_manual_directive_applies.py`
- `test_ops_param_clamp.py`
- `test_ops_hourly_throttle.py`
- `test_build_order_skip_notes.py`
- `test_candidate_prefilter_cycle_budget.py`

기존 회귀 유지(특히 `test_strategy_params_runtime.py`, `test_ops_param_catalog.py`, `test_model_default_hermes.py`, 매크로 게이트 관련). 전체 그린 확인 후 배포(재시작 1회).

## 배포

코드 변경 → `sudo systemctl restart arquant.service` 1회(사장 확인 후). 재시작 후 두 계정 1사이클씩 라이브 검증:
- 모의: 후보>0·퀀트리포트 생성·매크로 게이트 미발동 확인.
- 실거래: US 시세 재시도 동작·스킵사유 보고 노출·고가주 사전배제 확인.
- ops: 시간당 1회 spawn·ops_history 기록 재개·manual 지시 즉시 반영(클램프) 확인.

## 리스크

- 매크로 게이트 수식 변경이 실거래 매수 빈도를 높일 수 있음(올바른 방향이나, 첫 사이클 모니터링 필수).
- ops 적극화 + manual 면제로 파라미터가 더 자주 바뀜 → 범위 클램프가 폭주 방지. 클램프 범위는 보수적으로 시작.
- US 재시도 추가가 사이클 지연을 늘릴 수 있음(TPS 간격) → 재시도 횟수 상한·짧은 백오프로 제한.
