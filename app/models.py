"""Tables (DESIGN.md §5). Milestone 1 has only the audit log; items, sources, people,
dates and suggestions arrive with the milestones that use them."""

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Activity(SQLModel, table=True):
    """One row per write, by a person or a service token. Never edited or deleted."""

    __tablename__ = "activity"

    id: Optional[int] = Field(default=None, primary_key=True)
    at: datetime = Field(default_factory=utcnow, index=True)
    role: str  # person | agent
    who: str  # kev, elizabeth, or the service token's Client ID
    action: str  # e.g. "item.create"
    target: str = ""  # e.g. "item:42"
    detail: str = ""
