from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    anthropic_api_key: str | None = None
    anthropic_model: str | None = None
    anthropic_fast_model: str | None = None
    x_bearer_token: str | None = None
    x_daily_budget_usd: float = 5.0
    x_cost_per_read_usd: float = 0.005
    x_share_breaking: float = 0.5
    x_share_rival: float = 0.25
    x_share_velocity: float = 0.25
    draft_score_threshold: int = 60
    hot_score: int = 70
    list_min_score: int = 50
    breaking_max_age_min: int = 30
    topic_window_hours: int = 6
    topic_similarity: int = 82
    max_drafts_per_cycle: int = 5
    panel_user: str
    panel_password: str
    session_secret: str
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    timezone: str = "Europe/Istanbul"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_effective_settings(session) -> Settings:
    # Task 11: AppSetting kayitlari burada ayarlarin ustune yazilacak.
    return get_settings()
