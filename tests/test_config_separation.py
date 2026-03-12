import app.cli as cli_module
from app.openclaw.client import get_scene_anchor_image_api_config, get_scene_anchor_review_api_config


def test_scene_anchor_config_prefers_dedicated_env(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "legacy-key")
    monkeypatch.setenv("GEMINI_BASE_URL", "https://legacy.example/v1beta")
    monkeypatch.setenv("GEMINI_MODEL", "legacy-model")
    monkeypatch.setenv("SCENE_ANCHOR_IMAGE_API_KEY", "image-key")
    monkeypatch.setenv("SCENE_ANCHOR_IMAGE_BASE_URL", "https://image.example")
    monkeypatch.setenv("SCENE_ANCHOR_REVIEW_API_KEY", "review-key")
    monkeypatch.setenv("SCENE_ANCHOR_REVIEW_BASE_URL", "https://review.example/v1beta")
    monkeypatch.setenv("SCENE_ANCHOR_REVIEW_MODEL", "review-model")

    image_api_key, image_base_url = get_scene_anchor_image_api_config()
    review_api_key, review_base_url, review_model = get_scene_anchor_review_api_config()

    assert image_api_key == "image-key"
    assert image_base_url == "https://image.example"
    assert review_api_key == "review-key"
    assert review_base_url == "https://review.example/v1beta"
    assert review_model == "review-model"


def test_audit_config_prefers_dedicated_env_with_legacy_fallback(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "legacy-key")
    monkeypatch.setenv("GEMINI_BASE_URL", "https://legacy.example/v1beta")
    monkeypatch.setenv("GEMINI_MODEL", "legacy-model")
    monkeypatch.delenv("GEMINI_AUDIT_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_AUDIT_BASE_URL", raising=False)
    monkeypatch.delenv("GEMINI_AUDIT_MODEL", raising=False)

    legacy_config = cli_module._build_gemini_audit_config()
    assert legacy_config.api_key == "legacy-key"
    assert legacy_config.base_url == "https://legacy.example/v1beta"
    assert legacy_config.model_name == "legacy-model"

    monkeypatch.setenv("GEMINI_AUDIT_API_KEY", "audit-key")
    monkeypatch.setenv("GEMINI_AUDIT_BASE_URL", "https://audit.example/v1beta")
    monkeypatch.setenv("GEMINI_AUDIT_MODEL", "audit-model")

    dedicated_config = cli_module._build_gemini_audit_config()
    assert dedicated_config.api_key == "audit-key"
    assert dedicated_config.base_url == "https://audit.example/v1beta"
    assert dedicated_config.model_name == "audit-model"
