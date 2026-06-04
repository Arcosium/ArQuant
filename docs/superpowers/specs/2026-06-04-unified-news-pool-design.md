# 뉴스 풀 단일화 (KR/US 구분 폐지) — 설계

- 날짜: 2026-06-04
- 사장 지시: "US/KR 뉴스 구분 없애자. 뉴스 쌓기만 하고, 뉴스분석팀장이 알아서 구분. US 시간에 US 종목 살 게 없으면 운용전략실장이 컷." + "market 필드도 보존하지 말고, 사이클마다 풀 전체를 뉴스분석팀장에게 전달하고 분석 후 비움. 뉴스 없을 땐 최신 20개 전달."
- 배경: 현재 KR/US 2풀 분리 + 세션별 매칭 풀만 소비. US 세션에 KR 테마 뉴스가 KR 풀에 쌓여 있어도 못 보거나, 반대로 LLM이 엉뚱한 시장 종목을 골라 헛도는 사례(hh0908 cycle#157: 이란發 KR 방산주 픽 → 세션필터 전원탈락 → 매수0).

## 동작 변경

1. **단일 풀** `self._pending_news`(KR/US 2풀 폐지). 크롤 뉴스는 시장 구분·미러링·market 기반 분기 없이 한 풀에 적재. 제목 중복만 dedup.
2. **market 분류 제거**: 풀 라우팅이 유일 목적이던 `llm_classify_articles` 크롤별 호출 제거(LLM 비용↓). market 필드는 풀/분석에서 미사용(뉴스분석팀장이 직접 시장 구분).
3. **소비**: 사이클마다 풀 전체를 뉴스분석팀장에게 전달 → 분석 후 `clear()`. 세션 무관.
4. **빈 풀 폴백**: 풀이 비면 `news_monitor.get_recent_articles(20)`로 최신 20개 전달 → 뉴스 없이 헛도는 사이클 방지. (히스토리까지 완전히 비면 그때만 sell_only.)
5. **뉴스분석팀장 프롬프트**: "전체 누적 뉴스. 현재 세션=US(미국 장) — KR/US 네가 구분 표기, 지금 매매 가능한 시장(US) 종목 우선 분석, 반대 시장 뉴스는 맥락 정리"로 변경.
6. **운용전략실장**: 변경 없음 — US 세션 US티커만 선정(세션 하드필터 main_swarm:3479 유지), 살 게 없으면 컷(후보 0 → 매수 0).
7. **재시작 시드**: `seed_pending_news`(단일 리스트) 사용.
8. **상태 메시지**: "KR 누적 N / US 누적 M" → "누적 N건".
9. **무관**: `/api/news`는 monitor history 직접 조회라 영향 없음. 주문 조립의 order["market"]은 별개(유지).

## 테스트 가능 시드(순수 함수)

- `seed_pending_news(articles, now_iso, window_min=90) -> list`: crawled_at 윈도우 내 기사 단일 리스트(시장 분기 없음).
- `pick_cycle_news(pending, recent_fallback, fallback_n=20) -> (list, used_fallback: bool)`: 풀 있으면 그대로(False), 비면 최신 N개 폴백(True).

## 동작 변화 (의도됨)
빈 풀 폴백으로 정기 사이클이 거의 항상 뉴스 분석+매수 평가를 돌린다("신규 뉴스 0건 → 매도만" 사이클 사실상 소멸; 히스토리 완전 공백일 때만 sell_only). churn은 MIN_QUANT_SCORE 게이트·thesis 거부권·보유종목 스킵이 차단.

## 테스트
- `seed_pending_news`: 윈도우 내/외, crawled_at 결측 제외, 단일 리스트(시장 분기 없음).
- `pick_cycle_news`: 풀 있음→그대로·False / 풀 없음→최신20·True / 둘다 없음→[]·True.
- 회귀: 기존 `test_news_seed_on_restart.py`를 단일 리스트 API로 갱신.

## 배포·검증
- python3.11 전체 통과 후 `.running` 마커 → 재시작 1회 → hh09080(uid1·실거래)·hh0908(uid2·모의) 각 한 사이클 관찰·디버깅.
- 확인: 단일 풀 소비·비움, 빈 풀이면 최신20 폴백, 뉴스분석팀장이 KR/US 구분 표기, US세션 US픽(또는 컷), 에러 0.
