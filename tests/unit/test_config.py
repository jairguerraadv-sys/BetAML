from services.api.config import Settings


def test_settings_debug_accepts_release_string():
    settings = Settings(debug="release", environment="development")
    assert settings.debug is False


def test_settings_debug_accepts_debug_string():
    settings = Settings(debug="debug", environment="development")
    assert settings.debug is True
