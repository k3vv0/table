"""Trips (DESIGN.md §13): item.trip_id ties a prep checklist item to its trip. Creates the
new place, trip, trip_module and trip_check tables, and adds the column if it isn't there;
safe to run on every deploy.

    .venv/bin/python scripts/migrate_trips.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect, text  # noqa: E402

from app.db import engine, init_db  # noqa: E402


def main() -> int:
    init_db()  # creates the trip tables, and item itself on a new database
    columns = {c["name"] for c in inspect(engine).get_columns("item")}
    if "trip_id" in columns:
        print("item.trip_id: already there")
        return 0
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE item ADD COLUMN trip_id INTEGER REFERENCES trip(id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_item_trip_id ON item (trip_id)"))
    print("item.trip_id: added")
    return 0


if __name__ == "__main__":
    sys.exit(main())
