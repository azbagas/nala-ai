"""Automated SQLite backup and rotation.

Performs safe online SQLite backups using the sqlite3 backup API (WAL-safe),
prunes backups older than keep_days, and verifies backup integrity.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.utils.time import now_utc

log = logging.getLogger(__name__)


def perform_backup(
    db_path: Path,
    backup_dir: Path,
    keep_days: int = 14,
    now: datetime | None = None,
) -> Path:
    """Performs an online, transactionally consistent SQLite backup and rotates old backups.

    Args:
        db_path: Path to the source SQLite database.
        backup_dir: Directory where backups will be saved.
        keep_days: Number of days to retain backups before deleting.
        now: Optional current timestamp for deterministic testing.

    Returns:
        Path to the newly created backup file.
    """
    backup_dir.mkdir(parents=True, exist_ok=True)

    now_dt = now or now_utc()
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)

    timestamp_str = now_dt.strftime("%Y%m%d_%H%M%S")
    backup_filename = f"companion_backup_{timestamp_str}.db"
    dest_path = backup_dir / backup_filename

    log.info("starting_sqlite_backup source=%s dest=%s", db_path, dest_path)

    # Online backup using native sqlite3 backup API
    source_conn = sqlite3.connect(str(db_path))
    dest_conn = sqlite3.connect(str(dest_path))
    try:
        source_conn.backup(dest_conn)
    finally:
        dest_conn.close()
        source_conn.close()

    # Verify integrity of the newly created backup
    if not verify_backup_integrity(dest_path):
        raise RuntimeError(f"Backup integrity check failed for {dest_path}")

    log.info("sqlite_backup_completed size_bytes=%d", dest_path.stat().st_size)

    # Prune old backups
    prune_old_backups(backup_dir, keep_days=keep_days, now=now_dt)

    return dest_path


def verify_backup_integrity(backup_path: Path) -> bool:
    """Check that the backup file is a valid, readable SQLite database."""
    if not backup_path.exists() or backup_path.stat().st_size == 0:
        return False

    try:
        conn = sqlite3.connect(str(backup_path))
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check;")
        result = cursor.fetchone()
        conn.close()
        return bool(result and result[0] == "ok")
    except Exception as exc:
        log.error("backup_integrity_check_failed file=%s error=%r", backup_path, exc)
        return False


def prune_old_backups(
    backup_dir: Path,
    keep_days: int = 14,
    now: datetime | None = None,
) -> int:
    """Delete backups older than keep_days. Returns count of deleted files."""
    if not backup_dir.exists():
        return 0

    now_dt = now or now_utc()
    cutoff_time = now_dt - timedelta(days=keep_days)
    deleted_count = 0

    for file_path in backup_dir.glob("companion_backup_*.db"):
        try:
            mtime = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff_time:
                file_path.unlink()
                deleted_count += 1
                log.info("pruned_old_backup file=%s age_days=%.1f", file_path.name, (now_dt - mtime).total_seconds() / 86400)
        except Exception as exc:
            log.warning("failed_to_prune_backup file=%s error=%r", file_path, exc)

    return deleted_count
