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
