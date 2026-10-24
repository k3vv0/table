"""Milestone 5: people, key dates, the daily alerts, date and goal suggestions (DESIGN.md
§11). Access is off here, so requests are Kev; tests/test_access.py covers the tokens."""

import json
import sqlite3
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

from app.db import engine, init_db
from app.main import app
from app.models import Activity, DateAlert, Item, KeyDate
from app.services import dates
from app.services import items as items_svc
from app.services import suggestions

client = TestClient(app)
TODAY = date(2026, 10, 23)  # a Friday


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    SQLModel.metadata.drop_all(engine)
    init_db()
    for module in (items_svc, dates, suggestions):
        monkeypatch.setattr(module, "today_local", lambda: TODAY)
    yield


def add(space="/api/plates/kev", **body):
    return client.post(f"{space}/dates", json={"weight": "light", **body})


def alerts(today=TODAY):
    sent = []
    with Session(engine) as session:
        report = dates.run_alerts(session, today, send=lambda people, title, body, url: sent.append((tuple(people), title, url)) or len(people))
    return report, sent


def items_in(space):
    with Session(engine) as session:
        return list(session.exec(select(Item).where(Item.space == space).order_by(Item.id)))


# --- when dates fall --------------------------------------------------------------------------


def kd(**fields):
    return KeyDate(space="kev", title="x", **fields)


def test_rules_fixed_days_and_leap_days():
    assert dates.occurrence(kd(month=5, nth=2, weekday=6), 2027) == date(2027, 5, 9)  # Mother's Day
    assert dates.occurrence(kd(month=11, nth=4, weekday=3), 2026) == date(2026, 11, 26)  # Thanksgiving
    assert dates.occurrence(kd(month=5, nth=-1, weekday=0), 2027) == date(2027, 5, 31)  # Memorial Day
    assert dates.occurrence(kd(month=2, day=29), 2027) == date(2027, 2, 28)
    assert dates.occurrence(kd(month=2, day=29), 2028) == date(2028, 2, 29)


def test_next_occurrence_rolls_over_and_one_offs_expire():
    assert dates.next_on(kd(month=10, day=23), TODAY) == TODAY
    assert dates.next_on(kd(month=10, day=22), TODAY) == date(2027, 10, 22)
    assert dates.next_on(kd(month=6, day=12, year=2027, recurs=False), TODAY) == date(2027, 6, 12)
    assert dates.next_on(kd(month=6, day=12, year=2026, recurs=False), TODAY) is None
    # A yearly date's year is its first one: a wedding next summer starts then.
    assert dates.next_on(kd(month=6, day=12, year=2028), TODAY) == date(2028, 6, 12)
    assert dates.turns(kd(month=11, day=2, year=1966), date(2026, 11, 2)) == 60


def test_describe():
    assert dates.describe(kd(month=5, nth=2, weekday=6)) == "Every year on the second Sunday of May"
    assert dates.describe(kd(month=6, day=12, year=2027, recurs=False)) == "Once, June 12, 2027"
    assert dates.describe(kd(month=4, day=3, year=2021)) == "Every year on April 3, since 2021"


# --- adding dates, people and presets --------------------------------------------------------


def test_add_a_date_with_a_new_person_and_list_it():
    r = add(title="Mom's birthday", month=11, day=2, year=1966, weight="planned", person_name="Mom", relation="Kev's mom")
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["next_on"] == "2026-11-02" and d["days_away"] == 10 and d["turns"] == 60
    assert d["person"]["name"] == "Mom" and d["person"]["relation"] == "Kev's mom"
    # Same name again reuses the person.
    add(title="Mom and Dad's anniversary", month=7, day=4, person_name="Mom")
    people = client.get("/api/plates/kev/people").json()["people"]
    assert [p["name"] for p in people] == ["Mom"]
    listed = client.get("/api/plates/kev/dates").json()["dates"]
    assert [x["title"] for x in listed] == ["Mom's birthday", "Mom and Dad's anniversary"]  # soonest first


@pytest.mark.parametrize(
    "body, message",
    [
        ({"title": "", "month": 1, "day": 1}, "title"),
        ({"title": "x", "month": 13, "day": 1}, "month"),
        ({"title": "x", "month": 4, "day": 31}, "isn't in that month"),
        ({"title": "x", "month": 5, "nth": 5, "weekday": 6}, "rule"),
        ({"title": "x", "month": 6, "day": 12, "recurs": False}, "needs its year"),
        ({"title": "x", "month": 6, "day": 12, "year": 2025, "recurs": False}, "already passed"),
        ({"title": "x", "month": 2, "day": 29, "year": 2027, "recurs": False}, "that year"),
        ({"title": "x", "month": 1, "day": 1, "weight": "huge"}, "weight"),
    ],
)
def test_bad_dates_are_refused(body, message):
    r = add(**body)
    assert r.status_code == 422 and message in r.json()["detail"]


def test_owner_only_on_the_table_and_no_duplicate_titles():
    assert add(title="x", month=1, day=1, owner="kev").status_code == 422
    assert add("/api/table", title="Filters", month=1, day=1, owner="kev").json()["owner"] == "kev"
    assert add("/api/table", title="Filters", month=2, day=1).status_code == 409


def test_presets_add_once_and_leave_the_list():
    left = {p["key"] for p in client.get("/api/table/dates").json()["presets"]}
    assert {"valentines", "girlfriends-day", "mothers-day", "thanksgiving"} <= left
    r = client.post("/api/table/dates/presets/mothers-day")
    assert r.status_code == 201 and r.json()["next_on"] == "2027-05-09" and r.json()["weight"] == "planned"
    assert client.post("/api/table/dates/presets/mothers-day").status_code == 409
    assert client.post("/api/table/dates/presets/nope").status_code == 404
    assert "mothers-day" not in {p["key"] for p in client.get("/api/table/dates").json()["presets"]}
    # Presets are per space: Kev's Plate still offers it.
    assert "mothers-day" in {p["key"] for p in client.get("/api/plates/kev/dates").json()["presets"]}


def test_edit_and_remove_a_date():
    d = add(title="Anniversary", month=4, day=3, year=2021).json()
    r = client.patch(f"/api/plates/kev/dates/{d['id']}", json={"weight": "planned", "day": 4})
    assert r.json()["weight"] == "planned" and r.json()["day"] == 4 and r.json()["turns"] == 6
    assert client.patch(f"/api/plates/kev/dates/{d['id']}", json={"month": 2, "day": 30}).status_code == 422
    assert client.patch(f"/api/plates/kev/dates/{d['id']}", json={"active": False}).json() == {"id": d["id"], "removed": True}
    assert client.get("/api/plates/kev/dates").json()["dates"] == []
    # A removed date can be added again, and an id from another space is a 404.
    t = add("/api/table", title="Anniversary", month=4, day=3).json()
    assert client.patch(f"/api/plates/kev/dates/{t['id']}", json={"weight": "planned"}).status_code == 404


def test_people_notes_and_archive():
    p = client.post("/api/plates/kev/people", json={"name": "Beth", "relation": "partner", "notes": "Loves orchids"}).json()
    assert p["notes"] == "Loves orchids"
    assert client.patch(f"/api/plates/kev/people/{p['id']}", json={"notes": "Orchids, size M"}).json()["notes"] == "Orchids, size M"
    assert client.get("/api/table/people").json()["people"] == []  # a Plate's people stay there
    assert client.patch(f"/api/table/people/{p['id']}", json={"name": "x"}).status_code == 404
    client.patch(f"/api/plates/kev/people/{p['id']}", json={"archived": True})
    assert client.get("/api/plates/kev/people").json()["people"] == []


# --- the daily alerts -------------------------------------------------------------------------


def test_a_planned_date_goes_up_30_days_ahead_once():
    add(title="Beth's birthday", month=11, day=23, weight="planned", person_name="Beth")
    report, sent = alerts(TODAY)
    assert report["items"] == 0 and sent == []  # 31 days out: tomorrow it's 30
    report, sent = alerts(TODAY + timedelta(days=1))
    assert report["items"] == 1
    assert sent == [(("kev",), "In 30 days: Beth's birthday", "/kev#inbox")]
    [item] = items_in("kev")
    assert item.kind == "date" and item.status == "inbox" and item.due_on == date(2026, 11, 23)
    assert item.title == "Beth's birthday · Mon Nov 23" and item.created_by == "system"
    # The next run, and the one after, add nothing; the day itself pushes once.
    assert alerts(TODAY + timedelta(days=2)) == ({"items": 0, "pushes": 0, "delivered": 0}, [])
    _, sent = alerts(date(2026, 11, 23))
    assert sent == [(("kev",), "Today: Beth's birthday", "/kev#today")]
    assert alerts(date(2026, 11, 23))[1] == []
    assert len(items_in("kev")) == 1
    # The list shows the prep item for the date's next occurrence.
    listed = client.get("/api/plates/kev/dates").json()["dates"][0]
    assert listed["prep_item_id"] == item.id and listed["options_suggested"] is False


def test_a_missed_morning_catches_up_and_a_late_add_goes_up_at_once():
    add(title="Dentist's anniversary", month=11, day=10, weight="planned")  # 18 days away
    report, sent = alerts()
    assert report["items"] == 1 and sent[0][1] == "In 18 days: Dentist's anniversary"


def test_a_light_date_lands_on_today_the_day_before_and_pushes_twice():
    add("/api/table", title="Girlfriend's Day", month=10, day=24, owner="kev")
    _, sent = alerts()
    [item] = items_in("table")
    assert item.status == "today" and item.owner == "kev" and item.due_on == date(2026, 10, 24)
    assert sent == [(("kev",), "Tomorrow: Girlfriend's Day", "/#today")]  # only the owner on the Table
    _, sent = alerts(date(2026, 10, 24))
    assert sent == [(("kev",), "Today: Girlfriend's Day", "/#today")]
    assert len(items_in("table")) == 1


def test_a_shared_table_date_tells_everyone_and_birthdays_count_years():
    add("/api/table", title="Mom's birthday", month=10, day=24, year=1966, person_name="Mom")
    _, sent = alerts()
    assert sent == [(("kev", "elizabeth", "household"), "Tomorrow: Mom's birthday (turns 60)", "/#today")]


def test_removed_and_passed_dates_stay_quiet():
    d = add(title="Gone", month=10, day=24).json()
    client.patch(f"/api/plates/kev/dates/{d['id']}", json={"active": False})
    assert alerts() == ({"items": 0, "pushes": 0, "delivered": 0}, [])


def test_alerts_land_in_the_activity_log():
    add(title="Tomorrow thing", month=10, day=24)
    alerts()
    with Session(engine) as session:
        rows = session.exec(select(Activity).where(Activity.role == "system")).all()
        assert [r.action for r in rows] == ["item.create"]
        assert session.exec(select(DateAlert)).all()


# --- suggestions: dates and goals -------------------------------------------------------------


def file(space="/api/plates/kev", **body):
    return client.post(f"{space}/suggestions", json=body)


def test_a_date_suggestion_adds_the_date_and_person_only_when_accepted():
    payload = {"title": "Sam's graduation", "month": 5, "day": 15, "year": 2027, "recurs": False, "weight": "planned", "person_name": "Sam", "relation": "Beth's cousin"}
    r = file(kind="date", payload=payload, rationale="From the dates interview")
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["title"] == "Sam's graduation" and s["payload"]["rule"] == "Once, May 15, 2027"
    assert client.get("/api/plates/kev/dates").json()["dates"] == []
    assert file(kind="date", payload=payload).status_code == 409  # already pending
    out = client.post(f"/api/plates/kev/suggestions/{s['id']}/accept").json()
    assert out["item_id"] is None and out["suggestion"]["status"] == "accepted"
    [d] = client.get("/api/plates/kev/dates").json()["dates"]
    assert d["title"] == "Sam's graduation" and d["person"]["relation"] == "Beth's cousin" and d["weight"] == "planned"
    assert file(kind="date", payload=payload).status_code == 409  # now it's a real date


def test_a_date_suggestion_can_be_changed_before_it_is_accepted():
    payload = {"title": "Mother's Day", "month": 5, "nth": 2, "weekday": 6, "weight": "light", "person_name": "Mom"}
    s = file(kind="date", payload=payload).json()
    r = client.post(f"/api/plates/kev/suggestions/{s['id']}/accept", json={"date": {"month": 2, "day": 30}})
    assert r.status_code == 422  # checked like a date typed in, and still pending
    assert client.get("/api/plates/kev/suggestions").json()["suggestions"][0]["status"] == "pending"
    edits = {"title": "Mom's birthday", "month": 6, "day": 3, "year": 1960, "weight": "planned", "person_name": "Mom", "relation": "Kev's mom", "id": 99}
    r = client.post(f"/api/plates/kev/suggestions/{s['id']}/accept", json={"date": edits})
    assert r.status_code == 200, r.text
    [d] = client.get("/api/plates/kev/dates").json()["dates"]
    assert (d["title"], d["month"], d["day"], d["nth"], d["year"], d["weight"]) == ("Mom's birthday", 6, 3, None, 1960, "planned")
    assert d["person"]["name"] == "Mom" and d["person"]["relation"] == "Kev's mom"


def test_a_bad_date_suggestion_is_refused():
    r = file(kind="date", payload={"title": "x", "month": 2, "day": 31})
    assert r.status_code == 422


def test_a_goal_suggestion_links_an_item_and_copies_nothing_else():
    item = client.post("/api/table/items", json={"title": "Anniversary trip"}).json()
    assert file("/api/table", kind="goal", item_id=item["id"]).status_code == 422
    assert file("/api/table", kind="goal", item_id=item["id"], payload={"moneyhub_goal_id": "7"}).status_code == 422
    s = file("/api/table", kind="goal", item_id=item["id"], title="Trip fund", payload={"moneyhub_goal_id": 7, "balance": 1234}).json()
    assert s["payload"] == {"moneyhub_goal_id": 7}  # only the number is kept
    client.post(f"/api/table/suggestions/{s['id']}/accept")
    assert client.get(f"/api/table/items/{item['id']}").json()["moneyhub_goal_id"] == 7


def test_a_person_links_and_unlinks_a_goal_by_hand():
    item = client.post("/api/table/items", json={"title": "Wedding fund"}).json()
    assert client.patch(f"/api/table/items/{item['id']}", json={"moneyhub_goal_id": 3}).json()["moneyhub_goal_id"] == 3
    assert client.patch(f"/api/table/items/{item['id']}", json={"moneyhub_goal_id": 0}).status_code == 422
    assert client.patch(f"/api/table/items/{item['id']}", json={"moneyhub_goal_id": None}).json()["moneyhub_goal_id"] is None


def test_ideas_on_a_prep_item_show_as_suggested():
    add(title="Anniversary", month=11, day=1, weight="planned")
    alerts()
    [item] = items_in("kev")
    file(kind="note", item_id=item.id, notes="1. Dinner at X https://example.com/x\n2. ...\n3. ...")
    assert client.get("/api/plates/kev/dates").json()["dates"][0]["options_suggested"] is True


# --- the migration ----------------------------------------------------------------------------


def test_payload_migration_is_idempotent(tmp_path):
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE suggestion (id INTEGER PRIMARY KEY, space VARCHAR NOT NULL, kind VARCHAR NOT NULL)")
    con.execute("INSERT INTO suggestion (space, kind) VALUES ('table', 'item')")
    con.commit()
    con.close()
    script = Path(__file__).resolve().parent.parent / "scripts" / "migrate_suggestion_payload.py"
    env = {"DATABASE_URL": f"sqlite:///{db}", "PATH": "/usr/bin:/bin"}
    for expected in ("added", "already there"):
        out = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True, check=True).stdout
        assert expected in out
    con = sqlite3.connect(db)
    assert con.execute("SELECT payload FROM suggestion").fetchone() == ("",)
    assert {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")} >= {"person", "key_date", "date_alert"}
