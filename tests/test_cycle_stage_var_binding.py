"""회귀 (2026-06-04 라이브 디버깅): _cyc_stage_data_quant 가 macro_report 를 cyc 에서 읽지 않고
참조 → NameError 로 매 라이브 사이클 붕괴(결정론 점수 엔진의 _parse_macro_stock_pct(macro_report)).

불변식: cyc 스테이지 메서드(_cyc_stage_*)가 cyc 파생 지역변수(macro_report/news_report/quant_report)를
'참조'하면 반드시 그 함수 안에서 '할당'(macro_report = cyc.macro_report)해야 한다 — 안 그러면 전역
조회 실패(NameError). 테스트(함수 직접호출)·스모크로는 안 걸리고 라이브에서만 터지는 부류라 정적으로 못박는다.
"""
import ast
import inspect
import main_swarm

# cyc 에서 읽어와야 하는(전역이 아닌) 사이클 지역변수들 — 참조 시 같은 함수에서 할당돼야 함.
CYC_LOCAL_VARS = ("macro_report", "news_report", "quant_report")


def _stage_funcs(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name.startswith("_cyc_stage_"):
            yield node


def test_cycle_stages_bind_cyc_locals_before_use():
    tree = ast.parse(inspect.getsource(main_swarm))
    offenders = []
    for fn in _stage_funcs(tree):
        loaded = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        stored = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
        for v in CYC_LOCAL_VARS:
            if v in loaded and v not in stored:
                offenders.append(f"{fn.name} 가 {v} 를 할당 없이 참조(NameError 위험)")
    assert not offenders, "; ".join(offenders)
