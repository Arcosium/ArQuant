import ast
import re
from pathlib import Path


def _upsert_user_kwargs() -> list[str]:
    """server/app.py 의 auth_store.upsert_user(...) 호출에 넘기는 keyword 인자 이름들."""
    src = Path("server/app.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "upsert_user"):
            return [kw.arg for kw in node.keywords]
    raise AssertionError("server/app.py 에서 upsert_user 호출을 찾지 못함")


def test_dart_module_uses_only_env_config_key():
    src = Path("tools/dart_disclosure.py").read_text(encoding="utf-8")
    assert "from config import OPENDART_API_KEY" in src
    # crtfc_key 에 20자+ 리터럴이 직접 박히면(키 유출) 차단
    assert not re.search(r'crtfc_key["\']\s*:\s*["\'][A-Za-z0-9]{20,}', src)


def test_registerreq_dart_label_ignored_by_upsert_call():
    kwargs = _upsert_user_kwargs()
    assert kwargs, "upsert_user 는 keyword 인자로 호출되어야 함"
    assert "dart_key" not in kwargs, f"dart_key 를 upsert_user 에 넘기면 안 됨; got: {kwargs}"
    assert "label" not in kwargs, f"label 을 upsert_user 에 넘기면 안 됨; got: {kwargs}"
