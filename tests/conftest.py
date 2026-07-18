import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import db


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    """Point db.DB_PATH at a throwaway file for the duration of one test,
    so tests never touch the real vibegames.db."""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    return db_path


@pytest.fixture()
def games_dir(tmp_path):
    d = tmp_path / "games"
    d.mkdir()
    return d
