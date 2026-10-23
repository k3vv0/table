"""The evening daily-session reminder (DESIGN.md §9). Run by deploy/systemd/table-reminder.timer
at 8pm Eastern; run it by hand the same way:

    systemctl start table-reminder && journalctl -u table-reminder -n 20

Each person who left "Daily session reminder" on gets one push with what's waiting on their
Plate and the Table. Nobody gets a push when there's nothing to look at.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

from app.db import engine, init_db  # noqa: E402
from app.services import items, push  # noqa: E402

# Whose spaces each person's reminder covers, and where tapping it lands.
COVERS = {"kev": (("kev", "table"), "/kev#session"), "elizabeth": (("elizabeth", "table"), "/elizabeth#session"), "household": (("table",), "/#session")}


def summary(session: Session, spaces: tuple[str, ...]) -> str | None:
    today = inbox = due = 0
    for space in spaces:
        data = items.daily_session(session, space)
        today += len(data["today"])
        inbox += len(data["inbox"])
        due += len(data["due_this_week"])
    parts = []
    if today:
        parts.append(f"{today} on today's plate")
    if due:
        parts.append(f"{due} due this week")
    if inbox:
        parts.append(f"{inbox} in the inbox")
    return ", ".join(parts) if parts else None


def main() -> int:
    init_db()
    sent = 0
    with Session(engine) as session:
        bodies = {person: summary(session, spaces) for person, (spaces, _) in COVERS.items()}
    for person, body in bodies.items():
        if body:
            sent += push.send([person], "Daily session", body[0].upper() + body[1:] + ".", COVERS[person][1], kind="daily")
    print(f"daily reminder: {sent} push(es) sent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
