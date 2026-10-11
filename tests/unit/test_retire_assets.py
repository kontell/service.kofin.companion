"""``retire_assets`` against a pristine MyVideos149: a grouped movie's
version files and extras kept to exactly the server's."""

import sqlite3

import pytest

from companion import db as companion_db
from companion.contract import Request, content_hash
from companion.handlers.base import Context, Refused
from companion.handlers.retire_assets import RetireAssets
from companion.ledger import Entry
from tests.unit import kodifixtures, videorows
from tests.unit.videorows import LIB, NAMESPACE, movie_dir, movie_url, query

GATE = {"video": {"version": 149, "ok": True, "reason": ""}}


@pytest.fixture
def video(tmp_path):
    path = str(tmp_path / "MyVideos149.db")
    kodifixtures.create_video_db(path)
    companion_db.set_path_override("video", path)
    yield path
    companion_db.reset_overrides()


def _grouped(video, item_id="a"):
    """A movie with two grouped version files and two extras, as the
    scanner and a grouping leave them."""
    conn = sqlite3.connect(video)
    try:
        owner = videorows.add_movie(conn, item_id, "Fixture a")
        assets = {}
        for source, type_id in (("s2", 40406), ("s3", 40401)):
            file_id = videorows.add_file(
                conn, owner["pathid"], movie_url(item_id, source)
            )
            conn.execute(
                "INSERT INTO videoversion (idFile, idMedia, media_type, itemType, idType) VALUES (?, ?, 'movie', 1, ?)",
                (file_id, owner["movieid"], type_id),
            )
            conn.execute(
                "INSERT INTO streamdetails (idFile, iStreamType, strVideoCodec) VALUES (?, 0, 'h264')",
                (file_id,),
            )
            conn.execute(
                "INSERT INTO art (media_id, media_type, type, url) VALUES (?, 'videoversion', 'poster', 'http://art/v.jpg')",
                (file_id,),
            )
            assets[source] = file_id
        extras_type = int(
            conn.execute(
                "INSERT INTO videoversiontype (name, owner, itemType) VALUES ('Extras', 1, 2)"
            ).lastrowid
        )
        extras_path = videorows.add_path(conn, movie_dir(item_id) + "extras/", "")
        for stem in ("Deleted Scene.mkv", "Making Of.mp4"):
            file_id = videorows.add_file(
                conn, extras_path, movie_dir(item_id) + "extras/" + stem
            )
            conn.execute(
                "INSERT INTO videoversion (idFile, idMedia, media_type, itemType, idType) VALUES (?, ?, 'movie', 2, ?)",
                (file_id, owner["movieid"], extras_type),
            )
            assets[stem] = file_id
        conn.commit()
    finally:
        conn.close()
    return owner, assets


def _request(owner, versions, extras, item_id="a"):
    body = {
        "id": "r1",
        "kind": "retire_assets",
        "library": LIB,
        "item": item_id,
        "owner": {"movieid": owner["movieid"]},
        "versions": [{"file": movie_url(item_id, s), "name": n} for s, n in versions],
        "extras": [{"file": movie_dir(item_id) + "extras/" + stem} for stem in extras],
    }
    fields = {k: v for k, v in body.items() if k not in ("id", "kind")}
    return Request("r1", "retire_assets", LIB, item_id, fields, content_hash(body))


def test_dropped_assets_leave_renamed_ones_are_retyped_and_the_rest_stay(video):
    owner, assets = _grouped(video)
    handler = RetireAssets()
    context = Context(NAMESPACE, "dev", GATE)
    # s3 and Making Of are gone from the server; s2 was renamed.
    request = _request(owner, [("s2", "Festival Cut")], ["Deleted Scene.mkv"])
    data = handler.apply(request, context)
    assert [r["fileid"] for r in data["removed"]] == [
        assets["s3"],
        assets["Making Of.mp4"],
    ]
    (retyped,) = data["retyped"]
    assert retyped["fileid"] == assets["s2"]
    assert query(
        video,
        "SELECT name, owner FROM videoversiontype WHERE id = ?",
        (retyped["type"],),
    ) == [("Festival Cut", 2)]
    assert query(
        video, "SELECT idType FROM videoversion WHERE idFile = ?", (assets["s2"],)
    ) == [(retyped["type"],)]
    # The dropped rows and what hangs off them are gone; the kept ones and the owner's own are not.
    for gone in (assets["s3"], assets["Making Of.mp4"]):
        assert query(video, "SELECT COUNT(*) FROM files WHERE idFile = ?", (gone,)) == [
            (0,)
        ]
        assert query(
            video, "SELECT COUNT(*) FROM videoversion WHERE idFile = ?", (gone,)
        ) == [(0,)]
        assert query(
            video, "SELECT COUNT(*) FROM streamdetails WHERE idFile = ?", (gone,)
        ) == [(0,)]
        assert query(
            video,
            "SELECT COUNT(*) FROM art WHERE media_id = ? AND media_type = 'videoversion'",
            (gone,),
        ) == [(0,)]
    assert query(
        video,
        "SELECT COUNT(*) FROM files WHERE idFile IN (?, ?, ?)",
        (owner["fileid"], assets["s2"], assets["Deleted Scene.mkv"]),
    ) == [(3,)]
    assert query(video, "SELECT COUNT(*) FROM movie") == [(1,)]
    entry = Entry("retire_assets", request.hash, data, "t")
    assert handler.confirm(request, entry, context)
    # The same request again changes nothing.
    again = handler.apply(request, context)
    assert again["removed"] == [] and again["retyped"] == []
    # A row that comes back, or a type that moved, breaks the confirmation.
    conn = sqlite3.connect(video)
    conn.execute(
        "UPDATE videoversion SET idType = 40401 WHERE idFile = ?", (assets["s2"],)
    )
    conn.commit()
    conn.close()
    assert not handler.confirm(request, entry, context)
    handler.apply(request, context)
    assert handler.confirm(request, entry, context)
    handler.withdraw(entry, context)


def test_an_empty_desired_set_removes_every_asset_row_but_not_the_users(video):
    owner, assets = _grouped(video)
    conn = sqlite3.connect(video)
    foreign = videorows.add_file(conn, owner["pathid"], "smb://nas/film-extended.mkv")
    conn.execute(
        "INSERT INTO videoversion (idFile, idMedia, media_type, itemType, idType) VALUES (?, ?, 'movie', 1, 40401)",
        (foreign, owner["movieid"]),
    )
    conn.commit()
    conn.close()
    data = RetireAssets().apply(
        _request(owner, [], []), Context(NAMESPACE, "dev", GATE)
    )
    assert len(data["removed"]) == 4
    assert query(
        video,
        "SELECT COUNT(*) FROM videoversion WHERE idMedia = ?",
        (owner["movieid"],),
    ) == [(2,)]
    assert query(video, "SELECT COUNT(*) FROM files WHERE idFile = ?", (foreign,)) == [
        (1,)
    ]
    # The Extras type row Kodi owns is not swept; only USER-owned VERSION ones are.
    assert query(
        video, "SELECT COUNT(*) FROM videoversiontype WHERE name = 'Extras'"
    ) == [(1,)]


def _set_default(video, owner, file_id):
    """Kodi's Set as Default (SetDefaultVideoVersion), its three statements."""
    conn = sqlite3.connect(video)
    try:
        (old,) = conn.execute(
            "SELECT idFile FROM movie WHERE idMovie = ?", (owner["movieid"],)
        ).fetchone()
        (base,) = conn.execute(
            "SELECT strPath FROM path JOIN files ON files.idPath = path.idPath"
            " WHERE idFile = ?",
            (file_id,),
        ).fetchone()
        conn.execute(
            "UPDATE movie SET idFile = ?, c22 = ? WHERE idMovie = ?",
            (file_id, base, owner["movieid"]),
        )
        conn.execute(
            "UPDATE art SET media_type = 'videoversion', media_id = ?"
            " WHERE media_id = ? AND media_type = 'movie'",
            (old, owner["movieid"]),
        )
        conn.execute(
            "UPDATE art SET media_type = 'movie', media_id = ?"
            " WHERE media_id = ? AND media_type = 'videoversion'",
            (owner["movieid"], file_id),
        )
        conn.commit()
    finally:
        conn.close()


def test_the_movies_own_file_survives_another_default(video):
    """Set as Default moves movie.idFile and nothing else, so the movie's
    own file becomes one more asset row. Kofin never lists it among the
    assets; judged by movie.idFile it was deleted as undesired, with no
    scan to bring it back."""
    owner, assets = _grouped(video)
    _set_default(video, owner, assets["s2"])
    handler = RetireAssets()
    context = Context(NAMESPACE, "dev", GATE)
    # s2 is kept and renamed while it is the default; s3 is dropped.
    request = _request(
        owner, [("s2", "Festival Cut")], ["Deleted Scene.mkv", "Making Of.mp4"]
    )
    data = handler.apply(request, context)
    assert [r["fileid"] for r in data["removed"]] == [assets["s3"]]
    assert [r["fileid"] for r in data["retyped"]] == [assets["s2"]]
    assert query(
        video, "SELECT COUNT(*) FROM files WHERE idFile = ?", (owner["fileid"],)
    ) == [(1,)]
    assert query(
        video,
        "SELECT idMedia, idType FROM videoversion WHERE idFile = ?",
        (owner["fileid"],),
    ) == [(owner["movieid"], 40400)]
    assert query(
        video, "SELECT idFile FROM movie WHERE idMovie = ?", (owner["movieid"],)
    ) == [(assets["s2"],)]
    entry = Entry("retire_assets", request.hash, data, "t")
    assert handler.confirm(request, entry, context)
    assert handler.apply(request, context)["removed"] == []


def test_a_dropped_default_gives_the_movie_its_own_file_back(video):
    owner, assets = _grouped(video)
    before_art = query(
        video,
        "SELECT type, url FROM art WHERE media_id = ? AND media_type = 'movie'",
        (owner["movieid"],),
    )
    _set_default(video, owner, assets["s3"])
    handler = RetireAssets()
    context = Context(NAMESPACE, "dev", GATE)
    request = _request(
        owner, [("s2", "Director's Cut")], ["Deleted Scene.mkv", "Making Of.mp4"]
    )
    data = handler.apply(request, context)
    assert [r["fileid"] for r in data["removed"]] == [assets["s3"]]
    # Back on its own file the way Set as Default would have put it there:
    # the file, the base path, and the art that had gone to the version.
    assert query(
        video, "SELECT idFile, c22 FROM movie WHERE idMovie = ?", (owner["movieid"],)
    ) == [(owner["fileid"], movie_dir("a"))]
    assert (
        query(
            video,
            "SELECT type, url FROM art WHERE media_id = ? AND media_type = 'movie'",
            (owner["movieid"],),
        )
        == before_art
    )
    assert query(
        video, "SELECT COUNT(*) FROM files WHERE idFile = ?", (assets["s3"],)
    ) == [(0,)]
    assert query(
        video, "SELECT COUNT(*) FROM art WHERE media_id = ?", (assets["s3"],)
    ) == [(0,)]
    assert handler.confirm(
        request, Entry("retire_assets", request.hash, data, "t"), context
    )


def test_a_default_that_is_not_kofins_is_left_alone_and_the_rest_reconciled(video):
    """A local file the user attached and made the default used to refuse
    the whole request, which froze retirement for that movie."""
    owner, assets = _grouped(video)
    conn = sqlite3.connect(video)
    local_path = videorows.add_path(conn, "smb://nas/films/")
    foreign = videorows.add_file(conn, local_path, "film-extended.mkv")
    conn.execute(
        "INSERT INTO videoversion (idFile, idMedia, media_type, itemType, idType)"
        " VALUES (?, ?, 'movie', 1, 40401)",
        (foreign, owner["movieid"]),
    )
    conn.commit()
    conn.close()
    _set_default(video, owner, foreign)
    handler = RetireAssets()
    context = Context(NAMESPACE, "dev", GATE)
    request = _request(owner, [("s2", "Director's Cut")], ["Deleted Scene.mkv"])
    data = handler.apply(request, context)
    assert sorted(r["fileid"] for r in data["removed"]) == sorted(
        [assets["s3"], assets["Making Of.mp4"]]
    )
    assert query(
        video, "SELECT idFile FROM movie WHERE idMovie = ?", (owner["movieid"],)
    ) == [(foreign,)]
    assert query(
        video,
        "SELECT COUNT(*) FROM files WHERE idFile IN (?, ?)",
        (foreign, owner["fileid"]),
    ) == [(2,)]
    entry = Entry("retire_assets", request.hash, data, "t")
    assert handler.confirm(request, entry, context)
    # Another namespace's reconcile does not confirm it, as apply would
    # not serve it.
    assert not handler.confirm(request, entry, Context("other", "dev", GATE))


def test_a_movie_without_its_own_file_is_not_served(video):
    owner, assets = _grouped(video)
    _set_default(video, owner, assets["s2"])
    conn = sqlite3.connect(video)
    conn.execute("DELETE FROM files WHERE idFile = ?", (owner["fileid"],))
    conn.commit()
    conn.close()
    handler = RetireAssets()
    context = Context(NAMESPACE, "dev", GATE)
    with pytest.raises(Refused, match="not owned: owner"):
        handler.apply(_request(owner, [], []), context)
    assert query(
        video, "SELECT COUNT(*) FROM files WHERE idFile = ?", (assets["s2"],)
    ) == [(1,)]


def test_refusals(video):
    owner, assets = _grouped(video)
    handler = RetireAssets()
    with pytest.raises(Refused, match="not owned: owner"):
        handler.apply(_request(owner, [], []), Context("other", "dev", GATE))
    with pytest.raises(Refused, match="owner row 777 is gone"):
        handler.apply(
            _request({"movieid": 777}, [], []), Context(NAMESPACE, "dev", GATE)
        )
    with pytest.raises(Refused, match="malformed request"):
        handler.apply(
            _request({"movieid": "x"}, [], []), Context(NAMESPACE, "dev", GATE)
        )
    request = _request(owner, [], [])
    request.fields["versions"] = [{"name": "no file"}]
    with pytest.raises(Refused, match="malformed version"):
        handler.apply(request, Context(NAMESPACE, "dev", GATE))
    assert query(video, "SELECT COUNT(*) FROM videoversion") == [(5,)]


def test_the_registry_serves_the_kind():
    from companion.handlers import registry

    handler = registry()["retire_assets"]
    assert handler.family == "assetLifecycle" and handler.needs == ("video",)
