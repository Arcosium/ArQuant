"""뉴스 분류 폐루프 — 결정론 진단기 (사장 피드백 2026-05-18).

`news_classifier_log` 는 사이클마다 'KR/US/BOTH 헤드라인 분포' vs '실제 후보·
매수가 어느 시장이었나' 를 적립한다. 기존 주간 피드백 루프는 이 raw 통계를
LLM(운용지원실장)에게 그대로 던지고 키워드 가중치 조정을 "제안" 하라고만
했다 — LLM 이 약하거나 JSON 이 깨지면 루프가 헛돈다.

이 모듈은 그 통계에서 **분류 분포와 실제 매매 분포의 불일치**를 결정론적으로
계산해, 사람과 LLM 모두 바로 행동할 수 있는 구체 권고 문자열로 바꾼다.
부작용 없음 · 예외 없음 · 순수 함수 → 테스트로 고정 가능.
"""
from __future__ import annotations

from typing import Dict, List


def _share(part: float, whole: float) -> float:
    return (part / whole * 100.0) if whole else 0.0


def analyze(stats: Dict) -> Dict:
    """`news_classifier_log.recent_stats()` 결과 → 진단 dict.

    반환: {
      "ok": bool,                  # 분석 가능했는가
      "headline_share": {KR,US,BOTH %},
      "bought_share":   {KR,US %},
      "findings":  [구체 권고 문자열, ...],
      "verdict":   "균형" | "조정 권고" | "데이터 부족",
    }
    """
    out = {"ok": False, "headline_share": {}, "bought_share": {},
           "findings": [], "verdict": "데이터 부족"}
    if not stats:
        out["findings"].append("최근 분류 로그가 비어 있음 — 진단 불가.")
        return out

    cycles = stats.get("cycles", 0) or 0
    hk = stats.get("headlines_kr", 0) or 0
    hu = stats.get("headlines_us", 0) or 0
    hb = stats.get("headlines_both", 0) or 0
    h_tot = hk + hu + hb
    ck = stats.get("candidates_kr", 0) or 0
    cu = stats.get("candidates_us", 0) or 0
    bk = stats.get("bought_kr", 0) or 0
    bu = stats.get("bought_us", 0) or 0
    b_tot = bk + bu

    if cycles < 3 or h_tot == 0:
        out["findings"].append(
            f"표본 부족(사이클 {cycles}건·헤드라인 {h_tot}건) — 최소 3사이클 이상 누적 후 재평가.")
        return out

    out["ok"] = True
    out["headline_share"] = {"KR": round(_share(hk, h_tot), 1),
                             "US": round(_share(hu, h_tot), 1),
                             "BOTH": round(_share(hb, h_tot), 1)}
    out["bought_share"] = {"KR": round(_share(bk, b_tot), 1) if b_tot else 0.0,
                           "US": round(_share(bu, b_tot), 1) if b_tot else 0.0}

    findings: List[str] = []
    us_h_share = _share(hu, h_tot)
    kr_h_share = _share(hk, h_tot)

    # 1) US 헤드라인은 많은데 US 진입이 전무 → 과분류 또는 진입 약화.
    if us_h_share >= 15.0 and cu == 0 and bu == 0:
        findings.append(
            f"US 헤드라인 비중 {us_h_share:.0f}% 인데 US 후보·매수 0건 → "
            f"classify_market 의 US 키워드 가중치 하향, 또는 ALLOW_US_STOCKS/"
            f"US 세션 진입 로직 점검 권고.")
    # 2) KR 헤드라인이 압도적인데 후보도 KR 편중 → US 뉴스 미탐 가능.
    if kr_h_share >= 85.0 and us_h_share < 3.0:
        findings.append(
            f"KR 헤드라인 {kr_h_share:.0f}% 로 과편중·US {us_h_share:.0f}% → "
            f"US 종목명/티커 분류 키워드가 누락됐을 가능성. classify_market 의 "
            f"US 시그널(영문 티커·미국 거시 키워드) 가중치 상향 검토.")
    # 3) 실제 매수는 한쪽인데 헤드라인 분포는 반대 → 분류·매매 디커플링.
    if b_tot >= 5:
        bu_share = _share(bu, b_tot)
        if bu_share >= 60.0 and us_h_share <= 10.0:
            findings.append(
                f"매수의 {bu_share:.0f}% 가 US 인데 US 헤드라인은 {us_h_share:.0f}% 뿐 → "
                f"US 뉴스가 과소분류되어 매크로/뉴스 컨텍스트 없이 매수 중일 수 있음. "
                f"US 분류 민감도 상향 권고.")
    # 4) 후보는 뽑는데 매수 전환이 0 → 분류 문제 아님(사이징/리스크 게이트).
    if (ck + cu) >= 10 and b_tot == 0:
        findings.append(
            f"후보 {ck + cu}건 선정됐으나 매수 0건 → 뉴스 분류가 아니라 "
            f"사이징/리스크 게이트(예수금·단일종목 한도) 쪽 점검 권고.")

    if not findings:
        findings.append(
            f"분류 분포(KR {kr_h_share:.0f}%·US {us_h_share:.0f}%)와 매매 분포가 "
            f"심한 불일치 없음 — 키워드 가중치 조정 불필요.")
        out["verdict"] = "균형"
    else:
        out["verdict"] = "조정 권고"
    out["findings"] = findings
    return out


def summary_line(stats: Dict) -> str:
    """주간 피드백 directive 에 한 줄로 끼울 사람 친화 요약."""
    a = analyze(stats)
    return f"[뉴스분류 진단 · {a['verdict']}] " + " / ".join(a["findings"])
