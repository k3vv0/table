"""Milestone 4: mail and calendar sync, suggestions and routine runs (DESIGN.md §10).
Access is off here, so requests are Kev; tests/test_access.py covers the sweep token.
Nothing touches Gmail or Google Calendar: the IMAP client and the feed fetch are fakes."""

from datetime import date, datetime, timedelta
from email.message import EmailMessage

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

from app import access
from app.config import MailAccount, Settings, get_settings
from app.db import engine, init_db
from app.main import app
from app.models import CalendarEvent, Item, ItemSource, PushSubscription, SourceMessage, SyncState, utcnow
from app.services import calendar, mail, push
from app.services import items as items_svc

client = TestClient(app)
TODAY = date(2026, 10, 23)
KEV_MAIL = MailAccount("kev", "kev@example.com", "abcdabcdabcdabcd")
HOUSE_MAIL = MailAccount("table", "us@example.com", "efghefghefghefgh")


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    SQLModel.metadata.drop_all(engine)
    init_db()
    for module in (items_svc, calendar):
        monkeypatch.setattr(module, "today_local", lambda: TODAY)
    from app.services import suggestions

    monkeypatch.setattr(suggestions, "today_local", lambda: TODAY)
    yield
    get_settings.cache_clear()


def message(subject="Hello", body="Plain words", html=None, sender="Dentist <desk@dentist.example>", attach=False, date_hdr="Thu, 22 Oct 2026 14:00:00 -0400"):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"], msg["Date"] = sender, "kev@example.com", subject, date_hdr
    msg["Message-ID"] = f"<{subject.replace(' ', '')}@example>"
    if body is not None:
        msg.set_content(body)
    if html is not None:
        if body is None:
            msg.set_content(html, subtype="html")
        else:
            msg.add_alternative(html, subtype="html")
    if attach:
        msg.add_attachment(b"%PDF-1.4", maintype="application", subtype="pdf", filename="bill.pdf")
    return msg.as_bytes()


class FakeGmail:
    def __init__(self, messages=None, validity="1"):
        self.messages = dict(messages or {})  # uid -> (gm_msgid, bytes)
        self.validity = validity
        self.archived = []
        self.seen_since = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def select_inbox(self, readonly=True):
        return self.validity

    def uids_after(self, last_uid, since_days):
        self.seen_since.append(last_uid)
        return sorted(u for u in self.messages if last_uid is None or u > last_uid)

    def fetch(self, uid):
        msgid, data = self.messages[uid]
        return mail.RawMessage(uid, msgid, f"t{msgid}", data)

    def archive(self, gm_msgid, label):
        self.archived.append((gm_msgid, label))
        return True


def synced(*pairs, account=KEV_MAIL):
    """Sync these (gm_msgid, raw bytes) into the database; return the rows."""
    fake = FakeGmail({i + 1: p for i, p in enumerate(pairs)})
    with Session(engine) as session:
        mail.sync_account(session, account, fake)
        return session.exec(select(SourceMessage).order_by(SourceMessage.id)).all()


# --- turning mail into rows ------------------------------------------------------------------


def test_a_message_becomes_headers_and_an_excerpt():
    [row] = synced(("100", message("Confirm your cleaning", "Reply Y to confirm Tuesday 3pm.", attach=True)))
    assert (row.space, row.account, row.gm_msgid, row.gm_thrid) == ("kev", "kev@example.com", "100", "t100")
    assert (row.from_name, row.from_addr, row.subject) == ("Dentist", "desk@dentist.example", "Confirm your cleaning")
    assert row.sent_at == datetime(2026, 10, 22, 18, 0)  # 2pm Eastern, stored as UTC
    assert row.excerpt == "Reply Y to confirm Tuesday 3pm." and row.has_attachments and row.sort == "unsorted"


def test_html_only_mail_is_reduced_to_text():
    html = "<html><head><style>p{}</style></head><body><script>alert(1)</script><p>Pay&nbsp;$84 by <b>Nov 4</b></p></body></html>"
    [row] = synced(("1", message("Bill", body=None, html=html)))
    assert row.excerpt == "Pay $84 by Nov 4"


def test_excerpts_are_cut_short():
    [row] = synced(("1", message("Long", "x" * 10_000)))
    assert len(row.excerpt) == mail.EXCERPT_CHARS


def test_sync_only_takes_new_mail_and_never_duplicates():
    fake = FakeGmail({5: ("a", message("One")), 6: ("b", message("Two"))})
    with Session(engine) as session:
        assert mail.sync_account(session, KEV_MAIL, fake) == 2
        fake.messages[7] = ("c", message("Three"))
        assert mail.sync_account(session, KEV_MAIL, fake) == 1
        assert fake.seen_since == [None, 6]
        # Gmail reset UIDVALIDITY: start over, but the same messages aren't added twice.
        fake.validity = "2"
        assert mail.sync_account(session, KEV_MAIL, fake) == 0
        assert fake.seen_since[-1] is None
        assert session.get(SyncState, "mail:kev@example.com").value == "2:7"


def test_noise_is_archived_once_and_nothing_else_is():
    synced(("a", message("Sale")), ("b", message("Bill")))
    with Session(engine) as session:
        sale, bill = session.exec(select(SourceMessage).order_by(SourceMessage.id)).all()
        sale.sort, bill.sort = "noise", "flagged"
        session.add_all([sale, bill])
        session.commit()
        fake = FakeGmail()
        assert mail.archive_noise(session, KEV_MAIL, fake) == 1
        assert fake.archived == [("a", "Table/Noise")]
        assert mail.archive_noise(session, KEV_MAIL, fake) == 0


def test_archiving_is_off_unless_turned_on(monkeypatch):
    fake = FakeGmail({1: ("a", message("Sale"))})
    monkeypatch.setenv("MAIL_ACCOUNTS", "kev|kev@example.com|abcd abcd abcd abcd")
    get_settings.cache_clear()
    with Session(engine) as session:
        mail.sync_all(session, client_for=lambda account: fake)
        row = session.exec(select(SourceMessage)).one()
        row.sort = "noise"
        session.add(row)
        session.commit()
        mail.sync_all(session, client_for=lambda account: fake)
        assert fake.archived == []
        monkeypatch.setenv("MAIL_ARCHIVE_NOISE", "true")
        get_settings.cache_clear()
        mail.sync_all(session, client_for=lambda account: fake)
        assert fake.archived == [("a", "Table/Noise")]


def test_old_excerpts_are_forgotten_but_headers_stay():
    synced(("a", message("Old")), ("b", message("New")))
    with Session(engine) as session:
        old = session.exec(select(SourceMessage).where(SourceMessage.gm_msgid == "a")).one()
        old.created_at = utcnow() - timedelta(days=31)
        session.add(old)
        session.commit()
        assert mail.forget_old_excerpts(session, 30) == 1
        rows = {m.gm_msgid: m for m in session.exec(select(SourceMessage))}
    assert rows["a"].excerpt == "" and rows["a"].subject == "Old" and rows["b"].excerpt


def test_one_failing_mailbox_does_not_stop_the_others(monkeypatch):
    monkeypatch.setenv("MAIL_ACCOUNTS", "kev|kev@example.com|pw1pw1pw1pw1pw1p;table|us@example.com|pw2pw2pw2pw2pw2p")
    get_settings.cache_clear()

    def client_for(account):
        if account.space == "kev":
            raise OSError(f"login refused for {account.password}")
        return FakeGmail({1: ("z", message("Hi"))})

    with Session(engine) as session:
        report = mail.sync_all(session, client_for=client_for)
        state = session.get(SyncState, "mail:kev@example.com")
    assert report["kev@example.com"] == {"error": "OSError: login refused for ***"} and report["us@example.com"]["added"] == 1
    assert state.last_error == "OSError: login refused for ***"


def test_settings_parse_accounts_and_never_echo_a_password():
    s = Settings(_env_file=None, mail_accounts="kev|Kev@Example.com|abcd efgh ijkl mnop; table|us@example.com|qrstqrstqrstqrst")
    assert s.mail_account_list == [KEV_MAIL._replace(password="abcdefghijklmnop"), HOUSE_MAIL._replace(password="qrstqrstqrstqrst")]
    assert "abcd" not in repr(s.mail_account_list)
    with pytest.raises(ValueError) as exc:
        Settings(_env_file=None, mail_accounts="kev|kev@example.com").mail_account_list
    assert "kev@example.com" not in str(exc.value)


@pytest.mark.parametrize(
    "values, message",
    [
        ({"mail_accounts": "elizabeth|beth@example.com|pw"}, "not 'elizabeth'"),
        ({"calendar_feeds": "elizabeth|https://calendar.google.com/calendar/ical/x/private-y/basic.ics"}, "not 'elizabeth'"),
        ({"mail_accounts": "kev|only-two-fields"}, "3 fields"),
        ({"agent_service_token_id": "a", "sweep_service_token_id": "a"}, "must all differ"),
    ],
)
def test_check_settings_refuses_bad_sync_config(values, message):
    with pytest.raises(RuntimeError, match=message):
        access.check_settings(Settings(_env_file=None, **values))


# --- calendar --------------------------------------------------------------------------------

ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:weekly@example
DTSTART;TZID=America/New_York:20261020T190000
DTEND;TZID=America/New_York:20261020T200000
RRULE:FREQ=WEEKLY;COUNT=3
SUMMARY:Choir practice
LOCATION:Church hall
DESCRIPTION:Bring the folder
END:VEVENT
BEGIN:VEVENT
UID:wedding@example
DTSTART;VALUE=DATE:20261130
DTEND;VALUE=DATE:20261201
SUMMARY:Sam and Alex's wedding
END:VEVENT
BEGIN:VEVENT
UID:gone@example
DTSTART:20261101T150000Z
STATUS:CANCELLED
SUMMARY:Cancelled thing
END:VEVENT
BEGIN:VEVENT
UID:past@example
DTSTART:20260101T150000Z
SUMMARY:Long ago
END:VEVENT
END:VCALENDAR
"""

FEED_URL = "https://calendar.google.com/calendar/ical/us%40example.com/private-SECRETKEY/basic.ics"


def test_calendar_occurrences_inside_the_window():
    from app.config import CalendarFeed

    feed = CalendarFeed("table", FEED_URL)
    assert feed.name == "us%40example.com" and "SECRET" not in repr(feed)
    with Session(engine) as session:
        assert calendar.sync_feed(session, feed, fetcher=lambda url: ICS) == 3
        events = session.exec(select(CalendarEvent).order_by(CalendarEvent.starts_on)).all()
    # Oct 20 is before the window (yesterday onwards); Oct 27 and Nov 3 are in it.
    assert [(e.summary, e.starts_on) for e in events] == [
        ("Choir practice", date(2026, 10, 27)),
        ("Choir practice", date(2026, 11, 3)),
        ("Sam and Alex's wedding", date(2026, 11, 30)),
    ]
    choir, after_dst, wedding = events
    assert choir.starts_at == datetime(2026, 10, 27, 23, 0) and after_dst.starts_at == datetime(2026, 11, 4, 0, 0)
    assert wedding.all_day and wedding.starts_at is None and choir.feed == "us%40example.com"
    assert choir.ref == "weekly@example@2026-10-27"


def test_a_resync_replaces_the_feed():
    from app.config import CalendarFeed

    feed = CalendarFeed("table", FEED_URL)
    with Session(engine) as session:
        calendar.sync_feed(session, feed, fetcher=lambda url: ICS)
        smaller = ICS.replace(b"RRULE:FREQ=WEEKLY;COUNT=3\n", b"")
        assert calendar.sync_feed(session, feed, fetcher=lambda url: smaller) == 1


def test_a_failing_feed_keeps_its_address_out_of_the_error(monkeypatch):
    monkeypatch.setenv("CALENDAR_FEEDS", f"table|{FEED_URL}")
    get_settings.cache_clear()

    def boom(url):
        raise ValueError(f"bad response from {url}")

    with Session(engine) as session:
        report = calendar.sync_all(session, fetcher=boom)
        state = session.get(SyncState, "calendar:us%40example.com")
    assert report == {"us%40example.com": {"error": "ValueError"}} and "SECRET" not in state.last_error


# --- the API ---------------------------------------------------------------------------------


def test_mail_api_lists_and_sorts_by_space():
    synced(("a", message("Bill")))
    synced(("b", message("Groceries")), account=HOUSE_MAIL)
    kev = client.get("/api/plates/kev/mail").json()
    assert "untrusted" in kev["note"] and [m["subject"] for m in kev["messages"]] == ["Bill"]
    table = client.get("/api/table/mail?sort=unsorted").json()["messages"]
    assert [m["ref"] for m in table] == ["b"]
    # An id from the other space is a 404.
    assert client.patch(f"/api/table/mail/{kev['messages'][0]['id']}", json={"sort": "noise"}).status_code == 404
    r = client.patch(f"/api/table/mail/{table[0]['id']}", json={"sort": "flagged", "reason": "Needs a reply"})
    assert r.json()["sort"] == "flagged" and r.json()["sorted_by"] == "kev"
    assert client.patch(f"/api/table/mail/{table[0]['id']}", json={"sort": "unsorted"}).status_code == 422
    assert client.get("/api/table/mail?sort=flagged").json()["messages"][0]["sort_reason"] == "Needs a reply"


def test_events_api():
    from app.config import CalendarFeed

    with Session(engine) as session:
        calendar.sync_feed(session, CalendarFeed("table", FEED_URL), fetcher=lambda url: ICS)
    events = client.get("/api/table/events?days=14").json()["events"]
    assert [e["summary"] for e in events] == ["Choir practice", "Choir practice"]
    assert client.get("/api/plates/kev/events").json()["events"] == []


def file(space_path, **body):
    return client.post(f"{space_path}/suggestions", json=body)


def test_an_item_suggestion_accepted_becomes_an_item_with_its_source():
    synced(("m1", message("Water bill")), account=HOUSE_MAIL)
    r = file("/api/table", kind="item", title="Pay the water bill", notes="$84", due_on="2026-11-04", source_kind="email", source_ref="m1", rationale="Bill due Nov 4")
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["source"] == {"kind": "email", "from": "Dentist", "subject": "Water bill"}
    assert client.get("/api/table/suggestions").json()["suggestions"][0]["id"] == s["id"]
    accepted = client.post(f"/api/table/suggestions/{s['id']}/accept", json={"today": True}).json()
    item = client.get(f"/api/table/items/{accepted['item_id']}").json()
    assert (item["title"], item["status"], item["due_on"], item["notes"]) == ("Pay the water bill", "today", "2026-11-04", "$84")
    assert item["sources"] == [{"kind": "email", "ref": "m1", "snippet": "Pay the water bill"}]
    assert client.get("/api/table/suggestions").json()["suggestions"] == []
    # The same message is never suggested again, accepted or not.
    assert file("/api/table", kind="item", title="Pay it", source_kind="email", source_ref="m1").status_code == 409


def test_a_dismissed_source_is_not_suggested_again():
    synced(("m1", message("Sale")), account=HOUSE_MAIL)
    s = file("/api/table", kind="item", title="Buy a couch", source_kind="email", source_ref="m1").json()
    assert client.post(f"/api/table/suggestions/{s['id']}/dismiss").status_code == 204
    assert client.post(f"/api/table/suggestions/{s['id']}/accept").status_code == 409
    assert file("/api/table", kind="item", title="Buy a couch", source_kind="email", source_ref="m1").status_code == 409


def test_suggestions_must_point_at_real_things_in_their_space():
    synced(("kevs", message("Kev's own")))
    assert file("/api/table", kind="item", title="x", source_kind="email", source_ref="kevs").status_code == 422
    assert file("/api/table", kind="item", title="x", source_kind="email", source_ref="made-up").status_code == 422
    assert file("/api/table", kind="item", title="x", source_kind="fax", source_ref="1").status_code == 422
    assert file("/api/table", kind="item", title="").status_code == 422
    assert file("/api/table", kind="nuke").status_code == 422
    plate_item = client.post("/api/plates/kev/items", json={"title": "Mine"}).json()
    assert file("/api/table", kind="close", item_id=plate_item["id"]).status_code == 404
    assert file("/api/table", kind="note", item_id=None, notes="x").status_code == 422


def test_a_past_due_date_is_dropped_from_a_new_item():
    s = file("/api/table", kind="item", title="Old thing", due_on="2026-10-01").json()
    assert s["due_on"] is None


def test_note_merge_and_close_suggestions():
    watch = client.post("/api/table/items", json={"title": "Visa application", "notes": "Sent Oct 1"}).json()
    client.patch(f"/api/table/items/{watch['id']}", json={"kind": "watch"})
    dup = client.post("/api/table/items", json={"title": "visa"}).json()

    note = file("/api/table", kind="note", item_id=watch["id"], title="Approved", notes="Next: book flights", due_on="2026-11-10").json()
    assert note["item"] == {"id": watch["id"], "title": "Visa application"}
    assert file("/api/table", kind="note", item_id=watch["id"], notes="again").status_code == 409  # one pending at a time
    client.post(f"/api/table/suggestions/{note['id']}/accept")
    item = client.get(f"/api/table/items/{watch['id']}").json()
    assert item["notes"] == "Sent Oct 1\n\nNext: book flights" and item["due_on"] == "2026-11-10" and item["kind"] == "watch"

    merge = file("/api/table", kind="merge", item_id=watch["id"], merge_ids=[dup["id"], watch["id"]]).json()
    assert [m["id"] for m in merge["merge"]] == [dup["id"]]
    client.post(f"/api/table/suggestions/{merge['id']}/accept")
    assert client.get(f"/api/table/items/{dup['id']}").json()["status"] == "merged"

    close = file("/api/table", kind="close", item_id=watch["id"], title="Trip booked").json()
    client.post(f"/api/table/suggestions/{close['id']}/accept")
    assert client.get(f"/api/table/items/{watch['id']}").json()["status"] == "done"
    assert file("/api/table", kind="close", item_id=watch["id"]).status_code == 409  # done items take no suggestions


def test_only_a_task_and_a_watch_swap_kinds():
    item = client.post("/api/table/items", json={"title": "x"}).json()
    assert client.patch(f"/api/table/items/{item['id']}", json={"kind": "watch"}).json()["kind"] == "watch"
    assert client.patch(f"/api/table/items/{item['id']}", json={"kind": "date"}).status_code == 422


def test_suggestion_writes_are_logged():
    from app.models import Activity

    s = file("/api/table", kind="item", title="Call the plumber").json()
    client.post(f"/api/table/suggestions/{s['id']}/accept")
    with Session(engine) as session:
        actions = [a.action for a in session.exec(select(Activity).order_by(Activity.id))]
    assert actions == ["suggestion.file", "item.create", "suggestion.accept"]


# --- runs -----------------------------------------------------------------------------------


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "p")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "k")
    get_settings.cache_clear()
    monkeypatch.setattr(push, "_send_one", lambda sub, payload: out.append((sub.person, payload["title"], payload["body"])) or True)
    return out


def test_a_finished_run_pushes_one_summary_per_space(sent):
    with Session(engine) as session:
        for person in ("kev", "elizabeth"):
            session.add(PushSubscription(person=person, endpoint=f"https://push.example/{person}", p256dh="k", auth="a"))
        session.commit()
    run = client.post("/api/agent-runs", json={"routine": "sweep"}).json()
    assert run["status"] == "running"
    file("/api/table", kind="item", title="One", run_id=run["id"])
    file("/api/table", kind="item", title="Two", run_id=run["id"])
    file("/api/plates/kev", kind="item", title="Mine", run_id=run["id"])
    done = client.patch(f"/api/agent-runs/{run['id']}", json={"status": "ok", "summary": "Sorted 12, suggested 3"}).json()
    assert done["status"] == "ok" and done["finished_at"]
    assert sorted(sent) == [
        ("elizabeth", "Suggested for the table", "2 suggestions to look at."),
        ("kev", "Suggested for the table", "2 suggestions to look at."),
        ("kev", "Suggested for your plate", "1 suggestion to look at."),
    ]
    assert client.patch(f"/api/agent-runs/{run['id']}", json={"status": "ok"}).status_code == 409
    assert client.get("/api/agent-runs").json()["runs"][0]["summary"] == "Sorted 12, suggested 3"


def test_runs_need_a_known_routine_and_suggestions_a_real_run():
    assert client.post("/api/agent-runs", json={"routine": "anything"}).status_code == 422
    assert file("/api/table", kind="item", title="x", run_id=999).status_code == 422


def test_sync_status_shows_sources_without_secrets(monkeypatch):
    monkeypatch.setenv("MAIL_ACCOUNTS", "kev|kev@example.com|topsecretpasswd1")
    monkeypatch.setenv("CALENDAR_FEEDS", f"table|{FEED_URL}")
    get_settings.cache_clear()
    body = client.get("/api/sync-status").text
    assert "kev@example.com" in body and "us%40example.com" in body
    assert "topsecret" not in body and "SECRET" not in body


# --- pages -----------------------------------------------------------------------------------


def test_board_offers_mail_where_mail_syncs():
    assert 'href="#mail"' in client.get("/").text
    assert 'href="#mail"' in client.get("/kev").text


# --- the IMAP wrapper itself, against a fake imaplib -----------------------------------------


class FakeIMAP:
    """Answers the way imaplib does for Gmail."""

    def __init__(self):
        self.calls = []
        self.raw = message("Hello")

    def select(self, mailbox, readonly=False):
        self.calls.append(("select", mailbox, readonly))
        return "OK", [b"3"]

    def response(self, code):
        return code, [b"777"]

    def create(self, mailbox):
        self.calls.append(("create", mailbox))
        return "NO", [b"[ALREADYEXISTS] Duplicate folder name"]

    def uid(self, command, *args):
        self.calls.append((command, *args))
        if command == "SEARCH":
            return "OK", [b"40 41 42"] if "X-GM-MSGID" not in args[1] else [b"42"]
        if command == "FETCH" and "X-GM-MSGID" in args[1]:
            return "OK", [b"3 (X-GM-THRID 1800 X-GM-MSGID 1801 RFC822.SIZE 900 UID 42)"]
        if command == "FETCH":
            return "OK", [(b"3 (UID 42 BODY[] {900}", self.raw), b")"]
        return "OK", [b""]


def test_gmail_client_speaks_imap():
    client = mail.GmailClient(KEV_MAIL)
    client.imap = FakeIMAP()
    assert client.select_inbox() == "777"
    assert client.uids_after(40, 7) == [41, 42]
    assert client.uids_after(None, 7) == [40, 41, 42]
    assert client.imap.calls[-1][2].startswith("SINCE ")
    raw = client.fetch(42)
    assert (raw.uid, raw.gm_msgid, raw.gm_thrid, raw.data) == (42, "1801", "1800", client.imap.raw)
    assert ("FETCH", "42", "(BODY.PEEK[])") in client.imap.calls  # PEEK: never marks mail read
    assert client.archive("1801", "Table/Noise")
    assert client.imap.calls[-2:] == [("STORE", "42", "+X-GM-LABELS", '("Table/Noise")'), ("STORE", "42", "-X-GM-LABELS", r"(\Inbox)")]


def test_gmail_client_fetches_only_headers_of_huge_mail():
    client = mail.GmailClient(KEV_MAIL)
    client.imap = FakeIMAP()
    client.imap.uid = lambda command, *args: (
        ("OK", [b"3 (X-GM-THRID 1 X-GM-MSGID 2 RFC822.SIZE 9000000 UID 42)"])
        if "X-GM-MSGID" in args[1]
        else ("OK", [(b"3 (UID 42 BODY[HEADER] {50}", b"Subject: Big\r\n\r\n"), b")"])
    )
    raw = client.fetch(42)
    assert raw.data.startswith(b"Subject: Big")
