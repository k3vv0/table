"""Tables (DESIGN.md §5). Items and their sources arrive in milestone 2; synced mail,
people, dates and suggestions arrive with the milestones that use them."""

from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import UniqueConstraint
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


class PushSubscription(SQLModel, table=True):
    """One phone or browser that agreed to notifications (DESIGN.md §9)."""

    __tablename__ = "push_subscription"

    id: Optional[int] = Field(default=None, primary_key=True)
    person: str = Field(index=True)  # kev | elizabeth | household
    endpoint: str = Field(unique=True)
    p256dh: str
    auth: str
    device: str = ""  # a label from the browser's user agent, for the settings list
    new_items: bool = True  # a push when something lands that you didn't add yourself
    daily: bool = True  # the evening daily-session reminder
    created_at: datetime = Field(default_factory=utcnow)
    last_ok_at: Optional[datetime] = None


class CaptureToken(SQLModel, table=True):
    """A per-person secret the "Send to Plate" Shortcut sends with each capture. Only its
    SHA-256 is stored; the token itself is shown once, when it's made."""

    __tablename__ = "capture_token"

    id: Optional[int] = Field(default=None, primary_key=True)
    person: str = Field(index=True)  # kev | elizabeth
    name: str  # e.g. "Kev's iPhone"
    token_hash: str = Field(unique=True)
    created_at: datetime = Field(default_factory=utcnow)
    last_used_at: Optional[datetime] = None


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


# --- mail, calendar and suggestions (milestone 4, DESIGN.md §10) ----------------------------

# How email-sort files a message. unsorted until the sweep (or a person) decides.
MAIL_SORTS = ("unsorted", "noise", "vault", "flagged", "keep")


class SourceMessage(SQLModel, table=True):
    """One synced email: headers and a plain-text excerpt. The excerpt is untrusted input;
    agents treat it as data. It is blanked after MAIL_KEEP_DAYS; the headers stay so a
    message is never synced or suggested twice."""

    __tablename__ = "source_message"

    id: Optional[int] = Field(default=None, primary_key=True)
    space: str = Field(index=True)  # table | kev: where its commitments land
    account: str = Field(index=True)  # the mailbox address it came from
    gm_msgid: str = Field(index=True)  # Gmail's permanent message id (X-GM-MSGID)
    gm_thrid: str = ""  # Gmail's thread id (X-GM-THRID)
    message_id: str = ""  # the Message-ID header
    from_name: str = ""
    from_addr: str = ""
    to_addrs: str = ""
    subject: str = ""
    sent_at: Optional[datetime] = None  # naive UTC
    excerpt: str = ""
    has_attachments: bool = False
    sort: str = Field(default="unsorted", index=True)
    sort_reason: str = ""
    sorted_by: str = ""
    sorted_at: Optional[datetime] = None
    archived_at: Optional[datetime] = None  # moved under Table/Noise in Gmail
    created_at: datetime = Field(default_factory=utcnow)


class CalendarEvent(SQLModel, table=True):
    """One occurrence from a calendar's secret iCal address, inside the sync window.
    Replaced wholesale on every sync of its feed."""

    __tablename__ = "calendar_event"

    id: Optional[int] = Field(default=None, primary_key=True)
    space: str = Field(index=True)
    feed: str = Field(index=True)  # the calendar id, never the secret URL
    uid: str
    summary: str = ""
    location: str = ""
    description: str = ""  # untrusted, cut to 1,000 characters
    starts_on: date = Field(index=True)  # local date (US Eastern)
    starts_at: Optional[datetime] = None  # naive UTC; blank for all-day events
    ends_at: Optional[datetime] = None
    all_day: bool = False

    @property
    def ref(self) -> str:
        return f"{self.uid}@{self.starts_on.isoformat()}"


class SyncState(SQLModel, table=True):
    """Small per-source bookkeeping: last UID seen, last success, last error."""

    __tablename__ = "sync_state"

    key: str = Field(primary_key=True)  # e.g. mail:you@gmail.com, calendar:<feed>
    value: str = ""
    last_ok_at: Optional[datetime] = None
    last_error: str = ""
    updated_at: datetime = Field(default_factory=utcnow)


# item: add a new item. note: add to an item's notes (a watch's next steps, a date's
# options). merge: fold duplicates into item_id. close: mark item_id done. date: add a
# person's key date (payload). goal: link item_id to a money-hub goal (payload).
SUGGESTION_KINDS = ("item", "note", "merge", "close", "date", "goal")


class Suggestion(SQLModel, table=True):
    """Something an agent proposes. Nothing changes until a person accepts it
    (routers/suggestions.py, behind require_person)."""

    __tablename__ = "suggestion"

    id: Optional[int] = Field(default=None, primary_key=True)
    space: str = Field(index=True)
    kind: str
    status: str = Field(default="pending", index=True)  # pending | accepted | dismissed
    title: str = ""  # item: the new item's title. note/close: a one-line summary
    notes: str = ""
    item_kind: str = "task"  # for kind item: task | watch
    due_on: Optional[date] = None
    item_id: Optional[int] = Field(default=None, foreign_key="item.id")
    merge_ids: str = ""  # comma-separated item ids, for kind merge
    source_kind: str = ""  # email | calendar
    source_ref: str = Field(default="", index=True)  # source_message.gm_msgid or event ref
    rationale: str = ""  # why the agent thinks so, shown next to the buttons
    payload: str = ""  # JSON, for kinds date and goal (scripts/migrate_suggestion_payload.py)
    created_by: str = ""
    run_id: Optional[int] = Field(default=None, foreign_key="agent_run.id")
    created_at: datetime = Field(default_factory=utcnow)
    decided_by: str = ""
    decided_at: Optional[datetime] = None
    result_item_id: Optional[int] = None


class AgentRun(SQLModel, table=True):
    """One run of a routine (DESIGN.md §7): what it looked at and what it filed."""

    __tablename__ = "agent_run"

    id: Optional[int] = Field(default=None, primary_key=True)
    routine: str  # sweep | morning
    role: str = ""
    status: str = "running"  # running | ok | failed
    summary: str = ""
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: Optional[datetime] = None


# --- people and dates (milestone 5, DESIGN.md §11) ------------------------------------------

# planned: alerts 30 days ahead with an item to prepare (and date-options' three ideas).
# light: an item and a push the day before, and a push on the day.
WEIGHTS = ("planned", "light")


class Person(SQLModel, table=True):
    """Someone whose dates matter. Lives in one space like an item: a family birthday on
    the Table, Beth's birthday on Kev's Plate so the planning stays a surprise."""

    __tablename__ = "person"

    id: Optional[int] = Field(default=None, primary_key=True)
    space: str = Field(index=True)
    name: str
    relation: str = ""  # e.g. "Beth's mom", "friend from USC"
    notes: str = ""  # what they like, sizes, what they got last year: date-options reads it
    archived: bool = False
    created_by: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class KeyDate(SQLModel, table=True):
    """A date to remember. Either a fixed day (month and day) or a rule (the nth weekday
    of a month, as for Mother's Day); yearly unless recurs is off, when year says which
    one. On a yearly date, year is optional and means the first one (a birth year)."""

    __tablename__ = "key_date"

    id: Optional[int] = Field(default=None, primary_key=True)
    space: str = Field(index=True)
    person_id: Optional[int] = Field(default=None, foreign_key="person.id")
    owner: Optional[str] = None  # Table only: whose job the prep is; blank = shared
    title: str  # e.g. "Mom's birthday", "Girlfriend's Day"
    month: int
    day: Optional[int] = None  # a fixed day; blank when nth and weekday set the day
    nth: Optional[int] = None  # 1-4, or -1 for the last one in the month
    weekday: Optional[int] = None  # 0 = Monday ... 6 = Sunday
    year: Optional[int] = None
    recurs: bool = True
    weight: str = "light"  # planned | light
    notes: str = ""
    preset: str = Field(default="", index=True)  # the preloaded date it came from, if any
    active: bool = True
    created_by: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class DateAlert(SQLModel, table=True):
    """One alert sent for one occurrence of a date, so the daily run never sends it twice.
    stage: prep (the item went up), eve (the day before), day (the day of)."""

    __tablename__ = "date_alert"
    __table_args__ = (UniqueConstraint("key_date_id", "occurs_on", "stage"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    key_date_id: int = Field(foreign_key="key_date.id", index=True)
    occurs_on: date
    stage: str
    item_id: Optional[int] = Field(default=None, foreign_key="item.id")
    sent_at: datetime = Field(default_factory=utcnow)


# --- the vault (milestone 6, DESIGN.md §12) ------------------------------------------------


class VaultPick(SQLModel, table=True):
    """One of the day's two read-later picks: a vault item (kind link) the morning
    routine chose for today, with a short summary in its own words."""

    __tablename__ = "vault_pick"
    __table_args__ = (UniqueConstraint("space", "on", "item_id"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    space: str = Field(index=True)
    on: date = Field(index=True)  # the local day it's picked for
    item_id: int = Field(foreign_key="item.id", index=True)
    summary: str = ""  # what it says, in a few sentences (untrusted source, agent's words)
    why: str = ""  # why today
    created_by: str = ""
    run_id: Optional[int] = Field(default=None, foreign_key="agent_run.id")
    created_at: datetime = Field(default_factory=utcnow)
