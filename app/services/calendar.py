"""Google Calendar through each calendar's secret iCal address (DESIGN.md §10).

Every sync replaces a feed's events with the occurrences between yesterday and
WINDOW_DAYS ahead, recurring events expanded. The address is a credential: it is never
logged, stored or returned, only the calendar id inside it (CalendarFeed.name).
"""

import logging
from datetime import date, datetime, timedelta, timezone

import icalendar
import recurring_ical_events
import requests
from sqlmodel import Session, delete

from app.config import CalendarFeed, get_settings
from app.localtime import LOCAL_TZ as TZ, today_local
from app.models import CalendarEvent, SyncState, utcnow

log = logging.getLogger("table.sync")

WINDOW_DAYS = 120
DESCRIPTION_CHARS = 1000
MAX_BYTES = 10_000_000


def fetch(url: str) -> bytes:
    res = requests.get(url, timeout=60, headers={"User-Agent": "table-sync/1.0"}, stream=True)
    res.raise_for_status()
    data = res.raw.read(MAX_BYTES + 1, decode_content=True)
    if len(data) > MAX_BYTES:
        raise ValueError("calendar feed is larger than 10 MB")
    return data


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:  # a "floating" time: read it as US Eastern
        value = value.replace(tzinfo=TZ)
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def occurrences(ics: bytes, feed: CalendarFeed, start: date, end: date) -> list[CalendarEvent]:
    cal = icalendar.Calendar.from_ical(ics)
    out = []
    for ev in recurring_ical_events.of(cal).between(start, end):
        if str(ev.get("STATUS", "")).upper() == "CANCELLED":
            continue
        begin = ev.decoded("DTSTART")
        finish = ev.decoded("DTEND") if ev.get("DTEND") else None
        all_day = not isinstance(begin, datetime)
        if all_day:
            starts_on, starts_at, ends_at = begin, None, None
        else:
            starts_at = _utc(begin)
            ends_at = _utc(finish) if isinstance(finish, datetime) else None
            starts_on = starts_at.replace(tzinfo=timezone.utc).astimezone(TZ).date()
        out.append(
            CalendarEvent(
                space=feed.space,
                feed=feed.name,
                uid=str(ev.get("UID", ""))[:300],
                summary=str(ev.get("SUMMARY", ""))[:300],
                location=str(ev.get("LOCATION", ""))[:300],
                description=str(ev.get("DESCRIPTION", ""))[:DESCRIPTION_CHARS],
                starts_on=starts_on,
                starts_at=starts_at,
                ends_at=ends_at,
                all_day=all_day,
            )
        )
    return out


def sync_feed(session: Session, feed: CalendarFeed, fetcher=fetch) -> int:
    today = today_local()
    events = occurrences(fetcher(feed.url), feed, today - timedelta(days=1), today + timedelta(days=WINDOW_DAYS))
    session.exec(delete(CalendarEvent).where(CalendarEvent.space == feed.space, CalendarEvent.feed == feed.name))
    session.add_all(events)
    state = session.get(SyncState, f"calendar:{feed.name}") or SyncState(key=f"calendar:{feed.name}")
    state.value, state.last_ok_at, state.last_error, state.updated_at = str(len(events)), utcnow(), "", utcnow()
    session.add(state)
    session.commit()
    return len(events)


def sync_all(session: Session, fetcher=fetch) -> dict:
    report = {}
    for feed in get_settings().calendar_feed_list:
        try:
            report[feed.name] = {"events": sync_feed(session, feed, fetcher)}
        except Exception as exc:  # noqa: BLE001  (record it and move on)
            session.rollback()
            # requests puts the URL (the secret) in some messages, so keep only the type.
            state = session.get(SyncState, f"calendar:{feed.name}") or SyncState(key=f"calendar:{feed.name}")
            state.last_error, state.updated_at = type(exc).__name__, utcnow()
            session.add(state)
            session.commit()
            log.warning("calendar sync failed for %s: %s", feed.name, type(exc).__name__)
            report[feed.name] = {"error": type(exc).__name__}
    return report
