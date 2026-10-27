"""Disaster recovery and backup restore utility.

Usage:
    python -m deploy.restore --list
    python -m deploy.restore --latest
    python -m deploy.restore --file data/backups/companion_backup_20261007_030000.db
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.config import load_settings
from app.memory.backup import verify_backup_integrity


def list_backups(backup_dir: Path) -> list[Path]:
    if not backup_dir.exists():
        return []
    backups = sorted(backup_dir.glob("companion_backup_*.db"), reverse=True)
    return backups


def restore_backup(source_backup: Path, live_db: Path) -> bool:
    if not source_backup.exists():
        print(f"Error: Backup file not found: {source_backup}", file=sys.stderr)
        return False

    if not verify_backup_integrity(source_backup):
        print(f"Error: Backup file failed integrity check: {source_backup}", file=sys.stderr)
        return False

    live_db.parent.mkdir(parents=True, exist_ok=True)

    # If a live database exists, create a pre-restore safety copy first
    if live_db.exists():
        safety_path = live_db.with_name(f"{live_db.stem}_pre_restore_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.db")
        print(f"Creating safety copy of current live database -> {safety_path.name}")
        shutil.copy2(live_db, safety_path)

    # Perform the restore copy
    print(f"Restoring {source_backup.name} -> {live_db}...")
    shutil.copy2(source_backup, live_db)

    # Also clean up WAL and SHM files if they exist to avoid stale journals
    for journal_ext in ("-wal", "-shm"):
        journal_file = Path(f"{live_db}{journal_ext}")
        if journal_file.exists():
            journal_file.unlink()

    # Final integrity check on restored database
    if not verify_backup_integrity(live_db):
        print(f"Error: Restored live database failed integrity check!", file=sys.stderr)
        return False

    print("✅ Database restore completed successfully!")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore SQLite database from backup")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="list all available backups")
    group.add_argument("--latest", action="store_true", help="restore the most recent backup")
    group.add_argument("--file", type=str, help="path to a specific backup file to restore")

    args = parser.parse_args()

    try:
        settings = load_settings()
    except Exception as exc:
        print(f"Error loading configuration: {exc}", file=sys.stderr)
        return 1

    backup_dir = settings.backup_dir
    live_db = settings.db_path

    backups = list_backups(backup_dir)

    if args.list:
        print(f"Available backups in {backup_dir}:")
        if not backups:
            print("  (No backups found)")
            return 0
        for b in backups:
            size_kb = b.stat().st_size / 1024
            mtime = datetime.fromtimestamp(b.stat().st_mtime, tz=timezone.utc)
            print(f"  • {b.name} ({size_kb:.1f} KB, {mtime:%Y-%m-%d %H:%M:%S UTC})")
        return 0

    if args.latest:
        if not backups:
            print(f"No backups available in {backup_dir} to restore.", file=sys.stderr)
            return 1
        target_backup = backups[0]
        print(f"Selected latest backup: {target_backup.name}")
    else:
        target_backup = Path(args.file)

    success = restore_backup(target_backup, live_db)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
