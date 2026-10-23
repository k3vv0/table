import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import access
from app.config import get_settings
from app.db import init_db
from app.localtime import today_local


@asynccontextmanager
async def lifespan(app: FastAPI):
    access.check_settings(get_settings())
    init_db()
    yield


# Access decisions go to the service's journal (journalctl -u table) at INFO; uvicorn only
# configures its own loggers, so this one gets a handler of its own.
log = logging.getLogger("table.access")
if not log.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    log.addHandler(_handler)
    log.setLevel(logging.INFO)
    log.propagate = False

app = FastAPI(title="table", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


def _asset_version() -> str:
    """Newest mtime under app/static, appended to CSS/JS URLs so a browser never keeps a
    stale file after a deploy."""
    return str(int(max(os.path.getmtime(e.path) for e in os.scandir("app/static") if e.is_file())))


templates.env.globals["asset_version"] = _asset_version


# DESIGN.md §4. Every request is identified from Cloudflare Access's signed token and
# checked against its role before a route handler runs; see app/access.py.
@app.middleware("http")
async def _enforce_access(request: Request, call_next):
    # The raw path the router will dispatch on. Not request.url.path: Starlette rebuilds
    # request.url from the Host header, so a crafted Host could make any path look public.
    path = request.scope["path"]
    if path in access.PUBLIC_EXACT:
        return await call_next(request)

    try:
        identity = await access.identify(request.headers.get("cf-access-jwt-assertion"), get_settings())
    except access.AccessDenied as exc:
        log.warning("denied %s %s: %s", request.method, path, exc)
        return PlainTextResponse("Not signed in.", status_code=403)

    if not access.allowed(identity, request.method, path):
        log.warning("blocked %s %s for %s %s", request.method, path, identity.role, identity.who)
        return PlainTextResponse("Not available on this account.", status_code=403)
    if identity.role in access.SERVICE_ROLES and request.method not in ("GET", "HEAD"):
        log.info("%s %s %s", identity.role, request.method, path)  # every service-token write leaves a trace

    request.state.identity = identity
    response = await call_next(request)
    if path.startswith("/api/"):
        # Household data is never kept by Cloudflare or a browser cache.
        response.headers.setdefault("Cache-Control", "private, no-store")
    return response


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    identity = access.current_identity(request)
    return {"role": identity.role, "person": identity.person, "name": identity.name}


# --- pages -------------------------------------------------------------------------
# `/` is the shared Table; `/kev` and `/elizabeth` are each person's Plate.


def _page(request: Request, template: str, active: str, **context) -> HTMLResponse:
    identity = access.current_identity(request)
    return templates.TemplateResponse(
        request,
        template,
        {"active": active, "me": identity, "today": today_local(), **context},
    )


@app.get("/", response_class=HTMLResponse)
def table_page(request: Request):
    return _page(request, "table.html", "table")


@app.get("/{person}", response_class=HTMLResponse)
def plate_page(person: str, request: Request):
    if person not in access.PEOPLE:
        raise HTTPException(status_code=404)
    # The middleware already refused the other person's Plate; this is the page itself.
    return _page(request, "plate.html", person, owner=person, owner_name=access.DISPLAY_NAMES[person])
