# QuantInSight — 프로젝트 지침

KIS 증권사를 통해 KR + US 주식을 자동 매매하는 LLM 오케스트레이션 트레이딩 시스템.
멀티에이전트 "스웜"이 시장 감시 사이클을 돌며 매매를 결정하고 KIS에 실주문을 낸다.
구 ArQuant — 폴더·호환 심링크 모두 제거됨, 이제 QuantInSight 경로만 유효.
(2026-08-24 전역 ~/.claude/CLAUDE.md 에서 이관. 공통 규칙(한국어 응답·비밀 격리·위험 작업 확인)은 전역과 ~/projects/AGENTS.md 에 있다.)

## 명령어

```bash
# 테스트 — GB10 에선 python3(3.12) 사용 (3.11엔 pytest 없음; 구 Oracle 지침이 python3.11 이었음)
python3 -m pytest                    # 전체 (tests/test_*.py)
python3 -m pytest tests/test_x.py    # 단일 파일

# 코드 변경 반영 = 서비스 재시작 (이게 사실상의 "배포")
sudo systemctl restart quantinsight.service  # 모든 코드 변경 반영
sudo systemctl status quantinsight.service   # 헬스 확인 (port 8500)
# 수동/로컬: ./start_server.sh (cloudflared 터널까지 관리), ./supervise.sh = watchdog

# 안드로이드 APK
./build_apk.sh                       # QEMU docker 빌드 (~20분)
```

## 아키텍처

- `main_swarm.py` — 핵심 오케스트레이터. 시장 세션별로 연속 사이클을 돌린다
  (US_TRADING / KR_TRADING / KR_PRE_MARKET / OFF_HOURS).
- `agents/specialists.py` — 6개 에이전트: 전략리서치(macro) · 계량분석(quant) · 뉴스분석(news)
  · trader · 사후관리(post-manager, 매도) · 운용지원(ops_support, **파라미터 튜닝만** 가능).
- `agents/guardrails.py` — 리스크 가드 + 주문 초안 검증.
- `infra/kis_broker.py` — KIS API. **국내(KR)** (`kr_holdings`/`kr_buy`/`kr_sell`, 6자리 코드)와
  **미국/해외(US)** (`_overseas_holdings`/`us_buy`/`us_sell`, 티커)가 **별개 경로**다.
- `server/app.py` — FastAPI (port 8500). asyncio task 로 스웜을 띄우고, 대시보드 +
  WebSocket 피드를 제공한다(안드로이드 앱 `arquant_mobile/` 가 소비).
- `config.py` — 전략 기본 파라미터 + 플래그 (`LIVE_TRADING=True` 면 실주문 발생).
- `runtime.py` — 라이브 파라미터 오버라이드: `runtime.get("KEY")` 가 override-or-default 반환.
  **재시작 없이** 반영된다(대시보드 '전략' 탭).
- `infra/`, `tools/` — 각종 스토어(cycle/auth/ops), 시세 데이터, DART, 뉴스, 퀀트 지표.

## 함정 (Gotchas)

- **테스트는 `python3`(3.12)** 로 돌린다. `python3.11` 엔 pytest 가 없고 `python` 은 아예 없다
  (둘 다 구 Oracle 시절 지침의 잔재 — 2026-08-05 실측).
- **KR/US 비대칭 버그류**: KR 경로로만 구현하고 US 경로를 빠뜨리는 패턴이 반복된다.
  "KR엔 되는데 US엔 안 됨" 증상이면 **입력→평가→주문조립→실행** 전 단계를 따라가며
  `kr_*` 만 부르고 `_overseas_*`/`us_*` 를 빠뜨린 곳이 없는지 확인하라. KRW 한도와 USD 평가액을 섞지 말 것.
- **자동 Backup 커밋**: 외부 도구가 주기적으로 `git add -A` + `Backup:` 커밋(및 push)을 한다.
  내 변경이 거기 휩쓸려 들어가니 "nothing to commit" 에 당황하지 말 것. 커밋은 사장이 명시적으로 요청할 때만 직접 한다.
- **KIS 결제 과도기 잔고 글리치**: 결제 전환 구간엔 잔고 필드가 순간적으로 빈값/0 으로 읽힐 수 있다
  (빈 보유·cash=0·D1/D2=0). 자산곡선이 "튄다"류 증상은 대개 이것이니, raw KIS 필드를 직접 읽어 확인한 뒤 실제로 취급하라.
- **`data/` 는 대부분 gitignore** 됨(equity_curve.json, *.db, 토큰, auth). git 에 있다고 가정하지 말 것.

## 규칙

- 실주문은 절대 조용히 누락하면 안 된다 — fail-closed 보다 다중 폴백 전송을 우선한다.
