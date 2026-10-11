"""The indexes Kodi's own name lookups need and its schema does not have.

Kodi's video scanner finds each cast member, director and writer of an item
with ``select actor_id from actor where name like '<name>'``
(``CVideoDatabase::AddActor``), and each tag, studio, genre and country with
the same statement on its table (``AddToTable``). LIKE ignores case; the
unique index on ``actor.name`` does not, and the other tables have no index
on the name at all. So SQLite answers every lookup with a scan of the whole
table: the cost grows with the library, a movie makes about twenty-three
actor lookups and a dozen tag lookups, and an import of a few thousand items
is quadratic in its cast.

Measured on a Raspberry Pi 3, Kodi's statements as written, against a
library of 1,788 movies and 4,300 episodes:

    table    rows     as Kodi makes it   with the index below
    actor    38,284   28.2 ms a lookup   0.15 ms   (SEARCH ... name>? AND name<?)
    tag       8,140    6.1 ms            0.18 ms
    studio    2,443    2.3 ms            0.21 ms

and the lookups return the same rows. In a trace of the scanner half way
through the movies, the actor and tag scans were 215 ms of the 600 ms each
movie took. ``genre`` and ``country`` hold a few dozen rows and are left
alone, as are the music database's tables, whose lookups an existing index
already serves or whose rows number in the hundreds.

Each is one more index beside Kodi's own, on Kodi's table; nothing of
Kodi's is changed or dropped. A schema upgrade drops every index and
re-creates Kodi's (``DropAnalytics``), so they are asked for again at each
start, for a version the gate serves.
"""

import sqlite3
from typing import Any, Dict, List, Tuple

from companion import schema
from companion.log import Logger

LOG = Logger(__name__)

TIMEOUT = 5.0


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


def ensure(gate: Dict[str, Dict[str, Any]]) -> Dict[str, List[str]]:
    """Make the indexes of each served database that are not there yet; per
    kind, the ones present afterwards. A database that is locked keeps what
    it has and is asked again."""
    present: Dict[str, List[str]] = {}
    for kind, wanted in INDEXES.items():
        if not (gate.get(kind) or {}).get("ok"):
            continue
        try:
            conn = sqlite3.connect(schema.database_path(kind), timeout=TIMEOUT)
        except (schema.SchemaError, sqlite3.Error) as error:
            LOG.warning("%s database not opened for its indexes: %s", kind, error)
            continue
        try:
            have = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                ).fetchall()
            }
            for name, statement in wanted:
                if name not in have:
                    conn.execute(statement)
                    conn.commit()
                    have.add(name)
                    LOG.info("%s database: index %s created", kind, name)
            present[kind] = sorted(name for name, _ in wanted if name in have)
        except sqlite3.Error as error:
            LOG.info("%s database indexes not made: %s", kind, error)
            present[kind] = []
        finally:
            conn.close()
    return present


def settled(present: Dict[str, List[str]], gate: Dict[str, Dict[str, Any]]) -> bool:
    """Whether every served database has every index asked of it."""
    return all(
        len(present.get(kind, [])) == len(wanted)
        for kind, wanted in INDEXES.items()
        if (gate.get(kind) or {}).get("ok")
    )
