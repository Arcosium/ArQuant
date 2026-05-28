"""Per-uid data paths. Every user's runtime state lives under data/<uid>/."""
from __future__ import annotations
from pathlib import Path

_DATA_DIR = Path(__file__).parent.parent / "data"


def user_dir(uid: int) -> Path:
    d = _DATA_DIR / str(int(uid))
    d.mkdir(parents=True, exist_ok=True)
    return d


def equity_path(uid: int) -> Path:
    return user_dir(uid) / "equity_curve.json"


def trade_log_path(uid: int) -> Path:
    return user_dir(uid) / "trade_log.json"


def token_path(uid: int) -> Path:
    return user_dir(uid) / "kis_token.json"


def cost_rollup_path(uid: int) -> Path:
    return user_dir(uid) / "cost_rollup.json"


def running_marker(uid: int) -> Path:
    return user_dir(uid) / ".running"


def position_thesis_path(uid: int) -> Path:
    return user_dir(uid) / "position_thesis.json"
