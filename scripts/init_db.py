"""Create any missing tables in the database DATABASE_URL points at. The app also does this
on start; this is for a fresh local checkout."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import init_db  # noqa: E402

init_db()
print("tables ready")
