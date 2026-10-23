from functools import lru_cache
from typing import NamedTuple

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
    # Client ID (not the secret) of the sweep routine's own service token (DESIGN.md §10):
    # reads mail, events and items, sorts mail, files suggestions. Never Beth's Plate.
    sweep_service_token_id: str = ""
    # Client ID of the morning routine's own service token (DESIGN.md §11): reads the
    # Table and Kev's Plate, files suggestions (date-options) and logs its runs.
    morning_service_token_id: str = ""
    # Where an item's money-hub goal link points. Table stores only the goal's number.
    moneyhub_url: str = "https://money.sangmuah.net"

    # Mail and calendar sync (DESIGN.md §10, scripts/sync.py). Entries are separated by
    # ";", fields by "|". A space is where the account's commitments land: table or kev.
    #   MAIL_ACCOUNTS=kev|you@gmail.com|abcd efgh ijkl mnop;table|us@gmail.com|...
    #   CALENDAR_FEEDS=kev|https://calendar.google.com/calendar/ical/.../basic.ics;...
    mail_accounts: str = ""
    calendar_feeds: str = ""
    # Off until Kev has checked a week of sorting: noise is only marked in Table. On, the
    # sync also moves it out of the Gmail inbox under the Table/Noise label.
    mail_archive_noise: bool = False
    # Days a synced message keeps its excerpt; after that only headers stay (for dedupe).
    mail_keep_days: int = 30

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

    @property
    def mail_account_list(self) -> list["MailAccount"]:
        out = []
        for space, address, password in _entries(self.mail_accounts, 3, "MAIL_ACCOUNTS"):
            out.append(MailAccount(space, address.lower(), password.replace(" ", "")))
        return out

    @property
    def calendar_feed_list(self) -> list["CalendarFeed"]:
        return [CalendarFeed(space, url) for space, url in _entries(self.calendar_feeds, 2, "CALENDAR_FEEDS")]

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )


class MailAccount(NamedTuple):
    space: str
    address: str
    password: str  # a Gmail app password, never logged or returned by the API

    def __repr__(self) -> str:
        return f"MailAccount({self.space!r}, {self.address!r})"


class CalendarFeed(NamedTuple):
    space: str
    url: str  # the secret iCal address is a credential: never logged or returned

    @property
    def name(self) -> str:
        # A stable, non-secret label for the feed: the calendar id, without the key.
        parts = self.url.split("/ical/", 1)
        return parts[1].split("/", 1)[0] if len(parts) == 2 else "calendar"

    def __repr__(self) -> str:
        return f"CalendarFeed({self.space!r}, {self.name!r})"


def _entries(value: str, fields: int, name: str) -> list[list[str]]:
    out = []
    for raw in value.split(";"):
        if not raw.strip():
            continue
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) != fields or not all(parts):
            # Never echo the entry: it holds a password or a secret URL.
            raise ValueError(f"{name}: each entry needs {fields} fields separated by |")
        out.append(parts)
    return out


def _email_set(value: str) -> set[str]:
    return {e.strip().lower() for e in value.split(",") if e.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
