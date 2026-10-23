"""Milestone 5: suggestion.payload holds a date suggestion's fields and a goal suggestion's
money-hub goal number (DESIGN.md §11). Adds the column if it isn't there; safe to run on
every deploy.

    .venv/bin/python scripts/migrate_suggestion_payload.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect, text  # noqa: E402

from app.db import engine, init_db  # noqa: E402


def main() -> int:
    init_db()  # creates person, key_date and date_alert, and suggestion itself on a new database
    columns = {c["name"] for c in inspect(engine).get_columns("suggestion")}
    if "payload" in columns:
        print("suggestion.payload: already there")
        return 0
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE suggestion ADD COLUMN payload VARCHAR NOT NULL DEFAULT ''"))
    print("suggestion.payload: added")
    return 0


if __name__ == "__main__":
    sys.exit(main())
