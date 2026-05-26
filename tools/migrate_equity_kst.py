#!/usr/bin/env python3.11
"""일회성 마이그레이션 — data/equity_curve.json 의 기존 ts(UTC naive)를 KST 로 보정.

배경(사장 보고 2026-05-21): record_equity 가 과거 `datetime.now()`(OCI=UTC) 로 ts 를
저장해, 차트 라벨을 만드는 _ts_to_kst 가 '이미 KST'로 간주하면서 가로축이 9시간 어긋났다.
record_equity 는 이제 KST 로 저장하도록 고쳤고, 이 스크립트는 '그 이전에 쌓인' 공백포맷
엔트리에만 +9h 를 더해 과거 구간까지 KST 로 맞춘다.

멱등성: data/.equity_tz_migrated 마커가 있으면 아무 것도 하지 않는다(중복 보정 방지).
ISO(오프셋 포함) 엔트리는 _ts_to_kst 가 이미 정확히 변환하므로 건드리지 않는다.

배포 절차상 '서버 정지 후 / 새 코드로 기동 전'에 1회 실행해야 레이스가 없다.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
EQ = DATA / "equity_curve.json"
MARKER = DATA / ".equity_tz_migrated"


def main() -> int:
    if MARKER.exists():
        print("[migrate_equity_kst] 이미 마이그레이션됨 — 스킵")
        return 0
    if not EQ.exists():
        MARKER.write_text("no-file\n", encoding="utf-8")
        print("[migrate_equity_kst] equity_curve.json 없음 — 마커만 생성")
        return 0
    try:
        data = json.loads(EQ.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[migrate_equity_kst] 읽기 실패 — 중단: {e}")
        return 1
    if not isinstance(data, list):
        MARKER.write_text("not-list\n", encoding="utf-8")
        return 0

    shifted = 0
    for e in data:
        ts = str(e.get("ts", "")).strip()
        # 공백 포맷(오프셋/‘T’ 없음)만 = record_equity 가 UTC 로 저장했던 레거시 엔트리
        if not ts or "T" in ts or "+" in ts or ts.endswith("Z"):
            continue
        try:
            dt = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        e["ts"] = (dt + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")
        shifted += 1

    EQ.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    MARKER.write_text(f"shifted={shifted}\n", encoding="utf-8")
    print(f"[migrate_equity_kst] 완료 — {shifted}건 +9h(UTC→KST) 보정")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
