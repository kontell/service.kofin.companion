"""Kodi's databases in WAL mode.

Kodi opens its SQLite databases in whatever journal mode the file was left
in: it sets ``synchronous`` and the cache size and nothing else
(``SqliteDatabase::postconnect``). A database Kodi created is a rollback
journal, where every autocommit statement is a journal file written, synced
and removed around the write, and Kodi's library setters are bursts of
autocommit statements, four to a song's play count.

Kofin's SQL build has always switched the databases it writes, as this
add-on does for one it opens (``db.Database``). The API build may not open
them at all, so on a profile it serves they stay as Kodi made them until a
request happens to reach each one; a music-only library never sends one.

Measured on a Raspberry Pi 3, the music database on its SD card, first
import of 1,353 songs through the API build:

    AudioLibrary.SetSongDetails    133 ms  ->  17 ms a call
    AudioLibrary.SetAlbumDetails    54 ms  ->  15 ms
    AudioLibrary.SetArtistDetails  148 ms  ->  14 ms
    the scan itself               27.2 s  ->  21.5 s

So the three gated databases are switched once, at the first reconcile that
finds the library idle. The mode is a property of the file: it survives a
restart, and Kodi keeps using it.
"""

import sqlite3
from typing import Any, Dict

from companion import schema
from companion.log import Logger

LOG = Logger(__name__)

WAL = "wal"
# Changing the mode needs the file to itself for a moment. A reconcile is
# never worth waiting long for it: the next one asks again.
TIMEOUT = 5.0


def ensure(gate: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    """Ask each served database for WAL; per kind, the mode it is in now.

    A kind the gate does not serve is left alone and left out. A database
    another connection has locked keeps its mode and reports it, so the
    caller knows to ask again.
    """
    modes: Dict[str, str] = {}
    for kind in schema.PREFIXES:
        if not (gate.get(kind) or {}).get("ok"):
            continue
        try:
            path = schema.database_path(kind)
            conn = sqlite3.connect(path, timeout=TIMEOUT)
        except (schema.SchemaError, sqlite3.Error) as error:
            LOG.warning("%s database not opened for its journal mode: %s", kind, error)
            continue
        try:
            before = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
            after = before
            if before != WAL:
                after = str(
                    conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                ).lower()
                LOG.info("%s database journal mode: %s -> %s", kind, before, after)
            modes[kind] = after
        except sqlite3.Error as error:
            # Locked: Kodi or a scan has it. It keeps the mode it had.
            LOG.info("%s database journal mode not changed: %s", kind, error)
            modes[kind] = "busy"
        finally:
            conn.close()
    return modes


def settled(modes: Dict[str, str], gate: Dict[str, Dict[str, Any]]) -> bool:
    """Whether every served database is in WAL mode."""
    served = [kind for kind in schema.PREFIXES if (gate.get(kind) or {}).get("ok")]
    return all(modes.get(kind) == WAL for kind in served)
