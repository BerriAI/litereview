from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "sqlite:///./data/litereview.db"
    secret_key: str = ""
    admin_token: str = ""
    admin_users: str = ""
    public_url: str = "http://localhost:8000"
    github_app_id: str = ""
    github_private_key: str = ""
    github_webhook_secret: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""
    github_token: str = ""  # Explicit development-only integration testing.
    modal_token_id: str = ""
    modal_token_secret: str = ""
    modal_app_name: str = "litereview"
    concurrency: int = 8
    job_timeout: int = 600
    max_rounds: int = 60
    dispatcher_enabled: bool = True

    @property
    def admins(self):
        return {u.strip().lower() for u in self.admin_users.split(",") if u.strip()}


@lru_cache
def settings():
    return Settings()
