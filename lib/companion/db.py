"""One connection per block to a gated Kodi database, carried from Kofin.

The exception path rolls back to the last commit; a block that changed a row
or left a transaction open commits on the way out, so the connection never
leaves a block idle inside a transaction with the write lock held.
"""

import sqlite3
from typing import Any, Dict, Optional

from companion import schema
from companion.log import Logger

LOG = Logger(__name__)

KINDS = ("video", "music", "texture")

_path_overrides: Dict[str, str] = {}


def set_path_override(kind: str, path: str) -> None:
    """Point a database kind at an explicit file (tests only)."""
    _path_overrides[kind] = path


def reset_overrides() -> None:
    _path_overrides.clear()


def resolve_path(kind: str) -> str:
    if kind in _path_overrides:
        return _path_overrides[kind]
    if kind in KINDS:
        return schema.database_path(kind)
    return kind


class Database:
    """``with Database("video") as db: db.cursor.execute(...)``."""

    timeout = 120

    def __init__(self, kind: str = "video", commit_close: bool = True) -> None:
        self.kind = kind
        self.commit_close = commit_close
        self.path = ""
        self.conn: Optional[sqlite3.Connection] = None
        self.cursor: sqlite3.Cursor

    def __enter__(self) -> "Database":
        self.path = resolve_path(self.kind)
        self.conn = sqlite3.connect(self.path, timeout=self.timeout)
        if self.kind in KINDS:
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.cursor = self.conn.cursor()
        LOG.debug("--->[ database: %s ] %s", self.kind, id(self.conn))
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        conn = self.conn
        assert conn is not None
        try:
            if exc_type is not None:
                LOG.error("type: %s value: %s", exc_type, exc_val)
                conn.rollback()
            elif self.commit_close and (conn.total_changes or conn.in_transaction):
                LOG.debug("[%s] %s rows updated.", self.kind, conn.total_changes)
                conn.commit()
        finally:
            LOG.debug("---<[ database: %s ] %s", self.kind, id(conn))
            self.cursor.close()
            conn.close()
            self.conn = None
