"""Trips and places (DESIGN.md §13). Table only: trips are the household's, so there's no
Plate version of these routes. People write here; agents read, and propose trips and
options as suggestions (kinds trip and module) instead. No service token may reach a write
route below (tests/test_audit.py), and booking codes are left out of what they read.
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlmodel import Session, col, select

from app.access import PERSON, current_identity
from app.db import get_session
from app.models import Place
from app.services import trips

router = APIRouter(prefix="/api/table", tags=["trips"])


class TripIn(BaseModel):
    title: str
    status: str = "planning"
    window: str = ""
    starts_on: Optional[date] = None
    ends_on: Optional[date] = None
    travelers: list[str] = ["kev", "elizabeth"]
    places: str = ""
    notes: str = ""
    key_date_id: Optional[int] = None


class TripPatch(BaseModel):
    title: Optional[str] = None
    status: Optional[str] = None
    window: Optional[str] = None
    starts_on: Optional[date] = None
    ends_on: Optional[date] = None
    travelers: Optional[list[str]] = None
    places: Optional[str] = None
    notes: Optional[str] = None
    moneyhub_goal_id: Optional[int] = None


class ModuleIn(BaseModel):
    kind: str
    state: str = "option"
    title: str = ""
    starts_on: Optional[date] = None
    ends_on: Optional[date] = None
    travelers: list[str] = []
    payer: str = ""
    cost: Optional[int] = None
    points: Optional[int] = None
    from_airports: str = ""
    to_airports: str = ""
    flight: str = ""
    departs: str = ""
    arrives: str = ""
    link: str = ""
    confirmation: str = ""
    notes: str = ""


class ModulePatch(BaseModel):
    state: Optional[str] = None
    title: Optional[str] = None
    starts_on: Optional[date] = None
    ends_on: Optional[date] = None
    travelers: Optional[list[str]] = None
    payer: Optional[str] = None
    cost: Optional[int] = None
    points: Optional[int] = None
    from_airports: Optional[str] = None
    to_airports: Optional[str] = None
    flight: Optional[str] = None
    departs: Optional[str] = None
    arrives: Optional[str] = None
    link: Optional[str] = None
    confirmation: Optional[str] = None
    notes: Optional[str] = None


class PlaceIn(BaseModel):
    name: str
    airports: str = ""
    bags_airport: str = ""
    hosts: str = ""
    ride: str = ""
    ride_cost: Optional[int] = None
    notes: str = ""
    home: bool = False


class PlacePatch(BaseModel):
    name: Optional[str] = None
    airports: Optional[str] = None
    bags_airport: Optional[str] = None
    hosts: Optional[str] = None
    ride: Optional[str] = None
    ride_cost: Optional[int] = None
    notes: Optional[str] = None
    home: Optional[bool] = None


def _person(request: Request) -> bool:
    return current_identity(request).role == PERSON


def _changes(body: BaseModel) -> dict:
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="Nothing to change.")
    return changes


@router.get("/trips")
def list_trips(request: Request, session: Session = Depends(get_session)):
    return {"trips": trips.list_trips(session, _person(request))}


@router.post("/trips", status_code=201)
def add_trip(body: TripIn, request: Request, session: Session = Depends(get_session)):
    trip = trips.create_trip(session, current_identity(request), body.model_dump())
    return trips.trip_out(session, trip, True)


@router.get("/trips/{trip_id}")
def get_trip(trip_id: int, request: Request, session: Session = Depends(get_session)):
    return trips.trip_out(session, trips.get_trip(session, trip_id), _person(request))


@router.patch("/trips/{trip_id}")
def edit_trip(trip_id: int, body: TripPatch, request: Request, session: Session = Depends(get_session)):
    trip = trips.update_trip(session, current_identity(request), trips.get_trip(session, trip_id), _changes(body))
    return trips.trip_out(session, trip, True)


@router.get("/trips/{trip_id}/summary", response_class=PlainTextResponse)
def trip_summary(trip_id: int, session: Session = Depends(get_session)):
    return trips.summary(session, trips.get_trip(session, trip_id))


@router.post("/trips/{trip_id}/modules", status_code=201)
def add_module(trip_id: int, body: ModuleIn, request: Request, session: Session = Depends(get_session)):
    trip = trips.get_trip(session, trip_id)
    m = trips.add_module(session, current_identity(request), trip, body.model_dump())
    return trips.module_out(m, True)


@router.patch("/trips/{trip_id}/modules/{module_id}")
def edit_module(trip_id: int, module_id: int, body: ModulePatch, request: Request, session: Session = Depends(get_session)):
    trip = trips.get_trip(session, trip_id)
    m = trips.update_module(session, current_identity(request), trip, trips.get_module(session, trip, module_id), _changes(body))
    return trips.module_out(m, True)


@router.get("/places")
def list_places(session: Session = Depends(get_session)):
    return {"places": [trips.place_out(p) for p in session.exec(select(Place).order_by(col(Place.home).desc(), col(Place.name)))]}


@router.post("/places", status_code=201)
def add_place(body: PlaceIn, request: Request, session: Session = Depends(get_session)):
    return trips.place_out(trips.add_place(session, current_identity(request), body.model_dump()))


@router.patch("/places/{place_id}")
def edit_place(place_id: int, body: PlacePatch, request: Request, session: Session = Depends(get_session)):
    p = session.get(Place, place_id)
    if p is None:
        raise HTTPException(status_code=404, detail="No such place.")
    return trips.place_out(trips.update_place(session, current_identity(request), p, _changes(body)))
