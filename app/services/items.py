"""Items: capture, triage, merging, and the daily and weekly views (DESIGN.md §1, §5).

Views are computed from status and dates against today_local():

- today: status today, plus later items whose defer_until has arrived ("back today").
- inbox: captured, not yet triaged.
- later: deferred to a future date, or to no date at all.
- done: closed in the last 30 days.

The daily session is today's plate to keep or defer, what's due within 7 days, and the
inbox. The weekly review is everything deferred, everything overdue, inbox items older
than a week, and what got done this week.
"""

from datetime import date, timedelta

from fastapi import HTTPException
from sqlmodel import Session, col, select

from app.access import ELIZABETH, KEV, Identity
from app.localtime import today_local
from app.models import Activity, Item, ItemSource, utcnow

OPEN = ("inbox", "today", "later")
WEEK = timedelta(days=7)
DONE_WINDOW = timedelta(days=30)


def actor(identity: Identity) -> str:
    if identity.person:
        return identity.person
    return "household" if identity.role == "person" else identity.role


def record(session: Session, identity: Identity, action: str, target: str = "", detail: str = "") -> None:
    session.add(Activity(role=identity.role, who=actor(identity), action=action, target=target, detail=detail[:500]))


def _check_owner(space: str, owner: str | None) -> str | None:
    owner = owner or None
    if owner is None:
        return None
    if space != "table":
        raise HTTPException(status_code=422, detail="Only Table items take an owner.")
    if owner not in (KEV, ELIZABETH):
        raise HTTPException(status_code=422, detail="owner must be kev, elizabeth or blank.")
    return owner


def get_item(session: Session, space: str, item_id: int) -> Item:
    item = session.get(Item, item_id)
    # An id from another space is a 404, so a Plate's ids can't be probed from the Table.
    if item is None or item.space != space:
        raise HTTPException(status_code=404, detail="No such item here.")
    return item


def create(
    session: Session,
    identity: Identity,
    space: str,
    title: str,
    notes: str = "",
    due_on: date | None = None,
    owner: str | None = None,
    today: bool = False,
    source_kind: str = "manual",
    source_ref: str = "",
) -> Item:
    title = title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="An item needs a title.")
    item = Item(
        space=space,
        owner=_check_owner(space, owner),
        title=title[:300],
        notes=notes.strip(),
        due_on=due_on,
        status="today" if today else "inbox",
        created_by=actor(identity),
    )
    session.add(item)
    session.flush()
    session.add(ItemSource(item_id=item.id, kind=source_kind, ref=source_ref, snippet=title[:500]))
    record(session, identity, "item.create", f"item:{item.id}", item.title)
    session.commit()
    session.refresh(item)
    return item


EDITABLE = ("title", "notes", "due_on", "owner", "kind")
# By hand an item is a task, or a watch waiting on something (watch-check reads those).
# Dates and vault links get their kinds from their own milestones.
HAND_KINDS = ("task", "watch")


def update(session: Session, identity: Identity, item: Item, changes: dict) -> Item:
    if item.status == "merged":
        raise HTTPException(status_code=409, detail="This item was merged into another one.")
    if "title" in changes:
        title = (changes["title"] or "").strip()
        if not title:
            raise HTTPException(status_code=422, detail="An item needs a title.")
        item.title = title[:300]
    if "notes" in changes:
        item.notes = (changes["notes"] or "").strip()
    if "due_on" in changes:
        item.due_on = changes["due_on"]
    if "owner" in changes:
        item.owner = _check_owner(item.space, changes["owner"])
    if "kind" in changes:
        if changes["kind"] not in HAND_KINDS or item.kind not in HAND_KINDS:
            raise HTTPException(status_code=422, detail="Only a task can become a watch, and back.")
        item.kind = changes["kind"]
    item.updated_at = utcnow()
    record(session, identity, "item.edit", f"item:{item.id}", ", ".join(sorted(changes)))
    session.commit()
    session.refresh(item)
    return item


TRIAGE_ACTIONS = ("today", "later", "done", "reopen")


def triage(session: Session, identity: Identity, item: Item, action: str, defer_until: date | None = None) -> Item:
    if item.status == "merged":
        raise HTTPException(status_code=409, detail="This item was merged into another one.")
    if action == "today":
        item.status, item.defer_until, item.done_at = "today", None, None
    elif action == "later":
        if defer_until is not None and defer_until <= today_local():
            raise HTTPException(status_code=422, detail="Defer to a day after today, or leave the date blank.")
        item.status, item.defer_until, item.done_at = "later", defer_until, None
    elif action == "done":
        item.status, item.done_at = "done", utcnow()
    elif action == "reopen":
        item.status, item.defer_until, item.done_at = "inbox", None, None
    else:
        raise HTTPException(status_code=422, detail=f"action must be one of {', '.join(TRIAGE_ACTIONS)}.")
    item.updated_at = utcnow()
    detail = f"{action} {defer_until.isoformat()}" if defer_until else action
    record(session, identity, "item.triage", f"item:{item.id}", detail)
    session.commit()
    session.refresh(item)
    return item


def merge(session: Session, identity: Identity, space: str, keep_id: int, merge_ids: list[int]) -> Item:
    """Fold duplicates into one item: their sources move over, their notes are appended,
    the earliest due date wins, and each one is left as merged, pointing at the keeper."""
    merge_ids = [i for i in dict.fromkeys(merge_ids) if i != keep_id]
    if not merge_ids:
        raise HTTPException(status_code=422, detail="Pick at least one other item to merge.")
    keep = get_item(session, space, keep_id)
    others = [get_item(session, space, i) for i in merge_ids]
    for item in [keep, *others]:
        if item.status not in OPEN:
            raise HTTPException(status_code=409, detail=f"Item {item.id} is {item.status}; only open items merge.")
    for other in others:
        for source in session.exec(select(ItemSource).where(ItemSource.item_id == other.id)):
            source.item_id = keep.id
        if other.notes and other.notes not in keep.notes:
            keep.notes = f"{keep.notes}\n\n{other.notes}".strip()
        if other.due_on and (keep.due_on is None or other.due_on < keep.due_on):
            keep.due_on = other.due_on
        other.status, other.merged_into_id, other.updated_at = "merged", keep.id, utcnow()
    keep.updated_at = utcnow()
    record(session, identity, "item.merge", f"item:{keep.id}", "from " + ", ".join(f"item:{o.id}" for o in others))
    session.commit()
    session.refresh(keep)
    return keep


# --- views ---------------------------------------------------------------------------


def _open_items(session: Session, space: str) -> list[Item]:
    return list(session.exec(select(Item).where(Item.space == space, col(Item.status).in_(OPEN))))


def _is_today(item: Item, today: date) -> bool:
    return item.status == "today" or (
        item.status == "later" and item.defer_until is not None and item.defer_until <= today
    )


def _by_due(items: list[Item]) -> list[Item]:
    # Dated items first, soonest first; then the rest, oldest capture first.
    return sorted(items, key=lambda i: (i.due_on is None, i.due_on or date.max, i.created_at))


def view(session: Session, space: str, name: str) -> list[Item]:
    today = today_local()
    if name == "done":
        since = utcnow() - DONE_WINDOW
        stmt = select(Item).where(Item.space == space, Item.status == "done", col(Item.done_at) >= since)
        return sorted(session.exec(stmt), key=lambda i: i.done_at, reverse=True)
    items = _open_items(session, space)
    if name == "today":
        return _by_due([i for i in items if _is_today(i, today)])
    if name == "inbox":
        return sorted((i for i in items if i.status == "inbox"), key=lambda i: i.created_at, reverse=True)
    if name == "later":
        later = [i for i in items if i.status == "later" and not _is_today(i, today)]
        return sorted(later, key=lambda i: (i.defer_until is None, i.defer_until or date.max, i.created_at))
    raise HTTPException(status_code=404, detail="No such view.")


def counts(session: Session, space: str) -> dict:
    today = today_local()
    items = _open_items(session, space)
    return {
        "today": sum(_is_today(i, today) for i in items),
        "inbox": sum(i.status == "inbox" for i in items),
        "later": sum(i.status == "later" and not _is_today(i, today) for i in items),
    }


def daily_session(session: Session, space: str) -> dict:
    today = today_local()
    items = _open_items(session, space)
    on_plate = [i for i in items if _is_today(i, today)]
    plate_ids = {i.id for i in on_plate}
    due_soon = [i for i in items if i.due_on and i.due_on <= today + WEEK and i.id not in plate_ids]
    return {
        "date": today.isoformat(),
        "today": _by_due(on_plate),
        "due_this_week": _by_due(due_soon),
        "inbox": sorted((i for i in items if i.status == "inbox"), key=lambda i: i.created_at),
    }


def weekly_review(session: Session, space: str) -> dict:
    today = today_local()
    items = _open_items(session, space)
    week_ago = utcnow() - WEEK
    done_stmt = select(Item).where(Item.space == space, Item.status == "done", col(Item.done_at) >= week_ago)

    return {
        "date": today.isoformat(),
        "overdue": _by_due([i for i in items if i.due_on and i.due_on < today]),
        "later": view(session, space, "later"),
        "stale_inbox": sorted(
            (i for i in items if i.status == "inbox" and i.created_at < week_ago), key=lambda i: i.created_at
        ),
        "done_this_week": sorted(session.exec(done_stmt), key=lambda i: i.done_at, reverse=True),
    }
