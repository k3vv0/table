"""The daily date alerts (DESIGN.md §11). Run by deploy/systemd/table-dates.timer at 6:30am
Eastern, before the morning routine; run it by hand the same way:

    systemctl start table-dates && journalctl -u table-dates -n 20

Planned dates get an item 30 days ahead; light dates get one the day before, on today's
plate. Both push on the day. Nothing is sent twice, and a missed morning catches up.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

from app.db import engine, init_db  # noqa: E402
from app.services import dates  # noqa: E402


def main() -> int:
    init_db()
    with Session(engine) as session:
        report = dates.run_alerts(session)
    print(f"date alerts: {report['items']} item(s) up, {report['pushes']} push(es), {report['delivered']} delivered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
