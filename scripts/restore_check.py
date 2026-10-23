"""Proves a backup restores (DESIGN.md §6): decrypts one backup from
scripts/backup.sh into a scratch file and checks it like a real restore would.

Run it where the age private key lives (Kev's PC, not the droplet), against a file
downloaded from the R2 bucket's daily/ folder:

    python scripts/restore_check.py table-20261022T083012Z.db.gz.age path/to/backup-key.txt

Needs the `age` CLI on PATH. Prints the integrity check, row counts per table and the
newest activity, then deletes the decrypted copy unless --keep is given. It
never touches data/table.db or the droplet.
"""

import argparse
import gzip
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("backup", type=Path)
    parser.add_argument("identity", type=Path, help="age private key file")
    parser.add_argument("--keep", type=Path, help="also write the restored database here")
    args = parser.parse_args()

    decrypted = subprocess.run(
        ["age", "-d", "-i", str(args.identity), str(args.backup)], capture_output=True, check=False
    )
    if decrypted.returncode != 0:
        print(f"age could not decrypt {args.backup}: {decrypted.stderr.decode().strip()}")
        return 1

    with tempfile.TemporaryDirectory() as scratch:
        db_path = Path(args.keep) if args.keep else Path(scratch) / "restored.db"
        db_path.write_bytes(gzip.decompress(decrypted.stdout))
        conn = sqlite3.connect(db_path)
        try:
            check = conn.execute("PRAGMA integrity_check").fetchone()[0]
            print(f"integrity_check: {check}")
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            for table in tables:
                count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                print(f"  {table}: {count} rows")
            if "activity" in tables:
                newest = conn.execute("SELECT MAX(at) FROM activity").fetchone()[0]
                print(f"newest activity: {newest}")
        finally:
            conn.close()
        if args.keep:
            print(f"restored copy kept at {db_path}")
    return 0 if check == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
