"""The signed-in person's own settings: push subscriptions and Shortcut capture tokens
(DESIGN.md §9). Every route here needs a person's own sign-in."""

import hashlib
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, select

from app.access import Identity, require_person
from app.config import get_settings
from app.db import get_session
from app.models import CaptureToken, PushSubscription
from app.services import push
from app.services.items import actor, record

router = APIRouter(prefix="/api/me", tags=["me"])


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# --- push -----------------------------------------------------------------------------


class SubscriptionKeys(BaseModel):
    p256dh: str
    auth: str


class SubscriptionIn(BaseModel):
    endpoint: str
    keys: SubscriptionKeys
    device: str = ""


class SubscriptionPatch(BaseModel):
    new_items: Optional[bool] = None
    daily: Optional[bool] = None


def _sub_out(sub: PushSubscription) -> dict:
    return {
        "id": sub.id,
        "device": sub.device,
        "endpoint": sub.endpoint,
        "new_items": sub.new_items,
        "daily": sub.daily,
        "created_at": sub.created_at.isoformat(),
        "last_ok_at": sub.last_ok_at.isoformat() if sub.last_ok_at else None,
    }


def _my_sub(session: Session, identity: Identity, sub_id: int) -> PushSubscription:
    sub = session.get(PushSubscription, sub_id)
    if sub is None or sub.person != actor(identity):
        raise HTTPException(status_code=404, detail="No such device.")
    return sub


@router.get("/push")
def push_settings(identity: Identity = Depends(require_person), session: Session = Depends(get_session)):
    subs = session.exec(select(PushSubscription).where(PushSubscription.person == actor(identity))).all()
    settings = get_settings()
    return {
        "enabled": settings.push_enabled,
        "public_key": settings.vapid_public_key,
        "devices": [_sub_out(s) for s in subs],
    }


@router.post("/push", status_code=201)
def subscribe(body: SubscriptionIn, identity: Identity = Depends(require_person), session: Session = Depends(get_session)):
    if not body.endpoint.startswith("https://"):
        raise HTTPException(status_code=422, detail="A push endpoint is an https URL.")
    sub = session.exec(select(PushSubscription).where(PushSubscription.endpoint == body.endpoint)).first()
    if sub is None:
        sub = PushSubscription(endpoint=body.endpoint, p256dh="", auth="", person=actor(identity))
    # The same browser re-subscribing (or a shared device changing hands) takes it over.
    sub.person, sub.p256dh, sub.auth, sub.device = actor(identity), body.keys.p256dh, body.keys.auth, body.device[:80]
    session.add(sub)
    record(session, identity, "push.subscribe", "", sub.device)
    session.commit()
    session.refresh(sub)
    return _sub_out(sub)


@router.patch("/push/{sub_id}")
def update_subscription(
    sub_id: int, body: SubscriptionPatch, identity: Identity = Depends(require_person), session: Session = Depends(get_session)
):
    sub = _my_sub(session, identity, sub_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(sub, field, value)
    session.add(sub)
    session.commit()
    session.refresh(sub)
    return _sub_out(sub)


@router.delete("/push/{sub_id}", status_code=204)
def unsubscribe(sub_id: int, identity: Identity = Depends(require_person), session: Session = Depends(get_session)):
    session.delete(_my_sub(session, identity, sub_id))
    record(session, identity, "push.unsubscribe", f"push:{sub_id}")
    session.commit()


@router.post("/push/test")
def test_push(identity: Identity = Depends(require_person)):
    sent = push.send([actor(identity)], "Table", "Notifications are working.", "/settings")
    return {"sent": sent}


# --- Shortcut capture tokens ----------------------------------------------------------------


class TokenIn(BaseModel):
    name: str


def _plate_owner(identity: Identity) -> str:
    if not identity.person:
        raise HTTPException(status_code=403, detail="The household sign-in has no Plate to capture to.")
    return identity.person


@router.get("/capture-tokens")
def list_tokens(identity: Identity = Depends(require_person), session: Session = Depends(get_session)):
    rows = session.exec(select(CaptureToken).where(CaptureToken.person == _plate_owner(identity))).all()
    return [
        {
            "id": t.id,
            "name": t.name,
            "created_at": t.created_at.isoformat(),
            "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
        }
        for t in rows
    ]


@router.post("/capture-tokens", status_code=201)
def make_token(body: TokenIn, identity: Identity = Depends(require_person), session: Session = Depends(get_session)):
    person = _plate_owner(identity)
    name = body.name.strip()[:60] or "iPhone"
    token = "tbl_" + secrets.token_urlsafe(32)
    row = CaptureToken(person=person, name=name, token_hash=hash_token(token))
    session.add(row)
    record(session, identity, "capture_token.create", "", name)
    session.commit()
    session.refresh(row)
    # The only time the token itself leaves the server.
    return {"id": row.id, "name": row.name, "token": token}


@router.delete("/capture-tokens/{token_id}", status_code=204)
def revoke_token(token_id: int, identity: Identity = Depends(require_person), session: Session = Depends(get_session)):
    row = session.get(CaptureToken, token_id)
    if row is None or row.person != _plate_owner(identity):
        raise HTTPException(status_code=404, detail="No such token.")
    session.delete(row)
    record(session, identity, "capture_token.revoke", f"capture_token:{token_id}", row.name)
    session.commit()
