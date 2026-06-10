"""운용전략실장 → 주식운용실장 rename + 원자재운용실장 UI/roster (사장 지시 2026-06-09)."""
import io


def _read(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def test_no_old_persona_name_in_source():
    for path in ("main_swarm.py", "server/app.py", "server/static/index.html",
                 "agents/specialists.py", "infra/standing_directives.py", "infra/error_log.py",
                 "tools/market_data.py", "tools/gen_manual.js"):
        src = _read(path)
        assert "운용전략실장" not in src, f"{path} 에 옛 페르소나명 잔존"


def test_orchestrator_named_stock_manager():
    import main_swarm
    src = _read("main_swarm.py")
    assert "주식운용실장" in src
    # 내부 role 키는 chief_orchestrator 유지(불필요한 변경 회피)
    assert "chief_orchestrator" in src


def test_app_roster_has_sleeve_managers():
    src = _read("server/app.py")
    assert "주식운용실장" in src and "원자재운용실장" in src and "채권운용실장" in src
    assert "commodity_manager" in src
    assert "운용전략실장" not in src


def test_sidebar_has_commodity_and_stock_manager():
    html = _read("server/static/index.html")
    assert "주식운용실장" in html and "원자재운용실장" in html
    assert "운용전략실장" not in html
