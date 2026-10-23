"""Routine runs and sync health (DESIGN.md §10).

A routine opens a run when it starts and closes it with a short summary. Closing a run
that filed suggestions sends one push per space ("3 suggestions to look at") instead of
one per suggestion.
"""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, col, func, select

from app.access import PERSON, current_identity
from app.config import get_settings
from app.db import get_session
from app.models import AgentRun, Suggestion, SyncState, utcnow
from app.services import items as svc
from app.services import push

router = APIRouter(tags=["runs"])

ROUTINES = ("sweep", "morning")


class RunIn(BaseModel):
    routine: str


class RunPatch(BaseModel):
    status: str  # ok | failed
    summary: str = ""


def run_out(run: AgentRun) -> dict:
    return {
        "id": run.id,
        "routine": run.routine,
        "role": run.role,
        "status": run.status,
        "summary": run.summary,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


def notify_suggestions(run_id: int) -> None:
    from app.db import engine

    with Session(engine) as session:
        stmt = (
            select(Suggestion.space, func.count())
            .where(Suggestion.run_id == run_id, Suggestion.status == "pending")
            .group_by(Suggestion.space)
        )
        counts = list(session.exec(stmt))
    for space, n in counts:
        where = "the table" if space == "table" else "your plate"
        noun = "suggestion" if n == 1 else "suggestions"
        push.send(push.AUDIENCE[space], f"Suggested for {where}", f"{n} {noun} to look at.", push.space_url(space) + "#inbox", kind="new_items")


@router.post("/api/agent-runs", status_code=201)
def start_run(body: RunIn, request: Request, session: Session = Depends(get_session)):
    if body.routine not in ROUTINES:
        raise HTTPException(status_code=422, detail=f"routine must be one of {', '.join(ROUTINES)}.")
    identity = current_identity(request)
    run = AgentRun(routine=body.routine, role=identity.role)
    session.add(run)
    session.flush()
    svc.record(session, identity, "run.start", f"run:{run.id}", body.routine)
    session.commit()
    session.refresh(run)
    return run_out(run)


@router.patch("/api/agent-runs/{run_id}")
def finish_run(
    run_id: int, body: RunPatch, request: Request, background: BackgroundTasks, session: Session = Depends(get_session)
):
    run = session.get(AgentRun, run_id)
    identity = current_identity(request)
    if run is None or (identity.role != PERSON and run.role != identity.role):
        raise HTTPException(status_code=404, detail="No such run.")
    if run.status != "running":
        raise HTTPException(status_code=409, detail="That run is already closed.")
    if body.status not in ("ok", "failed"):
        raise HTTPException(status_code=422, detail="status must be ok or failed.")
    run.status, run.summary, run.finished_at = body.status, body.summary.strip()[:2000], utcnow()
    session.add(run)
    svc.record(session, identity, "run.finish", f"run:{run.id}", body.status)
    session.commit()
    background.add_task(notify_suggestions, run.id)
    return run_out(run)


@router.get("/api/agent-runs")
def recent_runs(session: Session = Depends(get_session)):
    runs = session.exec(select(AgentRun).order_by(col(AgentRun.started_at).desc()).limit(20))
    return {"runs": [run_out(r) for r in runs]}


@router.get("/api/sync-status")
def sync_status(session: Session = Depends(get_session)):
    """When each mailbox and calendar last synced, and the last error. No secrets: mail
    shows its address and space, a calendar its id and space."""
    settings = get_settings()
    states = {s.key: s for s in session.exec(select(SyncState))}

    def row(key: str, label: str, space: str) -> dict:
        s = states.get(key)
        return {
            "source": label,
            "space": space,
            "last_ok_at": s.last_ok_at.isoformat() if s and s.last_ok_at else None,
            "last_error": s.last_error if s else "",
        }

    mail = [row(f"mail:{a.address}", a.address, a.space) for a in settings.mail_account_list]
    cals = [row(f"calendar:{f.name}", f.name, f.space) for f in settings.calendar_feed_list]
    return {"mail": mail, "calendars": cals, "archive_noise": settings.mail_archive_noise}
