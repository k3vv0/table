"""The milestone 7 audit (DESIGN.md §8): every write route the app has, and exactly which
service tokens may reach each one. A new route, or a scope that grows, fails here until
this table is changed on purpose in a reviewed PR."""

import re

from fastapi.testclient import TestClient

from app import access
from app.access import AGENT, CAPTURE, MORNING, SWEEP, Identity
from app.main import app

client = TestClient(app)

# Writes each service token may reach, as concrete paths (kev for {person}, 1 for ids).
EXPECTED = {
    AGENT: {
        "POST /api/table/suggestions",
        "POST /api/plates/kev/suggestions",
    },
    SWEEP: {
        "POST /api/table/suggestions",
        "POST /api/plates/kev/suggestions",
        "PATCH /api/table/mail/1",
        "PATCH /api/plates/kev/mail/1",
        "POST /api/agent-runs",
        "PATCH /api/agent-runs/1",
    },
    MORNING: {
        "POST /api/table/suggestions",
        "POST /api/plates/kev/suggestions",
        "POST /api/table/vault/picks",
        "POST /api/plates/kev/vault/picks",
        "POST /api/agent-runs",
        "PATCH /api/agent-runs/1",
    },
    CAPTURE: {"POST /api/capture"},
}


def _write_routes() -> list[tuple[str, str]]:
    out = []
    for path, ops in app.openapi()["paths"].items():
        for method in ops:
            if method in ("get", "head"):
                continue
            for person in ("kev", "elizabeth"):
                concrete = path.replace("{person}", person)
                concrete = re.sub(r"\{key\}", "valentines", concrete)
                concrete = re.sub(r"\{[a-z_]+\}", "1", concrete)
                out.append((method.upper(), concrete))
    return sorted(set(out))


def test_every_write_route_is_known():
    routes = _write_routes()
    assert len(routes) > 50  # the walk found the real app, not an empty one
    for role, expected in EXPECTED.items():
        allowed = {f"{m} {p}" for m, p in routes if access.allowed(Identity(role, "audit"), m, p)}
        assert allowed == expected, role


def test_no_service_token_writes_under_beths_plate():
    for role in access.SERVICE_ROLES:
        for m, p in _write_routes():
            if p.startswith("/api/plates/elizabeth"):
                assert not access.allowed(Identity(role, "audit"), m, p), (role, m, p)


def test_routes_that_apply_a_suggestion_need_a_person():
    for path in ("/api/table/suggestions/1/accept", "/api/table/suggestions/1/dismiss"):
        for role in access.SERVICE_ROLES:
            assert not access.allowed(Identity(role, "audit"), "POST", path)


def test_no_api_docs_and_pages_carry_security_headers():
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404
    page = client.get("/kev")
    csp = page.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "'unsafe-inline'" not in csp and "frame-ancestors 'none'" in csp
    assert page.headers["x-frame-options"] == "DENY"
    assert page.headers["referrer-policy"] == "no-referrer"
    assert page.headers["x-content-type-options"] == "nosniff"
    assert "<script>" not in page.text  # no inline script for the policy to refuse
    assert client.get("/api/me").headers["x-frame-options"] == "DENY"
