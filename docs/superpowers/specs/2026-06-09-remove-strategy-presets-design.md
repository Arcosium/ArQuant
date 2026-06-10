# 전략 프리셋 제거 + 현재 설정값 가시화 — 설계

- 날짜: 2026-06-09
- 작성: 사장 지시 브레인스토밍
- 상태: 설계 확정 대기 (사장 검토 전)

## 배경 & 목표

라이브 트레이딩 "전략 프리셋"(빌트인 5종 + 사용자 저장 프리셋 + 저장/삭제)을
전부 제거하고, 단일 **기본값 세트**로 대체한다. '전략' 탭은 프리셋 선택 UI 대신
**현재 적용 중인 파라미터를 그룹별로 명확히 보여주고 그 자리에서 편집**하는
단일 패널로 단순화한다.

부수적으로, 프리셋에 의존하던 **백테스트**를 단일 성과 백테스트로 축소하고
토요일 주간 피드백 루프에 자동 연결한다(수동 CLI는 제거).

## 결정 사항 (사장 확정)

1. **표시 방식**: 그룹별 편집 패널 — 현재값이 입력칸에 보이고 거기서 수정·적용.
2. **제거 범위**: 빌트인 5종 + 사용자 프리셋(`data/user_presets.json`) + 저장/삭제
   기능 전부 제거, 단일 기본값 세트로 대체.
3. **백테스트**: 비교 리포트 폐지 → 단일 성과 백테스트로 축소 + 토요일 자동 연결,
   수동 호출(CLI `python3.11 -m backtest.report`) 제거.

## 범위 밖 (follow-up)

- **모바일 대시보드**: 실제로는 WebView가 서버 index.html을 로드(`WebDashboardScreen`).
  따라서 전략 탭 변경은 서버 재시작만으로 자동 반영되며 APK 재빌드 불필요.
  네이티브 `StrategyPreset`/`presets`/`setStrategy`는 어느 화면도 안 쓰는 죽은
  코드(서버는 `presets` 누락 시 Kotlin 기본값 → 크래시 없음) — 정리는 차후 APK
  작업 시 함께.
- **위젯 재디자인(테두리 제거) + APK 재빌드 + 구글드라이브 업로드**: 위젯은
  네이티브라 재빌드가 있어야 반영됨. 이번엔 재빌드 없이 가기로 결정(사장 지시
  2026-06-09) → 차후 APK를 손댈 때 일괄 처리. (업로드 경로: 호스트 `rclone` +
  `gdrive:` 리모트 확보됨.)

## 상세 설계

### 1. 설정 계층 — `config.py`

- `STRATEGY_PRESETS`(5종 dict)와 `DEFAULT_STRATEGY` 상수 **제거**.
- 단일 `STRATEGY_DEFAULTS: dict` 신설 = 기존 `balanced` 프리셋의 파라미터값
  (단, `"label"` 키 제외 — 순수 파라미터만).
- `STRATEGY_TUNABLE_KEYS`, `STRATEGY_KEY_META`는 **유지**(프리셋이 아니라
  파라미터 정의/메타데이터 — 패널 렌더링·런타임 폴백에 필수).

### 2. 런타임 — `runtime.py`

- 제거: `_USER_PRESETS`, `_load_user_presets`, `_save_user_presets`,
  `save_user_preset`, `delete_user_preset`, `list_presets`, `_preset_label`.
- `_default_state()`: 베이스를 `config.STRATEGY_DEFAULTS`로,
  `name="custom"`, `label`은 active()에서 "사용자 설정" 고정.
- `set_strategy(custom=None, by="user", uid=None)`: 프리셋 이름 분기·`name` 인자
  제거 → `STRATEGY_DEFAULTS` 베이스 위에 `custom` params(알려진 키만)만 얹는 단일
  경로. 유일한 호출부 `POST /api/strategy`도 그에 맞춰 수정.
- `active(uid)`: `label="사용자 설정"` 고정, `params/since/ops_since` 유지.
- `history()` / `_append_history()`: 변경 감사 로그로 **유지**(label="사용자 설정").
- 하위호환: 기존 `data/strategy_state.json`의 `name`(예: "balanced")은 무시하고
  저장된 `params`를 그대로 사용 — 별도 마이그레이션 불필요.

### 3. API — `server/app.py`

- `GET /api/strategy`: 응답에서 `presets` 필드 제거. `active`/`history`/
  `key_meta`/`key_order` 유지.
- `POST /api/strategy`: 커스텀 params 경로만. `name` 무시 가능(하위호환).
- `POST /api/strategy/preset`, `DELETE /api/strategy/preset/{name}`
  엔드포인트 **삭제**.

### 4. 대시보드 UI — `server/static/index.html`

- "🎛️ 적용 가능 전략" 카드(`#presetList`) 및 프리셋 렌더링/`applyStrategy`/
  `deleteUserPreset`/`saveCustomPreset`/`_ppHtml`/프리셋 저장 이름·라벨 입력 +
  "프리셋으로 저장" 버튼 전부 제거.
- 기존 한 줄 덤프(`#stratNow`) + 접힌 커스터마이즈 박스(`#customCard`)를
  **항상 펼쳐진 단일 "현재 적용 설정" 패널**로 통합:
  - 헤더: `▶ 현재 적용 설정` + 적용 시각 + 운용지원 반영 표시.
  - 본문: 그룹별(사이징/리스크/매도 규칙/필터/기타) 라벨 + 현재값 입력칸 + 단위
    (`_buildCustomFields` 재사용, 활성 params로 프리필).
  - 하단: `▶ 변경값 적용`(기존 applyCustom) + `↺ 되돌리기`(현재 적용값 재로드).
- 수익률 탭 '전략' KPI 타일·상태표시(`sStrat`): "사용자 설정"으로 표기.
  (최근 재편된 KPI 레이아웃은 변경하지 않음.)

### 5. 프롬프트 · 매뉴얼

- `main_swarm.py`(L2956, L4246, L4264, L4331) · `agents/specialists.py`(L308):
  "전략 프리셋" → "전략 설정"으로 표현 수정. 로직 불변
  (N=`MAX_BUY_NAMES`/`MAX_TRADES_PER_CYCLE`, `ALLOW_DAY_TRADING`은 이미 파라미터 구동).
- `tools/gen_manual.js`(L99, L174, L263, L279, L417): "프리셋 선택·적용·저장" →
  "전략 설정값 직접 확인·편집"으로 매뉴얼 갱신.

### 6. 백테스트 — 단일 성과 + 토요일 연결

- `backtest/engine.py`:
  - `run_backtest(params: dict, prices, start_cash, lookback)` — `preset_name`
    인자 제거, `p = config.STRATEGY_PRESETS[preset_name]` → `p = params`.
    사용 키: `PER_ORDER_BUDGET_RATIO`, `CONSERVATIVE_STOCK_RATIO`,
    `MAX_CYCLE_BUDGET_RATIO`, `MIN_CASH_BUFFER`, `CONSERVATIVE_MDD`,
    `TAKE_PROFIT_PCT`, `STOP_LOSS_PCT` (그대로).
  - `_metrics`: `preset`/`label` → `name`(예: "현재 설정") 기반으로 일반화.
  - 모듈 docstring의 "프리셋 비교" 서술 → "단일 설정 성과 측정"으로 수정.
- `backtest/report.py` **삭제**(수동 비교 CLI). `backtest/__init__.py` docstring 수정.
- `infra/weekly_review.py`:
  - `build_review_summary(uid)`에 `backtest` 섹션 추가 — 현재 적용 파라미터를
    `{k: runtime.get(k, uid=uid) for k in STRATEGY_TUNABLE_KEYS}`(프로필
    오버라이드 반영된 효과적 값)로 모아 `load_prices()` + `run_backtest(params)`
    실행, 지표(return/MDD/sharpe/trades/winrate) 포함. CSV 없으면
    `{"available": False}` 로 graceful degrade.
  - `build_review_message`: 백테스트 성과 한 줄 추가(직접 보고 원칙).
  - directive는 이미 `json.dumps(summary)`를 ops 워커에 주입하므로, summary에
    backtest를 넣으면 운용지원실장 입력으로 자동 전달됨.

### 7. 테스트 (TDD)

기존 프리셋 의존 테스트를 새 계약으로 갱신:

- `tests/test_backtest.py`: `run_backtest(params_dict)` 시그니처로 변경.
  - determinism: 동일 params → 동일 출력.
  - risk-monotonicity: 리터럴 방어형/공격형 params dict 두 개로 MDD 단조성 검증
    (config 프리셋 의존 제거).
  - metrics 키 존재 검증.
- `tests/test_runtime_per_uid.py`: `STRATEGY_PRESETS`/`DEFAULT_STRATEGY`/
  `list_presets` 참조 제거 → `STRATEGY_DEFAULTS` 단일 세트·per-uid set_strategy(custom).
- `tests/test_strategy_params_config.py`, `tests/test_deterministic_score_config.py`,
  `tests/test_institutional_params_config.py`, `tests/test_thesis_advisory_only.py`:
  `for name, preset in STRATEGY_PRESETS.items()` 순회 → `STRATEGY_DEFAULTS` 단일
  dict 검증으로 변경(모든 TUNABLE_KEYS 포함·타입 등).
- `tests/conftest.py`: 프리셋 참조 fixture 갱신.
- 신규: `weekly_review.build_review_summary`에 backtest 섹션이 포함되는지(또는
  CSV 부재 시 graceful) 테스트.
- 순서: 변경 후 실패 확인 → 구현 → `python3.11 -m pytest` 전체 통과.

### 8. 배포

전부 구현·테스트 통과 후 `sudo systemctl restart arquant.service` 1회
+ (루프 OFF 상태면) 대시보드 '시작'. 부분 배포 안 함.

## 데이터 영향

- `data/user_presets.json`: 더 이상 읽지 않음(사용자 저장 프리셋 소멸 — 승인됨).
- `data/strategy_state.json`: 기존 params 그대로 사용, name 무시.
- `data/strategy_history.json`: 감사 로그 보존(기존 엔트리 유지).
