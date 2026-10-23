"""Mail and calendar sync (DESIGN.md §10). Run by deploy/systemd/table-sync.timer every 15
minutes; run it by hand the same way:

    systemctl start table-sync && journalctl -u table-sync -n 20

Copies new inbox mail from every MAIL_ACCOUNTS mailbox, archives what was sorted as noise
(when MAIL_ARCHIVE_NOISE is on), and refreshes every CALENDAR_FEEDS calendar. Prints what
it did; never a password or a feed address.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

from app import access  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import engine, init_db  # noqa: E402
from app.services import calendar, mail  # noqa: E402


def main() -> int:
    access.check_settings(get_settings())
    init_db()
    with Session(engine) as session:
        report = {"mail": mail.sync_all(session), "calendar": calendar.sync_all(session)}
    print(json.dumps(report))
    failed = any("error" in v for part in report.values() for v in part.values() if isinstance(v, dict))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
