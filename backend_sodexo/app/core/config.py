from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env.sodexo"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "sqlite:///./data/sodexo.db"
    mock_api_base_url: str = "http://127.0.0.1:8200"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"

    # Auth. The three application logins; passwords are env-overridable so a
    # deployment never has to ship the documented demo values.
    auth_secret_key: str = "sodexo-poc-change-me-in-production"
    auth_token_expire_minutes: int = 480
    auth_admin_password: str = "Admin@2026"
    auth_manager_password: str = "Manager@2026"
    auth_analyst_password: str = "Analyst@2026"
    # When true, every /api route (other than login) requires a valid token,
    # not just the UI. Off by default so scripts and /docs keep working as-is.
    auth_protect_api: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
