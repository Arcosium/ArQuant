"""One-shot Phase 2 migration: move legacy single-tenant global state files into a
timestamped backup dir so every uid starts fresh (owner decision 2026-05-26).
Idempotent — a sentinel marks completion so reboots don't re-run it."""
from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("MIGRATION")
_DATA_DIR = Path(__file__).parent.parent / "data"

# Legacy global files that became per-uid in Phase 2.
_GLOBAL_FILES = ("equity_curve.json", "strategy_state.json", "strategy_history.json",
                 "cycles.db", "kis_token.json", "claude_response.json",
                 "api_cost_rollup.json")
_SENTINEL = ".phase2_migrated"


def migrate_once() -> bool:
    """Returns True if it performed the migration, False if already done / nothing to move."""
    sentinel = _DATA_DIR / _SENTINEL
    if sentinel.exists():
        return False
    present = [f for f in _GLOBAL_FILES if (_DATA_DIR / f).exists()]
    if not present:
        sentinel.write_text(datetime.now().isoformat(), encoding="utf-8")
        return False
    backup = _DATA_DIR / f"_migration_backup_{datetime.now():%Y%m%d_%H%M%S}"
    backup.mkdir(parents=True, exist_ok=True)
    for f in present:
        shutil.move(str(_DATA_DIR / f), str(backup / f))
        logger.info("Phase2 마이그레이션: %s → %s", f, backup.name)
    sentinel.write_text(datetime.now().isoformat(), encoding="utf-8")
    logger.info("Phase2 마이그레이션 완료 — %d개 파일 백업, 각 uid 빈 상태로 시작", len(present))
    return True
