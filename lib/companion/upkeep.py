"""What the companion does to a database it serves, besides its requests:
the journal mode Kodi never sets, and the indexes Kodi's own name lookups
need. One connection per database does both, once.

**WAL.** Kodi opens its SQLite databases in whatever journal mode the file
was left in: it sets ``synchronous`` and the cache size and nothing else
(``SqliteDatabase::postconnect``). A database Kodi created is a rollback
journal, where every autocommit statement is a journal file written,
synced and removed around the write, and Kodi's library setters are bursts
of autocommit statements. Kofin's SQL build has always switched the
databases it writes, as this add-on does for one it opens for a request
(``db.Database``); a profile the API build serves stays as Kodi made it
until a request happens to reach each database, and a music-only library
never sends one. Measured on a Raspberry Pi 3, the music database on its SD
card, a first import of 1,353 songs through the API build:

    AudioLibrary.SetSongDetails    133 ms  ->  17 ms a call
    AudioLibrary.SetAlbumDetails    57 ms  ->  16 ms
    AudioLibrary.SetArtistDetails  148 ms  ->  14 ms

The mode is a property of the file: it survives a restart, and Kodi keeps
using it.

**Name indexes.** Kodi's video scanner finds each cast member, director and
writer of an item with ``select actor_id from actor where name like
'<name>'`` (``CVideoDatabase::AddActor``), and each tag and studio with the
same statement on its table (``AddToTable``). Each of those tables has a
unique index on ``name``, but it is a BINARY one and LIKE ignores case, so
SQLite cannot use it for the lookup and scans instead: the cost grows with
the library, a movie makes about twenty-three actor lookups and a dozen
tag lookups, and an import of a few thousand items is quadratic in its
cast. An index on ``name COLLATE NOCASE`` beside Kodi's serves the same
statement as a search. On the Pi, Kodi's statements as written, against a
library of 1,788 movies and 4,300 episodes:

    table    rows     as Kodi makes it   with the index
    actor    38,284   28.2 ms a lookup   0.15 ms
    tag       8,140    6.1 ms            0.18 ms
    studio    2,443    2.3 ms            0.21 ms

``genre`` and ``country`` hold a few dozen rows and are left alone, as are
the music database's tables.

The lookup returns the same *set* of rows, and Kodi takes the first of
them with no ORDER BY. Which is first changes only where two rows differ
by case alone, which Kodi's own lookup never creates (it finds the
existing spelling) but another writer can: scanning its BINARY index Kodi
met the spelling that sorts first, capitals before lower case; searching
the NOCASE one it meets the row filed first. ``test_upkeep`` pins that.

Nothing of Kodi's is changed or dropped. A schema upgrade drops every
index and re-creates Kodi's (``DropAnalytics``), which is one reason a
database is tended again when the gate finds another version of it.
"""

import sqlite3
from typing import Dict, List, NamedTuple, Tuple

from companion import db, schema
from companion.log import Logger

LOG = Logger(__name__)

WAL = "wal"
# Changing the mode needs the file to itself for a moment, and so does a
# CREATE INDEX. A reconcile is never worth waiting long for either: a
# database that is busy is asked again at the next one.
TIMEOUT = 5.0

SETTLED = "settled"
BUSY = "busy"
FAILED = "failed"


def _nocase(table: str, column: str) -> Tuple[str, str]:
    name = "ix_kofin_%s_nocase" % table
    return (
        name,
        "CREATE INDEX IF NOT EXISTS %s ON %s (%s COLLATE NOCASE)"
        % (name, table, column),
    )


# kind -> (index name, the statement that makes it).
INDEXES: Dict[str, Tuple[Tuple[str, str], ...]] = {
    "video": (
        _nocase("actor", "name"),
        _nocase("tag", "name"),
        _nocase("studio", "name"),
    ),
}


class Upkeep(NamedTuple):
    # SETTLED: in WAL with every index asked of it. BUSY: something had the
    # file, asked again. FAILED: it cannot be done to this file (an error
    # that is no lock, or a WAL switch SQLite declined); said once.
    state: str
    # The journal mode the file was last seen in, or empty.
    journal: str
    # Of the indexes asked of the database, the ones it has.
    indexes: List[str]


def tend(kind: str) -> Upkeep:
    """Switch one served database to WAL and give it its indexes, on one
    connection. What was learned before a failure is still reported: the
    mode read, the indexes found."""
    wanted = INDEXES.get(kind, ())
    journal = ""
    present: List[str] = []
    try:
        conn = sqlite3.connect(schema.database_path(kind), timeout=TIMEOUT)
    except (schema.SchemaError, sqlite3.Error) as error:
        LOG.warning("%s database not opened for its upkeep: %s", kind, error)
        return Upkeep(FAILED, journal, present)
    try:
        journal = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        have = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        present = sorted(name for name, _ in wanted if name in have)
        if journal != WAL:
            before = journal
            journal = str(conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower()
            LOG.info("%s database journal mode: %s -> %s", kind, before, journal)
        for name, statement in wanted:
            if name not in have:
                conn.execute(statement)
                conn.commit()
                present = sorted(present + [name])
                LOG.info("%s database: index %s created", kind, name)
    except sqlite3.Error as error:
        if db.locked(error):
            # Kodi or a scan has it. It keeps what it has and is asked again.
            LOG.info("%s database is busy; its upkeep waits: %s", kind, error)
            return Upkeep(BUSY, journal, present)
        LOG.warning("%s database upkeep failed and is not retried: %s", kind, error)
        return Upkeep(FAILED, journal, present)
    finally:
        conn.close()
    if journal != WAL:
        # Declined, not locked: a lock raises and is handled above. SQLite
        # answers the pragma with the mode it kept where the file cannot
        # have a shared-memory file beside it (some network and FUSE
        # mounts), and asking again at every reconcile changes nothing.
        LOG.warning(
            "%s database stays in %s journal mode: SQLite declined WAL", kind, journal
        )
        return Upkeep(FAILED, journal, present)
    done = len(present) == len(wanted)
    return Upkeep(SETTLED if done else BUSY, journal, present)
