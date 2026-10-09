import pytest
from pydantic import ValidationError

from app.config import Settings


def _staging_settings(**overrides):
    values = {
        "_env_file": None,
        "app_env": "staging",
        "jwt_secret": "test-staging-secret-with-enough-entropy-0123456789",
        "email_provider": "resend",
        "resend_api_key": "re_test_key",
        "email_mock_mode": False,
        "auth_rate_limit_redis_url": "rediss://default:token@example.upstash.io:6379",
        "auth_allow_mock_google": False,
        "cors_origins": "https://finmate-staging.example.com",
    }
    values.update(overrides)
    return Settings(**values)


def test_staging_requires_shared_redis_rate_limiter():
    with pytest.raises(ValidationError, match="AUTH_RATE_LIMIT_REDIS_URL"):
        _staging_settings(auth_rate_limit_redis_url=None)


def test_staging_resend_requires_api_key():
    with pytest.raises(ValidationError, match="RESEND_API_KEY"):
        _staging_settings(resend_api_key=None)


def test_staging_rejects_email_mock_mode():
    with pytest.raises(ValidationError, match="EMAIL_MOCK_MODE"):
        _staging_settings(email_mock_mode=True)


def test_staging_rejects_mock_google_auth():
    with pytest.raises(ValidationError, match="AUTH_ALLOW_MOCK_GOOGLE"):
        _staging_settings(auth_allow_mock_google=True)


def test_staging_accepts_complete_resend_and_redis_configuration():
    settings = _staging_settings()
    assert settings.app_env == "staging"
    assert settings.email_provider == "resend"
    assert settings.auth_rate_limit_redis_url.startswith("rediss://")


def test_staging_rejects_short_jwt_secret():
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        _staging_settings(jwt_secret="too-short")


def test_staging_rejects_non_https_cors_origins():
    with pytest.raises(ValidationError, match="CORS_ORIGINS"):
        _staging_settings(cors_origins="http://localhost:5173")
