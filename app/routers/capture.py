"""POST /api/capture: the "Send to Plate" iOS Shortcut (DESIGN.md §9), and "Save to Vault"
with vault: true (§12).

Two locks: Cloudflare Access lets the request in only with the capture service token
(app/access.py, which allows it this one route and nothing else), and this route wants a
person's capture token in X-Capture-Token to know whose Plate the capture lands on."""

import re
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, select

from app.access import CAPTURE, PERSON, Identity, current_identity
from app.db import get_session
from app.models import CaptureToken, utcnow
from app.routers.me import hash_token
from app.services import items as svc
from app.services import push

router = APIRouter(tags=["capture"])

_URL = re.compile(r"https?://\S+")


class CaptureIn(BaseModel):
    text: str = ""
    url: Optional[str] = None
    to: str = "plate"  # plate | table
    vault: bool = False  # the "Save to Vault" Shortcut: a read-later link, not a task


@router.post("/api/capture", status_code=201)
def capture(
    body: CaptureIn,
    request: Request,
    background: BackgroundTasks,
    x_capture_token: str = Header(default=""),
    session: Session = Depends(get_session),
):
    caller = current_identity(request)
    if caller.role not in (CAPTURE, PERSON):
        raise HTTPException(status_code=403, detail="Not a capture client.")
    row = session.exec(select(CaptureToken).where(CaptureToken.token_hash == hash_token(x_capture_token))).first()
    if not x_capture_token or row is None:
        raise HTTPException(status_code=401, detail="Unknown capture token.")
    if body.to not in ("plate", "table"):
        raise HTTPException(status_code=422, detail="to must be plate or table.")

    text = (body.text or "").strip()
    url = (body.url or "").strip() or None
    if url is None:
        # The share sheet often hands over a URL as plain text.
        match = _URL.fullmatch(text)
        url = match.group(0) if match else None
    if not text and not url:
        raise HTTPException(status_code=422, detail="Nothing to capture.")

    space = row.person if body.to == "plate" else "table"
    who = Identity(CAPTURE, f"shortcut:{row.name}", row.person)
    title = text or url
    kind = "link" if body.vault else "task"
    item = svc.create(session, who, space, title, source_kind="shortcut", source_ref=url or "", kind=kind)
    row.last_used_at = utcnow()
    session.add(row)
    session.commit()
    if not body.vault:  # a saved read isn't news for anyone else
        background.add_task(push.notify_new_item, item.id)
    return {"id": item.id, "space": space, "title": item.title, "kind": item.kind}
