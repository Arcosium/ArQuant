"""운용지원실장 자가수정 가드 — 안전 불변식 회귀 테스트.

가장 중요한 불변식: **컴파일 실패 시 디스크가 변경 전과 비트 단위로 동일**.
이게 깨지면 깨진 코드가 남아 다음 supervise.sh 재기동에서 전체 다운된다.
"""
import infra.ops_guards as w
from infra import notifier


def _isolate(tmp_path, monkeypatch, rel="agents/specialists.py"):
    monkeypatch.setattr(w, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(w, "BACKUP_ROOT", tmp_path / "backup")
    monkeypatch.setattr(w, "ALLOWED_EDITS", {rel})
    # 롤백 경로의 notifier.alert 가 저장소의 실제 data/alerts.json 을 오염시키지
    # 않도록 알림 로그도 tmp 로 격리 (테스트 간 누수 차단).
    monkeypatch.setattr(notifier, "_ALERT_LOG", tmp_path / "alerts.json")
    f = tmp_path / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    return f


def test_compile_failure_triggers_full_rollback(tmp_path, monkeypatch):
    f = _isolate(tmp_path, monkeypatch)
    original = "VALUE = 1\n"
    f.write_text(original, encoding="utf-8")
    plan = {"changes": [{"file": "agents/specialists.py", "action": "modify",
                         "search": "VALUE = 1", "replace": "VALUE = (1  # 문법깨짐"}]}
    res = w.apply_changes(plan)
    assert res["compile_errors"], "구문 오류가 감지돼야 함"
    assert res["rolled_back"], "롤백이 수행돼야 함"
    assert res["applied"] == [], "롤백 후 효과적 적용은 0건"
    assert f.read_text(encoding="utf-8") == original, "디스크가 비트 단위로 원복돼야 함"


def test_valid_modify_applies_and_persists(tmp_path, monkeypatch):
    f = _isolate(tmp_path, monkeypatch)
    f.write_text("VALUE = 1\n", encoding="utf-8")
    plan = {"changes": [{"file": "agents/specialists.py", "action": "modify",
                         "search": "VALUE = 1", "replace": "VALUE = 2"}]}
    res = w.apply_changes(plan)
    assert not res["compile_errors"] and not res["rolled_back"]
    assert any("modify" in a for a in res["applied"])
    assert "VALUE = 2" in f.read_text(encoding="utf-8")


def test_oversized_change_is_rejected(tmp_path, monkeypatch):
    f = _isolate(tmp_path, monkeypatch)
    f.write_text("X = 1\n", encoding="utf-8")
    huge = "Y = 0\n" * 20000  # > MAX_CHANGE_BYTES
    plan = {"changes": [{"file": "agents/specialists.py", "action": "modify",
                         "search": "X = 1", "replace": huge}]}
    res = w.apply_changes(plan)
    assert any("변경 크기" in r for r in res["rejected"])
    assert f.read_text(encoding="utf-8") == "X = 1\n"  # 미변경


def test_too_many_new_lines_rejected(tmp_path, monkeypatch):
    f = _isolate(tmp_path, monkeypatch)
    f.write_text("A = 1\n", encoding="utf-8")
    many = "A = 1" + ("\n# pad" * (w.MAX_NET_NEW_LINES + 5))
    plan = {"changes": [{"file": "agents/specialists.py", "action": "modify",
                         "search": "A = 1", "replace": many}]}
    res = w.apply_changes(plan)
    assert any("순증 라인" in r for r in res["rejected"])


def test_forbidden_pattern_still_blocks_order_methods(tmp_path, monkeypatch):
    # 가드 회귀 방지: kis_broker 의 주문 메서드 패턴은 여전히 차단돼야 함.
    monkeypatch.setattr(w, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(w, "ALLOWED_EDITS", {"infra/kis_broker.py"})
    plan = {"changes": [{"file": "infra/kis_broker.py", "action": "modify",
                         "search": "def kr_buy(self):", "replace": "def kr_buy(self): pass"}]}
    res = w.apply_changes(plan)
    assert any("보호 패턴 위반" in r for r in res["rejected"])
