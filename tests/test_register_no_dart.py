import re
from pathlib import Path

def test_dart_module_uses_only_env_config_key():
    src = Path("tools/dart_disclosure.py").read_text(encoding="utf-8")
    # only source of the key is config.OPENDART_API_KEY
    assert "from config import OPENDART_API_KEY" in src
    # no hardcoded crtfc_key literal (would be a leaked key)
    assert not re.search(r'crtfc_key["\']\s*:\s*["\'][A-Za-z0-9]{20,}', src)

def test_registerreq_dart_label_ignored_by_upsert_call():
    src = Path("server/app.py").read_text(encoding="utf-8")
    m = re.search(r"uid = auth_store\.upsert_user\((.*?)\)", src, re.S)
    assert m and "dart_key=" not in m.group(1) and "label=" not in m.group(1)
