"""The vault: read-later links (DESIGN.md §12).

A vault entry is an item of kind link. It arrives three ways: the "Save to Vault" Shortcut,
the board's Vault view, or a synced message sorted as vault (by the sweep or by hand),
which becomes an entry pointing back at the message in Gmail. Each morning the morning
routine's vault-picks skill picks two entries per space for today and writes a short
summary of each. Picks are a reading list, not a change: nothing is opened, read or
closed for anyone, and done (read) is a person's tap.
"""

from datetime import timedelta

from fastapi import HTTPException
from sqlmodel import Session, col, select

from app.access import Identity
from app.localtime import today_local
from app.models import Item, ItemSource, SourceMessage, VaultPick
from app.services import items as svc

PICKS_PER_DAY = 2
REPICK_AFTER = timedelta(days=14)  # a pick that wasn't read can come back after this


def gmail_url(message: SourceMessage) -> str:
    # Gmail opens a message from its X-GM-MSGID in hex, in whichever account is named.
    return f"https://mail.google.com/mail/?authuser={message.account}#all/{int(message.gm_msgid):x}"


def link_of(session: Session, item: Item) -> dict:
    """Where the entry points: the saved URL, or the message in Gmail, plus the message's
    excerpt (untrusted) while Table still keeps it."""
    out = {"url": None, "from": None, "excerpt": None}
    for s in session.exec(select(ItemSource).where(ItemSource.item_id == item.id).order_by(ItemSource.id)):
        if s.kind == "email":
            m = session.exec(
                select(SourceMessage).where(SourceMessage.space == item.space, SourceMessage.gm_msgid == s.ref)
            ).first()
            if m:
                out.update(url=gmail_url(m), **{"from": m.from_name or m.from_addr}, excerpt=m.excerpt or None)
                return out
        elif s.ref.startswith(("https://", "http://")):
            out["url"] = s.ref
            return out
    return out


def _entry_for(session: Session, space: str, gm_msgid: str) -> Item | None:
    return session.exec(
        select(Item)
        .join(ItemSource, ItemSource.item_id == Item.id)
        .where(Item.space == space, Item.kind == "link", ItemSource.kind == "email", ItemSource.ref == gm_msgid)
    ).first()


def from_mail(session: Session, identity: Identity, message: SourceMessage) -> Item | None:
    """A message sorted as vault becomes a vault entry once (re-sorting it back is a no-op)."""
    if _entry_for(session, message.space, message.gm_msgid):
        return None
    sender = message.from_name or message.from_addr
    return svc.create(
        session,
        identity,
        message.space,
        message.subject or f"Mail from {sender}",
        f"From {sender}" if sender else "",
        source_kind="email",
        source_ref=message.gm_msgid,
        kind="link",
    )


def off_mail(session: Session, identity: Identity, message: SourceMessage) -> None:
    """A message moved out of vault takes its unread entry with it."""
    item = _entry_for(session, message.space, message.gm_msgid)
    if item and item.status in svc.OPEN:
        svc.triage(session, identity, item, "done")


def todays(session: Session, space: str) -> list[VaultPick]:
    stmt = select(VaultPick).where(VaultPick.space == space, VaultPick.on == today_local()).order_by(col(VaultPick.id))
    return list(session.exec(stmt))


def pick(session: Session, identity: Identity, space: str, body: dict, run_id: int | None = None) -> VaultPick:
    item = svc.get_item(session, space, int(body.get("item_id") or 0))
    if item.kind != "link" or item.status not in svc.OPEN:
        raise HTTPException(status_code=422, detail="Only an unread vault entry can be picked.")
    summary = (body.get("summary") or "").strip()[:1200]
    if not summary:
        raise HTTPException(status_code=422, detail="A pick needs a summary.")
    today = today_local()
    if len(todays(session, space)) >= PICKS_PER_DAY:
        raise HTTPException(status_code=409, detail=f"Already {PICKS_PER_DAY} picks today.")
    recent = session.exec(
        select(VaultPick.id).where(VaultPick.item_id == item.id, VaultPick.on > today - REPICK_AFTER)
    ).first()
    if recent:
        raise HTTPException(status_code=409, detail="Picked in the last two weeks; pick something else.")
    p = VaultPick(
        space=space,
        on=today,
        item_id=item.id,
        summary=summary,
        why=(body.get("why") or "").strip()[:300],
        created_by=svc.actor(identity),
        run_id=run_id,
    )
    session.add(p)
    session.flush()
    svc.record(session, identity, "vault.pick", f"item:{item.id}", today.isoformat())
    session.commit()
    session.refresh(p)
    return p
