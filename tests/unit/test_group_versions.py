"""``group_versions`` against a pristine MyVideos149: Kodi's own conversion,
statement for statement, with the trigger order the P1D stub learned."""

import sqlite3

import pytest

from companion import db as companion_db
from companion.contract import Request, content_hash
from companion.handlers.base import Context, Refused
from companion.handlers.group_versions import GroupVersions
from companion.ledger import Entry
from tests.unit import kodifixtures, videorows
from tests.unit.videorows import LIB, NAMESPACE, movie_url, query

GATE = {"video": {"version": 149, "ok": True, "reason": ""}}


@pytest.fixture
def video(tmp_path):
    path = str(tmp_path / "MyVideos149.db")
    kodifixtures.create_video_db(path)
    companion_db.set_path_override("video", path)
    yield path
    companion_db.reset_overrides()


def _rows(video, item_id="a"):
    """The owner (the movie's own file) and one duplicate (the version file
    the scanner imported as a movie), in one folder."""
    conn = sqlite3.connect(video)
    try:
        owner = videorows.add_movie(
            conn,
            item_id,
            "Fixture a",
            art={"poster": "http://art/a.jpg", "fanart": "http://art/a-f.jpg"},
        )
        duplicate = videorows.add_movie(
            conn,
            item_id,
            "Fixture a",
            source="s2",
            path_id=owner["pathid"],
            art={"poster": "http://art/s2.jpg"},
            actors=["Other"],
        )
        conn.commit()
    finally:
        conn.close()
    return owner, duplicate


def _request(owner, duplicate, name="Theatrical Cut", item_id="a", **extra):
    body = {
        "id": "r1",
        "kind": "group_versions",
        "library": LIB,
        "item": item_id,
        "owner": {"movieid": owner["movieid"], "file": movie_url(item_id)},
        "versions": [
            {
                "movieid": duplicate["movieid"],
                "file": movie_url(item_id, "s2"),
                "mediasourceid": "s2",
                "name": name,
            }
        ],
    }
    body.update(extra)
    fields = {k: v for k, v in body.items() if k not in ("id", "kind")}
    return Request("r1", "group_versions", LIB, item_id, fields, content_hash(body))


def test_the_duplicate_becomes_a_typed_version_of_the_owner(video):
    owner, duplicate = _rows(video)
    data = GroupVersions().apply(
        _request(owner, duplicate, name="Festival Cut"),
        Context(NAMESPACE, "dev", GATE),
    )
    assert data["owner"] == owner["movieid"]
    (grouped,) = data["grouped"]
    assert (
        grouped["movieid"] == duplicate["movieid"]
        and grouped["fileid"] == duplicate["fileid"]
    )
    # The movie row is gone; its file is a version of the owner, typed by name.
    assert query(video, "SELECT COUNT(*) FROM movie") == [(1,)]
    (row,) = query(
        video,
        "SELECT idMedia, itemType, idType FROM videoversion WHERE idFile = ?",
        (duplicate["fileid"],),
    )
    assert row[0] == owner["movieid"] and row[1] == 1
    (type_row,) = query(
        video,
        "SELECT name, owner, itemType FROM videoversiontype WHERE id = ?",
        (row[2],),
    )
    # A name Kodi does not seed is minted as a USER-owned row (a seeded one,
    # Theatrical Cut among them, is reused: the builtin test below).
    assert type_row == ("Festival Cut", 2, 1)
    assert grouped["type"] == row[2]
    # The owner's own version row is untouched, at the Standard Edition.
    assert query(
        video,
        "SELECT idMedia, idType FROM videoversion WHERE idFile = ?",
        (owner["fileid"],),
    ) == [(owner["movieid"], 40400)]
    # The duplicate's movie art became version art of its file; its links went with the row.
    assert query(
        video,
        "SELECT media_type, type, url FROM art WHERE media_id = ? AND media_type = 'videoversion'",
        (duplicate["fileid"],),
    ) == [("videoversion", "poster", "http://art/s2.jpg")]
    assert query(
        video,
        "SELECT COUNT(*) FROM art WHERE media_id = ? AND media_type = 'movie'",
        (duplicate["movieid"],),
    ) == [(0,)]
    assert query(
        video,
        "SELECT COUNT(*) FROM actor_link WHERE media_id = ? AND media_type = 'movie'",
        (duplicate["movieid"],),
    ) == [(0,)]
    assert query(
        video,
        "SELECT COUNT(*) FROM uniqueid WHERE media_id = ? AND media_type = 'movie'",
        (duplicate["movieid"],),
    ) == [(0,)]
    # Deviation from Kodi's ALL_ASSETS: the version file keeps its stream details.
    assert query(
        video,
        "SELECT COUNT(*) FROM streamdetails WHERE idFile = ?",
        (duplicate["fileid"],),
    ) == [(1,)]
    # The path hash is preserved, so the scanner will not re-import the file.
    assert query(
        video, "SELECT strHash FROM path WHERE idPath = ?", (owner["pathid"],)
    ) == [("hash",)]
    # The owner's art and file are its own still.
    assert query(
        video,
        "SELECT COUNT(*) FROM art WHERE media_id = ? AND media_type = 'movie'",
        (owner["movieid"],),
    ) == [(2,)]
    assert query(
        video, "SELECT idFile FROM movie WHERE idMovie = ?", (owner["movieid"],)
    ) == [(owner["fileid"],)]


def test_a_builtin_name_reuses_kodis_row_an_empty_one_is_the_standard_edition(video):
    owner, duplicate = _rows(video)
    GroupVersions().apply(
        _request(owner, duplicate, name="Director's Cut"),
        Context(NAMESPACE, "dev", GATE),
    )
    assert query(
        video,
        "SELECT idType FROM videoversion WHERE idFile = ?",
        (duplicate["fileid"],),
    ) == [(40407,)]
    assert query(video, "SELECT COUNT(*) FROM videoversiontype WHERE owner = 2") == [
        (0,)
    ]
    owner2, duplicate2 = _rows(video, item_id="b")
    GroupVersions().apply(
        _request(owner2, duplicate2, name="", item_id="b"),
        Context(NAMESPACE, "dev", GATE),
    )
    assert query(
        video,
        "SELECT idType FROM videoversion WHERE idFile = ?",
        (duplicate2["fileid"],),
    ) == [(40400,)]
    # Any other name is a user type: Kofin sends an empty name for a source
    # called after its own file, so nothing here compares a name with a path.
    owner3, duplicate3 = _rows(video, item_id="c")
    GroupVersions().apply(
        _request(owner3, duplicate3, name="Fixture c - 4K", item_id="c"),
        Context(NAMESPACE, "dev", GATE),
    )
    assert query(
        video,
        "SELECT t.name, t.owner FROM videoversion v JOIN videoversiontype t"
        " ON t.id = v.idType WHERE v.idFile = ?",
        (duplicate3["fileid"],),
    ) == [("Fixture c - 4K", 2)]


def test_the_owner_named_as_its_own_version_is_refused_untouched(video):
    """Kodi's ConvertVideoToVersion converts only when source and target
    differ. Without that guard the move is a no-op and the delete takes the
    owner, its version row and its stream details."""
    owner, duplicate = _rows(video)
    before = [
        query(video, "SELECT * FROM movie ORDER BY idMovie"),
        query(video, "SELECT * FROM videoversion ORDER BY idFile"),
        query(video, "SELECT * FROM streamdetails ORDER BY idFile"),
        query(video, "SELECT * FROM art ORDER BY art_id"),
    ]
    request = _request(owner, {"movieid": owner["movieid"]})
    request.fields["versions"][0]["file"] = movie_url("a")
    with pytest.raises(Refused, match="is the owner itself"):
        GroupVersions().apply(request, Context(NAMESPACE, "dev", GATE))
    # The same refusal with the owner listed after a real duplicate: the
    # writes made for the first entry are rolled back with it.
    both = _request(owner, duplicate)
    both.fields["versions"].append({"movieid": owner["movieid"], "name": ""})
    with pytest.raises(Refused, match="is the owner itself"):
        GroupVersions().apply(both, Context(NAMESPACE, "dev", GATE))
    assert before == [
        query(video, "SELECT * FROM movie ORDER BY idMovie"),
        query(video, "SELECT * FROM videoversion ORDER BY idFile"),
        query(video, "SELECT * FROM streamdetails ORDER BY idFile"),
        query(video, "SELECT * FROM art ORDER BY art_id"),
    ]


def test_a_second_row_by_the_movies_own_url_is_not_a_version(video):
    conn = sqlite3.connect(video)
    try:
        owner = videorows.add_movie(conn, "a", "Fixture a")
        twin = videorows.add_movie(conn, "a", "Fixture a", path_id=owner["pathid"])
        conn.commit()
    finally:
        conn.close()
    request = _request(owner, twin)
    request.fields["versions"][0]["file"] = movie_url("a")
    with pytest.raises(Refused, match="names no media source"):
        GroupVersions().apply(request, Context(NAMESPACE, "dev", GATE))
    assert query(video, "SELECT COUNT(*) FROM movie") == [(2,)]


def test_confirm_holds_until_a_row_comes_back(video):
    owner, duplicate = _rows(video)
    handler = GroupVersions()
    request = _request(owner, duplicate)
    context = Context(NAMESPACE, "dev", GATE)
    data = handler.apply(request, context)
    entry = Entry("group_versions", request.hash, data, "t")
    assert handler.confirm(request, entry, context)
    conn = sqlite3.connect(video)
    conn.execute(
        "UPDATE videoversion SET idMedia = 999 WHERE idFile = ?", (duplicate["fileid"],)
    )
    conn.commit()
    conn.close()
    assert not handler.confirm(request, entry, context)
    handler.withdraw(entry, context)  # nothing to undo, nothing raised
    # A failed confirm is a re-apply. The duplicate movie row is long gone,
    # so the file is found by its URL, put back under the owner, and still
    # named in what the ledger keeps: the next confirm is about that file.
    again = handler.apply(request, context)
    assert [g["fileid"] for g in again["grouped"]] == [duplicate["fileid"]]
    assert again["grouped"][0]["movieid"] == duplicate["movieid"]
    assert query(
        video,
        "SELECT idMedia, media_type FROM videoversion WHERE idFile = ?",
        (duplicate["fileid"],),
    ) == [(owner["movieid"], "movie")]
    assert handler.confirm(
        request, Entry("group_versions", request.hash, again, "t"), context
    )
    # With the file gone as well there is nothing left to group.
    conn = sqlite3.connect(video)
    conn.execute("DELETE FROM files WHERE idFile = ?", (duplicate["fileid"],))
    conn.commit()
    conn.close()
    assert handler.apply(request, context)["grouped"] == []


def test_applying_twice_is_harmless_and_a_gone_duplicate_is_skipped(video):
    owner, duplicate = _rows(video)
    handler = GroupVersions()
    context = Context(NAMESPACE, "dev", GATE)
    first = handler.apply(_request(owner, duplicate), context)
    data = handler.apply(_request(owner, duplicate), context)
    # The duplicate movie row is gone; its file is still the owner's
    # version, untouched, and still what the ledger watches.
    assert data == first
    assert query(video, "SELECT COUNT(*) FROM videoversion") == [(2,)]


def test_a_row_that_is_not_kofins_is_refused(video):
    owner, duplicate = _rows(video)
    handler = GroupVersions()
    with pytest.raises(Refused, match="not owned: owner"):
        handler.apply(
            _request(owner, duplicate), Context("other-namespace", "dev", GATE)
        )
    # The request named a different file for the duplicate than the row holds.
    request = _request(owner, duplicate)
    request.fields["versions"][0]["file"] = movie_url("a", "s9")
    with pytest.raises(Refused, match="not owned: version"):
        handler.apply(request, Context(NAMESPACE, "dev", GATE))
    assert query(video, "SELECT COUNT(*) FROM movie") == [(2,)]
    # A version file of another movie is not grouped under this owner.
    other_owner, other_duplicate = _rows(video, item_id="c")
    request = _request(owner, other_duplicate)
    request.fields["versions"][0]["file"] = movie_url("c", "s2")
    with pytest.raises(Refused, match="not a file of movie"):
        handler.apply(request, Context(NAMESPACE, "dev", GATE))
    with pytest.raises(Refused, match="owner row 777 is gone"):
        handler.apply(
            _request({"movieid": 777}, duplicate), Context(NAMESPACE, "dev", GATE)
        )
    with pytest.raises(Refused, match="malformed"):
        handler.apply(
            _request(owner, {"movieid": "x"}), Context(NAMESPACE, "dev", GATE)
        )


def test_the_registry_serves_the_kind():
    from companion.handlers import registry

    handlers = registry()
    assert handlers["group_versions"].family == "groupVersions"
    assert handlers["group_versions"].needs == ("video",)
