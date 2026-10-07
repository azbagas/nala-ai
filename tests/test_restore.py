from __future__ import annotations

import sqlite3
from pathlib import Path
import pytest

from deploy.restore import list_backups, restore_backup


@pytest.fixture
def dummy_backup(tmp_path: Path) -> Path:
    backup_file = tmp_path / "companion_backup_20261007_030000.db"
    conn = sqlite3.connect(str(backup_file))
    cur = conn.cursor()
    cur.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, msg TEXT);")
    cur.execute("INSERT INTO test (msg) VALUES ('restored data');")
    conn.commit()
    conn.close()
    return backup_file


def test_list_backups(tmp_path: Path, dummy_backup: Path):
    backups = list_backups(tmp_path)
    assert len(backups) == 1
    assert backups[0].name == "companion_backup_20261007_030000.db"


def test_restore_backup_success(tmp_path: Path, dummy_backup: Path):
    live_db = tmp_path / "live.db"
    # Create an initial live db
    conn = sqlite3.connect(str(live_db))
    cur = conn.cursor()
    cur.execute("CREATE TABLE old (x INT);")
    conn.commit()
    conn.close()

    success = restore_backup(dummy_backup, live_db)
    assert success is True

    # Verify live_db now has the restored table & data
    conn = sqlite3.connect(str(live_db))
    cur = conn.cursor()
    cur.execute("SELECT msg FROM test")
    assert cur.fetchone()[0] == "restored data"
    conn.close()

    # Verify pre-restore safety copy was created
    safety_files = list(tmp_path.glob("live_pre_restore_*.db"))
    assert len(safety_files) == 1


def test_restore_backup_corrupted_fails(tmp_path: Path):
    corrupt_backup = tmp_path / "companion_backup_corrupt.db"
    corrupt_backup.write_text("not a db", encoding="utf-8")

    live_db = tmp_path / "live.db"
    success = restore_backup(corrupt_backup, live_db)
    assert success is False
