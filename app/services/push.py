"""Web Push to the home-screen app (DESIGN.md §9).

Pushes go out on a worker thread after the response, so a slow push service never slows
a capture. A subscription the push service says is gone (404 or 410) is deleted; any
other failure is logged and the subscription kept for next time.
"""

import json
import logging
from collections.abc import Iterable

from pywebpush import WebPushException, webpush
from sqlmodel import Session, col, select

from app.config import get_settings
from app.db import engine
from app.models import Item, PushSubscription, utcnow

log = logging.getLogger("table.push")

# Who hears about each space. The household sign-in shares the Table only.
AUDIENCE = {"table": ("kev", "elizabeth", "household"), "kev": ("kev",), "elizabeth": ("elizabeth",)}


def _send_one(sub: PushSubscription, payload: dict) -> bool | None:
    """True on success, False if the subscription is gone for good, None on other errors."""
    settings = get_settings()
    try:
        webpush(
            subscription_info={"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
            data=json.dumps(payload),
            vapid_private_key=settings.vapid_private_key,
            vapid_claims={"sub": settings.vapid_subject},
            ttl=12 * 3600,
            timeout=10,
        )
        return True
    except WebPushException as exc:
        status = getattr(exc.response, "status_code", None)
        if status in (404, 410):
            return False
        log.warning("push to %s subscription %s failed: %s", sub.person, sub.id, status or exc)
        return None
    except Exception as exc:  # noqa: BLE001 -- a network hiccup must never break the caller
        log.warning("push to %s subscription %s failed: %s", sub.person, sub.id, exc)
        return None


def send(people: Iterable[str], title: str, body: str, url: str, kind: str | None = None) -> int:
    """Push to every subscription of these people that wants this kind ("new_items",
    "daily", or None for all). Returns how many were delivered."""
    if not get_settings().push_enabled:
        return 0
    payload = {"title": title, "body": body, "url": url}
    sent = 0
    with Session(engine) as session:
        stmt = select(PushSubscription).where(col(PushSubscription.person).in_(list(people)))
        for sub in session.exec(stmt).all():
            if kind and not getattr(sub, kind):
                continue
            result = _send_one(sub, payload)
            if result is True:
                sub.last_ok_at = utcnow()
                sent += 1
            elif result is False:
                log.info("push subscription %s for %s is gone; removing it", sub.id, sub.person)
                session.delete(sub)
        session.commit()
    return sent


def space_url(space: str) -> str:
    return "/" if space == "table" else f"/{space}"


def notify_new_item(item_id: int) -> None:
    """Tell everyone who shares the item's space, except whoever added it."""
    with Session(engine) as session:
        item = session.get(Item, item_id)
        if item is None:
            return
        people = [p for p in AUDIENCE.get(item.space, ()) if p != item.created_by]
        where = "the table" if item.space == "table" else "your plate"
        title, space = item.title, item.space
    if people:
        send(people, f"New on {where}", title, space_url(space) + "#inbox", kind="new_items")
