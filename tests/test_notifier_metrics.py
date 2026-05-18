"""notifier/metrics 는 except 블록에서 호출된다 → '절대 예외를 던지지 않음' 을 고정.

알림이 트레이딩 흐름을 막거나, 메트릭 기록 실패가 사이클을 깨면 안 된다.
"""
import time

from infra import notifier, metrics


def test_alert_returns_true_then_dedups(tmp_path, monkeypatch):
    # 알림 로그를 임시 파일로 격리.
    monkeypatch.setattr(notifier, "_ALERT_LOG", tmp_path / "alerts.json")
    notifier._last_sent.clear()
    key = "test:dup"
    assert notifier.alert("CRITICAL", "첫 알림", "detail", dedup_key=key) is True
    # 같은 dedup_key 는 윈도우 내 억제.
    assert notifier.alert("CRITICAL", "중복", "detail", dedup_key=key) is False
    # 윈도우 0 이면 즉시 재발송 허용.
    assert notifier.alert("WARN", "재발송", "d", dedup_key=key, dedup_window_sec=0) is True


def test_alert_never_raises_on_bad_sink(monkeypatch):
    # 파일 싱크가 깨져도 예외가 호출부로 새지 않아야 한다.
    def boom(_entry):
        raise RuntimeError("disk full")

    monkeypatch.setattr(notifier, "_append_alert_log", boom)
    # 예외 없이 불리언 반환.
    assert notifier.alert("WARN", "x", "y") in (True, False)


def test_unknown_level_normalized(tmp_path, monkeypatch):
    monkeypatch.setattr(notifier, "_ALERT_LOG", tmp_path / "a.json")
    notifier._last_sent.clear()
    assert notifier.alert("nonsense-level", "t") is True


def test_metrics_helpers_never_raise(tmp_path, monkeypatch):
    monkeypatch.setattr(metrics, "_PATH", tmp_path / "m.jsonl")
    metrics.incr("orders_executed", market="KR")
    metrics.gauge("equity", 1234.5, scope="poll")
    with metrics.timer("unit_block", session="KR"):
        time.sleep(0.001)
    snap = metrics.snapshot()
    assert "orders_executed" in snap and snap["orders_executed"]["n"] >= 1
    assert "unit_block" in snap


def test_timer_records_even_on_exception(tmp_path, monkeypatch):
    monkeypatch.setattr(metrics, "_PATH", tmp_path / "m2.jsonl")
    try:
        with metrics.timer("boom_block"):
            raise ValueError("intentional")
    except ValueError:
        pass
    assert "boom_block" in metrics.snapshot()
