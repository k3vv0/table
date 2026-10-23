"""Milestone 6: mail the sweep sorted as vault before the Vault existed gets its vault
entry (DESIGN.md §12). Creates the vault_pick table too. Safe to run on every deploy: a
message that already has an entry is skipped.

    .venv/bin/python scripts/migrate_vault_backfill.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select  # noqa: E402

from app.access import Identity  # noqa: E402
from app.db import engine, init_db  # noqa: E402
from app.models import SourceMessage  # noqa: E402
from app.services import vault  # noqa: E402

MIGRATION = Identity("system", "migrate_vault_backfill")


def main() -> int:
    init_db()
    added = 0
    with Session(engine) as session:
        for message in session.exec(select(SourceMessage).where(SourceMessage.sort == "vault")).all():
            if vault.from_mail(session, MIGRATION, message):
                added += 1
    print(f"vault backfill: {added} entr{'y' if added == 1 else 'ies'} added")
    return 0


if __name__ == "__main__":
    sys.exit(main())
