from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Loaded from .env at the repo root. See .env.example for what each value means."""

    database_url: str = "sqlite:///./data/table.db"

    app_host: str = "127.0.0.1"
    app_port: int = 8010

    # Cloudflare Access (DESIGN.md §4, app/access.py). All blank = no Access in front
    # (local dev, tests): every request is Kev. On the droplet, ACCESS_TEAM_DOMAIN turns on
    # verification of Access's signed token, and then only the identities below get in.
    access_team_domain: str = ""  # e.g. "yourteam.cloudflareaccess.com"
    access_aud: str = ""  # the Table Access application's Audience (AUD) tag
    # Sign-in emails for each person, comma-separated. Both share the Table; each one's
    # Plate is theirs alone.
    kev_emails: str = ""
    beth_emails: str = ""
    # Shared household sign-ins (a joint Google account): the Table only, no Plate.
    house_emails: str = ""
    # Client ID (not the secret) of the service token this project's cloud sessions use.
    agent_service_token_id: str = ""
    # Client ID (not the secret) of the service token the "Send to Plate" iOS Shortcut
    # sends. It may only POST /api/capture, and that route also wants a person's capture
    # token, so the Access token alone files nothing.
    capture_service_token_id: str = ""

    # Web Push (DESIGN.md §9). Generate once with `python scripts/make_vapid_keys.py`.
    # Blank = push is off and the settings page says so.
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = "mailto:dev@syncyn.io"

    @property
    def kev_email_set(self) -> set[str]:
        return _email_set(self.kev_emails)

    @property
    def beth_email_set(self) -> set[str]:
        return _email_set(self.beth_emails)

    @property
    def push_enabled(self) -> bool:
        return bool(self.vapid_public_key and self.vapid_private_key)

    @property
    def house_email_set(self) -> set[str]:
        return _email_set(self.house_emails)

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )


def _email_set(value: str) -> set[str]:
    return {e.strip().lower() for e in value.split(",") if e.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
