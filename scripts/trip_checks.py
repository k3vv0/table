"""The standby check pushes (DESIGN.md §13). Run by deploy/systemd/table-trips.timer every
15 minutes; run it by hand the same way:

    systemctl start table-trips && journalctl -u table-trips -n 20

For each flight leg not yet made: "check loads" at 8pm Eastern 14, 11 and 8 days out and
every day from 5 days out, the candidates in order the night before, and once more 3 hours
before the first candidate leaves. Nothing is sent twice.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

from app.db import engine, init_db  # noqa: E402
from app.services import trips  # noqa: E402


def main() -> int:
    init_db()
    with Session(engine) as session:
        report = trips.run_checks(session)
    if report["pushes"]:
        print(f"trip checks: {report['pushes']} push(es), {report['delivered']} delivered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
