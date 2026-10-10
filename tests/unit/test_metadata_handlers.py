"""The C4 handlers against a pristine MyVideos149."""

import sqlite3

import pytest

from companion import db as companion_db
from companion.contract import Request, content_hash
from companion.handlers.base import Context, Refused
from companion.handlers.metadata import EpisodeTags, RemoveSet, SeasonPlot, StreamFlags
from companion.ledger import Entry
from tests.unit import kodifixtures, videorows
from tests.unit.videorows import LIB, NAMESPACE, query

GATE = {"video": {"version": 149, "ok": True, "reason": ""}}
CONTEXT = Context(NAMESPACE, "dev", GATE)
SHOW_DIR = "plugin://plugin.video.kofin/native/%s/%s/tvshows/show1/" % (NAMESPACE, LIB)


@pytest.fixture
def video(tmp_path):
    path = str(tmp_path / "MyVideos149.db")
    kodifixtures.create_video_db(path)
    companion_db.set_path_override("video", path)
    yield path
    companion_db.reset_overrides()


def _show(video, path=SHOW_DIR):
    """A show with one season and one episode, filed under its own folder."""
    conn = sqlite3.connect(video)
    try:
        path_id = videorows.add_path(conn, path, "tvshows")
        show_id = int(
            conn.execute("INSERT INTO tvshow (c00) VALUES ('Show')").lastrowid
        )
        conn.execute(
            "INSERT INTO tvshowlinkpath (idShow, idPath) VALUES (?, ?)",
            (show_id, path_id),
        )
        season_id = int(
            conn.execute(
                "INSERT INTO seasons (idShow, season, name, plot) VALUES (?, 1, '', 'Old plot')",
                (show_id,),
            ).lastrowid
        )
        file_id = videorows.add_file(
            conn, path_id, path + "?mode=play&id=ep1&parent=show1"
        )
        episode_id = int(
            conn.execute(
                "INSERT INTO episode (idFile, c00, idShow, c12, c13, idSeason) VALUES (?, 'Pilot', ?, 1, 1, ?)",
                (file_id, show_id, season_id),
            ).lastrowid
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "show": show_id,
        "season": season_id,
        "episode": episode_id,
        "file": file_id,
    }


def _request(kind, item="x", **fields):
    body = dict(
        {"id": kind + ":" + item, "kind": kind, "library": LIB, "item": item}, **fields
    )
    stripped = {k: v for k, v in body.items() if k not in ("id", "kind")}
    return Request(body["id"], kind, LIB, item, stripped, content_hash(body))


# -- episode_tags -----------------------------------------------------------------


def test_an_episode_is_tagged_confirmed_and_untagged(video):
    rows = _show(video)
    handler = EpisodeTags()
    request = _request(
        "episode_tags", owner={"episodeid": rows["episode"]}, tags=["Favorite episodes"]
    )
    data = handler.apply(request, CONTEXT)
    ((tag_id, name),) = data["links"]
    assert name == "Favorite episodes"
    assert query(video, "SELECT name FROM tag WHERE tag_id = ?", (tag_id,)) == [
        ("Favorite episodes",)
    ]
    assert query(
        video,
        "SELECT media_type FROM tag_link WHERE tag_id = ? AND media_id = ?",
        (tag_id, rows["episode"]),
    ) == [("episode",)]
    entry = Entry("episode_tags", request.hash, data, "t")
    assert handler.confirm(request, entry, CONTEXT)
    # Applying again reuses the tag row.
    assert handler.apply(request, CONTEXT)["links"] == [[tag_id, name]]
    assert query(video, "SELECT COUNT(*) FROM tag") == [(1,)]
    handler.withdraw(entry, CONTEXT)
    assert not handler.confirm(request, entry, CONTEXT)
    # Kodi's delete_tag trigger dropped the orphan tag row with the link.
    assert query(video, "SELECT COUNT(*) FROM tag") == [(0,)]
    assert query(video, "SELECT COUNT(*) FROM tag_link") == [(0,)]


def test_episode_refusals(video):
    rows = _show(video)
    handler = EpisodeTags()
    with pytest.raises(Refused, match="not owned"):
        handler.apply(
            _request("episode_tags", owner={"episodeid": rows["episode"]}, tags=["x"]),
            Context("other", "dev", GATE),
        )
    with pytest.raises(Refused, match="is gone"):
        handler.apply(
            _request("episode_tags", owner={"episodeid": 999}, tags=["x"]), CONTEXT
        )
    with pytest.raises(Refused, match="no tags"):
        handler.apply(
            _request("episode_tags", owner={"episodeid": rows["episode"]}, tags=[" "]),
            CONTEXT,
        )
    with pytest.raises(Refused, match="malformed"):
        handler.apply(_request("episode_tags", owner={}, tags=["x"]), CONTEXT)


# -- season_plot ------------------------------------------------------------------


def test_a_seasons_plot_is_updated_through_its_shows_path(video):
    rows = _show(video)
    handler = SeasonPlot()
    request = _request(
        "season_plot", owner={"seasonid": rows["season"]}, plot="New plot"
    )
    data = handler.apply(request, CONTEXT)
    assert query(
        video, "SELECT plot FROM seasons WHERE idSeason = ?", (rows["season"],)
    ) == [("New plot",)]
    entry = Entry("season_plot", request.hash, data, "t")
    assert handler.confirm(request, entry, CONTEXT)
    conn = sqlite3.connect(video)
    conn.execute(
        "UPDATE seasons SET plot = 'Edited' WHERE idSeason = ?", (rows["season"],)
    )
    conn.commit()
    conn.close()
    assert not handler.confirm(request, entry, CONTEXT)
    handler.withdraw(entry, CONTEXT)
    with pytest.raises(Refused, match="not owned"):
        handler.apply(request, Context("other", "dev", GATE))
    with pytest.raises(Refused, match="is gone"):
        handler.apply(
            _request("season_plot", owner={"seasonid": 999}, plot="x"), CONTEXT
        )


# -- stream_flags -----------------------------------------------------------------


def test_stream_flags_land_on_the_rows_in_filing_order(video):
    conn = sqlite3.connect(video)
    movie = videorows.add_movie(conn, "a", "Fixture a", streams=False)
    for stream_type, codec in ((0, "hevc"), (1, "aac"), (1, "dts"), (2, ""), (2, "")):
        conn.execute(
            "INSERT INTO streamdetails (idFile, iStreamType, strAudioCodec, iFlags) VALUES (?, ?, ?, 0)",
            (movie["fileid"], stream_type, codec),
        )
    conn.commit()
    conn.close()
    handler = StreamFlags()
    request = _request(
        "stream_flags",
        owner={"movieid": movie["movieid"]},
        media="movie",
        flags={"audio": [0, 1], "subtitle": [64, 129]},
    )
    data = handler.apply(request, CONTEXT)
    assert data == {"fileid": movie["fileid"]}
    assert query(
        video,
        "SELECT iStreamType, iFlags FROM streamdetails WHERE idFile = ? ORDER BY rowid",
        (movie["fileid"],),
    ) == [(0, 0), (1, 0), (1, 1), (2, 64), (2, 129)]
    entry = Entry("stream_flags", request.hash, data, "t")
    assert handler.confirm(request, entry, CONTEXT)
    # Fewer rows than flags (a scanner that filed less) is written as far as it goes.
    more = _request(
        "stream_flags",
        owner={"movieid": movie["movieid"]},
        media="movie",
        flags={"audio": [1, 1, 1], "subtitle": []},
    )
    handler.apply(more, CONTEXT)
    assert query(
        video,
        "SELECT iFlags FROM streamdetails WHERE idFile = ? AND iStreamType = 1 ORDER BY rowid",
        (movie["fileid"],),
    ) == [(1,), (1,)]
    assert handler.confirm(more, Entry("stream_flags", more.hash, data, "t"), CONTEXT)
    with pytest.raises(Refused, match="unknown media"):
        handler.apply(
            _request("stream_flags", owner={"movieid": 1}, media="song", flags={}),
            CONTEXT,
        )
    with pytest.raises(Refused, match="not owned"):
        handler.apply(request, Context("other", "dev", GATE))
    with pytest.raises(Refused, match="malformed flags"):
        handler.apply(
            _request(
                "stream_flags",
                owner={"movieid": movie["movieid"]},
                media="movie",
                flags={"audio": ["x"]},
            ),
            CONTEXT,
        )


# -- remove_set -------------------------------------------------------------------


def test_an_emptied_set_is_removed_and_a_linked_one_kept(video):
    conn = sqlite3.connect(video)
    empty = int(conn.execute("INSERT INTO sets (strSet) VALUES ('Gone')").lastrowid)
    linked = int(conn.execute("INSERT INTO sets (strSet) VALUES ('Kept')").lastrowid)
    movie = videorows.add_movie(conn, "a", "Fixture a")
    conn.execute(
        "UPDATE movie SET idSet = ? WHERE idMovie = ?", (linked, movie["movieid"])
    )
    conn.commit()
    conn.close()
    handler = RemoveSet()
    request = _request("remove_set", item="k1", title="Gone")
    data = handler.apply(request, CONTEXT)
    assert data == {"title": "Gone", "removed": 1}
    assert query(video, "SELECT strSet FROM sets ORDER BY idSet") == [("Kept",)]
    assert (
        handler.apply(_request("remove_set", item="k2", title="Kept"), CONTEXT)[
            "removed"
        ]
        == 0
    )
    assert query(video, "SELECT COUNT(*) FROM sets WHERE idSet = ?", (linked,)) == [
        (1,)
    ]
    entry = Entry("remove_set", request.hash, data, "t")
    assert handler.confirm(request, entry, CONTEXT)
    assert empty not in [r[0] for r in query(video, "SELECT idSet FROM sets")]
    with pytest.raises(Refused, match="no title"):
        handler.apply(_request("remove_set", item="k3", title=""), CONTEXT)


def test_the_registry_serves_the_four_kinds():
    from companion.handlers import registry

    handlers = registry()
    for kind in ("episode_tags", "season_plot", "stream_flags", "remove_set"):
        assert handlers[kind].family == "videoMetadata" and handlers[kind].needs == (
            "video",
        )
