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
    monkeypatch.setattr(access, "_signing_key", lambda token, team: _key.public_key())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


KEV_H = lambda: _as(email=KEV)  # noqa: E731
BETH_H = lambda: _as(email="Beth@Example.com")  # noqa: E731  (case-insensitive on purpose)
AGENT_H = lambda: _as(common_name=AGENT_ID)  # noqa: E731


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
    ],
)
def test_check_settings_refuses_half_configured(values, message):
    blank = dict(access_team_domain="", access_aud="", kev_emails="", beth_emails="", house_emails="", agent_service_token_id="")
    with pytest.raises(RuntimeError, match=message):
        access.check_settings(Settings(_env_file=None, **{**blank, **values}))
