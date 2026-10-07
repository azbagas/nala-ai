from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

from app.memory.backup import perform_backup, prune_old_backups, verify_backup_integrity
from app.memory.store import MemoryStore


@pytest.fixture
async def sample_db(tmp_path: Path) -> Path:
    db_file = tmp_path / "companion.db"
    store = MemoryStore(db_file)
    await store.init_db()
    await store.set_state("test_key", "test_val")
    await store.save_message("user", "Halo ini pesan penting")
    return db_file


def test_perform_backup_and_integrity(sample_db: Path, tmp_path: Path):
    backup_dir = tmp_path / "backups"
    now = datetime(2026, 10, 7, 3, 0, 0, tzinfo=timezone.utc)

    backup_file = perform_backup(sample_db, backup_dir, keep_days=14, now=now)
    assert backup_file.exists()
    assert backup_file.name == "companion_backup_20261007_030000.db"
    assert verify_backup_integrity(backup_file) is True

    # Verify backed up data can be read
    conn = sqlite3.connect(str(backup_file))
    cur = conn.cursor()
    cur.execute("SELECT value FROM state WHERE key = 'test_key'")
    assert cur.fetchone()[0] == "test_val"

    cur.execute("SELECT content FROM messages WHERE role = 'user'")
    assert cur.fetchone()[0] == "Halo ini pesan penting"
    conn.close()


def test_verify_backup_integrity_corrupted(tmp_path: Path):
    corrupted_file = tmp_path / "corrupted.db"
    corrupted_file.write_text("this is not a valid sqlite database", encoding="utf-8")
    assert verify_backup_integrity(corrupted_file) is False

    empty_file = tmp_path / "empty.db"
    empty_file.touch()
    assert verify_backup_integrity(empty_file) is False


def test_prune_old_backups(sample_db: Path, tmp_path: Path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    now = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)

    # File 1: 5 days old (should be kept)
    f_recent = backup_dir / "companion_backup_20261015_030000.db"
    f_recent.write_text("dummy", encoding="utf-8")
    t_recent = (now - timedelta(days=5)).timestamp()
    os.utime(f_recent, (t_recent, t_recent))

    # File 2: 20 days old (should be pruned with keep_days=14)
    f_old = backup_dir / "companion_backup_20260930_030000.db"
    f_old.write_text("dummy", encoding="utf-8")
    t_old = (now - timedelta(days=20)).timestamp()
    os.utime(f_old, (t_old, t_old))

    # File 3: not matching pattern (should not be touched)
    f_other = backup_dir / "other_file.txt"
    f_other.write_text("keep me", encoding="utf-8")
    os.utime(f_other, (t_old, t_old))

    deleted = prune_old_backups(backup_dir, keep_days=14, now=now)
    assert deleted == 1
    assert f_recent.exists()
    assert not f_old.exists()
    assert f_other.exists()
