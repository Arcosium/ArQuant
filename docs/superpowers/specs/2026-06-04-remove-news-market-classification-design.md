# 뉴스 시장 분류 서브시스템 전체 제거 — 설계

- 날짜: 2026-06-04
- 사장 지시: "뉴스 피드 국내/미국/공통 구분 없애라. 뉴스 분류기도 필요없다. Admin에서 지워라." → 전체 제거 승인.
- 배경: [[arquant-data-dir-and-news-pipeline]] 단일 풀 전환 후 시장 분류는 죽은 코드. 표시·분류기·Admin·내부 로그/튜너/주간리뷰 섹션까지 사슬로 제거.

## 제거 목록
1. **index.html**: 🇰🇷국내/🇺🇸미국/🌐공통 필터 버튼 + 기사 시장 뱃지·테두리색·필터(2 렌더러). 제목+시각만.
2. **tools/news_monitor.py**: `classify_market`·`llm_classify_articles`·`_CLASSIFIER_SYSTEM`·`reclassify_in_history` 삭제, article dict의 `"market"` 태깅 제거.
3. **config.py**: `MODEL_ASSIGNMENTS["news_classifier"]`·`AGENT_MAX_TOKENS["news_classifier"]` 제거.
4. **server/app.py**: 모델배정 로스터 `"news_classifier"` 라벨 제거.
5. **infra/admin_config.py**: 뉴스 분류기 관련 주석 정리.
6. **infra/news_classifier_log.py**·**infra/news_weight_tuner.py**: 모듈 삭제.
7. **main_swarm.py**: import에서 `news_classifier_log` 제거, `classify_articles`/`record` 기록 블록 제거.
8. **infra/weekly_review.py**: 뉴스 분류 통계 섹션(news_classifier_log·news_weight_tuner 사용) 제거.
9. **tests**: `test_news_weight_tuner.py` 삭제, `test_model_override_tools.py`(llm_classify_articles)·`test_model_default_hermes.py`(news_classifier 모델) 갱신.

## 유지(무관)
- 주문 조립 `order["market"]`(KR/US 주문 경로) — 뉴스 분류와 별개.
- 단일 뉴스 풀(`_pending_news`), `seed_pending_news`, `pick_cycle_news` — 그대로.

## 검증
- python3.11 전체 통과(삭제 모듈 import·테스트 잔재 0). 재시작 후 뉴스 탭에 시장 구분 없음·에이전트 사이클 정상·에러 0.
