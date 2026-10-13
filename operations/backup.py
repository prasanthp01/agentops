"""Non-overwriting SQLite online backup. Encrypt the resulting file before off-host storage."""

import argparse
import sqlite3
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("database", type=Path)
parser.add_argument("destination", type=Path)
args = parser.parse_args()
if not args.database.is_file() or args.destination.exists():
    parser.error("Existing source and a new destination path are required")
with (
    sqlite3.connect(f"file:{args.database}?mode=ro", uri=True) as source,
    sqlite3.connect(args.destination) as dest,
):
    source.backup(dest)
    result = dest.execute("PRAGMA integrity_check").fetchone()[0]
args.destination.chmod(0o600)
if result != "ok":
    raise SystemExit("Backup integrity check failed")
print("Backup completed and SQLite integrity checked. Restore verification is a separate step.")
