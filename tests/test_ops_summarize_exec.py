from infra.ops_support_worker import _summarize_exec_results


def test_handles_json_string_input():
    # cycle_store 는 orders_executed 를 JSON 문자열로 저장한다 — 문자열도 처리해야 한다.
    assert _summarize_exec_results("[]") == "없음"
    s = '[{"ticker":"CVX","side":"buy","qty":1,"filled":true}]'
    assert "CVX buy x1: 체결확인" in _summarize_exec_results(s)


def test_handles_none_and_empty():
    assert _summarize_exec_results(None) == "없음"
    assert _summarize_exec_results([]) == "없음"


def test_skips_non_dict_elements():
    # 깨진 데이터(문자열 원소)가 섞여도 죽지 않는다.
    assert _summarize_exec_results(["garbage", {"ticker": "AAPL", "side": "buy", "qty": 2, "accepted": True}]) \
        == "AAPL buy x2: 접수—체결폴링중(실패아님)"


def test_list_of_dicts_unchanged():
    out = _summarize_exec_results([{"ticker": "NVDA", "side": "sell", "qty": 3}])
    assert "NVDA sell x3: 미접수·반려" in out
