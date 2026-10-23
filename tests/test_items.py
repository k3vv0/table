"""Items: capture, triage, merging, and the daily and weekly views (app/services/items.py).

Access is off here, so every request is Kev; tests/test_access.py covers who reaches
which space. Each test works in a fresh database so counts and views start empty."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

from app.db import engine, init_db
from app.main import app
from app.models import Activity, Item
from app.services import items as svc

client = TestClient(app)
TODAY = date(2026, 10, 23)


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    SQLModel.metadata.drop_all(engine)
    init_db()
    monkeypatch.setattr(svc, "today_local", lambda: TODAY)
    yield


def add(title, base="/api/plates/kev", **body):
    r = client.post(f"{base}/items", json={"title": title, **body})
    assert r.status_code == 201, r.text
    return r.json()


def triage(item_id, action, base="/api/plates/kev", **body):
    r = client.post(f"{base}/items/{item_id}/triage", json={"action": action, **body})
    assert r.status_code == 200, r.text
    return r.json()


def titles(view, base="/api/plates/kev"):
    return [i["title"] for i in client.get(f"{base}/items", params={"view": view}).json()["items"]]


# --- capture ---------------------------------------------------------------------------


def test_capture_lands_in_the_inbox_with_a_manual_source():
    item = add("Look into the Pinterest API approval")
    assert item["status"] == "inbox"
    assert item["space"] == "kev"
    assert item["created_by"] == "kev"
    assert item["sources"] == [{"kind": "manual", "ref": "", "snippet": "Look into the Pinterest API approval"}]
    assert titles("inbox") == ["Look into the Pinterest API approval"]


def test_capture_straight_onto_today():
    add("Call the plumber", today=True)
    assert titles("today") == ["Call the plumber"]
    assert titles("inbox") == []


def test_blank_title_is_refused():
    assert client.post("/api/plates/kev/items", json={"title": "   "}).status_code == 422


def test_spaces_are_separate():
    add("Change the filters", base="/api/table")
    add("Etsy restock")
    assert titles("inbox", "/api/table") == ["Change the filters"]
    assert titles("inbox") == ["Etsy restock"]


def test_an_id_from_another_space_is_a_404():
    plate_item = add("Private thing")
    assert client.get(f"/api/table/items/{plate_item['id']}").status_code == 404
    r = client.post(f"/api/table/items/{plate_item['id']}/triage", json={"action": "done"})
    assert r.status_code == 404


def test_unknown_plate_is_a_404():
    assert client.get("/api/plates/nobody/items").status_code == 404


# --- owners on the Table -----------------------------------------------------------------


def test_table_items_are_shared_unless_someone_takes_one():
    item = add("Book the cabin", base="/api/table")
    assert item["owner"] is None
    r = client.patch(f"/api/table/items/{item['id']}", json={"owner": "elizabeth"})
    assert r.json()["owner"] == "elizabeth"
    r = client.patch(f"/api/table/items/{item['id']}", json={"owner": None})
    assert r.json()["owner"] is None


@pytest.mark.parametrize("base, owner", [("/api/table", "bob"), ("/api/plates/kev", "kev")])
def test_bad_owners_are_refused(base, owner):
    assert client.post(f"{base}/items", json={"title": "x", "owner": owner}).status_code == 422


# --- triage ------------------------------------------------------------------------------


def test_triage_moves_between_views():
    item = add("Renew passport")
    triage(item["id"], "today")
    assert titles("today") == ["Renew passport"]
    triage(item["id"], "later", defer_until=(TODAY + timedelta(days=3)).isoformat())
    assert titles("today") == [] and titles("later") == ["Renew passport"]
    done = triage(item["id"], "done")
    assert done["done_at"] and titles("done") == ["Renew passport"]
    triage(item["id"], "reopen")
    assert titles("inbox") == ["Renew passport"]


def test_later_without_a_date_waits_for_the_weekly_review():
    item = add("Someday: repaint the fence")
    triage(item["id"], "later")
    assert titles("later") == ["Someday: repaint the fence"]
    review = client.get("/api/plates/kev/review").json()
    assert [i["title"] for i in review["later"]] == ["Someday: repaint the fence"]


def test_deferring_to_today_or_earlier_is_refused():
    item = add("x")
    r = client.post(f"/api/plates/kev/items/{item['id']}/triage", json={"action": "later", "defer_until": TODAY.isoformat()})
    assert r.status_code == 422


def test_deferred_items_come_back_on_their_day(monkeypatch):
    item = add("Follow up with the landlord")
    triage(item["id"], "later", defer_until=(TODAY + timedelta(days=2)).isoformat())
    assert titles("today") == []
    monkeypatch.setattr(svc, "today_local", lambda: TODAY + timedelta(days=2))
    assert titles("today") == ["Follow up with the landlord"]
    assert titles("later") == []
    counts = client.get("/api/plates/kev/items").json()["counts"]
    assert counts == {"today": 1, "inbox": 0, "later": 0}


def test_unknown_triage_action_is_refused():
    item = add("x")
    assert client.post(f"/api/plates/kev/items/{item['id']}/triage", json={"action": "nuke"}).status_code == 422


# --- merging -----------------------------------------------------------------------------


def test_merge_keeps_one_item_with_every_source():
    a = add("Pinterest approval", notes="from the meeting")
    b = add("Check Pinterest app status", notes="Slack thread", due_on=(TODAY + timedelta(days=5)).isoformat())
    c = add("pinterest api", due_on=(TODAY + timedelta(days=2)).isoformat())
    r = client.post("/api/plates/kev/items/merge", json={"keep_id": a["id"], "merge_ids": [b["id"], c["id"]]})
    assert r.status_code == 200, r.text
    kept = r.json()
    assert [s["snippet"] for s in kept["sources"]] == ["Pinterest approval", "Check Pinterest app status", "pinterest api"]
    assert kept["due_on"] == (TODAY + timedelta(days=2)).isoformat()  # earliest wins
    assert "from the meeting" in kept["notes"] and "Slack thread" in kept["notes"]
    assert titles("inbox") == ["Pinterest approval"]
    gone = client.get(f"/api/plates/kev/items/{b['id']}").json()
    assert gone["status"] == "merged" and gone["merged_into_id"] == a["id"]
    # A merged item can't be triaged back to life.
    assert client.post(f"/api/plates/kev/items/{b['id']}/triage", json={"action": "today"}).status_code == 409


def test_merge_needs_another_open_item_in_the_same_space():
    a = add("a")
    assert client.post("/api/plates/kev/items/merge", json={"keep_id": a["id"], "merge_ids": [a["id"]]}).status_code == 422
    other = add("on the table", base="/api/table")
    r = client.post("/api/plates/kev/items/merge", json={"keep_id": a["id"], "merge_ids": [other["id"]]})
    assert r.status_code == 404
    done = add("done one")
    triage(done["id"], "done")
    r = client.post("/api/plates/kev/items/merge", json={"keep_id": a["id"], "merge_ids": [done["id"]]})
    assert r.status_code == 409


# --- daily session and weekly review ---------------------------------------------------------


def test_daily_session_shows_today_due_this_week_and_inbox():
    on_plate = add("Pay the water bill", today=True)
    due_soon = add("Mom's flowers", due_on=(TODAY + timedelta(days=6)).isoformat())
    triage(due_soon["id"], "later", defer_until=(TODAY + timedelta(days=4)).isoformat())
    add("Far off", due_on=(TODAY + timedelta(days=20)).isoformat())
    session = client.get("/api/plates/kev/session").json()
    assert session["date"] == TODAY.isoformat()
    assert [i["title"] for i in session["today"]] == ["Pay the water bill"]
    assert [i["title"] for i in session["due_this_week"]] == ["Mom's flowers"]
    assert [i["title"] for i in session["inbox"]] == ["Far off"]
    assert on_plate["id"] not in [i["id"] for i in session["due_this_week"]]


def test_weekly_review_shows_overdue_stale_inbox_and_done():
    add("Overdue thing", due_on=(TODAY - timedelta(days=1)).isoformat())
    old = add("Old capture")
    with Session(engine) as session:
        row = session.get(Item, old["id"])
        row.created_at = row.created_at - timedelta(days=8)
        session.add(row)
        session.commit()
    finished = add("Finished")
    triage(finished["id"], "done")
    review = client.get("/api/plates/kev/review").json()
    assert [i["title"] for i in review["overdue"]] == ["Overdue thing"]
    assert [i["title"] for i in review["stale_inbox"]] == ["Old capture"]
    assert [i["title"] for i in review["done_this_week"]] == ["Finished"]


# --- audit -----------------------------------------------------------------------------------


def test_every_write_is_logged():
    a = add("a")
    b = add("b")
    client.patch(f"/api/plates/kev/items/{a['id']}", json={"title": "a, renamed"})
    triage(a["id"], "today")
    client.post("/api/plates/kev/items/merge", json={"keep_id": a["id"], "merge_ids": [b["id"]]})
    with Session(engine) as session:
        actions = [r.action for r in session.exec(select(Activity).order_by(Activity.id))]
    assert actions == ["item.create", "item.create", "item.edit", "item.triage", "item.merge"]
