"""Tables (DESIGN.md §5). Items and their sources arrive in milestone 2; synced mail,
people, dates and suggestions arrive with the milestones that use them."""

from datetime import date, datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    """Now in UTC, without tzinfo: SQLite keeps no timezone, so every stored moment is
    naive UTC and is compared with naive UTC."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# Where an item lives: the shared Table, or one person's Plate.
SPACES = ("table", "kev", "elizabeth")
# inbox: captured, not triaged. today: on today's plate. later: deferred, back on
# defer_until (or whenever, if blank). done. merged: folded into merged_into_id.
STATUSES = ("inbox", "today", "later", "done", "merged")
KINDS = ("task", "watch", "date", "link")


class Item(SQLModel, table=True):
    """Every commitment, watch, date prep and vault link (DESIGN.md §5)."""

    __tablename__ = "item"

    id: Optional[int] = Field(default=None, primary_key=True)
    space: str = Field(index=True)  # table | kev | elizabeth
    # On the Table: blank means shared, or kev / elizabeth when someone takes it. On a
    # Plate it stays blank; the space already says whose it is.
    owner: Optional[str] = None
    kind: str = "task"
    title: str
    notes: str = ""
    status: str = Field(default="inbox", index=True)
    due_on: Optional[date] = None
    defer_until: Optional[date] = None
    merged_into_id: Optional[int] = Field(default=None, foreign_key="item.id")
    automated: bool = False  # closes itself; everything else is closed by a person
    created_by: str = ""  # kev, elizabeth, household, or a service role
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    done_at: Optional[datetime] = None
    moneyhub_goal_id: Optional[int] = None


class ItemSource(SQLModel, table=True):
    """Where an item came from. An item gains its duplicates' sources when they merge."""

    __tablename__ = "item_source"

    id: Optional[int] = Field(default=None, primary_key=True)
    item_id: int = Field(foreign_key="item.id", index=True)
    kind: str  # manual | shortcut | email | calendar | url
    ref: str = ""  # a message id, event uid or URL; blank for manual capture
    snippet: str = ""  # the captured words, as they arrived
    created_at: datetime = Field(default_factory=utcnow)


class Activity(SQLModel, table=True):
    """One row per write, by a person or a service token. Never edited or deleted."""

    __tablename__ = "activity"

    id: Optional[int] = Field(default=None, primary_key=True)
    at: datetime = Field(default_factory=utcnow, index=True)
    role: str  # person | agent
    who: str  # kev, elizabeth, household, or the service token's Client ID
    action: str  # e.g. "item.create"
    target: str = ""  # e.g. "item:42"
    detail: str = ""
