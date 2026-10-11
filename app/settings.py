import os
from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "sqlite:///./data/litereview.db"
    secret_key: str = ""
    admin_token: str = ""
    admin_users: str = ""  # Legacy GitHub-only sign-in allowlist; ignored for Google workspace access.
    admin_emails: str = ""  # Verified Google emails allowed to view workspace spend.
    google_sso_required: bool = False
    google_client_id: str = ""
    google_client_secret: str = ""
    google_allowed_domains: str = ""
    sso_session_seconds: int = Field(default=43200, ge=1)
    public_url: str = ""
    gateway_url: str = ""
    gateway_api_key: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    github_app_id: str = ""
    github_private_key: str = ""
    github_webhook_secret: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""
    github_token: str = ""  # Explicit development-only integration testing.
    github_organization: str = ""
    modal_token_id: str = ""
    modal_token_secret: str = ""
    modal_app_name: str = "litereview"
    concurrency: int = Field(default=0, ge=0)  # 0 launches on demand without an app-level cap.
    dispatcher_enabled: bool = True
    litereview_trace_endpoint: str = ""
    litereview_trace_api_key: str = ""
    litereview_trace_content: bool = False

    @model_validator(mode="after")
    def resolve_public_url(self):
        self.public_url = (
            self.public_url or os.environ.get("RENDER_EXTERNAL_URL") or "http://localhost:8000"
        ).rstrip("/")
        return self

    @property
    def admins(self):
        return {u.strip().lower() for u in self.admin_users.split(",") if u.strip()}

    @property
    def spend_admin_emails(self):
        return {email.strip().lower() for email in self.admin_emails.split(",") if email.strip()}

    @property
    def sso_required(self):
        return self.google_sso_required or bool(self.google_client_id)

    @property
    def google_domains(self):
        return {v.strip().lower() for v in self.google_allowed_domains.split(",") if v.strip()}


@lru_cache
def settings():
    return Settings()
