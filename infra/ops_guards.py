"""Arquant — 운용지원실장 자가수정 안전 가드 (RETIRED 기능 보존용).

사장 지시 2026-05-20 으로 운용지원실장의 **코드 자가수정 기능은 폐지**되었다
(운용지원실장은 이제 프로필 한정 전략 파라미터(param_overrides) 만 조정한다 —
infra/ops_support_worker.py 참조). 그러나 자가수정 가드의 **안전 불변식**(컴파일 실패
시 비트 단위 롤백, 보호 파일/패턴 차단)은 향후 기능 복구 가능성과 회귀 테스트
(test_ops_worker_guards.py)를 위해 이 모듈에 격리·보존한다.

이 모듈은 **실행 경로에서 호출되지 않는다.** import 하는 곳은 회귀 테스트뿐이다.
"""
from __future__ import annotations
import re
import subprocess
import py_compile
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Any, Tuple

KST = timezone(timedelta(hours=9))
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKUP_ROOT = PROJECT_ROOT / "backup"

logger = logging.getLogger("OPS.guards")

# ─── Safety guards (LLM CANNOT override) ──────────────────────────────────
# Files the worker MUST NEVER write to, under any circumstance.
FORBIDDEN_FILES = {
    "infra/ops_support_worker.py",     # the running worker
    "infra/ops_guards.py",             # these guards themselves
    ".env",
    ".gitignore",
    "data/kis_token.json",
    "data/cycles.db",
    "data/equity_curve.json",
    "data/strategy_history.json",
    "claude_response.json",
    "start_server.sh",
    "stop_server.sh",
    "supervise.sh",
}

# Regex patterns the worker MUST NEVER modify, even if the file is otherwise editable.
# If any FORBIDDEN_PATTERN matches the `search` string OR the `replace` string, the change is rejected.
FORBIDDEN_PATTERNS = [
    ("config.py", re.compile(r"KIS_APP_KEY|KIS_APP_SECRET|KIS_ACCOUNT_NO")),
    ("infra/kis_broker.py", re.compile(r"\b(place_order|kr_buy|kr_sell|us_buy|us_sell|bond_buy|futures_buy)\b")),
    ("main_swarm.py", re.compile(r"\b(_run_analysis_cycle|_build_orders|start_continuous)\b")),
    ("infra/cycle_store.py", re.compile(r"CREATE TABLE|INSERT INTO cycles")),
]

# Files the worker IS allowed to edit (whitelist). Anything outside this list is rejected.
ALLOWED_EDITS = {
    "tools/news_monitor.py", "tools/market_data.py", "tools/quant_indicators.py",
    "tools/coresight_rag.py", "tools/dart_disclosure.py", "tools/global_search.py",
    "tools/naver_search.py",
    "agents/specialists.py", "agents/guardrails.py", "agents/base_agent.py",
    "infra/kis_broker.py",        # selectively — order methods are regex-protected
    "main_swarm.py",              # selectively — core loop methods are regex-protected
    "config.py",                  # selectively — credentials are regex-protected
    "server/app.py",
    "server/static/index.html",
}

# LLM 이 작은 search 앵커로 파일 전체를 갈아엎는 것을 막는다 — 변경은 '국소 조정' 이어야 한다.
MAX_CHANGE_BYTES = 24_000          # 단일 change 의 replace/content 최대 바이트
MAX_NET_NEW_LINES = 400            # modify 1건이 늘릴 수 있는 순 라인 수 상한


def _backup_path(target: Path) -> Path:
    """target 의 백업 경로를 backup/<원본 상대경로>.bak.<타임스탬프> 로 생성."""
    rel = Path(target).resolve().relative_to(PROJECT_ROOT)
    bdir = BACKUP_ROOT / rel.parent
    bdir.mkdir(parents=True, exist_ok=True)
    return bdir / f"{rel.name}.bak.{datetime.now(KST).strftime('%Y%m%d_%H%M%S')}"


def _list_backups(target: Path) -> List[Path]:
    """target 의 기존 백업들을 오래된→최신 순으로 반환 (backup/ 폴더 기준)."""
    rel = Path(target).resolve().relative_to(PROJECT_ROOT)
    bdir = BACKUP_ROOT / rel.parent
    return sorted(bdir.glob(rel.name + ".bak.*")) if bdir.exists() else []


def _violates_pattern(rel_path: str, payload: str) -> Optional[str]:
    """Return the failing pattern description if `payload` violates any FORBIDDEN_PATTERN for `rel_path`."""
    for fpath, rx in FORBIDDEN_PATTERNS:
        if rel_path == fpath and rx.search(payload or ""):
            return f"{fpath} 보호 패턴 매칭: {rx.pattern}"
    return None


def apply_changes(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Apply each change with hard-guard checks. Returns
    {applied, rejected, compile_errors, rolled_back}. Never raises.

    안전 불변식 (사장 피드백 2026-05-18): 최종 컴파일 검증이 실패하면 이
    함수가 만든 모든 변경을 **백업에서 비트 단위로 원복**한다 — 깨진 코드를
    디스크에 남겨 다음 supervise.sh 재기동에서 전체 다운되던 문제를 차단.
    """
    applied: List[str] = []
    rejected: List[str] = []
    # 롤백 원장: (target, backup_path|None, created_bool) — 적용 순서대로.
    ledger: List[Tuple[Path, Optional[Path], bool]] = []
    for ch in (plan.get("changes") or []):
        rel = (ch.get("file") or "").strip().lstrip("/").replace("\\", "/")
        action = (ch.get("action") or "modify").strip().lower()
        desc = ch.get("description") or ch.get("summary") or action

        # ── Guard 1: forbidden files (self, secrets, dbs, scripts) ──
        if rel in FORBIDDEN_FILES:
            rejected.append(f"❌ {rel}: 보호 파일 — 절대 수정 불가 ({desc})")
            continue
        # ── Guard 2: whitelist ──
        if rel not in ALLOWED_EDITS:
            rejected.append(f"❌ {rel}: 화이트리스트 외 — 수정 거부 ({desc})")
            continue
        # ── Guard 3: path containment (no traversal) ──
        try:
            target = (PROJECT_ROOT / rel).resolve()
            target.relative_to(PROJECT_ROOT)
        except Exception:
            rejected.append(f"❌ {rel}: 프로젝트 루트 밖 경로 — 거부")
            continue

        search = ch.get("search") or ""
        replace = ch.get("replace") or ch.get("content") or ""

        # ── Guard 4: forbidden patterns in either search OR replace ──
        bad = _violates_pattern(rel, search) or _violates_pattern(rel, replace)
        if bad:
            rejected.append(f"❌ {rel}: 보호 패턴 위반 — {bad}")
            continue

        # ── Guard 5: change-size cap (작은 앵커로 파일 전체 갈아엎기 차단) ──
        payload = replace
        if len(payload.encode("utf-8")) > MAX_CHANGE_BYTES:
            rejected.append(f"❌ {rel}: 변경 크기 {len(payload.encode('utf-8'))}B > "
                            f"한도 {MAX_CHANGE_BYTES}B — 국소 변경만 허용 ({desc})")
            continue
        if action == "modify":
            net_new = replace.count("\n") - search.count("\n")
            if net_new > MAX_NET_NEW_LINES:
                rejected.append(f"❌ {rel}: 순증 라인 {net_new} > 한도 "
                                f"{MAX_NET_NEW_LINES} — 대규모 재작성 거부 ({desc})")
                continue

        try:
            if action == "modify":
                if not search:
                    rejected.append(f"❌ {rel}: modify에 search 비어있음")
                    continue
                content = target.read_text(encoding="utf-8")
                cnt = content.count(search)
                if cnt == 0:
                    rejected.append(f"❌ {rel}: search 문자열 미발견 ({desc})")
                    continue
                if cnt > 1:
                    rejected.append(f"❌ {rel}: search가 {cnt}회 등장 — 모호함 (정확히 1회여야 함)")
                    continue
                # backup — backup/ 폴더에 원본 디렉터리 구조를 미러링하여 저장
                backup = _backup_path(target)
                backup.write_text(content, encoding="utf-8")
                target.write_text(content.replace(search, replace, 1), encoding="utf-8")
                ledger.append((target, backup, False))
                applied.append(f"✅ {rel}: modify ({desc}) [백업 {backup.relative_to(PROJECT_ROOT)}]")
            elif action == "append":
                if not replace:
                    rejected.append(f"❌ {rel}: append에 추가 내용 비어있음")
                    continue
                _existed = target.exists()
                before = target.read_text(encoding="utf-8") if _existed else ""
                backup = _backup_path(target) if _existed else None
                if backup is not None:
                    backup.write_text(before, encoding="utf-8")
                with target.open("a", encoding="utf-8") as f:
                    f.write("\n" + replace)
                ledger.append((target, backup, not _existed))
                _bk = f" [백업 {backup.relative_to(PROJECT_ROOT)}]" if backup else " [신규파일]"
                applied.append(f"✅ {rel}: append ({desc}){_bk}")
            elif action == "create":
                if target.exists():
                    rejected.append(f"❌ {rel}: 이미 존재 — create 거부 (modify로 바꾸세요)")
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(replace or "", encoding="utf-8")
                ledger.append((target, None, True))
                applied.append(f"✅ {rel}: create ({desc})")
            else:
                rejected.append(f"❌ {rel}: 알 수 없는 action={action}")
        except Exception as e:
            rejected.append(f"❌ {rel}: 적용 예외 — {e}")

    # ── Final compile check on any modified .py file ──
    bad_compile = []
    for entry in applied:
        m = re.match(r"^✅ (\S+):", entry)
        if not m:
            continue
        rel = m.group(1)
        if rel.endswith(".py"):
            try:
                py_compile.compile(str(PROJECT_ROOT / rel), doraise=True)
            except py_compile.PyCompileError as e:
                bad_compile.append(f"⚠ {rel}: 구문 오류 — {e}")

    # ── 컴파일 실패 시 전면 롤백 (안전 불변식) ──
    rolled_back: List[str] = []
    if bad_compile:
        for target, backup, created in reversed(ledger):
            try:
                rel = target.relative_to(PROJECT_ROOT)
                if created:
                    if target.exists():
                        target.unlink()
                    rolled_back.append(f"↩ {rel}: 신규 파일 삭제")
                elif backup is not None and backup.exists():
                    target.write_text(backup.read_text(encoding="utf-8"), encoding="utf-8")
                    rolled_back.append(f"↩ {rel}: 백업에서 원복")
                else:
                    rolled_back.append(f"⚠ {rel}: 백업 없음 — 원복 불가(수동 확인 필요)")
            except Exception as e:  # 롤백 실패도 절대 raise 안 함
                rolled_back.append(f"⚠ {target}: 롤백 예외 — {e}")
        rejected.append(
            f"🛑 컴파일 실패 {len(bad_compile)}건 → 변경 {len(ledger)}건 전면 롤백 "
            f"(디스크 원복 완료, 서버 재시작 안 함)")
        try:
            from infra import notifier
            notifier.alert("CRITICAL", "운용지원실장 자가수정 롤백",
                           "; ".join(bad_compile)[:1500],
                           dedup_key="ops_self_edit_rollback")
        except Exception as e:
            logger.warning(f"롤백 알림 실패: {e}")
        applied = []  # 효과적으로 적용된 변경 없음 → 호출부의 재시작 조건 미충족

    return {"applied": applied, "rejected": rejected,
            "compile_errors": bad_compile, "rolled_back": rolled_back}


def restart_server() -> str:
    """Spawn start_server.sh detached so this worker can exit cleanly while the server bounces.
    Server reboots auto-resume the watch loop if RESUME_ON_BOOT marker exists (see main_swarm)."""
    try:
        marker = PROJECT_ROOT / "data" / ".resume_on_boot"
        marker.write_text(datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
        subprocess.Popen(
            ["bash", "-c", f"sleep 2 && bash {PROJECT_ROOT}/start_server.sh >> /tmp/arquant_restart.log 2>&1"],
            start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return "🔄 서버 재시작 예약됨 (2초 후) + RESUME_ON_BOOT 마커 설정"
    except Exception as e:
        return f"⚠ 서버 재시작 실패: {e}"
