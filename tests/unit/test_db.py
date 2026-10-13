"""The connection block around a gated Kodi database."""

import sqlite3

import pytest

from companion import db


def test_a_connection_that_cannot_be_prepared_is_closed(tmp_path, monkeypatch):
    """__exit__ never runs for a block that did not begin; the pragma is the
    call that fails while Kodi holds the file."""
    closed = []

    class Conn:
        def execute(self, statement):
            raise sqlite3.OperationalError("database is locked")

        def cursor(self):
            raise AssertionError("not reached")

        def close(self):
            closed.append(True)

    monkeypatch.setattr(db.sqlite3, "connect", lambda path, timeout: Conn())
    db.set_path_override("video", str(tmp_path / "MyVideos149.db"))
    try:
        block = db.Database("video")
        with pytest.raises(sqlite3.OperationalError):
            with block:
                raise AssertionError("the block must not begin")
        assert closed == [True] and block.conn is None
    finally:
        db.reset_overrides()


def test_a_block_commits_and_closes(tmp_path):
    path = str(tmp_path / "MyVideos149.db")
    db.set_path_override("video", path)
    try:
        with db.Database("video") as block:
            block.cursor.execute("CREATE TABLE t (n INTEGER)")
            block.cursor.execute("INSERT INTO t VALUES (1)")
        assert block.conn is None
        check = sqlite3.connect(path)
        assert check.execute("SELECT n FROM t").fetchall() == [(1,)]
        check.close()
    finally:
        db.reset_overrides()


def test_a_refusal_leaving_a_block_is_not_logged_as_an_error(tmp_path, monkeypatch):
    """A handler's refusal is an answer. Each one left its database block as
    an exception and was logged as an error: two error lines a request, for
    thousands of requests a run."""
    from companion.handlers.base import Refused, Retry
    from tests.unit import kodifixtures

    path = str(tmp_path / "MyVideos149.db")
    kodifixtures.create_video_db(path)
    db.set_path_override("video", path)
    errors, quiet = [], []
    monkeypatch.setattr(db.LOG, "error", lambda *args: errors.append(args))
    monkeypatch.setattr(db.LOG, "debug", lambda *args: quiet.append(args))
    try:
        for answer in (Refused("row is gone"), Retry("busy")):
            with pytest.raises(type(answer)):
                with db.Database("video"):
                    raise answer
        assert errors == []
        assert sum(1 for args in quiet if args[0].startswith("type:")) == 2
        with pytest.raises(ValueError):
            with db.Database("video"):
                raise ValueError("a fault")
        assert len(errors) == 1
    finally:
        db.reset_overrides()
