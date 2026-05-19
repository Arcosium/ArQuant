# ArQuant — 프로필 시스템 · 회원 관리 · 복구 인자 변경 설계

작성일: 2026-05-19 · 브랜치: 신규 `feature/profile-system` (main 분기) · 상태: 설계 승인됨

## 1. 배경 / 목적

로그인 오버홀(완료, main 머지)로 계정·복구 인프라가 갖춰졌다. 이번 작업은
운영 편의·관리 기능을 추가한다.

- 상단바의 상태 배지(IDLE 등)를 통신 로그 옆으로 이동(모바일과 통일).
- 🚪 로그아웃 버튼을 **사람 아이콘 → 프로필 모달**로 대체. 모달에서
  로그아웃·비밀번호 변경·정보(자격증명) 변경·회원 탈퇴·사장님 상시
  지시사항 관리를 수행.
- 사장님(hh09080) 단독 ADMIN. ADMIN 전용 "회원 관리"(현황·삭제·실전/모의 전환).
- 비밀번호 찾기 인증 인자 변경: 3인자 → **한국투자증권 계좌번호 + 한국투자증권 App Secret (2인자)**.

비-목표(out of scope): ADMIN 승격/강등 기능(명시적으로 제거), 다중 ADMIN,
Phase 2 멀티테넌트 격리(별도 스펙), UUP 버그(이미 별도 트랙 완료).

## 2. 사용자 결정 사항 (확정)

| 항목 | 결정 |
|---|---|
| 작업 순서 | UUP 버그 먼저(완료) → 본 기능 |
| 복구 인자 | 한투 계좌번호 + 한투 App Secret (2개) |
| 회원 탈퇴/삭제 | **완전 삭제(hard delete)**, 본인 탈퇴 시 경고창 1회 추가 |
| ADMIN 모델 | hh09080 단독. **승격/강등 기능 없음** |
| 배포 | 기능 구현까지 끝낸 뒤 arquant 1회 재시작으로 일괄 반영 |

## 3. 아키텍처 개요

기존 SPA(`server/static/index.html`) 모달 패턴 + FastAPI 세션 인증 +
`auth_store.is_admin` 게이트를 재사용한다. 프로필 모달은 index.html에
존재하므로 **안드로이드 WebView(대시보드)가 자동 반영**된다. 네이티브
코드 변경은 `LoginScreen.kt`의 복구 입력 필드 교체뿐(APK 1회 재빌드).

컴포넌트 경계:
- **프론트(index.html)**: 배지 위치 CSS/표시 토글, 프로필 모달 UI,
  fetch 호출. 단일 모달 컴포넌트(섹션 분리).
- **백엔드 프로필 API(`server/app.py`)**: 세션 필요 엔드포인트 묶음.
- **백엔드 ADMIN API**: `auth_store.is_admin` 게이트(비ADMIN 403).
- **auth_store**: 자격증명 변경, 계정 hard delete, 복구 2인자, 신규
  `kis_account_no_bidx` + 부팅 1회 멱등 마이그레이션.
- **안드로이드**: `LoginScreen.kt` 복구 폼 필드만 교체.

## 4. 상세 설계

### 4.1 배지 이동 (웹)
- 상단바 `#badge` 제거, 그 자리에 사람 아이콘 버튼(프로필 트리거).
- 통신 로그 헤더의 기존 `#badgeMirror`를 데스크톱에서도 표시(현재
  `@media` 모바일 한정 CSS 해제). `updateBadge()`는 이미 `#badge`·
  `#badgeMirror` 양쪽 갱신 → 로직 변경 없음(상단 배지 제거만 반영).

### 4.2 프로필 모달 (전 사용자)
헤더: 계정명 + (hh09080이면) `ADMIN` 배지. 섹션:
1. **로그아웃** — 기존 `/api/logout` 재사용.
2. **비밀번호 변경** — 현재 비번 검증(argon2) → 신규 비번 정책 재검증 →
   `password_policy_error` 통과 시 argon2 해시 갱신.
3. **정보 변경** — OpenRouter Key / KIS App Key / KIS App Secret /
   한국투자증권 계좌번호 / Base URL. 변경분만 제출. 저장 전 실검증
   (`_validate_kis`, `_validate_openrouter` 재사용), 성공 시 저장 +
   활성 계정이면 런타임 재주입(`_activate_with_policy`). bidx 동시 갱신
   (계좌번호/Secret 변경 시 복구 인덱스도 재계산).
4. **상시 지시사항 관리** — 활성 uid 기준 `infra/standing_directives.py`
   CRUD: 목록(`load`)·추가(`append_directive`)·개별 삭제(`remove_directive`).
5. **회원 탈퇴** — 경고창 1회 추가 + 비밀번호 재입력 확인 →
   **완전 삭제**: users 행 + `data/profiles/<uid>/` + 활성 세션 +
   상시 지시 파일. 활성 계정이면 세션 종료·로그아웃 처리.

엔드포인트(세션 필요, `_PUBLIC_PATHS` 아님):
- `POST /api/profile/password` `{current, new}`
- `POST /api/profile/credentials` `{openrouter_key?, kis_app_key?, kis_app_secret?, kis_account_no?, kis_base_url?}`
- `GET /api/profile/directives` → 목록
- `POST /api/profile/directives` `{text}` → 추가
- `DELETE /api/profile/directives/{id}` → 삭제
- `POST /api/profile/delete_account` `{password}` → 본인 완전 삭제

### 4.3 ADMIN 회원 관리 (hh09080만)
모달 내 별도 섹션(비ADMIN엔 미렌더 + 서버 재검증).
- **현황**: 아이디·생성일·최근 로그인·실전/모의·ADMIN 여부.
- **회원 삭제**: 완전 삭제. **안전장치 — 본인 계정/단독 ADMIN 자기삭제 차단.**
- **실전/모의 전환**: 회원의 KIS Base URL을 실전↔모의로 전환.
  ⚠️ **제약(중요)**: KIS는 모의/실전 App Key·Secret이 서로 다르다
  (`kis_broker.is_mock` 분기). Base URL만 바꾸면 키 불일치로 주문이
  전부 실패한다. 따라서 모드 전환 UI는 (a) Base URL 전환 + (b) 해당
  모드용 KIS App Key/Secret/계좌번호를 함께 입력받아 실검증
  (`_validate_kis`) 후에만 적용한다. 검증 실패 시 전환 거부(기존 모드
  유지). 실거래 영향 → 실행 전 확인 다이얼로그 필수.
- ADMIN 승격/강등: **없음**(요청대로 제외).

엔드포인트(ADMIN 게이트, 비ADMIN 403):
- `GET /api/admin/members`
- `POST /api/admin/members/delete` `{username}` (자기/단독ADMIN 거부)
- `POST /api/admin/members/mode` `{username, mode:"live"|"paper", kis_app_key, kis_app_secret, kis_account_no}` — 모드용 키 실검증 통과 시에만 Base URL+키 동시 갱신

### 4.4 복구 인자 변경 (보안)
- 기존: `find_username_by_factors(kis_app_key, kis_app_secret, openrouter_key)`,
  `reset_password_by_factors(username, …동일3…)`.
- 변경: 인자 = **kis_account_no + kis_app_secret** (2개).
- 스키마: `users`에 `kis_account_no_bidx` 컬럼 추가. **부팅 1회 멱등
  마이그레이션**으로 기존 행 백필(기존 `migrate_passwords_and_bidx`
  패턴·sentinel 재사용 방식). 기존 `kis_app_secret_bidx` 재사용,
  `kis_app_key_bidx`/`openrouter_key_bidx`는 복구에서 미사용(컬럼은 유지).
- 함수 시그니처 변경 + 호출부(`/api/recover_id`, `/api/recover_password`)
  요청 모델·검증을 2인자로. **열거 오라클 방지 불변식 유지**(정책
  검증을 인자 매칭보다 먼저).
- 복구 UI: 웹 패널 입력칸 2개로 교체. 네이티브 `LoginScreen.kt`의
  복구 폼 필드 2개로 교체.
- ⚠️ **보안 트레이드오프(사용자 수용함)**: 계좌번호는 명세서 등에
  노출돼 비밀성이 낮다 → 복구 난이도↓, App Secret이 사실상 단일
  강(强)인자. 완화책: 기존 레이트리밋·감사로그 유지. 스펙에 명시해
  추후 재검토 가능하도록 남긴다.

### 4.5 안드로이드
- 대시보드(프로필 모달 포함)는 WebView → 서버 반영 시 자동.
- 네이티브 변경: `LoginScreen.kt` 복구 폼 입력 2개(계좌번호+App Secret),
  관련 `ArQuantApi.kt`/`AuthViewModel.kt` 요청 모델. APK 1회 재빌드
  (`/home/opc/android-build` Docker 환경).

## 5. 데이터 / 마이그레이션

- `users.kis_account_no_bidx TEXT`(신규). 부팅 1회 멱등 백필: 기존 행의
  복호된 계좌번호 → `bidx()` 계산. 이미 채워졌으면 skip. 손상/복호
  실패 행은 스킵(로그). 기존 마이그레이션과 동일 안전 원칙(행 손상 금지).
- 회원 hard delete: `users` 행 DELETE + `data/profiles/<uid>/` 트리 삭제
  + 세션 무효화. 트랜잭션 경계: DB 삭제 성공 후 파일 삭제(파일 실패는
  로그·경고, DB 일관성 우선).

## 6. 보안 고려사항

- 모든 신규 `/api/profile/*`·`/api/admin/*`는 세션 필수, `_PUBLIC_PATHS` 제외.
- ADMIN 엔드포인트는 `auth_store.is_admin(uid)` 서버 재검증(프론트 숨김만 신뢰 X).
- 자격증명 변경은 저장 전 실검증(잘못된 키로 잠기는 것 방지).
- 회원 삭제·실전/모의 전환은 비가역/실거래 영향 → 확인 다이얼로그 +
  본인/단독ADMIN 자기삭제 차단 불변식.
- 복구 2인자 약화는 문서화된 수용 리스크. 레이트리밋·감사로그 유지.
- 비밀번호 변경/탈퇴는 현재 비밀번호 재확인 요구.

## 7. 테스트 전략 (pytest, 결정론)

- 프로필 엔드포인트: 미인증 401, 비밀번호 변경(정책·현재비번 오류),
  자격증명 변경 후 bidx 재계산, 본인 삭제 후 재로그인 불가.
- ADMIN 게이트: 비ADMIN 403, 본인/단독ADMIN 자기삭제 차단, 실전/모의 토글.
- 복구 2인자: 계좌번호+App Secret 일치 시 find/reset 성공, 불일치 실패,
  열거 오라클 불변식(정책 선검증), `kis_account_no_bidx` 마이그레이션 멱등.
- 회귀: 전체 스위트 그린 유지(현 140건).
- UI는 수동 확인(웹/모바일).

## 8. 배포

- 구현·테스트 완료 후 **arquant 1회 재시작**으로 UUP 버그 수정 + 본 기능
  일괄 반영. 부팅 시 `kis_account_no_bidx` 마이그레이션 자동 1회 실행.
- APK 재빌드(LoginScreen 변경분) → `ArQuant.apk` 갱신.
- 재시작 후 사장님 요청대로 익일 한국 장 마감까지 사이클별 모니터링.

## 9. 작업 분해(요약 — 상세는 구현 계획에서)

1. auth_store: `kis_account_no_bidx` 컬럼·마이그레이션·복구 2인자 함수.
2. 복구 엔드포인트/모델 2인자 전환 + 웹 복구 UI.
3. 프로필 백엔드 엔드포인트(비번/자격증명/지시/탈퇴).
4. ADMIN 백엔드 엔드포인트(현황/삭제/모드) + 안전 불변식.
5. index.html: 배지 이동 + 프로필 모달(공통) + ADMIN 섹션.
6. 안드로이드 LoginScreen 복구 폼 + APK 재빌드.
7. 회귀 테스트 + 전체 그린 + 일괄 배포 + 모니터링.
