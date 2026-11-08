import sqlite3

import pytest

from chess_coach.infrastructure.sqlite.constants import SCHEMA_VERSION
from chess_coach.infrastructure.sqlite.transactions import SQLiteUnitOfWork
from chess_coach.lichess.repository import GameRepository


def test_repository_uses_shared_unit_of_work_without_schema_version_change(tmp_path):
    path = tmp_path / "synthetic.sqlite3"
    repository = GameRepository(path)
    try:
        assert isinstance(repository._unit_of_work, SQLiteUnitOfWork)
        with repository._connection() as db:
            assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    finally:
        repository.shutdown()


def test_unit_of_work_commits_and_rolls_back_each_short_transaction(tmp_path):
    uow = SQLiteUnitOfWork(tmp_path / "uow.sqlite3")
    with uow.connection() as db:
        db.execute("CREATE TABLE sample(value INTEGER)")
        db.execute("INSERT INTO sample VALUES (1)")
    with pytest.raises(RuntimeError):
        with uow.connection() as db:
            db.execute("INSERT INTO sample VALUES (2)")
            raise RuntimeError("synthetic rollback")
    with uow.connection() as db:
        assert [row[0] for row in db.execute("SELECT value FROM sample ORDER BY value")] == [1]


def test_unit_of_work_keeps_foreign_key_enforcement_per_connection(tmp_path):
    uow = SQLiteUnitOfWork(tmp_path / "fk.sqlite3")
    with uow.connection() as db:
        db.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY)")
        db.execute("CREATE TABLE child(parent_id INTEGER REFERENCES parent(id))")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO child VALUES (99)")
