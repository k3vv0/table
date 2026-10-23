"""Suggestions (DESIGN.md §10): an agent proposes, a person accepts or dismisses.

Filing checks that whatever a suggestion points at is real and in the same space: the
source message or event was synced into it, the items are open and belong to it. A source
is suggested as a new item at most once, so a dismissed suggestion never comes back on
the next sweep. A date suggestion (from dates-interview) carries the date in payload and
is checked like a date a person types in; a goal suggestion carries only a money-hub goal's
number. Accepting is the only path from a suggestion to a change, and its route
depends on require_person.
"""

import json
from datetime import date

from fastapi import HTTPException
from sqlmodel import Session, col, select

from app.access import Identity
from app.models import (
    SUGGESTION_KINDS,
    CalendarEvent,
    Item,
    ItemSource,
    KeyDate,
    SourceMessage,
    Suggestion,
    utcnow,
)
from app.services import dates
from app.services import items as svc
from app.localtime import today_local

ITEM_KINDS = ("task", "watch")


def _open_item(session: Session, space: str, item_id: int | None) -> Item:
    if item_id is None:
        raise HTTPException(status_code=422, detail="This kind of suggestion needs item_id.")
    item = svc.get_item(session, space, item_id)
    if item.status not in svc.OPEN:
        raise HTTPException(status_code=409, detail=f"Item {item.id} is {item.status}.")
    return item


def _check_source(session: Session, space: str, kind: str, ref: str) -> None:
    if not kind and not ref:
        return
    if kind == "email":
        found = session.exec(
            select(SourceMessage.id).where(SourceMessage.space == space, SourceMessage.gm_msgid == ref)
        ).first()
    elif kind == "calendar":
        uid, _, day = ref.rpartition("@")
        try:
            day = date.fromisoformat(day)
        except ValueError:
            day = None
        found = day and session.exec(
            select(CalendarEvent.id).where(
                CalendarEvent.space == space, CalendarEvent.uid == uid, CalendarEvent.starts_on == day
            )
        ).first()
    else:
        raise HTTPException(status_code=422, detail="source_kind must be email or calendar.")
    if not found:
        raise HTTPException(status_code=422, detail=f"No synced {kind} {ref!r} in this space.")


def _merge_ids(raw: list[int]) -> list[int]:
    return [int(i) for i in dict.fromkeys(raw)]


def file(session: Session, identity: Identity, space: str, body: dict, run_id: int | None = None) -> Suggestion:
    kind = body.get("kind")
    if kind not in SUGGESTION_KINDS:
        raise HTTPException(status_code=422, detail=f"kind must be one of {', '.join(SUGGESTION_KINDS)}.")
    title = (body.get("title") or "").strip()[:300]
    notes = (body.get("notes") or "").strip()[:4000]
    source_kind, source_ref = body.get("source_kind") or "", (body.get("source_ref") or "").strip()
    _check_source(session, space, source_kind, source_ref)
    s = Suggestion(
        space=space,
        kind=kind,
        title=title,
        notes=notes,
        due_on=body.get("due_on"),
        source_kind=source_kind,
        source_ref=source_ref,
        rationale=(body.get("rationale") or "").strip()[:1000],
        created_by=svc.actor(identity),
        run_id=run_id,
    )
    if kind == "date":
        payload = body.get("payload") or {}
        fields = dates.clean(space, payload)
        s.title = fields["title"]
        s.notes = fields["notes"]
        person = (payload.get("person_name") or "").strip()[:120]
        relation = (payload.get("relation") or "").strip()[:200]
        s.payload = json.dumps({**fields, "person_name": person, "relation": relation})
        if session.exec(
            select(KeyDate.id).where(KeyDate.space == space, KeyDate.active == True, KeyDate.title == s.title)  # noqa: E712
        ).first() or session.exec(
            select(Suggestion.id).where(
                Suggestion.space == space, Suggestion.kind == "date", Suggestion.status == "pending", Suggestion.title == s.title
            )
        ).first():
            raise HTTPException(status_code=409, detail="That date is already here or already suggested.")
    elif kind == "item":
        if not title:
            raise HTTPException(status_code=422, detail="A new item needs a title.")
        s.item_kind = body.get("item_kind") or "task"
        if s.item_kind not in ITEM_KINDS:
            raise HTTPException(status_code=422, detail="item_kind must be task or watch.")
        if s.due_on and s.due_on < today_local():
            s.due_on = None  # a past due date on a new item is noise, not a commitment
        if source_ref:
            already = session.exec(
                select(Suggestion.id).where(
                    Suggestion.space == space, Suggestion.kind == "item", Suggestion.source_ref == source_ref
                )
            ).first()
            on_item = session.exec(
                select(ItemSource.id)
                .join(Item, Item.id == ItemSource.item_id)
                .where(Item.space == space, ItemSource.ref == source_ref)
            ).first()
            if already or on_item:
                raise HTTPException(status_code=409, detail="That source was already suggested or captured.")
    else:
        item = _open_item(session, space, body.get("item_id"))
        s.item_id = item.id
        if kind == "goal":
            goal = (body.get("payload") or {}).get("moneyhub_goal_id")
            if not isinstance(goal, int) or goal < 1:
                raise HTTPException(status_code=422, detail="A goal suggestion needs payload.moneyhub_goal_id.")
            s.payload = json.dumps({"moneyhub_goal_id": goal})
        if kind == "note" and not notes:
            raise HTTPException(status_code=422, detail="A note suggestion needs notes.")
        if kind == "merge":
            ids = [i for i in _merge_ids(body.get("merge_ids") or []) if i != item.id]
            if not ids:
                raise HTTPException(status_code=422, detail="A merge needs merge_ids besides item_id.")
            for i in ids:
                _open_item(session, space, i)
            s.merge_ids = ",".join(map(str, ids))
        pending = session.exec(
            select(Suggestion.id).where(
                Suggestion.space == space,
                Suggestion.kind == kind,
                Suggestion.item_id == item.id,
                Suggestion.status == "pending",
            )
        ).first()
        if pending:
            raise HTTPException(status_code=409, detail="There's already a pending suggestion like that.")
    session.add(s)
    session.flush()
    svc.record(session, identity, "suggestion.file", f"suggestion:{s.id}", f"{kind} {title}".strip())
    session.commit()
    session.refresh(s)
    return s


def get(session: Session, space: str, suggestion_id: int) -> Suggestion:
    s = session.get(Suggestion, suggestion_id)
    if s is None or s.space != space:
        raise HTTPException(status_code=404, detail="No such suggestion here.")
    if s.status != "pending":
        raise HTTPException(status_code=409, detail=f"Already {s.status}.")
    return s


def accept(session: Session, identity: Identity, s: Suggestion, edits: dict) -> Item | None:
    """Apply a suggestion. edits may change an item suggestion's title, notes or due date,
    or put it straight on today's plate. A date suggestion adds the date (and its person)
    and returns None: there's no item until the date's window opens."""
    if s.kind == "date":
        kd = dates.add_date(session, identity, s.space, json.loads(s.payload))
        s.status, s.decided_by, s.decided_at = "accepted", svc.actor(identity), utcnow()
        session.add(s)
        svc.record(session, identity, "suggestion.accept", f"suggestion:{s.id}", f"date:{kd.id}")
        session.commit()
        return None
    if s.kind == "item":
        item = svc.create(
            session,
            identity,
            s.space,
            edits.get("title") or s.title,
            edits.get("notes", s.notes),
            edits.get("due_on", s.due_on),
            today=bool(edits.get("today")),
            source_kind=s.source_kind or "agent",
            source_ref=s.source_ref,
        )
        if s.item_kind != item.kind:
            item.kind = s.item_kind
            session.add(item)
    elif s.kind == "note":
        item = _open_item(session, s.space, s.item_id)
        changes = {"notes": f"{item.notes}\n\n{s.notes}".strip()}
        if s.due_on:
            changes["due_on"] = s.due_on
        item = svc.update(session, identity, item, changes)
    elif s.kind == "goal":
        item = _open_item(session, s.space, s.item_id)
        item = svc.update(session, identity, item, {"moneyhub_goal_id": json.loads(s.payload)["moneyhub_goal_id"]})
    elif s.kind == "merge":
        item = svc.merge(session, identity, s.space, s.item_id, [int(i) for i in s.merge_ids.split(",") if i])
    else:  # close
        item = svc.triage(session, identity, _open_item(session, s.space, s.item_id), "done")
    s.status, s.decided_by, s.decided_at, s.result_item_id = "accepted", svc.actor(identity), utcnow(), item.id
    session.add(s)
    svc.record(session, identity, "suggestion.accept", f"suggestion:{s.id}", f"item:{item.id}")
    session.commit()
    return item


def dismiss(session: Session, identity: Identity, s: Suggestion) -> None:
    s.status, s.decided_by, s.decided_at = "dismissed", svc.actor(identity), utcnow()
    session.add(s)
    svc.record(session, identity, "suggestion.dismiss", f"suggestion:{s.id}")
    session.commit()


def pending(session: Session, space: str) -> list[Suggestion]:
    stmt = (
        select(Suggestion)
        .where(Suggestion.space == space, Suggestion.status == "pending")
        .order_by(col(Suggestion.created_at))
    )
    return list(session.exec(stmt))
