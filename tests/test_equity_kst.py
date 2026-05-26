"""record_equity 가 평가금액 포인트의 ts 를 KST 로 저장해야 함 — 회귀 테스트.

버그(사장 보고 2026-05-21): 수익률 탭 '평가금액 추이' 차트의 가로축 시각이 UTC 로
9시간 어긋나 표시됐다. 원인은 record_equity 가 `datetime.now()`(타임존 없는 UTC —
OCI 서버는 UTC 로 동작)로 ts 를 저장하는데, 차트 라벨을 만드는 _ts_to_kst 는 공백
포맷 ts 를 '이미 KST'로 간주(변환 안 함)하기 때문. 그래서 UTC 시각이 KST 라벨로 찍혔다.

요구 동작: record_equity 가 저장하는 ts 는 KST wall-clock 이어야 한다.
"""
import json
from datetime import datetime

import main_swarm


def test_record_equity_stores_kst_not_utc(tmp_path, monkeypatch):
    # Phase 2 멀티테넌트: record_equity 는 유저별 equity_path 를 첫 인자로 받는다.
    ep = tmp_path / "equity_curve.json"
    main_swarm.record_equity(ep, {"ok": True, "total_eval": 1_000_000.0, "cash": 500_000.0,
                              "pnl_ratio": 0.0}, "test")
    data = json.loads(ep.read_text(encoding="utf-8"))
    assert data, "포인트가 기록돼야 한다"
    ts = data[-1]["ts"]
    now_kst = datetime.now(main_swarm.KST).strftime("%Y-%m-%d %H:%M:%S")
    # 같은 날짜+시(hour)면 KST 로 저장된 것 (UTC 라면 9시간 차이로 시가 다름)
    assert ts[:13] == now_kst[:13], f"ts 가 KST 가 아님: stored={ts} expected≈{now_kst}"


def test_record_equity_stores_indices_and_passthrough(tmp_path, monkeypatch):
    """사장 지시 2026-05-21: 5분 폴링이 잔고와 함께 수집한 KOSPI·NASDAQ 가 equity 포인트에
    저장되고, get_equity_series 가 그대로 통과시켜야 벤치마크가 동일 타임스탬프로 그려진다."""
    ep = tmp_path / "equity_curve.json"
    main_swarm.record_equity(ep, {"ok": True, "total_eval": 1_000_000.0, "cash": 0.0, "pnl_ratio": 0.0},
                             "poll", kospi=2700.5, nasdaq=480.25)
    data = json.loads(ep.read_text(encoding="utf-8"))
    assert data[-1]["kospi"] == 2700.5 and data[-1]["nasdaq"] == 480.25
    # get_equity_series passthrough (daily 뷰는 거래시간 필터가 없어 항상 포함)
    sd = main_swarm.get_equity_series(ep, view="daily")
    assert sd and sd[-1].get("kospi") == 2700.5 and sd[-1].get("nasdaq") == 480.25
