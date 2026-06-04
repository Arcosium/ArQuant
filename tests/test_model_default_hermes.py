"""뉴스 분류기·매크로 리서처 기본 모델 회귀 방지 (사장 지시 3회 반복).

버그: admin 패널 '전역 설정 저장'이 빈 모델칸이면 model_overrides 를 통째로 비우고(set_config),
그러면 resolve_model 이 config.py 기본값(과거 alibaba/tongyi-deepresearch)으로 폴백해 뉴스 분석이
이상해졌다. 사장님이 nousresearch/hermes-4-70b 를 세 번 지정 → 오버라이드가 또 날아가도 절대 tongyi 로
돌아가지 않도록 **config.py 기본값 자체**를 hermes 로 고정한다. 이 테스트가 그 기본값을 잠근다."""
from infra import admin_config
import config

_HERMES = "nousresearch/hermes-4-70b"


def test_config_default_is_hermes_not_tongyi():
    assert config.MODEL_ASSIGNMENTS["news_classifier"] == _HERMES
    assert config.MODEL_ASSIGNMENTS["macro_researcher"] == _HERMES
    # tongyi 로 되돌아가지 않았는지 명시적으로 확인
    assert "tongyi" not in config.MODEL_ASSIGNMENTS["news_classifier"]
    assert "tongyi" not in config.MODEL_ASSIGNMENTS["macro_researcher"]


def test_resolve_model_falls_back_to_hermes_when_override_cleared(monkeypatch):
    # 오버라이드가 비워져도(=admin 저장으로 날아가도) hermes 로 해석돼야 한다(tongyi 폴백 금지).
    monkeypatch.setattr(admin_config, "_read", lambda: {"model_overrides": {}})
    assert admin_config.resolve_model("news_classifier") == _HERMES
    assert admin_config.resolve_model("macro_researcher") == _HERMES
