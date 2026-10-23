"""Trips (DESIGN.md §13): places, trips and modules, the prep checklist, the text summary,
trip and module suggestions, and the standby check pushes. Access is off here, so requests
are Kev; the service-token cases call app.access and the services directly."""

import sqlite3
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

from app import access
from app.access import AGENT, MORNING, SWEEP, Identity
from app.db import engine, init_db
from app.main import app
from app.models import CalendarEvent, Item, TripCheck
from app.services import items as items_svc
from app.services import suggestions, trips

client = TestClient(app)
TODAY = date(2026, 10, 23)  # a Friday
T = "/api/table"


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    SQLModel.metadata.drop_all(engine)
    init_db()
    for module in (items_svc, trips, suggestions):
        monkeypatch.setattr(module, "today_local", lambda: TODAY)
    yield


def thanksgiving(**body):
    r = client.post(f"{T}/trips", json={"title": "Thanksgiving in San Marcos", "window": "week before to the weekend after", **body})
    assert r.status_code == 201, r.text
    return r.json()


def module(trip_id, **body):
    r = client.post(f"{T}/trips/{trip_id}/modules", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def file(skind="module", **payload):
    return client.post(f"{T}/suggestions", json={"kind": skind, "payload": payload, "rationale": "test"})


# --- trips and modules -------------------------------------------------------------------------


def test_a_trip_starts_in_planning_for_both_of_you():
    t = thanksgiving()
    assert t["status"] == "planning" and t["travelers"] == ["kev", "elizabeth"] and t["starts_on"] is None
    assert t["prep"] == [] and t["strip"] == [] and t["suggested"] == []
    assert client.get(f"{T}/trips").json()["trips"][0]["title"] == "Thanksgiving in San Marcos"


def test_trip_checks():
    thanksgiving()
    assert client.post(f"{T}/trips", json={"title": "Thanksgiving in San Marcos"}).status_code == 409
    assert client.post(f"{T}/trips", json={"title": " "}).status_code == 422
    assert client.post(f"{T}/trips", json={"title": "x", "travelers": ["andy"]}).status_code == 422
    assert client.post(f"{T}/trips", json={"title": "x", "starts_on": "2026-11-29", "ends_on": "2026-11-21"}).status_code == 422
    # Set means the dates are firm.
    assert client.post(f"{T}/trips", json={"title": "y", "status": "set"}).status_code == 422
    assert client.post(f"{T}/trips", json={"title": "z", "travelers": ["Beth"]}).json()["travelers"] == ["elizabeth"]


def test_modules_flights_legs_and_states():
    t = thanksgiving(starts_on="2026-11-21", ends_on="2026-11-29")
    leg = module(t["id"], kind="flight", starts_on="2026-11-21", from_airports="jfk, lga", to_airports="AUS", payer="standby", travelers=["kev"])
    assert leg["leg"] and leg["title"] == "Flight JFK/LGA → AUS" and leg["travelers"] == "kev"
    f = module(t["id"], kind="flight", starts_on="2026-11-21", from_airports="JFK", to_airports="AUS", flight="dl 1234", departs="06:00", arrives="09:10", payer="standby")
    assert f["title"] == "DL 1234 JFK → AUS" and not f["leg"] and f["state"] == "option"
    r = client.patch(f"{T}/trips/{t['id']}/modules/{f['id']}", json={"state": "chosen", "confirmation": "ABC123"})
    assert r.json()["state"] == "chosen" and r.json()["confirmation"] == "ABC123"
    bad = [
        {"kind": "boat", "title": "x"},
        {"kind": "flight", "flight": "DL 1", "from_airports": "JFK"},  # a real flight needs both ends and a day
        {"kind": "flight", "from_airports": "NEWYORK"},
        {"kind": "stay", "title": "x", "link": "http://example.com"},
        {"kind": "stay", "title": "x", "departs": "6am"},
        {"kind": "stay", "title": "x", "cost": -5},
        {"kind": "stay", "title": "x", "payer": "venmo"},
        {"kind": "stay", "title": ""},
        {"kind": "stay", "title": "x", "starts_on": "2026-11-22", "ends_on": "2026-11-21"},
    ]
    for body in bad:
        assert client.post(f"{T}/trips/{t['id']}/modules", json=body).status_code == 422, body
    solo = thanksgiving(title="Chicago work trip", travelers=["kev"])
    assert client.post(f"{T}/trips/{solo['id']}/modules", json={"kind": "stay", "title": "x", "travelers": ["elizabeth"]}).status_code == 422
    assert client.patch(f"{T}/trips/{solo['id']}/modules/{f['id']}", json={"state": "booked"}).status_code == 404


def test_the_strip_shows_gaps():
    t = thanksgiving(starts_on="2026-11-21", ends_on="2026-11-24")
    module(t["id"], kind="flight", starts_on="2026-11-21", from_airports="JFK", to_airports="AUS")
    module(t["id"], kind="stay", title="Beth's apartment", starts_on="2026-11-21", ends_on="2026-11-23", payer="family")
    strip = client.get(f"{T}/trips/{t['id']}").json()["strip"]
    assert [d["on"] for d in strip] == ["2026-11-21", "2026-11-22", "2026-11-23", "2026-11-24"]
    assert strip[0]["gaps"] == ["No ride to or from the airport"]
    assert strip[1]["gaps"] == []
    assert strip[2]["gaps"] == ["No stay tonight"]
    assert strip[3]["gaps"] == []  # the last day: no night to sleep


# --- the prep checklist ------------------------------------------------------------------------


def test_setting_a_trip_puts_up_the_prep_checklist_once():
    t = thanksgiving(starts_on="2026-11-21", ends_on="2026-11-29")
    with Session(engine) as session:
        session.add(CalendarEvent(space="kev", feed="work", uid="oc", summary="On-call (primary)", starts_on=date(2026, 11, 23)))
        session.commit()
    r = client.patch(f"{T}/trips/{t['id']}", json={"status": "set"})
    prep = r.json()["prep"]
    titles = [p["title"].split(" · ")[0] for p in prep]
    assert "Ask about subleasing the apartment" in titles  # 8 nights
    assert "On call: confirm you can work remotely from there" in titles
    assert prep[0]["due_on"] == "2026-10-31"  # 21 days before Nov 21
    assert next(p for p in prep if p["title"].startswith("On call"))["owner"] == "kev"
    assert next(p for p in prep if p["title"].startswith("Water the peppers"))["due_on"] == "2026-11-21"
    # Saving again doesn't make a second checklist.
    again = client.patch(f"{T}/trips/{t['id']}", json={"status": "away"}).json()["prep"]
    assert len(again) == len(prep)
    item = client.get(f"{T}/items/{prep[0]['id']}").json()
    assert item["trip_id"] == t["id"] and item["sources"][0]["kind"] == "trip"


def test_a_short_trip_skips_the_sublease_and_no_on_call_skips_that():
    t = thanksgiving(title="Weekend in Charlotte", starts_on="2026-11-06", ends_on="2026-11-08", status="set")
    titles = [p["title"] for p in client.get(f"{T}/trips/{t['id']}").json()["prep"]]
    assert not any("sublease" in x or "On call" in x for x in titles)
    assert len(titles) == 5


# --- the text summary --------------------------------------------------------------------------


def test_summary_lists_what_is_chosen_and_nothing_private():
    t = thanksgiving(starts_on="2026-11-21", ends_on="2026-11-29")
    f = module(t["id"], kind="flight", starts_on="2026-11-21", from_airports="JFK", to_airports="AUS", flight="DL 1234", departs="06:00", arrives="09:10", payer="standby", travelers=["kev"], confirmation="SECRET")
    client.patch(f"{T}/trips/{t['id']}/modules/{f['id']}", json={"state": "chosen"})
    module(t["id"], kind="stay", title="Beth's apartment", starts_on="2026-11-21", ends_on="2026-11-26", state="chosen")
    module(t["id"], kind="flight", starts_on="2026-11-21", from_airports="LGA", to_airports="AUS", flight="DL 999")  # just an option
    text = client.get(f"{T}/trips/{t['id']}/summary").text
    assert text.splitlines() == [
        "Thanksgiving in San Marcos",
        "Kev and Beth, Sat Nov 21 to Sun Nov 29",
        "Sat Nov 21: DL 1234 JFK to AUS, leaves 6am, lands 9:10am (standby, may change) (Kev)",
        "Sat Nov 21 to Thu Nov 26: staying at Beth's apartment",
    ]
    assert "SECRET" not in text and "DL 999" not in text


# --- places ------------------------------------------------------------------------------------


def test_places_and_one_home():
    home = client.post(f"{T}/places", json={"name": "Home", "airports": "JFK LGA", "bags_airport": "lga", "home": True}).json()
    assert home["airports"] == "JFK LGA" and home["bags_airport"] == "LGA" and home["home"]
    sm = client.post(f"{T}/places", json={"name": "San Marcos", "airports": "AUS SAT", "ride_cost": 60}).json()
    assert client.post(f"{T}/places", json={"name": "Home"}).status_code == 409
    client.patch(f"{T}/places/{sm['id']}", json={"home": True})
    places = client.get(f"{T}/places").json()["places"]
    assert [p["name"] for p in places if p["home"]] == ["San Marcos"]
    assert client.patch(f"{T}/places/999", json={"name": "x"}).status_code == 404


# --- suggestions -------------------------------------------------------------------------------


def test_a_trip_suggestion_accepted_becomes_a_trip():
    r = file(skind="trip", title="Christmas in Atlanta", window="Dec 19 to 27", places="Atlanta", travelers=["kev", "elizabeth"])
    assert r.status_code == 201, r.text
    assert file(skind="trip", title="Christmas in Atlanta").status_code == 409
    assert file(skind="trip", title="Booked already", status="set").status_code == 422
    s = r.json()
    assert client.get(f"{T}/suggestions").json()["suggestions"][0]["payload"]["window"] == "Dec 19 to 27"
    assert client.post(f"{T}/suggestions/{s['id']}/accept", json={}).status_code == 200
    assert client.get(f"{T}/trips").json()["trips"][0]["title"] == "Christmas in Atlanta"
    assert file(skind="trip", title="Christmas in Atlanta").status_code == 409  # the trip is there now
    # Trips are the Table's.
    plate = client.post("/api/plates/kev/suggestions", json={"kind": "trip", "payload": {"title": "x"}})
    assert plate.status_code == 422


def test_module_options_from_agents_show_on_the_trip_and_can_be_chosen_in_one_tap():
    t = thanksgiving(starts_on="2026-11-21", ends_on="2026-11-29")
    body = dict(trip_id=t["id"], kind="flight", starts_on="2026-11-21", from_airports="JFK", to_airports="AUS", flight="DL 1234", departs="06:00", payer="standby", link="https://www.delta.com/")
    r = file(**body)
    assert r.status_code == 201, r.text
    assert file(**body).status_code == 409
    assert file(**{**body, "trip_id": 999}).status_code == 404
    assert file(**{**body, "link": "javascript:alert(1)"}).status_code == 422
    # An agent can't slip in a state or a booking code.
    sneaky = file(**{**body, "flight": "DL 5", "state": "booked", "confirmation": "X"}).json()
    assert sneaky["payload"]["state"] == "option" and "confirmation" not in sneaky["payload"]
    page = client.get(f"{T}/trips/{t['id']}").json()
    assert [s["flight"] for s in page["suggested"]] == ["DL 1234", "DL 5"]
    # Options live on the trip page, but the list says which trip.
    listed = client.get(f"{T}/suggestions").json()["suggestions"]
    assert listed[0]["payload"]["trip_title"] == "Thanksgiving in San Marcos"
    client.post(f"{T}/suggestions/{r.json()['id']}/accept", json={"state": "chosen"})
    m = client.get(f"{T}/trips/{t['id']}").json()["modules"][0]
    assert m["flight"] == "DL 1234" and m["state"] == "chosen" and m["created_by"] == "kev"
    assert file(**body).status_code == 409  # it's on the trip now


def test_a_refresh_updates_an_option_and_never_a_chosen_one():
    t = thanksgiving(starts_on="2026-11-21", ends_on="2026-11-29")
    m = module(t["id"], kind="flight", starts_on="2026-11-21", from_airports="JFK", to_airports="AUS", flight="B6 1", payer="cash", cost=210)
    r = file(trip_id=t["id"], module_id=m["id"], cost=189, notes="Down $21 since Tuesday")
    assert r.status_code == 201, r.text
    assert file(trip_id=t["id"], module_id=m["id"], cost=180).status_code == 409
    client.post(f"{T}/suggestions/{r.json()['id']}/accept", json={})
    after = client.get(f"{T}/trips/{t['id']}").json()["modules"][0]
    assert after["cost"] == 189 and after["flight"] == "B6 1" and after["state"] == "option"
    client.patch(f"{T}/trips/{t['id']}/modules/{m['id']}", json={"state": "chosen"})
    assert file(trip_id=t["id"], module_id=m["id"], cost=150).status_code == 409


def test_service_tokens_read_trips_but_never_write_them_or_see_booking_codes():
    for role in (AGENT, SWEEP, MORNING):
        who = Identity(role, "test")
        assert access.allowed(who, "GET", "/api/table/trips/1")
        for method, path in (("POST", "/api/table/trips"), ("PATCH", "/api/table/trips/1"), ("POST", "/api/table/trips/1/modules"),
                             ("PATCH", "/api/table/trips/1/modules/1"), ("POST", "/api/table/places"), ("PATCH", "/api/table/places/1")):
            assert not access.allowed(who, method, path), (role, method, path)
        assert access.allowed(who, "POST", "/api/table/suggestions")
    t = thanksgiving()
    module(t["id"], kind="stay", title="Hotel", confirmation="ABC123")
    with Session(engine) as session:
        trip = trips.get_trip(session, t["id"])
        assert "confirmation" not in trips.trip_out(session, trip, person=False)["modules"][0]
        assert trips.trip_out(session, trip, person=True)["modules"][0]["confirmation"] == "ABC123"


# --- standby check pushes ----------------------------------------------------------------------


def checks(at_eastern: str):
    """Run the timer at a US Eastern wall-clock time ("2026-11-20 20:05")."""
    local = datetime.fromisoformat(at_eastern).replace(tzinfo=trips.EASTERN)
    now = local.astimezone(trips.timezone.utc).replace(tzinfo=None)
    sent = []
    with Session(engine) as session:
        trips.run_checks(session, now, send=lambda people, title, body, url: sent.append((people, title, body, url)) or 1)
    return sent


def standby_leg():
    t = thanksgiving(starts_on="2026-11-21", ends_on="2026-11-29")
    module(t["id"], kind="flight", starts_on="2026-11-21", from_airports="LGA", to_airports="AUS", flight="DL 2", departs="07:00", payer="standby")
    module(t["id"], kind="flight", starts_on="2026-11-21", from_airports="JFK", to_airports="AUS", flight="DL 1", departs="06:00", payer="standby")
    module(t["id"], kind="flight", starts_on="2026-11-21", from_airports="JFK", to_airports="AUS", flight="B6 9", departs="08:00", payer="cash", cost=189)
    return t


def test_the_check_rhythm():
    t = standby_leg()
    assert checks("2026-11-07 20:05")[0][1] == "Check standby loads: Thanksgiving in San Marcos"  # 14 days out
    assert checks("2026-11-07 21:00") == []  # once
    assert checks("2026-11-08 20:05") == []  # 13 days out: not a check day
    assert checks("2026-11-16 19:00") == []  # before 8pm
    five = checks("2026-11-16 20:30")
    assert five[0][0] == ("kev", "elizabeth") and "5 days out. 2 candidates" in five[0][2] and "B6 9 at $189" in five[0][2]
    eve = checks("2026-11-20 20:00")
    assert eve[0][1] == "Flying tomorrow: Thanksgiving in San Marcos"
    assert eve[0][2].startswith("Check loads tonight. In order: DL 1 JFK 6am, DL 2 LGA 7am.")
    assert eve[0][3] == f"/#trip-{t['id']}"
    assert checks("2026-11-21 02:30") == []  # 3h30 before DL 1
    morning = checks("2026-11-21 03:05")
    assert morning[0][1].startswith("Standby check") and "Check loads now" in morning[0][2]
    assert checks("2026-11-21 03:20") == []


def test_pending_agent_options_count_as_candidates_and_times_are_local_to_the_origin():
    t = thanksgiving(starts_on="2026-11-28", ends_on="2026-11-29")
    module(t["id"], kind="flight", starts_on="2026-11-29", from_airports="AUS SAT", to_airports="JFK LGA")  # the leg
    file(trip_id=t["id"], kind="flight", starts_on="2026-11-29", from_airports="AUS", to_airports="JFK", flight="DL 3", departs="06:00", payer="standby")
    eve = checks("2026-11-28 20:01")
    assert "DL 3 AUS 6am" in eve[0][2]
    # 6am in Austin is 7am Eastern: the morning push is at 4am Eastern, not 3am.
    assert checks("2026-11-29 03:30") == []
    assert checks("2026-11-29 04:01")[0][1].startswith("Standby check")


def test_made_it_stops_the_pushes_for_that_day():
    t = standby_leg()
    first = client.get(f"{T}/trips/{t['id']}").json()["modules"][0]
    client.patch(f"{T}/trips/{t['id']}/modules/{first['id']}", json={"state": "booked"})
    assert checks("2026-11-20 20:00") == []
    with Session(engine) as session:
        assert session.exec(select(TripCheck)).all() == []


def test_ideas_and_finished_trips_get_no_pushes():
    t = standby_leg()
    client.patch(f"{T}/trips/{t['id']}", json={"status": "idea"})
    assert checks("2026-11-20 20:00") == []


# --- the migration -----------------------------------------------------------------------------


def test_migration_adds_trip_id_to_an_old_database(tmp_path):
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE item (id INTEGER PRIMARY KEY, title VARCHAR NOT NULL)")
    con.execute("INSERT INTO item (title) VALUES ('Change the filter')")
    con.commit()
    con.close()
    script = Path(__file__).resolve().parent.parent / "scripts" / "migrate_trips.py"
    env = {"DATABASE_URL": f"sqlite:///{db}", "PATH": ""}
    for _ in range(2):  # idempotent
        out = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True, cwd=script.parent.parent)
        assert out.returncode == 0, out.stderr
    assert "already there" in out.stdout
    con = sqlite3.connect(db)
    assert "trip_id" in [r[1] for r in con.execute("PRAGMA table_info(item)")]
    assert {"trip", "trip_module", "place", "trip_check"} <= {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert con.execute("SELECT title FROM item").fetchone() == ("Change the filter",)


def test_prep_items_are_ordinary_items():
    t = thanksgiving(starts_on="2026-11-21", ends_on="2026-11-29", status="set")
    with Session(engine) as session:
        prep = session.exec(select(Item).where(Item.trip_id == t["id"])).all()
    assert prep and all(i.space == "table" and i.status == "inbox" for i in prep)
