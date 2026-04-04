from services.api.config import Settings


def test_settings_debug_accepts_release_string():
    settings = Settings(debug="release", environment="development")
    assert settings.debug is False


def test_settings_debug_accepts_debug_string():
    settings = Settings(debug="debug", environment="development")
    assert settings.debug is True


def test_settings_rejects_default_epsilon_secret_in_production():
    import pytest

    with pytest.raises(ValueError, match="EPSILON_WEBHOOK_SECRET"):
        Settings(
            environment="production",
            jwt_secret="x" * 32,
            pii_encryption_key="secure-pii-key",
            internal_webhook_secret="y" * 32,
            epsilon_webhook_secret="dev-secret-change-me",
        )


def test_settings_rejects_blank_pii_key():
    import pytest

    with pytest.raises(ValueError, match="Security secret cannot be blank"):
        Settings(pii_encryption_key="   ")
