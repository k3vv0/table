"""Milestone 3: the home-screen app, Web Push and the "Send to Plate" Shortcut
(DESIGN.md §9). Access is off here, so every request is Kev unless a test turns it on;
pushes never leave the process (push._send_one is swapped out)."""

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

from app.config import get_settings
from app.db import engine, init_db
from app.main import app
from app.models import Item, ItemSource, PushSubscription
from app.services import push

client = TestClient(app)


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    SQLModel.metadata.drop_all(engine)
    init_db()
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "test-public")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "test-private")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def sent(monkeypatch):
    """Every push that would have gone out: (person, title, body, url)."""
    out = []

    def fake(sub, payload):
        out.append((sub.person, payload["title"], payload["body"], payload["url"]))
        return True

    monkeypatch.setattr(push, "_send_one", fake)
    return out


def subscribe(person, endpoint, **flags):
    with Session(engine) as session:
        session.add(PushSubscription(person=person, endpoint=endpoint, p256dh="k", auth="a", **flags))
        session.commit()


# --- home-screen app ------------------------------------------------------------------------


def test_manifest_opens_on_your_own_plate():
    r = client.get("/manifest.webmanifest")
    assert r.headers["content-type"].startswith("application/manifest+json")
    body = r.json()
    assert body["display"] == "standalone" and body["start_url"] == "/kev" and body["scope"] == "/"
    for icon in body["icons"]:
        assert client.get(icon["src"]).status_code == 200


def test_service_worker_is_served_from_the_root():
    r = client.get("/sw.js")
    assert r.status_code == 200 and r.headers["service-worker-allowed"] == "/"
    assert "showNotification" in r.text


def test_pages_link_the_manifest_and_settings():
    body = client.get("/").text
    assert 'rel="manifest"' in body and 'href="/settings"' in body
    assert client.get("/settings").status_code == 200


# --- push subscriptions ------------------------------------------------------------------------


def test_subscribe_list_toggle_and_remove():
    r = client.post("/api/me/push", json={"endpoint": "https://push.example/1", "keys": {"p256dh": "k", "auth": "a"}, "device": "iPhone"})
    assert r.status_code == 201, r.text
    sub = r.json()
    assert sub["new_items"] and sub["daily"]
    listing = client.get("/api/me/push").json()
    assert listing["enabled"] and listing["public_key"] == "test-public"
    assert [d["device"] for d in listing["devices"]] == ["iPhone"]
    assert client.patch(f"/api/me/push/{sub['id']}", json={"daily": False}).json()["daily"] is False
    assert client.delete(f"/api/me/push/{sub['id']}").status_code == 204
    assert client.get("/api/me/push").json()["devices"] == []


def test_resubscribing_the_same_endpoint_updates_it():
    body = {"endpoint": "https://push.example/1", "keys": {"p256dh": "k", "auth": "a"}}
    first = client.post("/api/me/push", json=body).json()
    second = client.post("/api/me/push", json={**body, "keys": {"p256dh": "k2", "auth": "a2"}}).json()
    assert first["id"] == second["id"]


def test_push_endpoint_must_be_https():
    r = client.post("/api/me/push", json={"endpoint": "http://evil.example", "keys": {"p256dh": "k", "auth": "a"}})
    assert r.status_code == 422


def test_someone_elses_device_is_a_404():
    subscribe("elizabeth", "https://push.example/beth")
    with Session(engine) as session:
        sub_id = session.exec(select(PushSubscription)).one().id
    assert client.patch(f"/api/me/push/{sub_id}", json={"daily": False}).status_code == 404
    assert client.delete(f"/api/me/push/{sub_id}").status_code == 404


def test_push_is_off_without_vapid_keys(monkeypatch, sent):
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "")
    get_settings.cache_clear()
    subscribe("kev", "https://push.example/kev")
    assert push.send(["kev"], "t", "b", "/") == 0 and sent == []
    assert client.get("/api/me/push").json()["enabled"] is False


# --- who hears about a new item ------------------------------------------------------------------


def test_a_table_item_tells_everyone_but_whoever_added_it(sent):
    subscribe("kev", "https://push.example/kev")
    subscribe("elizabeth", "https://push.example/beth")
    subscribe("household", "https://push.example/house")
    client.post("/api/table/items", json={"title": "Change the filters"})  # Kev, with Access off
    assert sorted(p for p, *_ in sent) == ["elizabeth", "household"]
    assert sent[0][1:] == ("New on the table", "Change the filters", "/#inbox")


def test_your_own_plate_capture_pushes_nobody(sent):
    subscribe("kev", "https://push.example/kev")
    client.post("/api/plates/kev/items", json={"title": "Etsy restock"})
    assert sent == []


def test_new_item_pushes_respect_the_toggle(sent):
    subscribe("elizabeth", "https://push.example/beth", new_items=False)
    client.post("/api/table/items", json={"title": "x"})
    assert sent == []


def test_a_gone_subscription_is_removed(monkeypatch):
    subscribe("elizabeth", "https://push.example/beth")
    monkeypatch.setattr(push, "_send_one", lambda sub, payload: False)
    assert push.send(["elizabeth"], "t", "b", "/") == 0
    with Session(engine) as session:
        assert session.exec(select(PushSubscription)).all() == []


# --- the Shortcut ------------------------------------------------------------------------------------


def make_token(name="Kev's iPhone"):
    r = client.post("/api/me/capture-tokens", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


def test_token_is_shown_once_and_stored_hashed():
    made = make_token()
    assert made["token"].startswith("tbl_")
    listing = client.get("/api/me/capture-tokens").json()
    assert listing == [{"id": made["id"], "name": "Kev's iPhone", "created_at": listing[0]["created_at"], "last_used_at": None}]
    from app.models import CaptureToken

    with Session(engine) as session:
        row = session.exec(select(CaptureToken)).one()
    assert made["token"] not in row.token_hash and len(row.token_hash) == 64


def test_capture_lands_in_the_plate_inbox(sent):
    token = make_token()["token"]
    r = client.post("/api/capture", json={"text": "Ask Mom about Thanksgiving"}, headers={"X-Capture-Token": token})
    assert r.status_code == 201, r.text
    assert r.json()["space"] == "kev"
    with Session(engine) as session:
        item = session.exec(select(Item)).one()
        source = session.exec(select(ItemSource)).one()
    assert (item.status, item.created_by, source.kind) == ("inbox", "kev", "shortcut")
    assert sent == []  # Kev sent it to himself
    tokens = client.get("/api/me/capture-tokens").json()
    assert tokens[0]["last_used_at"]


def test_a_shared_url_keeps_its_link():
    token = make_token()["token"]
    url = "https://example.com/article"
    r = client.post("/api/capture", json={"text": url}, headers={"X-Capture-Token": token})
    with Session(engine) as session:
        source = session.exec(select(ItemSource)).one()
    assert r.json()["title"] == url and source.ref == url


def test_capture_to_the_table(sent):
    subscribe("elizabeth", "https://push.example/beth")
    token = make_token()["token"]
    r = client.post("/api/capture", json={"text": "Buy filters", "to": "table"}, headers={"X-Capture-Token": token})
    assert r.json()["space"] == "table"
    assert [p for p, *_ in sent] == ["elizabeth"]


@pytest.mark.parametrize("headers", [{}, {"X-Capture-Token": "tbl_wrong"}])
def test_capture_without_a_good_token_is_refused(headers):
    make_token()
    assert client.post("/api/capture", json={"text": "x"}, headers=headers).status_code == 401


def test_a_revoked_token_stops_working():
    made = make_token()
    assert client.delete(f"/api/me/capture-tokens/{made['id']}").status_code == 204
    assert client.post("/api/capture", json={"text": "x"}, headers={"X-Capture-Token": made["token"]}).status_code == 401


def test_empty_capture_is_refused():
    token = make_token()["token"]
    assert client.post("/api/capture", json={"text": "  "}, headers={"X-Capture-Token": token}).status_code == 422


# --- the evening reminder ----------------------------------------------------------------------------


def _reminder():
    spec = importlib.util.spec_from_file_location("daily_reminder", Path("scripts/daily_reminder.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_daily_reminder_sums_your_plate_and_the_table(sent):
    subscribe("kev", "https://push.example/kev")
    subscribe("elizabeth", "https://push.example/beth", daily=False)
    client.post("/api/plates/kev/items", json={"title": "a", "today": True})
    client.post("/api/table/items", json={"title": "b"})
    sent.clear()
    _reminder().main()
    assert sent == [("kev", "Daily session", "1 on today's plate, 1 in the inbox.", "/kev#session")]


def test_daily_reminder_stays_quiet_with_nothing_waiting(sent):
    subscribe("kev", "https://push.example/kev")
    _reminder().main()
    assert sent == []
