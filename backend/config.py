"""
RevPilot Configuration
======================

Centralized settings using pydantic-settings.
All values can be overridden via environment variables prefixed with REVPILOT_.
"""

from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_prefix="REVPILOT_")

    app_name: str = "RevPilot"
    debug: bool = False
    database_url: str = "sqlite:///./revpilot.db"

    # Guardrail limits
    max_retries_per_payment: int = 3
    max_retries_per_card_24h: int = 5
    guardrail_cooloff_seconds: int = 300

    # Bandit priors
    bandit_prior_alpha: float = 1.0
    bandit_prior_beta: float = 1.0

    # LLM configuration (for semantic failure diagnosis — never for financial decisions)
    llm_provider: str = "gemini"
    llm_model: str = "gemini-2.5-flash"
    gemini_api_key: Optional[str] = None
    llm_timeout_seconds: float = 5.0
    llm_max_retries: int = 2
    llm_enabled: bool = False

    # Logging
    log_level: str = "INFO"


def get_settings() -> Settings:
    """Factory function for dependency injection."""
    return Settings()
