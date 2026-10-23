"""Trips (DESIGN.md §13): places, trips and their modules, the prep checklist, the text
summary and the standby check pushes.

A trip is a stretch of time away on the Table. Its modules say how each part happens: a
flight, a stay, a ride, a plan. A flight with no flight number is a leg still to fill, and
the morning routine's trip-options skill suggests standby candidates and a paid backup for
it. Agents only suggest (kinds trip and module, app/services/suggestions.py); people add,
choose and book.

run_checks() is the standby rhythm (scripts/trip_checks.py, every 15 minutes): for each
leg not yet made, a push to check loads at 8pm Eastern 14, 11 and 8 days out and every day
from 5 days out, the candidates in order the night before, and one more 3 hours before the
first candidate leaves. trip_check keeps any of them from going out twice.
"""

import json
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlmodel import Session, col, select

from app.access import ELIZABETH, KEV, Identity
from app.localtime import today_local
from app.models import (
    MODULE_KINDS,
    MODULE_STATES,
    PAYERS,
    TRIP_STATUSES,
    CalendarEvent,
    Item,
    KeyDate,
    Place,
    Suggestion,
    Trip,
    TripCheck,
    TripModule,
    utcnow,
)
from app.services import items as svc
from app.services import push

EASTERN = ZoneInfo("America/New_York")
SYSTEM = Identity("system", "trip-checks")
TRAVELERS = (KEV, ELIZABETH)
NAMES = {KEV: "Kev", ELIZABETH: "Beth"}
OPEN_TRIPS = ("idea", "planning", "set", "away")

# The airports you use, for the morning-of push: departure times are local to the origin.
# Anything not listed is taken as Eastern, which is right for most of the East Coast.
AIRPORT_TZ = {
    **dict.fromkeys(("JFK", "LGA", "EWR", "ATL", "CLT", "BOS", "DCA", "IAD", "MIA", "FLL", "MCO", "TPA", "RDU", "DTW"), "America/New_York"),
    **dict.fromkeys(("AUS", "SAT", "DFW", "DAL", "IAH", "HOU", "ORD", "MDW", "MSP", "MSY", "BNA", "STL"), "America/Chicago"),
    **dict.fromkeys(("DEN", "SLC"), "America/Denver"),
    "PHX": "America/Phoenix",
    **dict.fromkeys(("LAX", "BUR", "SNA", "ONT", "LGB", "SAN", "SFO", "OAK", "SJC", "SEA", "PDX", "LAS"), "America/Los_Angeles"),
    "HNL": "Pacific/Honolulu",
    "OGG": "Pacific/Honolulu",
}

# Days out (8pm Eastern) when the timer says "check loads tonight": every 3 days in the last
# two weeks, then daily. The night before (1 day out) gets the candidates in order instead.
CHECK_DAYS = (14, 11, 8, 5, 4, 3, 2)
CHECK_HOUR = 20
MORNING_LEAD = timedelta(hours=3)

# The prep checklist (§13): ordinary Table items, due this many days before you leave.
# key, title, days before, notes, when (a test on the trip), owner
PREP = (
    ("sublease", "Ask about subleasing the apartment", 21, "Only asked for trips of a week or more.", "long", None),
    ("water", "Ask the neighbor across the hall to water the plants", 7, "Agree on which days. Hand over the key, or the subtenant waters.", None, None),
    ("oncall", "On call: confirm you can work remotely from there", 7, "Kev's calendar shows on-call during the trip.", "oncall", KEV),
    ("pack", "Pack, and decide on checked bags", 2, "No bags: transit to JFK. Bags: Uber to LGA.", None, None),
    ("key", "Hand off the key to the neighbor or subtenant", 1, "", None, None),
    ("summary", "Text family the trip summary", 1, "The trip page copies it in one tap.", None, None),
    ("plants", "Water the peppers deeply", 0, "Late in the season, think about bringing them inside.", None, None),
)

_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_IATA = re.compile(r"^[A-Z]{3}$")


# --- checking what people and agents send ------------------------------------------------


def _int(value, name: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        out = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"{name} must be a whole number.") from exc
    if out < 0:
        raise HTTPException(status_code=422, detail=f"{name} can't be negative.")
    return out


def _day(value, name: str) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{name} must be a day, YYYY-MM-DD.") from exc


def travelers_of(value) -> str:
    """kev,elizabeth from a list or a comma string; blank stays blank."""
    raw = value if isinstance(value, (list, tuple)) else str(value or "").split(",")
    names = [str(t).strip().lower() for t in raw if str(t).strip()]
    names = ["elizabeth" if n == "beth" else n for n in names]
    for n in names:
        if n not in TRAVELERS:
            raise HTTPException(status_code=422, detail="travelers are kev and elizabeth.")
    return ",".join(t for t in TRAVELERS if t in names)


def airports_of(value) -> str:
    raw = value if isinstance(value, (list, tuple)) else re.split(r"[\s,/]+", str(value or ""))
    codes = [str(c).strip().upper() for c in raw if str(c).strip()]
    for c in codes:
        if not _IATA.match(c):
            raise HTTPException(status_code=422, detail=f"{c!r} isn't an airport code like JFK.")
    return " ".join(dict.fromkeys(codes))


def _link(value) -> str:
    link = str(value or "").strip()
    if link and not link.startswith("https://"):
        raise HTTPException(status_code=422, detail="Links must start with https://.")
    return link[:1000]


def clean_trip(body: dict, current: dict | None = None) -> dict:
    out = dict(current or {})
    if "title" in body or current is None:
        title = (body.get("title") or "").strip()[:200]
        if not title:
            raise HTTPException(status_code=422, detail="A trip needs a name.")
        out["title"] = title
    if "status" in body or current is None:
        status = body.get("status") or "planning"
        if status not in TRIP_STATUSES:
            raise HTTPException(status_code=422, detail=f"status must be one of {', '.join(TRIP_STATUSES)}.")
        out["status"] = status
    for name in ("window", "places"):
        if name in body or current is None:
            out[name] = (body.get(name) or "").strip()[:300]
    if "notes" in body or current is None:
        out["notes"] = (body.get("notes") or "").strip()[:4000]
    for name in ("starts_on", "ends_on"):
        if name in body or current is None:
            out[name] = _day(body.get(name), name)
    if "travelers" in body or current is None:
        out["travelers"] = travelers_of(body.get("travelers") if "travelers" in body else "kev,elizabeth")
        if not out["travelers"]:
            raise HTTPException(status_code=422, detail="A trip needs at least one traveler.")
    if "moneyhub_goal_id" in body:
        goal = body.get("moneyhub_goal_id")
        if goal is not None and (not isinstance(goal, int) or goal < 1):
            raise HTTPException(status_code=422, detail="moneyhub_goal_id must be a goal's number, or blank.")
        out["moneyhub_goal_id"] = goal
    if out.get("starts_on") and out.get("ends_on") and out["ends_on"] < out["starts_on"]:
        raise HTTPException(status_code=422, detail="A trip can't end before it starts.")
    if out.get("status") in ("set", "away") and not (out.get("starts_on") and out.get("ends_on")):
        raise HTTPException(status_code=422, detail="Give the trip its first and last day before marking it set.")
    return out


MODULE_FIELDS = (
    "kind", "state", "title", "starts_on", "ends_on", "travelers", "payer", "cost", "points",
    "from_airports", "to_airports", "flight", "departs", "arrives", "link", "confirmation", "notes",
)


def clean_module(trip: Trip, body: dict, current: dict | None = None) -> dict:
    """The fields of a module, checked against its trip. Raises 422 with a plain reason."""
    out = dict(current or {})
    has = lambda name: name in body or current is None  # noqa: E731
    if has("kind"):
        if body.get("kind") not in MODULE_KINDS:
            raise HTTPException(status_code=422, detail=f"kind must be one of {', '.join(MODULE_KINDS)}.")
        out["kind"] = body["kind"]
    if has("state"):
        state = body.get("state") or "option"
        if state not in MODULE_STATES:
            raise HTTPException(status_code=422, detail=f"state must be one of {', '.join(MODULE_STATES)}.")
        out["state"] = state
    for name in ("starts_on", "ends_on"):
        if has(name):
            out[name] = _day(body.get(name), name)
    if out.get("starts_on") and out.get("ends_on") and out["ends_on"] < out["starts_on"]:
        raise HTTPException(status_code=422, detail="A module can't end before it starts.")
    if has("travelers"):
        out["travelers"] = travelers_of(body.get("travelers"))
        outside = set(filter(None, out["travelers"].split(","))) - set(trip.travelers.split(","))
        if outside:
            raise HTTPException(status_code=422, detail="Only people on the trip can be on a module.")
    if has("payer"):
        payer = body.get("payer") or ""
        if payer not in PAYERS:
            raise HTTPException(status_code=422, detail=f"payer must be one of {', '.join(p for p in PAYERS if p)}, or blank.")
        out["payer"] = payer
    for name in ("cost", "points"):
        if has(name):
            out[name] = _int(body.get(name), name)
    for name in ("from_airports", "to_airports"):
        if has(name):
            out[name] = airports_of(body.get(name))
    if has("flight"):
        out["flight"] = re.sub(r"\s+", " ", str(body.get("flight") or "").strip().upper())[:20]
    for name in ("departs", "arrives"):
        if has(name):
            value = str(body.get(name) or "").strip()
            if value and not _TIME.match(value):
                raise HTTPException(status_code=422, detail=f"{name} must be a 24-hour time like 06:05.")
            out[name] = value
    if has("link"):
        out["link"] = _link(body.get("link"))
    if has("confirmation"):
        out["confirmation"] = str(body.get("confirmation") or "").strip()[:60]
    if has("notes"):
        out["notes"] = str(body.get("notes") or "").strip()[:2000]
    if has("title"):
        out["title"] = str(body.get("title") or "").strip()[:200]
    if out["kind"] == "flight":
        if out.get("flight") and not (out.get("from_airports") and out.get("to_airports") and out.get("starts_on")):
            raise HTTPException(status_code=422, detail="A flight needs its day and both airports.")
        if not out.get("title"):
            ends = [(out.get(k) or "?").replace(" ", "/") for k in ("from_airports", "to_airports")]
            route = " → ".join(ends)
            out["title"] = f"{out['flight']} {route}" if out.get("flight") else f"Flight {route}"
    if not out.get("title"):
        raise HTTPException(status_code=422, detail="A module needs a title.")
    return out


def get_trip(session: Session, trip_id: int) -> Trip:
    trip = session.get(Trip, trip_id)
    if trip is None:
        raise HTTPException(status_code=404, detail="No such trip.")
    return trip


def get_module(session: Session, trip: Trip, module_id: int) -> TripModule:
    m = session.get(TripModule, module_id)
    if m is None or m.trip_id != trip.id:
        raise HTTPException(status_code=404, detail="No such module on this trip.")
    return m


def _dup_title(session: Session, title: str, exclude: int | None = None) -> bool:
    stmt = select(Trip.id).where(Trip.title == title, col(Trip.status).in_(OPEN_TRIPS))
    if exclude:
        stmt = stmt.where(Trip.id != exclude)
    return session.exec(stmt).first() is not None


# --- writing -----------------------------------------------------------------------------


def create_trip(session: Session, identity: Identity, body: dict) -> Trip:
    fields = clean_trip(body)
    if _dup_title(session, fields["title"]):
        raise HTTPException(status_code=409, detail=f"There's already a trip called {fields['title']!r}.")
    key_date_id = body.get("key_date_id")
    if key_date_id is not None:
        kd = session.get(KeyDate, key_date_id)
        if kd is None or kd.space != "table" or not kd.active:
            raise HTTPException(status_code=422, detail="key_date_id must be a date on the Table.")
    trip = Trip(**fields, key_date_id=key_date_id, created_by=svc.actor(identity))
    session.add(trip)
    session.flush()
    svc.record(session, identity, "trip.create", f"trip:{trip.id}", trip.title)
    session.commit()
    session.refresh(trip)
    make_prep(session, identity, trip)
    return trip


def update_trip(session: Session, identity: Identity, trip: Trip, changes: dict) -> Trip:
    current = {k: getattr(trip, k) for k in ("title", "status", "window", "places", "notes", "starts_on", "ends_on", "travelers", "moneyhub_goal_id")}
    fields = clean_trip(changes, current)
    if "title" in changes and _dup_title(session, fields["title"], exclude=trip.id):
        raise HTTPException(status_code=409, detail=f"There's already a trip called {fields['title']!r}.")
    for k, v in fields.items():
        setattr(trip, k, v)
    trip.updated_at = utcnow()
    svc.record(session, identity, "trip.edit", f"trip:{trip.id}", ", ".join(sorted(changes)))
    session.commit()
    session.refresh(trip)
    make_prep(session, identity, trip)
    return trip


def add_module(session: Session, identity: Identity, trip: Trip, body: dict) -> TripModule:
    m = TripModule(trip_id=trip.id, created_by=svc.actor(identity), **clean_module(trip, body))
    session.add(m)
    session.flush()
    svc.record(session, identity, "trip.module.create", f"module:{m.id}", f"trip:{trip.id} {m.kind} {m.title}")
    session.commit()
    session.refresh(m)
    return m


def update_module(session: Session, identity: Identity, trip: Trip, m: TripModule, changes: dict) -> TripModule:
    current = {k: getattr(m, k) for k in MODULE_FIELDS}
    for k, v in clean_module(trip, changes, current).items():
        setattr(m, k, v)
    m.updated_at = utcnow()
    svc.record(session, identity, "trip.module.edit", f"module:{m.id}", ", ".join(sorted(changes)))
    session.commit()
    session.refresh(m)
    return m


def _on_call(session: Session, trip: Trip) -> bool:
    stmt = select(CalendarEvent.summary).where(
        col(CalendarEvent.space).in_(("kev", "table")),
        CalendarEvent.starts_on >= trip.starts_on - timedelta(days=1),
        CalendarEvent.starts_on <= trip.ends_on,
    )
    return any(re.search(r"on[\s-]?call", s or "", re.I) for s in session.exec(stmt))


def make_prep(session: Session, identity: Identity, trip: Trip, today: date | None = None) -> list[Item]:
    """The prep checklist, once, when a trip is set and has its dates."""
    if trip.prep_made or trip.status not in ("set", "away") or not trip.starts_on or not trip.ends_on:
        return []
    today = today or today_local()
    nights = (trip.ends_on - trip.starts_on).days
    made = []
    for key, title, days, notes, when, owner in PREP:
        if when == "long" and nights < 7:
            continue
        if when == "oncall" and (KEV not in trip.travelers.split(",") or not _on_call(session, trip)):
            continue
        due = max(trip.starts_on - timedelta(days=days), today)
        item = svc.create(
            session, identity, "table", f"{title} · {trip.title}", notes, due_on=due, owner=owner,
            source_kind="trip", source_ref=f"trip:{trip.id}:{key}",
        )
        item.trip_id = trip.id
        session.add(item)
        made.append(item)
    trip.prep_made = True
    session.add(trip)
    svc.record(session, identity, "trip.prep", f"trip:{trip.id}", f"{len(made)} items")
    session.commit()
    return made


# --- places ------------------------------------------------------------------------------


def clean_place(body: dict, current: dict | None = None) -> dict:
    out = dict(current or {})
    has = lambda name: name in body or current is None  # noqa: E731
    if has("name"):
        name = (body.get("name") or "").strip()[:120]
        if not name:
            raise HTTPException(status_code=422, detail="A place needs a name.")
        out["name"] = name
    if has("airports"):
        out["airports"] = airports_of(body.get("airports"))
    if has("bags_airport"):
        out["bags_airport"] = airports_of(body.get("bags_airport"))[:3]
    for name, limit in (("hosts", 300), ("ride", 300), ("notes", 2000)):
        if has(name):
            out[name] = str(body.get(name) or "").strip()[:limit]
    if has("ride_cost"):
        out["ride_cost"] = _int(body.get("ride_cost"), "ride_cost")
    if has("home"):
        out["home"] = bool(body.get("home"))
    return out


def add_place(session: Session, identity: Identity, body: dict) -> Place:
    fields = clean_place(body)
    if session.exec(select(Place.id).where(Place.name == fields["name"])).first():
        raise HTTPException(status_code=409, detail=f"There's already a place called {fields['name']!r}.")
    if fields.get("home"):
        _unset_home(session)
    p = Place(**fields, created_by=svc.actor(identity))
    session.add(p)
    session.flush()
    svc.record(session, identity, "place.create", f"place:{p.id}", p.name)
    session.commit()
    session.refresh(p)
    return p


def update_place(session: Session, identity: Identity, p: Place, changes: dict) -> Place:
    current = {k: getattr(p, k) for k in ("name", "airports", "bags_airport", "hosts", "ride", "ride_cost", "notes", "home")}
    fields = clean_place(changes, current)
    if fields["name"] != p.name and session.exec(select(Place.id).where(Place.name == fields["name"])).first():
        raise HTTPException(status_code=409, detail=f"There's already a place called {fields['name']!r}.")
    if fields["home"] and not p.home:
        _unset_home(session)
    for k, v in fields.items():
        setattr(p, k, v)
    svc.record(session, identity, "place.edit", f"place:{p.id}", ", ".join(sorted(changes)))
    session.commit()
    session.refresh(p)
    return p


def _unset_home(session: Session) -> None:
    for other in session.exec(select(Place).where(Place.home == True)):  # noqa: E712
        other.home = False
        session.add(other)


def place_out(p: Place) -> dict:
    return {
        "id": p.id, "name": p.name, "airports": p.airports, "bags_airport": p.bags_airport, "hosts": p.hosts,
        "ride": p.ride, "ride_cost": p.ride_cost, "notes": p.notes, "home": p.home,
    }


# --- reading -----------------------------------------------------------------------------


def module_out(m: TripModule, person: bool) -> dict:
    out = {k: getattr(m, k) for k in MODULE_FIELDS}
    out.update(
        id=m.id,
        trip_id=m.trip_id,
        starts_on=m.starts_on.isoformat() if m.starts_on else None,
        ends_on=m.ends_on.isoformat() if m.ends_on else None,
        leg=m.kind == "flight" and not m.flight,
        created_by=m.created_by,
    )
    if not person:
        out.pop("confirmation")  # booking codes are for the people travelling
    return out


def _payload(s: Suggestion) -> dict:
    try:
        return json.loads(s.payload) if s.payload else {}
    except ValueError:
        return {}


def pending_modules(session: Session, trip_id: int | None = None) -> list[tuple[Suggestion, dict]]:
    stmt = select(Suggestion).where(Suggestion.space == "table", Suggestion.kind == "module", Suggestion.status == "pending")
    out = []
    for s in session.exec(stmt.order_by(col(Suggestion.created_at))):
        p = _payload(s)
        if trip_id is None or p.get("trip_id") == trip_id:
            out.append((s, p))
    return out


def _covers_night(m: TripModule, night: date) -> bool:
    end = m.ends_on or (m.starts_on + timedelta(days=1) if m.starts_on else None)
    return bool(m.starts_on and m.starts_on <= night < end)


def strip(trip: Trip, modules: list[TripModule]) -> list[dict]:
    """Day by day from the first day to the last, with what's on and what's missing."""
    if not trip.starts_on or not trip.ends_on:
        return []
    live = [m for m in modules if m.state != "dropped"]
    days = []
    day = trip.starts_on
    while day <= trip.ends_on and len(days) < 60:
        on = [m.id for m in live if m.kind != "stay" and m.starts_on == day]
        stays = [m.id for m in live if m.kind == "stay" and _covers_night(m, day)]
        gaps = []
        if day < trip.ends_on and not stays:
            gaps.append("No stay tonight")
        flying = any(m.kind == "flight" and m.starts_on == day for m in live)
        if flying and not any(m.kind == "ground" and m.starts_on == day for m in live):
            gaps.append("No ride to or from the airport")
        days.append({"on": day.isoformat(), "modules": on, "stays": stays, "gaps": gaps})
        day += timedelta(days=1)
    return days


def trip_out(session: Session, trip: Trip, person: bool, full: bool = True) -> dict:
    modules = list(session.exec(select(TripModule).where(TripModule.trip_id == trip.id).order_by(col(TripModule.starts_on), col(TripModule.departs), col(TripModule.id))))
    out = {
        "id": trip.id,
        "title": trip.title,
        "status": trip.status,
        "window": trip.window,
        "starts_on": trip.starts_on.isoformat() if trip.starts_on else None,
        "ends_on": trip.ends_on.isoformat() if trip.ends_on else None,
        "travelers": [t for t in trip.travelers.split(",") if t],
        "places": trip.places,
        "notes": trip.notes,
        "key_date_id": trip.key_date_id,
        "moneyhub_goal_id": trip.moneyhub_goal_id,
        "prep_made": trip.prep_made,
        "legs": sorted({m.starts_on.isoformat() for m in modules if m.kind == "flight" and m.starts_on and m.state != "dropped"}),
        "modules": [module_out(m, person) for m in modules],
    }
    if full:
        prep = session.exec(select(Item).where(Item.trip_id == trip.id).order_by(col(Item.due_on), col(Item.id)))
        out["prep"] = [
            {"id": i.id, "title": i.title, "status": i.status, "due_on": i.due_on.isoformat() if i.due_on else None, "owner": i.owner}
            for i in prep
        ]
        out["strip"] = strip(trip, modules)
        out["suggested"] = [{"id": s.id, "rationale": s.rationale, "created_by": s.created_by, **p} for s, p in pending_modules(session, trip.id)]
    return out


def list_trips(session: Session, person: bool) -> list[dict]:
    trips = session.exec(select(Trip).order_by(col(Trip.starts_on), col(Trip.id))).all()
    order = {s: i for i, s in enumerate(("away", "set", "planning", "idea", "home", "reviewed"))}
    return [trip_out(session, t, person, full=False) for t in sorted(trips, key=lambda t: order[t.status])]


# --- the text summary --------------------------------------------------------------------


def _day_words(d: date) -> str:
    return d.strftime("%a %b ") + str(d.day)


def _time_words(hhmm: str) -> str:
    if not hhmm:
        return ""
    t = time.fromisoformat(hhmm)
    return t.strftime("%I:%M%p").lstrip("0").replace(":00", "").lower()


def summary(session: Session, trip: Trip) -> str:
    """Plain text to send family: dates, flights, where you're staying. Nothing private."""
    modules = sorted(
        (m for m in session.exec(select(TripModule).where(TripModule.trip_id == trip.id)) if m.state in ("chosen", "booked")),
        key=lambda m: (m.starts_on or date.max, m.kind != "flight", m.departs, m.id),
    )
    who = " and ".join(NAMES[t] for t in trip.travelers.split(",") if t)
    lines = [trip.title]
    if trip.starts_on and trip.ends_on:
        lines.append(f"{who}, {_day_words(trip.starts_on)} to {_day_words(trip.ends_on)}")
    elif trip.window:
        lines.append(f"{who}, {trip.window} (dates not set yet)")
    for m in modules:
        people = ""
        if m.travelers and m.travelers != trip.travelers:
            people = " (" + " and ".join(NAMES[t] for t in m.travelers.split(",") if t) + ")"
        day = _day_words(m.starts_on) if m.starts_on else ""
        if m.kind == "flight":
            route = f"{m.from_airports} to {m.to_airports}"
            times = f", leaves {_time_words(m.departs)}" if m.departs else ""
            times += f", lands {_time_words(m.arrives)}" if m.arrives else ""
            standby = " (standby, may change)" if m.payer == "standby" and m.state != "booked" else ""
            lines.append(f"{day}: {m.flight or 'flight'} {route}{times}{standby}{people}")
        elif m.kind == "stay":
            until = f" to {_day_words(m.ends_on)}" if m.ends_on else ""
            lines.append(f"{day}{until}: staying at {m.title}{people}")
        else:
            lines.append(f"{day}: {m.title}{people}")
    return "\n".join(lines)


# --- standby check pushes ----------------------------------------------------------------


def _departure(m: TripModule, on: date) -> datetime | None:
    """When a candidate leaves, in naive UTC, from its local time at the origin."""
    if not m.departs:
        return None
    origin = (m.from_airports.split() or ["JFK"])[0]
    local = datetime.combine(on, time.fromisoformat(m.departs), tzinfo=ZoneInfo(AIRPORT_TZ.get(origin, "America/New_York")))
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def _candidates(session: Session, trip: Trip, on: date) -> tuple[list[dict], list[dict], bool]:
    """A leg's standby candidates and paid backups, from its modules and pending agent
    options, earliest first; and whether it's already made (a booked flight that day)."""
    flights = [m for m in session.exec(select(TripModule).where(TripModule.trip_id == trip.id, TripModule.kind == "flight", TripModule.starts_on == on)) if m.state != "dropped"]
    if any(m.state == "booked" for m in flights):
        return [], [], True
    rows = [m for m in flights if m.flight]
    for _, p in pending_modules(session, trip.id):
        if p.get("kind") == "flight" and p.get("flight") and p.get("starts_on") == on.isoformat():
            rows.append(TripModule(trip_id=trip.id, **{k: v for k, v in p.items() if k in MODULE_FIELDS and k not in ("starts_on", "ends_on")}, starts_on=on))
    rows.sort(key=lambda m: (m.departs or "99", m.flight))
    as_dict = lambda m: {"flight": m.flight, "from": m.from_airports, "to": m.to_airports, "departs": m.departs, "cost": m.cost, "points": m.points, "payer": m.payer, "at": _departure(m, on)}  # noqa: E731
    standby = [as_dict(m) for m in rows if m.payer in ("standby", "")]
    backup = [as_dict(m) for m in rows if m.payer in ("cash", "points")]
    return standby, backup, False


def _audience(trip: Trip) -> tuple[str, ...]:
    return tuple(t for t in trip.travelers.split(",") if t)


def _sent(session: Session, trip: Trip, on: date, stage: str) -> bool:
    return session.exec(select(TripCheck.id).where(TripCheck.trip_id == trip.id, TripCheck.leg_on == on, TripCheck.stage == stage)).first() is not None


def _flight_words(c: dict) -> str:
    return f"{c['flight']} {c['from']} {_time_words(c['departs'])}".strip()


def _backup_words(backup: list[dict]) -> str:
    if not backup:
        return ""
    cheapest = min(backup, key=lambda b: (b["cost"] is None, b["cost"] or 0))
    price = f"${cheapest['cost']}" if cheapest["cost"] is not None else f"{cheapest['points']:,} points" if cheapest["points"] else "price on the trip"
    return f" Backup: {cheapest['flight']} at {price}."


def run_checks(session: Session, now: datetime | None = None, send=None) -> dict:
    """Send the standby check pushes that are due. now is naive UTC."""
    now = now or utcnow()
    send = send or push.send
    local = now.replace(tzinfo=timezone.utc).astimezone(EASTERN)
    today = local.date()
    pushes = []
    trips = session.exec(select(Trip).where(col(Trip.status).in_(("planning", "set", "away")))).all()
    for trip in trips:
        legs = sorted({m.starts_on for m in session.exec(select(TripModule).where(TripModule.trip_id == trip.id, TripModule.kind == "flight")) if m.starts_on and m.state != "dropped" and m.starts_on >= today})
        for on in legs:
            standby, backup, made = _candidates(session, trip, on)
            if made:
                continue
            days = (on - today).days
            url = f"/#trip-{trip.id}"
            when = "tomorrow" if days == 1 else _day_words(on)
            stage, title, body = None, "", ""
            first = next((c for c in standby if c["at"]), None)
            if first and first["at"] - MORNING_LEAD <= now < first["at"] and not _sent(session, trip, on, "morning"):
                stage, title = "morning", f"Standby check: {trip.title}"
                body = "Check loads now. In order: " + ", ".join(_flight_words(c) for c in standby[:4]) + "." + _backup_words(backup)
            elif local.hour >= CHECK_HOUR and days == 1 and not _sent(session, trip, on, "eve"):
                stage, title = "eve", f"Flying tomorrow: {trip.title}"
                body = ("Check loads tonight. In order: " + ", ".join(_flight_words(c) for c in standby[:4]) + ".") if standby else "No standby candidates on the trip yet."
                body += _backup_words(backup)
            elif local.hour >= CHECK_HOUR and days in CHECK_DAYS and not _sent(session, trip, on, f"check:{days}"):
                stage, title = f"check:{days}", f"Check standby loads: {trip.title}"
                body = f"Flying {when}, {days} days out. {len(standby)} candidate{'s' if len(standby) != 1 else ''} on the trip." + _backup_words(backup)
            if stage:
                session.add(TripCheck(trip_id=trip.id, leg_on=on, stage=stage))
                session.commit()
                pushes.append((_audience(trip), title, body, url))
    sent = 0
    for people, title, body, url in pushes:
        sent += send(people, title, body, url)
    return {"pushes": len(pushes), "delivered": sent}
