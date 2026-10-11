"""WAL and the name indexes: one connection per served database, once."""

import json
import sqlite3

import pytest

from companion import reconciler, upkeep
from companion.ledger import Ledger
from tests.unit import kodifixtures

# The tests' own looks at a database go round the counter below, which
# replaces sqlite3.connect for everything while it is in place.
_connect = sqlite3.connect


@pytest.fixture
def rig(profile, kodi):
    kodifixtures.create_video_db(str(profile / "Database" / "MyVideos149.db"))
    kofin = profile / "addon_data" / "plugin.video.kofin"
    own = profile / "addon_data" / "service.kofin.companion"
    (kofin / "api-capabilities.json").write_text('{"backend": "api"}', encoding="utf-8")
    (kofin / "companion" / "requests.json").write_text(
        json.dumps(
            {
                "v": 2,
                "generation": 1,
                "namespace": "ns",
                "device": "dev",
                "kinds": [],
                "requests": [],
            }
        ),
        encoding="utf-8",
    )
    worker = reconciler.Reconciler(
        {},
        Ledger(str(own / "written.json")),
        str(kofin / "companion" / "requests.json"),
        str(kofin / "api-capabilities.json"),
        str(own / "status.json"),
        setting=lambda name: True,
        busy=lambda: kodi.scanning,
        version=lambda: "0.1.0",
    )
    return worker, profile, kodi


@pytest.fixture
def opens(monkeypatch):
    """Every connection upkeep opens, by file name."""
    opened = []

    def counting(path, timeout=5.0):
        if "/Database/" in str(path):
            opened.append(str(path).rsplit("/", 1)[-1])
        return _connect(path, timeout=timeout)

    monkeypatch.setattr(upkeep.sqlite3, "connect", counting)
    return opened


def _mode(path):
    conn = _connect(str(path))
    try:
        return conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()


def _plan(path, statement):
    conn = _connect(str(path))
    try:
        return [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + statement)]
    finally:
        conn.close()


WANTED = ["ix_kofin_actor_nocase", "ix_kofin_studio_nocase", "ix_kofin_tag_nocase"]


def test_a_served_database_is_tended_once_on_one_connection(rig, opens):
    worker, profile, kodi = rig
    path = profile / "Database" / "MyVideos149.db"
    assert _mode(path) == "delete"
    # Never during a scan.
    kodi.scanning = True
    report = worker.run("start")
    assert opens == [] and report["journal"] == {} and report["upkeep"] == {}
    kodi.scanning = False
    report = worker.run("timer")
    # The mode and the indexes on one open, not one each.
    assert opens == ["MyVideos149.db"]
    assert report["journal"] == {"video": "wal"}
    assert report["indexes"] == {"video": WANTED}
    assert report["upkeep"] == {"video": "settled"}
    assert _mode(path) == "wal"
    # Settled: not opened again, and still reported.
    report = worker.run("timer")
    assert opens == ["MyVideos149.db"]
    assert report["journal"] == {"video": "wal"}


def test_a_database_that_appears_or_is_upgraded_later_is_tended_then(rig, opens):
    """Settlement is per database and version. A music database Kodi had not
    created at the first run, or a file Kodi has since upgraded (an upgrade
    copies the file and drops every index), is not covered by a flag set
    when only the others were there."""
    worker, profile, kodi = rig
    worker.run("start")
    assert opens == ["MyVideos149.db"]
    # Kodi creates its music database after the companion's first run.
    staged = profile / "staged-music.db"
    kodifixtures.create_music_db(str(staged))
    staged.rename(profile / "Database" / "MyMusic84.db")
    report = worker.run("timer")
    assert opens == ["MyVideos149.db", "MyMusic84.db"]
    assert report["journal"] == {"music": "wal", "video": "wal"}
    assert report["indexes"] == {"video": WANTED}
    # A database the gate stops serving is forgotten, and tended again
    # when it is served once more.
    (profile / "Database" / "MyMusic84.db").rename(profile / "Database" / "aside.db")
    report = worker.run("timer")
    assert report["journal"] == {"video": "wal"}
    (profile / "Database" / "aside.db").rename(profile / "Database" / "MyMusic84.db")
    worker.run("timer")
    assert opens == ["MyVideos149.db", "MyMusic84.db", "MyMusic84.db"]


def test_a_locked_database_is_asked_again_and_reports_what_it_has(
    rig, opens, monkeypatch
):
    worker, profile, kodi = rig
    monkeypatch.setattr(upkeep, "TIMEOUT", 0.05)
    path = profile / "Database" / "MyVideos149.db"
    holder = _connect(str(path))
    holder.execute("BEGIN EXCLUSIVE")
    try:
        report = worker.run("start")
        assert report["upkeep"] == {"video": "busy"}
        assert report["indexes"] == {"video": []}
        assert _plan_blocked(path)
    finally:
        holder.rollback()
        holder.close()
    report = worker.run("timer")
    assert opens == ["MyVideos149.db", "MyVideos149.db"]
    assert report["upkeep"] == {"video": "settled"}
    assert report["journal"] == {"video": "wal"}


def _plan_blocked(path):
    # The holder's lock is real: nothing else can write the file either.
    conn = _connect(str(path), timeout=0.05)
    try:
        conn.execute("CREATE TABLE probe (n INTEGER)")
    except sqlite3.OperationalError:
        return True
    finally:
        conn.close()
    return False


def test_a_database_that_cannot_be_tended_is_said_once_and_not_reopened(
    rig, opens, monkeypatch
):
    """A lock is retried; anything else about the file is not, or every
    reconcile would reopen it and wait out the timeout for nothing."""
    worker, profile, kodi = rig

    class Broken:
        def execute(self, statement):
            raise sqlite3.DatabaseError("database disk image is malformed")

        def close(self):
            pass

    counting = upkeep.sqlite3.connect
    monkeypatch.setattr(
        upkeep.sqlite3,
        "connect",
        lambda path, timeout=5.0: counting(path, timeout) and Broken(),
    )
    report = worker.run("start")
    assert report["upkeep"] == {"video": "failed"}
    assert report["journal"] == {"video": "failed"}
    assert opens == ["MyVideos149.db"]
    worker.run("timer")
    worker.run("timer")
    assert opens == ["MyVideos149.db"]


def test_a_declined_wal_switch_is_said_once_and_keeps_its_indexes(
    rig, opens, monkeypatch
):
    """SQLite declines WAL without an error where the file cannot have a
    shared-memory file beside it: the pragma answers with the mode it kept.
    That is not a lock, and asking again at every reconcile would reopen
    the file and log the same line for ever. The indexes do not depend on
    the journal and are made all the same."""
    worker, profile, kodi = rig

    class Declining:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, statement):
            if statement == "PRAGMA journal_mode=WAL":
                statement = "PRAGMA journal_mode"
            return self._conn.execute(statement)

        def commit(self):
            self._conn.commit()

        def close(self):
            self._conn.close()

    counting = upkeep.sqlite3.connect
    monkeypatch.setattr(
        upkeep.sqlite3,
        "connect",
        lambda path, timeout=5.0: Declining(counting(path, timeout)),
    )
    report = worker.run("start")
    assert report["upkeep"] == {"video": "failed"}
    assert report["journal"] == {"video": "delete"}
    assert report["indexes"] == {"video": sorted(n for n, _ in upkeep.INDEXES["video"])}
    worker.run("timer")
    worker.run("timer")
    assert opens == ["MyVideos149.db"]
    assert _mode(profile / "Database" / "MyVideos149.db") == "delete"


def test_kodis_name_lookups_search_the_new_indexes(rig):
    """AddActor and AddToTable look a name up with LIKE. Each table has a
    unique index on the name, but a BINARY one, which LIKE cannot use."""
    worker, profile, kodi = rig
    path = profile / "Database" / "MyVideos149.db"
    lookups = {
        "actor": "select actor_id from actor where name like 'Somebody'",
        "tag": "select tag_id from tag where name like 'noir'",
        "studio": "select studio_id from studio where name like 'Gaumont'",
    }
    for lookup in lookups.values():
        assert any(step.startswith("SCAN") for step in _plan(path, lookup))
    worker.run("start")
    for table, lookup in lookups.items():
        (step,) = _plan(path, lookup)
        assert step.startswith("SEARCH") and "ix_kofin_%s_nocase" % table in step
    conn = _connect(str(path))
    try:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='actor'"
            )
        }
        assert {"ix_actor_1", "ix_kofin_actor_nocase"} <= names
    finally:
        conn.close()


def test_which_of_two_spellings_a_lookup_meets_first_is_the_row_filed_first(rig):
    """The lookup returns the same rows either way and Kodi takes the first
    with no ORDER BY. Rows that differ by case alone are the one place the
    index changes which that is: scanning its BINARY index Kodi met the
    spelling that sorts first (capitals before lower case), searching the
    NOCASE one it meets the row filed first. Pinned, so it is deliberate."""
    worker, profile, kodi = rig
    path = profile / "Database" / "MyVideos149.db"
    conn = _connect(str(path))
    ids = {}
    for name in ("jean reno", "Jean Reno", "JEAN RENO"):
        ids[name] = conn.execute(
            "INSERT INTO actor (name, art_urls) VALUES (?, '')", (name,)
        ).lastrowid
    conn.commit()
    lookup = "select actor_id from actor where name like 'Jean Reno'"
    before = [row[0] for row in conn.execute(lookup)]
    conn.close()
    assert sorted(before) == sorted(ids.values())
    assert before[0] == ids["JEAN RENO"]
    worker.run("start")
    conn = _connect(str(path))
    after = [row[0] for row in conn.execute(lookup)]
    conn.close()
    assert sorted(after) == sorted(before)
    assert after[0] == ids["jean reno"] == min(ids.values())
