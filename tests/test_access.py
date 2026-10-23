"""Cloudflare Access identity and per-identity access (app/access.py, DESIGN.md §4).

Tokens here are signed with a throwaway RSA key and app.access._signing_key is swapped to
return its public half, so verification runs for real without fetching Cloudflare's keys.
"""

import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import access
from app.config import Settings, get_settings
from app.db import init_db
from app.main import app

init_db()
client = TestClient(app)

TEAM = "example-team.cloudflareaccess.com"
AUD = "test-aud-tag"
KEV = "kev@example.com"
KEV_WORK = "kev@syncyn.example"
BETH = "beth@example.com"
HOUSE = "us@example.com"
AGENT_ID = "agent-client-id.access"
CAPTURE_ID = "capture-client-id.access"
SWEEP_ID = "sweep-client-id.access"

_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _token(key=_key, **overrides) -> str:
    now = int(time.time())
    claims = {"aud": [AUD], "iss": f"https://{TEAM}", "iat": now, "exp": now + 300}
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm="RS256")


def _as(key=_key, **claims) -> dict:
    return {"Cf-Access-Jwt-Assertion": _token(key, **claims)}


@pytest.fixture(autouse=True)
def access_on(monkeypatch):
    monkeypatch.setenv("ACCESS_TEAM_DOMAIN", TEAM)
    monkeypatch.setenv("ACCESS_AUD", AUD)
    monkeypatch.setenv("KEV_EMAILS", f"{KEV}, {KEV_WORK}")
    monkeypatch.setenv("BETH_EMAILS", BETH)
    monkeypatch.setenv("HOUSE_EMAILS", HOUSE)
    monkeypatch.setenv("AGENT_SERVICE_TOKEN_ID", AGENT_ID)
    monkeypatch.setenv("CAPTURE_SERVICE_TOKEN_ID", CAPTURE_ID)
    monkeypatch.setenv("SWEEP_SERVICE_TOKEN_ID", SWEEP_ID)
    monkeypatch.setattr(access, "_signing_key", lambda token, team: _key.public_key())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


KEV_H = lambda: _as(email=KEV)  # noqa: E731
BETH_H = lambda: _as(email="Beth@Example.com")  # noqa: E731  (case-insensitive on purpose)
AGENT_H = lambda: _as(common_name=AGENT_ID)  # noqa: E731
CAPTURE_H = lambda: _as(common_name=CAPTURE_ID)  # noqa: E731
SWEEP_H = lambda: _as(common_name=SWEEP_ID)  # noqa: E731


# --- who gets in --------------------------------------------------------------------


def test_access_off_treats_everyone_as_kev(monkeypatch):
    for name in ("ACCESS_TEAM_DOMAIN", "BETH_EMAILS", "HOUSE_EMAILS"):
        monkeypatch.setenv(name, "")
    get_settings.cache_clear()
    for path in ("/", "/kev"):
        assert client.get(path).status_code == 200, path
    assert client.get("/api/me").json()["person"] == "kev"
    # Local dev is Kev, so Beth's Plate stays hers even there.
    assert client.get("/elizabeth").status_code == 403


def test_health_needs_no_identity():
    assert client.get("/api/health").json() == {"ok": True}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Cf-Access-Jwt-Assertion": "not-a-jwt"},
        _as(key=_other_key, email=KEV),  # signed by someone else
        _as(email=KEV, aud=["another-app"]),
        _as(email=KEV, iss="https://evil.example"),
        _as(email=KEV, exp=int(time.time()) - 60),
        _as(email="stranger@example.com"),
        _as(common_name="some-other-token.access"),
    ],
)
def test_bad_tokens_are_refused(headers):
    assert client.get("/", headers=headers).status_code == 403


def test_old_email_header_alone_gets_nothing():
    assert client.get("/", headers={"Cf-Access-Authenticated-User-Email": KEV}).status_code == 403


def test_a_crafted_host_cannot_make_a_path_public():
    assert client.get("/kev", headers={"Host": "x/api/health?"}).status_code == 403


def test_each_person_is_named():
    assert client.get("/api/me", headers=KEV_H()).json() == {"role": "person", "person": "kev", "name": "Kev"}
    assert client.get("/api/me", headers=_as(email=KEV_WORK)).json()["person"] == "kev"
    assert client.get("/api/me", headers=BETH_H()).json() == {"role": "person", "person": "elizabeth", "name": "Beth"}


# --- what each one reaches ------------------------------------------------------------


def test_both_people_share_the_table():
    assert client.get("/", headers=KEV_H()).status_code == 200
    assert client.get("/", headers=BETH_H()).status_code == 200


def test_each_plate_is_its_owners_alone():
    assert client.get("/kev", headers=KEV_H()).status_code == 200
    assert client.get("/elizabeth", headers=BETH_H()).status_code == 200
    assert client.get("/elizabeth", headers=KEV_H()).status_code == 403
    assert client.get("/kev", headers=BETH_H()).status_code == 403
    # The API under a Plate, and anything deeper, follows the page.
    assert client.get("/api/plates/kev/items", headers=BETH_H()).status_code == 403
    assert client.post("/api/plates/elizabeth/items", headers=KEV_H()).status_code == 403


def test_a_plate_prefix_does_not_swallow_a_neighbour():
    assert access.allowed(access.Identity(access.PERSON, BETH, access.ELIZABETH), "GET", "/kevin-notes") is True


def test_household_sign_in_reaches_the_table_but_no_plate():
    house = _as(email=HOUSE)
    assert client.get("/api/me", headers=house).json() == {"role": "person", "person": None, "name": "Household"}
    assert client.get("/", headers=house).status_code == 200
    assert client.get("/kev", headers=house).status_code == 403
    assert client.get("/elizabeth", headers=house).status_code == 403
    assert "My plate" not in client.get("/", headers=house).text


def test_agent_reads_the_table_and_kevs_plate_only():
    assert client.get("/", headers=AGENT_H()).status_code == 200
    assert client.get("/kev", headers=AGENT_H()).status_code == 200
    assert client.get("/elizabeth", headers=AGENT_H()).status_code == 403
    assert client.get("/api/plates/elizabeth/items", headers=AGENT_H()).status_code == 403


def test_agent_writes_nothing_unlisted():
    assert client.post("/api/anything", headers=AGENT_H()).status_code == 403
    assert client.delete("/api/plates/kev/items/1", headers=AGENT_H()).status_code == 403


def test_item_apis_follow_the_plate_rules():
    assert client.post("/api/plates/kev/items", json={"title": "x"}, headers=KEV_H()).status_code == 201
    assert client.get("/api/plates/kev/items", headers=BETH_H()).status_code == 403
    assert client.get("/api/plates/elizabeth/session", headers=KEV_H()).status_code == 403
    assert client.get("/api/plates/kev/items", headers=AGENT_H()).status_code == 200
    assert client.get("/api/plates/elizabeth/items", headers=AGENT_H()).status_code == 403


def test_household_writes_to_the_table_as_household():
    r = client.post("/api/table/items", json={"title": "Change the filters"}, headers=_as(email=HOUSE))
    assert r.status_code == 201 and r.json()["created_by"] == "household"
    assert client.post("/api/plates/kev/items", json={"title": "x"}, headers=_as(email=HOUSE)).status_code == 403


def test_agent_cannot_write_items_yet():
    assert client.post("/api/table/items", json={"title": "x"}, headers=AGENT_H()).status_code == 403
    assert client.post("/api/plates/kev/items/1/triage", json={"action": "done"}, headers=AGENT_H()).status_code == 403


def test_require_person_refuses_service_tokens():
    request = SimpleNamespace(
        state=SimpleNamespace(identity=access.Identity(access.AGENT, AGENT_ID)),
        method="POST",
        url=SimpleNamespace(path="/api/suggestions/1/accept"),
    )
    with pytest.raises(HTTPException) as exc:
        access.require_person(request)
    assert exc.value.status_code == 403
    request.state.identity = access.Identity(access.PERSON, BETH, access.ELIZABETH)
    assert access.require_person(request).person == "elizabeth"


def test_capture_token_reaches_only_the_capture_route():
    made = client.post("/api/me/capture-tokens", json={"name": "phone"}, headers=KEV_H()).json()
    headers = {**CAPTURE_H(), "X-Capture-Token": made["token"]}
    r = client.post("/api/capture", json={"text": "From the share sheet"}, headers=headers)
    assert r.status_code == 201 and r.json()["space"] == "kev"
    for method, path in [
        ("GET", "/"),
        ("GET", "/kev"),
        ("GET", "/api/me"),
        ("GET", "/api/plates/kev/items"),
        ("POST", "/api/plates/kev/items"),
        ("POST", "/api/me/capture-tokens"),
        ("GET", "/manifest.webmanifest"),
    ]:
        assert client.request(method, path, json={"title": "x"}, headers=headers).status_code == 403, path


def test_capture_still_needs_a_persons_token():
    assert client.post("/api/capture", json={"text": "x"}, headers=CAPTURE_H()).status_code == 401


def test_settings_are_for_people_and_tokens_for_plates():
    assert client.get("/settings", headers=AGENT_H()).status_code == 403
    assert client.get("/api/me/push", headers=AGENT_H()).status_code == 403
    house = _as(email=HOUSE)
    assert client.get("/settings", headers=house).status_code == 200
    assert client.get("/api/me/push", headers=house).status_code == 200
    assert client.post("/api/me/capture-tokens", json={"name": "x"}, headers=house).status_code == 403


def test_sweep_sorts_mail_and_files_suggestions_but_approves_nothing():
    from sqlmodel import Session

    from app.db import engine
    from app.models import SourceMessage

    with Session(engine) as session:
        msg = SourceMessage(space="table", account="us@example.com", gm_msgid="sweep-test", subject="Bill")
        session.add(msg)
        session.commit()
        msg_id = msg.id
    assert client.get("/api/table/mail", headers=SWEEP_H()).status_code == 200
    assert client.get("/api/plates/kev/events", headers=SWEEP_H()).status_code == 200
    assert client.patch(f"/api/table/mail/{msg_id}", json={"sort": "keep"}, headers=SWEEP_H()).json()["sorted_by"] == "sweep"
    run = client.post("/api/agent-runs", json={"routine": "sweep"}, headers=SWEEP_H()).json()
    s = client.post("/api/table/suggestions", json={"kind": "item", "title": "Pay it", "run_id": run["id"]}, headers=SWEEP_H())
    assert s.status_code == 201 and s.json()["created_by"] == "sweep"
    sid = s.json()["id"]
    # Approving is a person's job: the scope refuses it, and require_person would too.
    assert client.post(f"/api/table/suggestions/{sid}/accept", headers=SWEEP_H()).status_code == 403
    assert client.post(f"/api/table/suggestions/{sid}/dismiss", headers=SWEEP_H()).status_code == 403
    for method, path in [
        ("POST", "/api/table/items"),
        ("PATCH", "/api/table/items/1"),
        ("POST", "/api/table/items/1/triage"),
        ("GET", "/api/plates/elizabeth/mail"),
        ("GET", "/elizabeth"),
        ("POST", "/api/plates/elizabeth/suggestions"),
        ("POST", "/api/capture"),
        ("POST", "/api/me/capture-tokens"),
    ]:
        assert client.request(method, path, json={"title": "x"}, headers=SWEEP_H()).status_code == 403, path
    assert client.patch(f"/api/agent-runs/{run['id']}", json={"status": "ok"}, headers=SWEEP_H()).status_code == 200
    # A person accepts it.
    assert client.post(f"/api/table/suggestions/{sid}/accept", headers=KEV_H()).status_code == 200


def test_agent_files_suggestions_but_sorts_no_mail_and_runs_nothing():
    assert client.post("/api/table/suggestions", json={"kind": "item", "title": "x"}, headers=AGENT_H()).status_code == 201
    assert client.patch("/api/table/mail/1", json={"sort": "keep"}, headers=AGENT_H()).status_code == 403
    assert client.post("/api/agent-runs", json={"routine": "sweep"}, headers=AGENT_H()).status_code == 403
    assert client.post("/api/table/suggestions/1/accept", headers=AGENT_H()).status_code == 403


def test_a_routine_closes_only_its_own_runs():
    run = client.post("/api/agent-runs", json={"routine": "sweep"}, headers=KEV_H()).json()
    assert client.patch(f"/api/agent-runs/{run['id']}", json={"status": "ok"}, headers=SWEEP_H()).status_code == 404


def test_api_responses_are_never_cached():
    assert client.get("/api/me", headers=KEV_H()).headers["cache-control"] == "private, no-store"


# --- refusing to start half-configured -------------------------------------------------


@pytest.mark.parametrize(
    "values, message",
    [
        ({"access_team_domain": TEAM, "kev_emails": KEV}, "ACCESS_AUD"),
        ({"access_team_domain": TEAM, "access_aud": AUD}, "KEV_EMAILS"),
        ({"beth_emails": BETH}, "ACCESS_TEAM_DOMAIN"),
        ({"house_emails": HOUSE}, "ACCESS_TEAM_DOMAIN"),
        ({"access_team_domain": TEAM, "access_aud": AUD, "kev_emails": KEV, "beth_emails": KEV}, "more than one"),
        ({"access_team_domain": TEAM, "access_aud": AUD, "kev_emails": KEV, "house_emails": KEV}, "more than one"),
        ({"agent_service_token_id": "same", "capture_service_token_id": "same"}, "must all differ"),
    ],
)
def test_check_settings_refuses_half_configured(values, message):
    blank = dict(access_team_domain="", access_aud="", kev_emails="", beth_emails="", house_emails="", agent_service_token_id="", capture_service_token_id="", sweep_service_token_id="", mail_accounts="", calendar_feeds="")
    with pytest.raises(RuntimeError, match=message):
        access.check_settings(Settings(_env_file=None, **{**blank, **values}))
