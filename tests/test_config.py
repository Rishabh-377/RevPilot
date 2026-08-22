"""
Tests for application configuration.
"""

from backend.config import Settings


class TestConfig:
    """Verify configuration defaults."""

    def test_default_config(self) -> None:
        settings = Settings()
        assert settings.app_name == "RevPilot"
        assert settings.debug is False
        assert settings.database_url == "sqlite:///./revpilot.db"
        assert settings.max_retries_per_payment == 3
        assert settings.max_retries_per_card_24h == 5

    def test_bandit_defaults(self) -> None:
        settings = Settings()
        assert settings.bandit_prior_alpha == 1.0
        assert settings.bandit_prior_beta == 1.0

    def test_guardrail_defaults(self) -> None:
        settings = Settings()
        assert settings.guardrail_cooloff_seconds == 300

    def test_llm_defaults(self) -> None:
        settings = Settings()
        assert settings.llm_provider == "gemini"
        assert settings.llm_model == "gemini-2.5-flash"
