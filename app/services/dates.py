"""People and their key dates (DESIGN.md §11).

A date is either a fixed day (Nov 22) or a rule (the second Sunday of May), yearly or
once. Its weight decides how early Table speaks up:

- planned: 30 days ahead an item goes into the space's inbox, due on the day, and a push
  says it's coming. The morning routine's date-options skill then suggests three ideas on
  that item. A push on the day too.
- light: the day before, an item goes straight onto today's plate with a push; a push on
  the day.

run_alerts() is the daily job (scripts/date_alerts.py). It works from a window rather than
an exact day, so a missed run catches up the next morning, and date_alert keeps it from
sending anything twice.
"""

import calendar as cal
from datetime import date, timedelta

from fastapi import HTTPException
from sqlmodel import Session, select

from app.access import Identity
from app.localtime import today_local
from app.models import WEIGHTS, DateAlert, KeyDate, Person, Suggestion
from app.services import items as svc
from app.services import push

LEAD_DAYS = {"planned": 30, "light": 1}
SYSTEM = Identity("system", "date-alerts")
NTH_WORDS = {1: "first", 2: "second", 3: "third", 4: "fourth", -1: "last"}
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

# The preloaded dates (§11): offered on every board's Dates view, one tap to add. Birthdays
# and anniversaries need a real day, so the dates-interview skill asks for those.
PRESETS: dict[str, dict] = {
    "valentines": {"title": "Valentine's Day", "month": 2, "day": 14, "weight": "planned"},
    "mothers-day": {"title": "Mother's Day", "month": 5, "nth": 2, "weekday": 6, "weight": "planned"},
    "fathers-day": {"title": "Father's Day", "month": 6, "nth": 3, "weekday": 6, "weight": "planned"},
    "girlfriends-day": {"title": "Girlfriend's Day", "month": 8, "day": 1, "weight": "light"},
    "thanksgiving": {"title": "Thanksgiving", "month": 11, "nth": 4, "weekday": 3, "weight": "planned"},
    "christmas": {"title": "Christmas", "month": 12, "day": 25, "weight": "planned"},
    "new-years-eve": {"title": "New Year's Eve", "month": 12, "day": 31, "weight": "light"},
    "halloween": {"title": "Halloween", "month": 10, "day": 31, "weight": "light"},
    "tax-day": {"title": "Tax Day", "month": 4, "day": 15, "weight": "light"},
}


# --- when a date falls ---------------------------------------------------------------------


def occurrence(kd: KeyDate, year: int) -> date:
    if kd.nth is not None:
        days = cal.monthrange(year, kd.month)[1]
        matches = [d for d in range(1, days + 1) if date(year, kd.month, d).weekday() == kd.weekday]
        return date(year, kd.month, matches[-1] if kd.nth == -1 else matches[kd.nth - 1])
    # Feb 29 falls on Feb 28 in other years.
    return date(year, kd.month, min(kd.day, cal.monthrange(year, kd.month)[1]))


def next_on(kd: KeyDate, today: date) -> date | None:
    """The next time it falls, today included; None for a one-off that has passed."""
    if not kd.recurs:
        when = occurrence(kd, kd.year)
        return when if when >= today else None
    first = max(today.year, kd.year or 0)  # a yearly date's year is its first one
    for year in (first, first + 1):
        when = occurrence(kd, year)
        if when >= today:
            return when
    return None  # unreachable: next year's always counts


def turns(kd: KeyDate, when: date) -> int | None:
    """How many years on (a birthday's age, an anniversary's count), when year is known."""
    if kd.recurs and kd.year and when.year > kd.year:
        return when.year - kd.year
    return None


def describe(kd: KeyDate) -> str:
    month = cal.month_name[kd.month]
    when = f"the {NTH_WORDS[kd.nth]} {WEEKDAYS[kd.weekday]} of {month}" if kd.nth is not None else f"{month} {kd.day}"
    if not kd.recurs:
        return f"Once, {when}, {kd.year}"
    return f"Every year on {when}" + (f", since {kd.year}" if kd.year else "")


# --- checking what people and agents send --------------------------------------------------


def _int(value, name: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"{name} must be a whole number.") from exc


def clean(space: str, body: dict, today: date | None = None) -> dict:
    """The fields of a key date, checked. Raises 422 with a plain reason."""
    title = (body.get("title") or "").strip()[:200]
    if not title:
        raise HTTPException(status_code=422, detail="A date needs a title.")
    month, day = _int(body.get("month"), "month"), _int(body.get("day"), "day")
    nth, weekday, year = _int(body.get("nth"), "nth"), _int(body.get("weekday"), "weekday"), _int(body.get("year"), "year")
    if month is None or not 1 <= month <= 12:
        raise HTTPException(status_code=422, detail="month must be 1 to 12.")
    if nth is not None or weekday is not None:
        if nth not in NTH_WORDS or weekday is None or not 0 <= weekday <= 6:
            raise HTTPException(status_code=422, detail="A rule needs nth (1-4 or -1) and weekday (0 = Monday to 6).")
        day = None
    elif day is None or not 1 <= day <= cal.monthrange(2024, month)[1]:  # 2024: a leap year, so Feb 29 is fine
        raise HTTPException(status_code=422, detail="That day isn't in that month.")
    if year is not None and not 1900 <= year <= 2100:
        raise HTTPException(status_code=422, detail="year looks wrong.")
    recurs = bool(body.get("recurs", True))
    if not recurs and year is None:
        raise HTTPException(status_code=422, detail="A one-off date needs its year.")
    weight = body.get("weight") or "light"
    if weight not in WEIGHTS:
        raise HTTPException(status_code=422, detail="weight must be planned or light.")
    out = {
        "title": title,
        "month": month,
        "day": day,
        "nth": nth,
        "weekday": weekday,
        "year": year,
        "recurs": recurs,
        "weight": weight,
        "notes": (body.get("notes") or "").strip()[:2000],
        "owner": svc._check_owner(space, body.get("owner")),
    }
    if not recurs:
        try:
            when = date(year, month, day) if day else occurrence(KeyDate(space=space, **out), year)
        except ValueError as exc:  # Feb 29 in a year without one
            raise HTTPException(status_code=422, detail="That day isn't in that year.") from exc
        if when < (today or today_local()):
            raise HTTPException(status_code=422, detail="That date has already passed.")
    return out


def get_person(session: Session, space: str, person_id: int) -> Person:
    p = session.get(Person, person_id)
    if p is None or p.space != space:
        raise HTTPException(status_code=404, detail="No such person here.")
    return p


def get_date(session: Session, space: str, date_id: int) -> KeyDate:
    kd = session.get(KeyDate, date_id)
    if kd is None or kd.space != space or not kd.active:
        raise HTTPException(status_code=404, detail="No such date here.")
    return kd


def find_or_add_person(session: Session, identity: Identity, space: str, name: str, relation: str = "") -> Person:
    name = name.strip()[:120]
    found = session.exec(
        select(Person).where(Person.space == space, Person.archived == False, Person.name == name)  # noqa: E712
    ).first()
    if found:
        return found
    p = Person(space=space, name=name, relation=relation.strip()[:200], created_by=svc.actor(identity))
    session.add(p)
    session.flush()
    svc.record(session, identity, "person.create", f"person:{p.id}", p.name)
    return p


def add_date(session: Session, identity: Identity, space: str, body: dict, preset: str = "") -> KeyDate:
    fields = clean(space, body)
    person = None
    if body.get("person_id"):
        person = get_person(session, space, _int(body["person_id"], "person_id"))
    elif (body.get("person_name") or "").strip():
        person = find_or_add_person(session, identity, space, body["person_name"], body.get("relation") or "")
    same = session.exec(
        select(KeyDate.id).where(KeyDate.space == space, KeyDate.active == True, KeyDate.title == fields["title"])  # noqa: E712
    ).first()
    if same:
        raise HTTPException(status_code=409, detail=f"There's already a date called {fields['title']!r} here.")
    kd = KeyDate(space=space, person_id=person.id if person else None, preset=preset, created_by=svc.actor(identity), **fields)
    session.add(kd)
    session.flush()
    svc.record(session, identity, "date.create", f"date:{kd.id}", kd.title)
    session.commit()
    session.refresh(kd)
    return kd


def add_preset(session: Session, identity: Identity, space: str, key: str) -> KeyDate:
    if key not in PRESETS:
        raise HTTPException(status_code=404, detail="No such preloaded date.")
    if session.exec(select(KeyDate.id).where(KeyDate.space == space, KeyDate.preset == key, KeyDate.active == True)).first():  # noqa: E712
        raise HTTPException(status_code=409, detail="Already added.")
    return add_date(session, identity, space, PRESETS[key], preset=key)


def edit_date(session: Session, identity: Identity, kd: KeyDate, changes: dict) -> KeyDate:
    if changes.get("active") is False:
        kd.active = False
        svc.record(session, identity, "date.remove", f"date:{kd.id}", kd.title)
        session.commit()
        return kd
    current = {k: getattr(kd, k) for k in ("title", "month", "day", "nth", "weekday", "year", "recurs", "weight", "notes", "owner")}
    if "day" in changes and changes["day"] is not None:  # a fixed day replaces a rule
        current["nth"] = current["weekday"] = None
    current.update({k: v for k, v in changes.items() if k in current})
    for k, v in clean(kd.space, current).items():
        setattr(kd, k, v)
    if "person_id" in changes:
        kd.person_id = get_person(session, kd.space, changes["person_id"]).id if changes["person_id"] else None
    svc.record(session, identity, "date.edit", f"date:{kd.id}", ", ".join(sorted(changes)))
    session.commit()
    session.refresh(kd)
    return kd


# --- what the board and the morning routine see -------------------------------------------


def upcoming(session: Session, space: str, today: date | None = None) -> list[dict]:
    today = today or today_local()
    dates = session.exec(select(KeyDate).where(KeyDate.space == space, KeyDate.active == True)).all()  # noqa: E712
    people = {p.id: p for p in session.exec(select(Person).where(Person.space == space))}
    out = []
    for kd in dates:
        when = next_on(kd, today)
        prep = None
        if when:
            prep = session.exec(
                select(DateAlert).where(DateAlert.key_date_id == kd.id, DateAlert.occurs_on == when, DateAlert.stage == "prep")
            ).first()
        options = bool(
            prep
            and prep.item_id
            and session.exec(select(Suggestion.id).where(Suggestion.item_id == prep.item_id, Suggestion.kind == "note")).first()
        )
        person = people.get(kd.person_id)
        out.append(
            {
                "id": kd.id,
                "title": kd.title,
                "person": {"id": person.id, "name": person.name, "relation": person.relation, "notes": person.notes} if person else None,
                "owner": kd.owner,
                "weight": kd.weight,
                "month": kd.month,
                "day": kd.day,
                "nth": kd.nth,
                "weekday": kd.weekday,
                "year": kd.year,
                "recurs": kd.recurs,
                "notes": kd.notes,
                "preset": kd.preset,
                "rule": describe(kd),
                "next_on": when.isoformat() if when else None,
                "days_away": (when - today).days if when else None,
                "turns": turns(kd, when) if when else None,
                "prep_item_id": prep.item_id if prep else None,
                "options_suggested": options,
            }
        )
    return sorted(out, key=lambda d: (d["days_away"] is None, d["days_away"] if d["days_away"] is not None else 0, d["title"]))


def presets_left(session: Session, space: str) -> list[dict]:
    taken = set(session.exec(select(KeyDate.preset).where(KeyDate.space == space, KeyDate.active == True, KeyDate.preset != "")))  # noqa: E712
    return [{"key": k, "rule": describe(KeyDate(space=space, **v)), **v} for k, v in PRESETS.items() if k not in taken]


# --- the daily alert run -------------------------------------------------------------------


def _audience(kd: KeyDate) -> tuple[str, ...]:
    if kd.space == "table" and kd.owner:
        return (kd.owner,)
    return push.AUDIENCE[kd.space]


def _label(kd: KeyDate, when: date) -> str:
    age = turns(kd, when)
    if not age:
        return kd.title
    return f"{kd.title} (turns {age})" if "birthday" in kd.title.lower() else f"{kd.title} ({age} years)"


def _alerted(session: Session, kd: KeyDate, when: date, stage: str) -> bool:
    return (
        session.exec(
            select(DateAlert.id).where(DateAlert.key_date_id == kd.id, DateAlert.occurs_on == when, DateAlert.stage == stage)
        ).first()
        is not None
    )


def run_alerts(session: Session, today: date | None = None, send=None) -> dict:
    """Put up prep items and send the pushes that are due. Returns what it did."""
    today = today or today_local()
    send = send or push.send
    pushes: list[tuple[tuple[str, ...], str, str, str]] = []
    made = 0
    for kd in session.exec(select(KeyDate).where(KeyDate.active == True)).all():  # noqa: E712
        when = next_on(kd, today)
        if when is None:
            continue
        days = (when - today).days
        label = _label(kd, when)
        on = when.strftime("%a %b ") + str(when.day)
        url = push.space_url(kd.space)
        if days <= LEAD_DAYS[kd.weight] and not _alerted(session, kd, when, "prep"):
            item = svc.create(
                session,
                SYSTEM,
                kd.space,
                f"{label} · {on}",
                kd.notes,
                due_on=when,
                owner=kd.owner,
                today=kd.weight == "light",
                source_kind="date",
                source_ref=f"date:{kd.id}@{when.isoformat()}",
            )
            item.kind = "date"
            session.add(item)
            session.add(DateAlert(key_date_id=kd.id, occurs_on=when, stage="prep", item_id=item.id))
            made += 1
            if kd.weight == "planned" and days > 0:
                pushes.append((_audience(kd), f"In {days} days: {label}", f"{on}. Ideas are on the way.", url + "#inbox"))
        if days == 1 and kd.weight == "light" and not _alerted(session, kd, when, "eve"):
            session.add(DateAlert(key_date_id=kd.id, occurs_on=when, stage="eve"))
            pushes.append((_audience(kd), f"Tomorrow: {label}", "It's on today's plate so it doesn't slip.", url + "#today"))
        if days == 0 and not _alerted(session, kd, when, "day"):
            session.add(DateAlert(key_date_id=kd.id, occurs_on=when, stage="day"))
            pushes.append((_audience(kd), f"Today: {label}", on, url + "#today"))
        session.commit()
    sent = 0
    for people, title, body, url in pushes:
        sent += send(people, title, body, url)
    return {"items": made, "pushes": len(pushes), "delivered": sent}

