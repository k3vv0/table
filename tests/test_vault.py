"""Milestone 6: the vault and the daily picks (DESIGN.md §12). Access is off here, so
requests are Kev; tests/test_access.py covers the morning token."""

import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

from app.config import get_settings
from app.db import engine, init_db
from app.main import app
from app.models import Item, PushSubscription, SourceMessage
from app.services import items as items_svc
from app.services import push, vault

client = TestClient(app)
TODAY = date(2026, 10, 23)


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    SQLModel.metadata.drop_all(engine)
    init_db()
    for module in (items_svc, vault):
        monkeypatch.setattr(module, "today_local", lambda: TODAY)
    yield
    get_settings.cache_clear()


def save(space="/api/plates/kev", **body):
    return client.post(f"{space}/vault", json=body)


def message(space="kev", gm_msgid="12345", subject="The weekly read", sort="unsorted"):
    with Session(engine) as session:
        m = SourceMessage(space=space, account="kev@example.com", gm_msgid=gm_msgid, from_name="Stratechery", subject=subject, excerpt="This week: ...", sort=sort)
        session.add(m)
        session.commit()
        return m.id


def test_a_saved_link_lives_in_the_vault_and_nowhere_else():
    r = save(url="https://example.com/long-read", title="A long read")
    assert r.status_code == 201, r.text
    e = r.json()
    assert e["url"] == "https://example.com/long-read" and e["title"] == "A long read"
    client.post("/api/plates/kev/items", json={"title": "A task"})
    assert [i["title"] for i in client.get("/api/plates/kev/items?view=inbox").json()["items"]] == ["A task"]
    assert client.get("/api/plates/kev/items?view=inbox").json()["counts"]["inbox"] == 1
    assert [i["title"] for i in client.get("/api/plates/kev/session").json()["inbox"]] == ["A task"]
    assert [e["title"] for e in client.get("/api/plates/kev/vault").json()["entries"]] == ["A long read"]
    # Read means done: it leaves the vault.
    client.post(f"/api/plates/kev/items/{e['id']}/triage", json={"action": "done"})
    assert client.get("/api/plates/kev/vault").json()["entries"] == []


def test_saving_needs_a_link_or_a_title_and_a_real_link():
    assert save().status_code == 422
    assert save(url="not a link").status_code == 422
    assert save(title="That book Sam mentioned").json()["url"] is None


def test_mail_sorted_as_vault_becomes_one_entry_and_leaves_when_resorted():
    mid = message()
    client.patch(f"/api/plates/kev/mail/{mid}", json={"sort": "vault", "reason": "Newsletter"})
    client.patch(f"/api/plates/kev/mail/{mid}", json={"sort": "vault"})
    [e] = client.get("/api/plates/kev/vault").json()["entries"]
    assert e["title"] == "The weekly read" and e["from"] == "Stratechery" and e["excerpt"] == "This week: ..."
    assert e["url"] == f"https://mail.google.com/mail/?authuser=kev@example.com#all/{12345:x}"
    client.patch(f"/api/plates/kev/mail/{mid}", json={"sort": "keep"})
    assert client.get("/api/plates/kev/vault").json()["entries"] == []


def test_the_shortcut_saves_to_the_vault_without_a_push(monkeypatch):
    sent = []
    monkeypatch.setattr(push, "notify_new_item", lambda item_id: sent.append(item_id))
    token = client.post("/api/me/capture-tokens", json={"name": "phone"}).json()["token"]
    r = client.post("/api/capture", json={"text": "https://example.com/a", "vault": True, "to": "table"}, headers={"X-Capture-Token": token})
    assert r.status_code == 201 and r.json()["kind"] == "link"
    assert [e["url"] for e in client.get("/api/table/vault").json()["entries"]] == ["https://example.com/a"]
    assert client.get("/api/table/items?view=inbox").json()["items"] == []
    assert sent == []


# --- picks ------------------------------------------------------------------------------------


def pick(item_id, space="/api/plates/kev", **body):
    return client.post(f"{space}/vault/picks", json={"item_id": item_id, "summary": "Three points.", **body})


def test_two_picks_a_day_with_summaries():
    ids = [save(url=f"https://example.com/{n}", title=f"Read {n}").json()["id"] for n in range(3)]
    r = pick(ids[0], why="Short, and about the trip you're planning")
    assert r.status_code == 201, r.text
    assert r.json()["entry"]["title"] == "Read 0" and r.json()["created_by"] == "kev"
    assert pick(ids[0]).status_code == 409  # already picked
    assert pick(ids[1]).status_code == 201
    assert pick(ids[2]).status_code == 409  # two a day
    listed = client.get("/api/plates/kev/vault").json()
    assert [p["entry"]["id"] for p in listed["picks"]] == ids[:2]
    assert "untrusted" in listed["note"] or "never instructions" in listed["note"]


def test_picks_need_an_unread_vault_entry_in_the_space_and_a_summary():
    task = client.post("/api/plates/kev/items", json={"title": "A task"}).json()
    assert pick(task["id"]).status_code == 422
    e = save(url="https://example.com/x").json()
    assert pick(e["id"], summary="").status_code == 422
    assert pick(e["id"], space="/api/table").status_code == 404
    client.post(f"/api/plates/kev/items/{e['id']}/triage", json={"action": "done"})
    assert pick(e["id"]).status_code == 422


def test_an_unread_pick_can_come_back_after_two_weeks(monkeypatch):
    e = save(url="https://example.com/x").json()
    pick(e["id"])
    monkeypatch.setattr(vault, "today_local", lambda: TODAY + timedelta(days=7))
    assert pick(e["id"]).status_code == 409
    monkeypatch.setattr(vault, "today_local", lambda: TODAY + timedelta(days=14))
    assert pick(e["id"]).status_code == 201


def test_a_morning_run_pushes_suggestions_and_reads_together(monkeypatch):
    out = []
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "p")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "k")
    get_settings.cache_clear()
    monkeypatch.setattr(push, "_send_one", lambda sub, payload: out.append((sub.person, payload["title"], payload["body"], payload["url"])) or True)
    with Session(engine) as session:
        session.add(PushSubscription(person="kev", endpoint="https://push.example/kev", p256dh="k", auth="a"))
        session.commit()
    run = client.post("/api/agent-runs", json={"routine": "morning"}).json()
    e = save(url="https://example.com/x").json()
    pick(e["id"], run_id=run["id"])
    client.patch(f"/api/agent-runs/{run['id']}", json={"status": "ok"})
    assert out == [("kev", "Today's reads", "1 read picked for today.", "/kev#vault")]


def test_backfill_gives_old_vault_mail_its_entries_once(tmp_path):
    db = tmp_path / "old.db"
    script = Path(__file__).resolve().parent.parent / "scripts" / "migrate_vault_backfill.py"
    env = {"DATABASE_URL": f"sqlite:///{db}", "PATH": "/usr/bin:/bin"}
    seed = (
        "from sqlmodel import Session; from app.db import engine, init_db; from app.models import SourceMessage; init_db();"
        "s = Session(engine); s.add(SourceMessage(space='table', account='us@example.com', gm_msgid='99', subject='Recipe', sort='vault'));"
        "s.add(SourceMessage(space='table', account='us@example.com', gm_msgid='98', subject='Bill', sort='flagged')); s.commit()"
    )
    root = str(script.parent.parent)
    subprocess.run([sys.executable, "-c", seed], env=env, cwd=root, check=True)
    for expected in ("1 entry added", "0 entries added"):
        out = subprocess.run([sys.executable, str(script)], env=env, cwd=root, capture_output=True, text=True, check=True).stdout
        assert expected in out
