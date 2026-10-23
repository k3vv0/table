"""People and key dates (DESIGN.md §11). Like the items API, the same routes serve the Table
at /api/table and each Plate at /api/plates/<person>, so the Plate rules in app/access.py
cover them: Beth's dates are hers, and no service token may write here. Agents propose a
date as a suggestion (kind date) instead.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, col, select

from app.access import current_identity
from app.db import get_session
from app.models import Person
from app.routers.items import _plate_space, _table_space
from app.services import dates
from app.services import items as svc


class DateIn(BaseModel):
    title: str
    month: int
    day: Optional[int] = None
    nth: Optional[int] = None
    weekday: Optional[int] = None
    year: Optional[int] = None
    recurs: bool = True
    weight: str = "light"
    notes: str = ""
    owner: Optional[str] = None
    person_id: Optional[int] = None
    person_name: str = ""  # adds the person if they aren't here yet
    relation: str = ""


class DatePatch(BaseModel):
    title: Optional[str] = None
    month: Optional[int] = None
    day: Optional[int] = None
    year: Optional[int] = None
    recurs: Optional[bool] = None
    weight: Optional[str] = None
    notes: Optional[str] = None
    owner: Optional[str] = None
    person_id: Optional[int] = None
    active: Optional[bool] = None  # false removes it


class PersonIn(BaseModel):
    name: str
    relation: str = ""
    notes: str = ""


class PersonPatch(BaseModel):
    name: Optional[str] = None
    relation: Optional[str] = None
    notes: Optional[str] = None
    archived: Optional[bool] = None


def person_out(p: Person) -> dict:
    return {"id": p.id, "name": p.name, "relation": p.relation, "notes": p.notes, "archived": p.archived}


def _build(router: APIRouter, space_of) -> APIRouter:
    @router.get("/dates")
    def list_dates(space: str = Depends(space_of), session: Session = Depends(get_session)):
        return {"dates": dates.upcoming(session, space), "presets": dates.presets_left(session, space)}

    @router.post("/dates", status_code=201)
    def add_date(body: DateIn, request: Request, space: str = Depends(space_of), session: Session = Depends(get_session)):
        kd = dates.add_date(session, current_identity(request), space, body.model_dump())
        return next(d for d in dates.upcoming(session, space) if d["id"] == kd.id)

    @router.post("/dates/presets/{key}", status_code=201)
    def add_preset(key: str, request: Request, space: str = Depends(space_of), session: Session = Depends(get_session)):
        kd = dates.add_preset(session, current_identity(request), space, key)
        return next(d for d in dates.upcoming(session, space) if d["id"] == kd.id)

    @router.patch("/dates/{date_id}")
    def edit_date(
        date_id: int, body: DatePatch, request: Request, space: str = Depends(space_of), session: Session = Depends(get_session)
    ):
        changes = body.model_dump(exclude_unset=True)
        if not changes:
            raise HTTPException(status_code=422, detail="Nothing to change.")
        kd = dates.edit_date(session, current_identity(request), dates.get_date(session, space, date_id), changes)
        if not kd.active:
            return {"id": kd.id, "removed": True}
        return next(d for d in dates.upcoming(session, space) if d["id"] == kd.id)

    @router.get("/people")
    def list_people(space: str = Depends(space_of), session: Session = Depends(get_session)):
        stmt = select(Person).where(Person.space == space, Person.archived == False).order_by(col(Person.name))  # noqa: E712
        return {"people": [person_out(p) for p in session.exec(stmt)]}

    @router.post("/people", status_code=201)
    def add_person(body: PersonIn, request: Request, space: str = Depends(space_of), session: Session = Depends(get_session)):
        if not body.name.strip():
            raise HTTPException(status_code=422, detail="A person needs a name.")
        identity = current_identity(request)
        p = dates.find_or_add_person(session, identity, space, body.name, body.relation)
        if body.notes.strip():
            p.notes = body.notes.strip()[:2000]
            session.add(p)
        session.commit()
        session.refresh(p)
        return person_out(p)

    @router.patch("/people/{person_id}")
    def edit_person(
        person_id: int, body: PersonPatch, request: Request, space: str = Depends(space_of), session: Session = Depends(get_session)
    ):
        p = dates.get_person(session, space, person_id)
        changes = body.model_dump(exclude_unset=True)
        if "name" in changes:
            if not (changes["name"] or "").strip():
                raise HTTPException(status_code=422, detail="A person needs a name.")
            p.name = changes["name"].strip()[:120]
        if "relation" in changes:
            p.relation = (changes["relation"] or "").strip()[:200]
        if "notes" in changes:
            p.notes = (changes["notes"] or "").strip()[:2000]
        if "archived" in changes:
            p.archived = bool(changes["archived"])
        session.add(p)
        svc.record(session, current_identity(request), "person.edit", f"person:{p.id}", ", ".join(sorted(changes)))
        session.commit()
        session.refresh(p)
        return person_out(p)

    return router


table_router = _build(APIRouter(prefix="/api/table", tags=["table"]), _table_space)
plate_router = _build(APIRouter(prefix="/api/plates/{person}", tags=["plates"]), _plate_space)
