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
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"

    # Auth. The three application logins; passwords are env-overridable so a
    # deployment never has to ship the documented demo values.
    auth_secret_key: str = "sodexo-poc-change-me-in-production"
    auth_token_expire_minutes: int = 480
    # One per account, numbered rather than named after a role: a setting
    # called AUTH_ADMIN_PASSWORD tells anyone reading the config which
    # credential is worth attacking.
    #
    # Deliberately simple, because these are demo credentials read off a
    # sheet and typed by hand - a long passphrase gets mistyped and the
    # first thing a client sees is a failed login. They are weak by design
    # and must be overridden for anything beyond a demo.
    auth_password_1: str = "Priya@123"
    auth_password_2: str = "Daniel@123"
    auth_password_3: str = "Sofia@123"
    auth_password_4: str = "Thomas@123"
    auth_password_5: str = "Grace@123"
    # When true, every /api route (other than login) requires a valid token,
    # not just the UI. Off by default so scripts and /docs keep working as-is.
    auth_protect_api: bool = False

    # Single-tenant POC behaviour: wipe all data on startup so one client never
    # sees another's upload, and a restart is a guaranteed clean slate. Turn
    # this off for local development, where re-ingesting on every reload hurts.
    reset_on_startup: bool = True

    # Training concurrency. Series are independent, so they fit in parallel.
    # 0 means "decide from the CPU count"; the fitters spawn their own BLAS and
    # TensorFlow threads, so more workers than cores makes it slower rather
    # than faster. Pin explicitly on a constrained host - Azure Container Apps
    # gives this app 2 vCPU.
    training_max_workers: int = 0

    # Which slice of the 13-model portfolio to fit. Default is every model:
    # a leaderboard that silently omits three of thirteen invites the
    # question "why is LSTM missing", and the comparison is the point of
    # running a portfolio at all.
    #   full - every model, always. The default.
    #   fast - skip the three slow ones (LSTM, Auto ARIMA, Auto ARIMA+exog).
    #          They measured at 88% of training time while winning on no
    #          series, so this is the lever to pull when a large multi-series
    #          upload would otherwise take hours.
    #   auto - full up to training_fast_mode_threshold series, fast above it.
    # Skipped models still appear as not_applicable with the reason, so
    # nothing is ever silently dropped.
    training_profile: str = "full"
    training_fast_mode_threshold: int = 6


@lru_cache
def get_settings() -> Settings:
    return Settings()
