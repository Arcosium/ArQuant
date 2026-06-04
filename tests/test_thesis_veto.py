"""펀드기획실장 거부권 게이트 (apply_thesis_veto) — 결정론적 무계획 단타 차단.

사장 지시 2026-05-29: 펀드기획팀장 → 펀드기획실장 승격. thesis 가 프롬프트 조언에 그치지
않고, 사후관리실장의 매도결정 직후 결정론적으로 검증한다.

사장 지시 2026-06-04: 거부권을 손실 종목까지 '노이즈 밴드'로 확대한다. 데이트레이딩 허용
(ALLOW_DAY_TRADING) 여부와 무관하게 발동한다 — 그래야 라이브에서 실제로 churn 을 막는다.

거부권 규칙(보류=거부권 발동):
  손절가 위 + 목표가 아래 + 계획보유 미경과 + 손익이 노이즈밴드(±band) 이내
  (= 진입가×(1-band) ≤ 현재가 < 목표가). 소폭이익뿐 아니라 소폭손실(-band 이내)도 차단.
비차단:
  - 손절가 터치(현재가 ≤ 손절가) → 매도 허용 (손절 절대 비차단)
  - 목표가 도달(현재가 ≥ 목표가) → 매도 허용
  - 계획 보유기간 경과 → 매도 허용
  - 진짜 손실(현재가 < 진입가×(1-band)) → 손절성 매도이므로 비차단
  - THESIS_VETO_ENABLED=False → 거부권 비활성
  - 평가 불가(현재가/진입가 0) → PM 결정 존중(비차단)
"""
from agents.specialists import apply_thesis_veto

NOW = "2026-05-29 02:00:00"


def _thesis(entry=100.0, target=110.0, stop=95.0, hold=48.0, ts="2026-05-29 01:00:00"):
    return {"entry_price": entry, "target_price": target, "stop_price": stop,
            "planned_hold_hours": hold, "entry_ts": ts, "entry_reason": "테스트"}


def _hold(code="XOM", cur=101.0, name="엑손모빌"):
    return {"code": code, "cur_price": cur, "name": name}


def test_premature_small_profit_flip_is_vetoed():
    """계획기간(48h) 중 1h만 보유, +1% 소폭이익, 손절·목표 미해당 → 보유로 오버라이드."""
    theses = {"XOM": _thesis()}
    holdings = [_hold(cur=101.0)]
    out, vetoes = apply_thesis_veto(theses, holdings, {"XOM": "전량"}, NOW)
    assert out["XOM"] == "보유"
    assert len(vetoes) == 1 and "거부권" in vetoes[0]


def test_small_loss_within_noise_band_is_vetoed():
    """계획기간 중 -1.5% 소폭손실(밴드 3% 이내, 손절 95 미터치) → 보유로 오버라이드.

    오늘(2026-06-04) 036800 -1.5% / 230240 -0.6% churn 이 정확히 이 밴드에 걸려 차단되어야 한다."""
    theses = {"XOM": _thesis()}            # entry 100, stop 95(-5%), target 110
    holdings = [_hold(cur=98.5)]           # -1.5% → 밴드(±3%) 이내
    out, vetoes = apply_thesis_veto(theses, holdings, {"XOM": "전량"}, NOW)
    assert out["XOM"] == "보유"
    assert len(vetoes) == 1 and "거부권" in vetoes[0]


def test_real_loss_beyond_noise_band_is_not_vetoed():
    """-4% 실손실(밴드 3% 밖, 손절 95 미터치) → 손절성 매도이므로 비차단."""
    theses = {"XOM": _thesis()}
    holdings = [_hold(cur=96.0)]           # -4% → 밴드 밖, 손절 95 위
    out, vetoes = apply_thesis_veto(theses, holdings, {"XOM": "전량"}, NOW)
    assert out["XOM"] == "전량" and vetoes == []


def test_stop_hit_is_not_vetoed():
    """손절가(95) 터치 → 매도 허용, 거부권 발동 안 함."""
    theses = {"XOM": _thesis()}
    holdings = [_hold(cur=94.0)]
    out, vetoes = apply_thesis_veto(theses, holdings, {"XOM": "전량"}, NOW)
    assert out["XOM"] == "전량" and vetoes == []


def test_target_reached_is_not_vetoed():
    """목표가(110) 도달 → 매도 허용."""
    theses = {"XOM": _thesis()}
    holdings = [_hold(cur=111.0)]
    out, vetoes = apply_thesis_veto(theses, holdings, {"XOM": "절반"}, NOW)
    assert out["XOM"] == "절반" and vetoes == []


def test_planned_hold_elapsed_is_not_vetoed():
    """계획 보유기간(2h) 경과 → 매도 허용."""
    theses = {"XOM": _thesis(hold=2.0, ts="2026-05-28 20:00:00")}  # 6h 경과
    holdings = [_hold(cur=101.0)]
    out, vetoes = apply_thesis_veto(theses, holdings, {"XOM": "전량"}, NOW)
    assert out["XOM"] == "전량" and vetoes == []


def test_noise_band_pct_configurable():
    """노이즈밴드 폭은 파라미터로 조절된다 — 좁히면 같은 손익도 밴드 밖이 되어 매도 허용."""
    theses = {"XOM": _thesis()}
    holdings = [_hold(cur=98.5)]           # -1.5%
    # 좁은 밴드(1%): -1.5%는 밴드 밖 → 손절성 매도 허용
    out, v = apply_thesis_veto(theses, holdings, {"XOM": "전량"}, NOW, noise_band_pct=0.01)
    assert out["XOM"] == "전량" and v == []
    # 기본 밴드(3%): -1.5%는 밴드 이내 → 보류
    out2, v2 = apply_thesis_veto(theses, holdings, {"XOM": "전량"}, NOW, noise_band_pct=0.03)
    assert out2["XOM"] == "보유" and len(v2) == 1


def test_disabled_flag_disables_veto():
    theses = {"XOM": _thesis()}
    holdings = [_hold(cur=101.0)]
    out, vetoes = apply_thesis_veto(theses, holdings, {"XOM": "전량"}, NOW, enabled=False)
    assert out["XOM"] == "전량" and vetoes == []


def test_no_thesis_means_no_veto():
    """thesis 없는 종목은 거부권 대상 아님 (PM 결정 그대로)."""
    out, vetoes = apply_thesis_veto({}, [_hold(cur=101.0)], {"XOM": "전량"}, NOW)
    assert out["XOM"] == "전량" and vetoes == []


def test_hold_directive_unchanged():
    """이미 '보유' 결정은 건드리지 않고 메시지도 없다."""
    theses = {"XOM": _thesis()}
    out, vetoes = apply_thesis_veto(theses, [_hold(cur=101.0)], {"XOM": "보유"}, NOW)
    assert out["XOM"] == "보유" and vetoes == []


def test_missing_price_respects_pm_decision():
    """현재가 0(평가 불가) → 거부권 발동 안 함 (PM 결정 존중)."""
    theses = {"XOM": _thesis()}
    out, vetoes = apply_thesis_veto(theses, [_hold(cur=0.0)], {"XOM": "전량"}, NOW)
    assert out["XOM"] == "전량" and vetoes == []


def test_multiple_holdings_mixed():
    """여러 종목 혼합: 단타만 보류, 나머지는 그대로."""
    theses = {"XOM": _thesis(), "AAA": _thesis(entry=50.0, target=55.0, stop=47.0)}
    holdings = [_hold("XOM", cur=101.0), _hold("AAA", cur=46.0, name="에이")]  # AAA 손절 터치
    out, vetoes = apply_thesis_veto(theses, holdings, {"XOM": "전량", "AAA": "전량"}, NOW)
    assert out["XOM"] == "보유"   # 단타 → 보류
    assert out["AAA"] == "전량"   # 손절 → 허용
    assert len(vetoes) == 1
