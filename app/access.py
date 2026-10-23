"""Who is asking, and what they may reach (DESIGN.md §4). Modelled on money-hub's
app/access.py.

Every request that reaches the droplet has already passed Cloudflare Access, which adds a
signed JWT in the Cf-Access-Jwt-Assertion header. This module verifies that token
(signature against the team's published keys, audience, issuer, expiry) and maps it to one
identity. Anything else gets a 403 before a route handler runs.

- person: Kev (KEV_EMAILS) or Beth (BETH_EMAILS). Both share the Table at `/`. Each one's
  Plate (`/kev`, `/elizabeth` and its API under `/api/plates/<name>`) is theirs alone:
  the other person gets a 403, not an empty page.
- household: the joint Google account (HOUSE_EMAILS). A person at the keyboard, so it may
  approve suggestions on the Table, but it has no Plate and reaches neither person's.
- agent: this project's cloud sessions, signing in with a Cloudflare Access service token
  whose Client ID is AGENT_SERVICE_TOKEN_ID. Service-token JWTs carry that Client ID in
  `common_name` and no email. Reads the Table and Kev's Plate, never Beth's, and writes
  only what SERVICE_SCOPES lists.

- capture: the "Send to Plate" iOS Shortcut's service token (CAPTURE_SERVICE_TOKEN_ID).
  Reads nothing and may only POST /api/capture, which also checks a person's capture
  token to know whose Plate it is.

Routine tokens (sweep, morning) join SERVICE_SCOPES in milestone 4, so the audit checks one
table. Approving anything an agent suggested is a person's job: such routes also depend on
require_person, which refuses every service token even if a scope lists the route by
mistake.

ACCESS_TEAM_DOMAIN blank (the default) turns all of this off and treats every request as
Kev -- required for local dev and tests, where there is no Access in front.
"""

import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache

import jwt
from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.config import Settings

log = logging.getLogger("table.access")

PERSON, AGENT, CAPTURE = "person", "agent", "capture"
KEV, ELIZABETH = "kev", "elizabeth"
PEOPLE = (KEV, ELIZABETH)
DISPLAY_NAMES = {KEV: "Kev", ELIZABETH: "Beth"}


@dataclass(frozen=True)
class Identity:
    role: str  # person | agent
    who: str  # email, the service token's Client ID, or "local" when Access is off
    person: str | None = None  # kev | elizabeth, or None for the household sign-in

    @property
    def name(self) -> str:
        if self.role == PERSON and self.person is None:
            return "Household"
        return DISPLAY_NAMES.get(self.person or "", self.role)


LOCAL_KEV = Identity(PERSON, "local", KEV)


class AccessDenied(Exception):
    pass


def check_settings(settings: Settings) -> None:
    """Refuse to start in a half-configured state rather than fail open."""
    if settings.access_team_domain and not settings.access_aud:
        raise RuntimeError("ACCESS_TEAM_DOMAIN is set but ACCESS_AUD is blank (DESIGN.md §4).")
    if settings.access_team_domain and not settings.kev_email_set:
        raise RuntimeError("ACCESS_TEAM_DOMAIN is set but KEV_EMAILS is blank (DESIGN.md §4).")
    if settings.beth_emails and not settings.access_team_domain:
        # Without Access verification Beth would silently be signed in as Kev and see his
        # Plate, so this is a deploy that forgot the Access settings.
        raise RuntimeError("BETH_EMAILS is set but ACCESS_TEAM_DOMAIN is blank; add the Access settings.")
    if settings.house_emails and not settings.access_team_domain:
        raise RuntimeError("HOUSE_EMAILS is set but ACCESS_TEAM_DOMAIN is blank; add the Access settings.")
    sets = (settings.kev_email_set, settings.beth_email_set, settings.house_email_set)
    if any(a & b for i, a in enumerate(sets) for b in sets[i + 1 :]):
        raise RuntimeError("An email is in more than one of KEV_EMAILS, BETH_EMAILS and HOUSE_EMAILS.")
    agent_id, capture_id = settings.agent_service_token_id.strip(), settings.capture_service_token_id.strip()
    if agent_id and agent_id == capture_id:
        # One token can't be both roles; the capture one exists to be narrower.
        raise RuntimeError("AGENT_SERVICE_TOKEN_ID and CAPTURE_SERVICE_TOKEN_ID must differ.")


@lru_cache
def _jwks_client(team_domain: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"https://{team_domain}/cdn-cgi/access/certs", cache_keys=True, lifespan=3600)


def _signing_key(token: str, team_domain: str):
    # Separate so tests can swap in their own key instead of fetching Cloudflare's.
    return _jwks_client(team_domain).get_signing_key_from_jwt(token).key


async def identify(token: str | None, settings: Settings) -> Identity:
    if not settings.access_team_domain:
        return LOCAL_KEV
    if not token:
        raise AccessDenied("no Access token")
    try:
        key = await run_in_threadpool(_signing_key, token, settings.access_team_domain)
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=settings.access_aud,
            issuer=f"https://{settings.access_team_domain}",
            options={"require": ["exp", "aud", "iss"]},
        )
    except Exception as exc:  # any verification or key-fetch failure is a denial
        raise AccessDenied(f"invalid Access token: {type(exc).__name__}") from exc

    email = (claims.get("email") or "").strip().lower()
    if email:
        if email in settings.kev_email_set:
            return Identity(PERSON, email, KEV)
        if email in settings.beth_email_set:
            return Identity(PERSON, email, ELIZABETH)
        if email in settings.house_email_set:
            return Identity(PERSON, email)
        raise AccessDenied(f"unknown user {email}")

    common_name = (claims.get("common_name") or "").strip()
    if common_name:
        if settings.agent_service_token_id and common_name == settings.agent_service_token_id.strip():
            return Identity(AGENT, common_name)
        if settings.capture_service_token_id and common_name == settings.capture_service_token_id.strip():
            return Identity(CAPTURE, common_name)
    raise AccessDenied("unknown service token")


# --- what each identity may reach ----------------------------------------------------

# Reachable without any identity: just says the process is up (deploy.sh's health check).
PUBLIC_EXACT = {"/api/health"}

_READ_METHODS = {"GET", "HEAD"}


def plate_prefixes(person: str) -> tuple[str, ...]:
    return (f"/{person}", f"/api/plates/{person}")


def _under(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == p or path.startswith(p + "/") for p in prefixes)


def _other_plates(person: str | None) -> tuple[str, ...]:
    # The household sign-in (person None) has no Plate, so every Plate is someone else's.
    return tuple(p for other in PEOPLE if other != person for p in plate_prefixes(other))


@dataclass(frozen=True)
class Scope:
    """What a service-token role may reach. Reads are everything outside blocked_prefixes
    (or nothing, with reads=False); writes are only the listed paths and whole-path
    patterns (for routes with an id in them), and never under blocked_prefixes either."""

    reads: bool = True
    blocked_prefixes: tuple[str, ...] = ()
    write_exact: frozenset[str] = frozenset()
    write_patterns: tuple[re.Pattern, ...] = field(default=())

    def allows(self, method: str, path: str) -> bool:
        if _under(path, self.blocked_prefixes):
            return False
        if method in _READ_METHODS:
            return self.reads
        return path in self.write_exact or any(p.fullmatch(path) for p in self.write_patterns)


SERVICE_SCOPES: dict[str, Scope] = {
    # Cloud threads working for Kev: read the Table and Kev's Plate, never Beth's. No
    # writes yet; milestone 2 onwards lists each one here.
    AGENT: Scope(blocked_prefixes=plate_prefixes(ELIZABETH)),
    # The iOS Shortcut: files one capture, reads nothing.
    CAPTURE: Scope(reads=False, write_exact=frozenset({"/api/capture"})),
}
SERVICE_ROLES = frozenset(SERVICE_SCOPES)


def allowed(identity: Identity, method: str, path: str) -> bool:
    if identity.role == PERSON:
        return not _under(path, _other_plates(identity.person))
    scope = SERVICE_SCOPES.get(identity.role)
    return scope is not None and scope.allows(method, path)


def current_identity(request: Request) -> Identity:
    return getattr(request.state, "identity", LOCAL_KEV)


def require_person(request: Request) -> Identity:
    """Dependency for routes that apply or approve something an agent suggested: only
    Kev's, Beth's or the household's own sign-in may. A second lock behind
    SERVICE_SCOPES, so listing such a route there by mistake still can't let an agent
    approve its own suggestion."""
    identity = current_identity(request)
    if identity.role != PERSON:
        log.warning("refused %s %s: needs a person, got %s %s", request.method, request.url.path, identity.role, identity.who)
        raise HTTPException(status_code=403, detail="Needs a person's own sign-in, not an agent.")
    return identity
