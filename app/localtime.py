from datetime import date, datetime
from zoneinfo import ZoneInfo

# Every "today" in Table is the day Kev and Beth would say it is, in US Eastern, not the
# droplet's UTC clock. A bare date.today() disagrees with itself for several hours every
# night after midnight UTC, which money-hub learned the hard way (its DESIGN.md §13l).
LOCAL_TZ = ZoneInfo("America/New_York")


def today_local() -> date:
    """Today in US Eastern. Use this, never date.today() or datetime.now()."""
    return datetime.now(LOCAL_TZ).date()


def now_local() -> datetime:
    return datetime.now(LOCAL_TZ)
