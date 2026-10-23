"""Items API (DESIGN.md §5). The same routes serve the Table at /api/table and each Plate
at /api/plates/<person>; app/access.py has already refused the other person's Plate before
a request gets here."""

from datetime import date
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, col, select

from app.access import PEOPLE, current_identity
from app.db import get_session
from app.models import Item, ItemSource
from app.services import items as svc
from app.services import push


class ItemIn(BaseModel):
    title: str
    notes: str = ""
    due_on: Optional[date] = None
    owner: Optional[str] = None
    today: bool = False  # straight onto today's plate instead of the inbox


class ItemPatch(BaseModel):
    title: Optional[str] = None
    kind: Optional[str] = None  # task | watch
    notes: Optional[str] = None
    due_on: Optional[date] = None
    owner: Optional[str] = None
    moneyhub_goal_id: Optional[int] = None  # a money-hub goal's number; null unlinks


class TriageIn(BaseModel):
    action: str  # today | later | done | reopen
    defer_until: Optional[date] = None


class MergeIn(BaseModel):
    keep_id: int
    merge_ids: list[int]


def _sources(session: Session, items: list[Item]) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {i.id: [] for i in items}
    if out:
        stmt = select(ItemSource).where(col(ItemSource.item_id).in_(list(out))).order_by(ItemSource.id)
        for s in session.exec(stmt):
            out[s.item_id].append({"kind": s.kind, "ref": s.ref, "snippet": s.snippet})
    return out


def _out(item: Item, sources: list[dict]) -> dict:
    return {
        "id": item.id,
        "space": item.space,
        "owner": item.owner,
        "kind": item.kind,
        "title": item.title,
        "notes": item.notes,
        "status": item.status,
        "due_on": item.due_on.isoformat() if item.due_on else None,
        "defer_until": item.defer_until.isoformat() if item.defer_until else None,
        "merged_into_id": item.merged_into_id,
        "created_by": item.created_by,
        "created_at": item.created_at.isoformat(),
        "done_at": item.done_at.isoformat() if item.done_at else None,
        "moneyhub_goal_id": item.moneyhub_goal_id,
        "sources": sources,
    }


def _many(session: Session, items: list[Item]) -> list[dict]:
    sources = _sources(session, items)
    return [_out(i, sources[i.id]) for i in items]


def _one(session: Session, item: Item) -> dict:
    return _many(session, [item])[0]


def _lists(session: Session, data: dict) -> dict:
    return {"date": data["date"], **{k: _many(session, v) for k, v in data.items() if k != "date"}}


def _build(router: APIRouter, space_of) -> APIRouter:
    @router.get("/items")
    def list_items(view: str = "today", space: str = Depends(space_of), session: Session = Depends(get_session)):
        items = svc.view(session, space, view)
        return {"view": view, "items": _many(session, items), "counts": svc.counts(session, space)}

    @router.post("/items", status_code=201)
    def create_item(
        body: ItemIn,
        request: Request,
        background: BackgroundTasks,
        space: str = Depends(space_of),
        session: Session = Depends(get_session),
    ):
        identity = current_identity(request)
        item = svc.create(session, identity, space, body.title, body.notes, body.due_on, body.owner, body.today)
        background.add_task(push.notify_new_item, item.id)
        return _one(session, item)

    @router.get("/items/{item_id}")
    def get_item(item_id: int, space: str = Depends(space_of), session: Session = Depends(get_session)):
        return _one(session, svc.get_item(session, space, item_id))

    @router.patch("/items/{item_id}")
    def edit_item(
        item_id: int,
        body: ItemPatch,
        request: Request,
        space: str = Depends(space_of),
        session: Session = Depends(get_session),
    ):
        changes = body.model_dump(exclude_unset=True)
        if not changes:
            raise HTTPException(status_code=422, detail="Nothing to change.")
        item = svc.update(session, current_identity(request), svc.get_item(session, space, item_id), changes)
        return _one(session, item)

    @router.post("/items/{item_id}/triage")
    def triage_item(
        item_id: int,
        body: TriageIn,
        request: Request,
        space: str = Depends(space_of),
        session: Session = Depends(get_session),
    ):
        item = svc.get_item(session, space, item_id)
        item = svc.triage(session, current_identity(request), item, body.action, body.defer_until)
        return _one(session, item)

    @router.post("/items/merge")
    def merge_items(
        body: MergeIn, request: Request, space: str = Depends(space_of), session: Session = Depends(get_session)
    ):
        return _one(session, svc.merge(session, current_identity(request), space, body.keep_id, body.merge_ids))

    @router.get("/session")
    def daily_session(space: str = Depends(space_of), session: Session = Depends(get_session)):
        return _lists(session, svc.daily_session(session, space))

    @router.get("/review")
    def weekly_review(space: str = Depends(space_of), session: Session = Depends(get_session)):
        return _lists(session, svc.weekly_review(session, space))

    return router


def _table_space() -> str:
    return "table"


def _plate_space(person: str) -> str:
    if person not in PEOPLE:
        raise HTTPException(status_code=404, detail="No such plate.")
    return person


table_router = _build(APIRouter(prefix="/api/table", tags=["table"]), _table_space)
plate_router = _build(APIRouter(prefix="/api/plates/{person}", tags=["plates"]), _plate_space)
