"""KIS 호출 간격 적응적 자가완화 — 사장 지시 2026-06-17(11시 US 사이클 점검).

배경: US 세션 내내 '초당 거래건수 초과(EGW00201)' 거부가 수십 회 폭주했다. 재전송으로
자가복구는 되나, 고정 _min_interval(실전 0.06s≈15TPS)이 KIS 실측 한도를 넘는 버스트
구간에서 매 호출이 거부→재전송→로그폭주를 반복한다.
교정: rate-limit 거부를 만나면 _min_interval 을 곱셈 상향(상한까지)해 버스트 동안 스스로
간격을 벌리고, 거부 없이 호출이 흐르면 base 로 점감 복귀한다(평소 속도 유지).
"""
from infra.kis_broker import KISBroker


def _broker():
    return KISBroker({"kis_app_key": "k", "kis_app_secret": "s",
                      "kis_account_no": "1234567801",
                      "kis_base_url": "https://openapi.koreainvestment.com:9443"})


def test_rate_limit_bumps_interval_capped():
    """거부를 거듭 만나면 간격이 상향되되 _RATE_MAX_INTERVAL 을 넘지 않는다."""
    b = _broker()
    base = b._rate_base
    b._note_rate_limited()
    assert b._min_interval > base
    for _ in range(50):
        b._note_rate_limited()
    assert b._min_interval <= b._RATE_MAX_INTERVAL + 1e-9


def test_interval_decays_back_to_base():
    """거부가 그치면(_decay 가 반복되면) 간격이 base 로 복귀한다(아래로 안 뚫음)."""
    b = _broker()
    base = b._rate_base
    for _ in range(5):
        b._note_rate_limited()
    assert b._min_interval > base
    for _ in range(200):
        b._decay_interval()
    assert abs(b._min_interval - base) < 1e-9


def test_decay_noop_when_at_base():
    """base 상태에서 점감은 무동작(과도한 가속 방지)."""
    b = _broker()
    base = b._rate_base
    b._decay_interval()
    assert b._min_interval == base
