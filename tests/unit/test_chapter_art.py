"""Chapter art: the bench-verified key vectors carried from Kofin, the texture
writer against the Textures14 fixture, and the handler end to end."""

import json
import os
import sqlite3

import pytest

from companion import schema
from companion.contract import Request, content_hash
from companion.handlers.base import Context, Refused
from companion.handlers.chapter_art import ChapterArt
from companion.kodidb.texture import (
    TextureCache,
    cached_rel_path,
    chapter_art_key,
    crc32_mpeg2,
    kodi_urlencode,
)
from companion.ledger import Entry
from tests.unit import kodifixtures

# The dialog lookup Kodi runs (TextureDatabase::GetCachedTexture): the INNER
# JOIN on sizes.size=1 is why the writer must land both rows.
KODI_LOOKUP = (
    "SELECT id, cachedurl, lasthashcheck, imagehash, width, height "
    "FROM texture JOIN sizes ON (texture.id=sizes.idtexture AND sizes.size=1) "
    "WHERE url=?"
)

OMEGA_DYNPATH = (
    "https://jelly.konell.xyz/Videos/ec40da70c171ea60b1b376bd312af9ea/stream.mkv"
    "?static=true&mediaSourceId=ec40da70c171ea60b1b376bd312af9ea"
    "&deviceId=731a0c17b0fe4428bbeadcff0be26c4c"
    "&playSessionId=43cfe963439044098e45fa59f79566db"
)

PIERS_DYNPATH = (
    "http://192.168.1.167:8096/Videos/6971670cd3807b9a28efe29104479aab/stream.mkv"
    "?static=true&mediaSourceId=6971670cd3807b9a28efe29104479aab"
)

PIERS_KEY_1 = (
    "image://video@http%3a%2f%2f192.168.1.167%3a8096%2fVideos"
    "%2f6971670cd3807b9a28efe29104479aab%2fstream.mkv%3fstatic%3dtrue"
    "%26mediaSourceId%3d6971670cd3807b9a28efe29104479aab/?chapter=1"
)

DEVICE = "device-1"
DYNPATH = (
    "http://192.168.1.167:8096/Videos/m1/stream.mkv?static=true&mediaSourceId=m1"
    "&deviceId=" + DEVICE + "&playSessionId=ps1"
)
GATE = {"texture": {"version": 14, "ok": True, "reason": ""}}


# -- bench-verified vectors (Kofin, 2026-08-03) -------------------------------------


def test_omega_key_is_the_raw_chapter_url():
    key = chapter_art_key(OMEGA_DYNPATH, 1, wrapped=False)
    assert key == "chapter://%s/1" % OMEGA_DYNPATH
    assert cached_rel_path(key) == "b/b86d5cb1.jpg"


def test_piers_key_is_the_wrapped_image_url():
    assert chapter_art_key(PIERS_DYNPATH, 1, wrapped=True) == PIERS_KEY_1
    assert cached_rel_path(PIERS_KEY_1) == "4/4c26c20a.jpg"
    key2 = chapter_art_key(PIERS_DYNPATH, 2, wrapped=True)
    assert cached_rel_path(key2) == "4/4165e4d3.jpg"


def test_urlencode_matches_kodi():
    assert kodi_urlencode("Az09-._!()") == "Az09-._!()"
    assert kodi_urlencode("://?&=+ ") == "%3a%2f%2f%3f%26%3d%2b%20"
    assert kodi_urlencode("é") == "%c3%a9"
    assert kodi_urlencode("Videos") == "Videos"


def test_crc_is_mpeg2_of_the_lowercased_key():
    assert crc32_mpeg2(b"") == 0xFFFFFFFF
    assert cached_rel_path("ABC") == cached_rel_path("abc")


def test_the_piers_form_is_keyed_by_texture_schema():
    assert schema.CHAPTER_ART_WRAPPED == {14: True}
    assert schema.SUPPORTED["texture"] == {14}


# -- the texture writer against the fixture ------------------------------------------


@pytest.fixture
def texture_db(tmp_path):
    path = str(tmp_path / "Textures14.db")
    kodifixtures.create_texture_db(path)
    return path


def _query(path, sql, args=()):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def test_add_lands_the_row_pair_kodi_looks_up(texture_db):
    conn = sqlite3.connect(texture_db)
    TextureCache(conn.cursor()).add("chapter://x/1", "a/abc.jpg", 640, 360)
    conn.commit()
    conn.close()
    rows = _query(texture_db, KODI_LOOKUP, ("chapter://x/1",))
    assert len(rows) == 1
    _id, cachedurl, lasthashcheck, imagehash, width, height = rows[0]
    assert cachedurl == "a/abc.jpg"
    assert imagehash == "" and lasthashcheck == ""
    assert (width, height) == (640, 360)


def test_add_replaces_remove_cascades_and_like_is_scoped(texture_db):
    conn = sqlite3.connect(texture_db)
    cache = TextureCache(conn.cursor())
    cache.add("chapter://x/1", "a/old.jpg", 100, 100)
    cache.add("chapter://x/1", "a/new.jpg", 640, 360)
    cache.add("chapter://y/1", "b/y.jpg", 1, 1)
    assert cache.get("chapter://x/1") == "a/new.jpg" and cache.get("nope") is None
    assert cache.remove("chapter://x/1") == "a/new.jpg"
    assert cache.remove("chapter://x/1") is None
    cache.add("chapter://y/2", "b/y2.jpg", 1, 1)
    assert cache.remove_like("chapter://y/%", keep={"chapter://y/2"}) == [
        ("chapter://y/1", "b/y.jpg")
    ]
    assert cache.remove_like("chapter://y/%") == [("chapter://y/2", "b/y2.jpg")]
    conn.commit()
    conn.close()
    assert _query(texture_db, "SELECT COUNT(*) FROM texture")[0][0] == 0
    assert _query(texture_db, "SELECT COUNT(*) FROM sizes")[0][0] == 0


# -- the handler -----------------------------------------------------------------


@pytest.fixture
def rig(profile, tmp_path):
    kodifixtures.create_texture_db(str(profile / "Database" / "Textures14.db"))
    staged = tmp_path / "staged"
    staged.mkdir()
    files = []
    for n in (1, 2, 3):
        path = staged / ("%d.jpg" % n)
        path.write_bytes(b"\xff\xd8image-%d" % n)
        files.append(str(path))
    return profile, files


def _request(files, dynpath=DYNPATH, item="m1", chapters=None):
    body = {
        "id": "c1",
        "kind": "chapter_art",
        "library": "",
        "item": item,
        "dynpath": dynpath,
        "session": "ps1",
        "chapters": (
            chapters
            if chapters is not None
            else [
                {"n": n, "file": f, "width": 640, "height": 360}
                for n, f in enumerate(files, 1)
            ]
        ),
    }
    fields = {k: v for k, v in body.items() if k not in ("id", "kind")}
    return Request("c1", "chapter_art", "", item, fields, content_hash(body))


def test_the_tiles_are_seeded_confirmed_and_removed(rig):
    profile, files = rig
    handler = ChapterArt()
    context = Context("ns", DEVICE, GATE)
    request = _request(files)
    data = handler.apply(request, context)
    assert data["item"] == "m1" and len(data["keys"]) == 3
    db_path = str(profile / "Database" / "Textures14.db")
    for n, (key, rel) in enumerate(data["keys"], 1):
        assert key == chapter_art_key(DYNPATH, n, True)
        assert rel == cached_rel_path(key)
        rows = _query(db_path, KODI_LOOKUP, (key,))
        assert rows[0][1] == rel and rows[0][4:] == (640, 360)
        with open(profile / "Thumbnails" / rel, "rb") as handle:
            assert handle.read() == b"\xff\xd8image-%d" % n
    entry = Entry("chapter_art", request.hash, data, "t")
    assert handler.confirm(request, entry, context)
    os.remove(profile / "Thumbnails" / data["keys"][1][1])
    assert not handler.confirm(request, entry, context)
    handler.apply(request, context)  # again: the row pair replaced, the file back
    assert handler.confirm(request, entry, context)
    handler.withdraw(entry, context)
    assert _query(db_path, "SELECT COUNT(*) FROM texture")[0][0] == 0
    assert not any(
        (profile / "Thumbnails" / rel).exists() for _key, rel in data["keys"]
    )
    handler.withdraw(entry, context)  # nothing left, nothing raised


def test_refusals(rig):
    profile, files = rig
    handler = ChapterArt()
    with pytest.raises(Refused, match="not owned"):
        handler.apply(_request(files), Context("ns", "other-device", GATE))
    with pytest.raises(Refused, match="not owned"):
        handler.apply(_request(files), Context("ns", "", GATE))
    with pytest.raises(Refused, match="no chapters"):
        handler.apply(_request(files, chapters=[]), Context("ns", DEVICE, GATE))
    with pytest.raises(Refused, match="no chapter image could be read"):
        handler.apply(
            _request(files, chapters=[{"n": 1, "file": str(profile / "missing.jpg")}]),
            Context("ns", DEVICE, GATE),
        )
    with pytest.raises(Refused, match="malformed chapter"):
        handler.apply(
            _request(files, chapters=[{"file": "x"}]), Context("ns", DEVICE, GATE)
        )
    with pytest.raises(Refused, match="no chapter key form"):
        handler.apply(
            _request(files),
            Context(
                "ns", DEVICE, {"texture": {"version": 13, "ok": True, "reason": ""}}
            ),
        )
    db_path = str(profile / "Database" / "Textures14.db")
    assert _query(db_path, "SELECT COUNT(*) FROM texture")[0][0] == 0


def _thumbnails(profile):
    root = profile / "Thumbnails"
    return sorted(
        str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()
    )


def test_nothing_is_copied_for_a_request_that_is_refused(rig):
    """Every chapter is read before any file is copied: a malformed entry
    after a good one used to leave the good one's file in Thumbnails with
    no row, where the sweep, which finds files through rows, never sees it."""
    profile, files = rig
    handler = ChapterArt()
    context = Context("ns", DEVICE, GATE)
    for bad in ({"file": "x"}, "not-a-chapter", {"n": "two", "file": files[1]}):
        with pytest.raises(Refused, match="malformed chapter"):
            handler.apply(
                _request(files, chapters=[{"n": 1, "file": files[0]}, bad]), context
            )
    db_path = str(profile / "Database" / "Textures14.db")
    assert _query(db_path, "SELECT COUNT(*) FROM texture")[0][0] == 0
    assert _thumbnails(profile) == []


def test_a_call_that_does_not_commit_takes_its_copies_with_it(rig, monkeypatch):
    profile, files = rig
    handler = ChapterArt()
    context = Context("ns", DEVICE, GATE)
    db_path = str(profile / "Database" / "Textures14.db")
    # One tile is already seeded: its file is not this call's to remove.
    first = handler.apply(_request(files[:1]), context)
    kept = [rel for _key, rel in first["keys"]]
    assert _thumbnails(profile) == kept
    calls = []
    real_add = TextureCache.add

    def failing_add(self, url, cachedurl, width, height):
        calls.append(url)
        if len(calls) == 2:
            raise sqlite3.OperationalError("database is locked")
        real_add(self, url, cachedurl, width, height)

    monkeypatch.setattr(TextureCache, "add", failing_add)
    with pytest.raises(sqlite3.OperationalError):
        handler.apply(_request(files), context)
    assert _thumbnails(profile) == kept
    assert [u for (u,) in _query(db_path, "SELECT url FROM texture")] == [
        first["keys"][0][0]
    ]


def test_a_changed_request_drops_the_tiles_it_no_longer_names(rig, kodi):
    """Through the reconciler: the request comes back with fewer chapters,
    its earlier form is withdrawn before the new one is applied, and the
    tile that left is gone, row and file."""
    from companion import reconciler
    from companion.ledger import Ledger

    profile, files = rig
    kofin = profile / "addon_data" / "plugin.video.kofin"
    own = profile / "addon_data" / "service.kofin.companion"
    (kofin / "api-capabilities.json").write_text('{"backend": "api"}', encoding="utf-8")
    ledger = Ledger(str(own / "written.json"))
    worker = reconciler.Reconciler(
        {"chapter_art": ChapterArt()},
        ledger,
        str(kofin / "companion" / "requests.json"),
        str(kofin / "api-capabilities.json"),
        str(own / "status.json"),
        setting=lambda name: True,
        busy=lambda: False,
        version=lambda: "0.1.0",
    )

    def publish(count, generation):
        chapters = [
            {"n": n, "file": f, "width": 640, "height": 360}
            for n, f in enumerate(files[:count], 1)
        ]
        document = {
            "v": 2,
            "generation": generation,
            "namespace": "ns",
            "device": DEVICE,
            "kinds": ["chapter_art"],
            "requests": [
                {
                    "id": "c1",
                    "kind": "chapter_art",
                    "item": "m1",
                    "dynpath": DYNPATH,
                    "chapters": chapters,
                }
            ],
        }
        (kofin / "companion" / "requests.json").write_text(
            json.dumps(document), encoding="utf-8"
        )

    db_path = str(profile / "Database" / "Textures14.db")
    publish(3, 1)
    assert worker.run("ping")["requests"]["c1"]["state"] == "done"
    assert len(_thumbnails(profile)) == 3
    publish(2, 2)
    assert worker.run("ping")["requests"]["c1"]["state"] == "done"
    keys = [key for key, _rel in ledger.get("c1").data["keys"]]
    assert keys == [chapter_art_key(DYNPATH, n, True) for n in (1, 2)]
    assert sorted(u for (u,) in _query(db_path, "SELECT url FROM texture")) == sorted(
        keys
    )
    assert _thumbnails(profile) == sorted(
        rel for _key, rel in ledger.get("c1").data["keys"]
    )


def test_a_device_id_is_matched_whole(rig):
    """``deviceId=tv-1`` is a substring of ``deviceId=tv-10`` and a LIKE
    pattern reads ``%`` and ``_`` in an id as wildcards. Ownership and the
    sweep both compare the stream URL's deviceId value as a whole."""
    profile, files = rig
    handler = ChapterArt()
    db_path = str(profile / "Database" / "Textures14.db")
    longer = DYNPATH.replace("deviceId=" + DEVICE, "deviceId=" + DEVICE + "0")
    with pytest.raises(Refused, match="not owned"):
        handler.apply(_request(files, dynpath=longer), Context("ns", DEVICE, GATE))
    theirs = handler.apply(
        _request(files, dynpath=longer), Context("ns", DEVICE + "0", GATE)
    )
    # Nothing in the ledger names those rows, and they are not this device's.
    for device in (DEVICE, "device-_", "%"):
        assert handler.sweep(Context("ns", device, GATE), []) == 0
    assert _query(db_path, "SELECT COUNT(*) FROM texture")[0][0] == 3
    assert len(_thumbnails(profile)) == 3
    assert handler.sweep(Context("ns", DEVICE + "0", GATE), []) == len(theirs["keys"])
    assert _thumbnails(profile) == []


def test_a_file_stays_while_another_row_names_it(rig):
    """Kodi's cache file name is the CRC of the lower-cased key, so two keys
    that differ by case alone are two rows and one file."""
    profile, files = rig
    handler = ChapterArt()
    context = Context("ns", DEVICE, GATE)
    db_path = str(profile / "Database" / "Textures14.db")
    upper = DYNPATH.replace("http://", "HTTP://")
    assert upper != DYNPATH
    first = handler.apply(_request(files[:1]), context)
    second = handler.apply(_request(files[:1], dynpath=upper), context)
    (key_a, rel_a), (key_b, rel_b) = first["keys"][0], second["keys"][0]
    assert key_a != key_b and rel_a == rel_b
    assert _query(db_path, "SELECT COUNT(*) FROM texture")[0][0] == 2
    handler.withdraw(Entry("chapter_art", "h", first, "t"), context)
    assert _thumbnails(profile) == [rel_b]
    assert handler.confirm(
        _request(files[:1], dynpath=upper),
        Entry("chapter_art", "h", second, "t"),
        context,
    )
    # The sweep, with the second still in the ledger and the first's row
    # back as a leftover: the row goes, the shared file stays.
    conn = sqlite3.connect(db_path)
    TextureCache(conn.cursor()).add(key_a, rel_a, 1, 1)
    conn.commit()
    conn.close()
    assert handler.sweep(context, [Entry("chapter_art", "h", second, "t")]) == 1
    assert _thumbnails(profile) == [rel_b]
    handler.withdraw(Entry("chapter_art", "h", second, "t"), context)
    assert _thumbnails(profile) == []


def test_the_sweep_removes_only_this_devices_leftovers(rig):
    profile, files = rig
    handler = ChapterArt()
    context = Context("ns", DEVICE, GATE)
    data = handler.apply(_request(files), context)
    db_path = str(profile / "Database" / "Textures14.db")
    conn = sqlite3.connect(db_path)
    cache = TextureCache(conn.cursor())
    stale_key = chapter_art_key(DYNPATH.replace("ps1", "old"), 1, True)
    cache.add(stale_key, "s/stale.jpg", 1, 1)
    cache.add(
        "chapter://http://s/stream?deviceId=%s&playSessionId=older/3" % DEVICE,
        "s/older.jpg",
        1,
        1,
    )
    cache.add("image://video@smb%3a%2f%2fnas%2ffilm.mkv/?chapter=1", "f/kodi.jpg", 1, 1)
    conn.commit()
    conn.close()
    for rel in ("s/stale.jpg", "s/older.jpg", "f/kodi.jpg"):
        target = profile / "Thumbnails" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x")
    entry = Entry("chapter_art", "h", data, "t")
    assert handler.sweep(context, [entry]) == 2
    urls = sorted(u for (u,) in _query(db_path, "SELECT url FROM texture"))
    assert "image://video@smb%3a%2f%2fnas%2ffilm.mkv/?chapter=1" in urls
    assert stale_key not in urls and len(urls) == 4
    assert not (profile / "Thumbnails" / "s/stale.jpg").exists()
    assert (profile / "Thumbnails" / "f/kodi.jpg").exists()
    assert handler.sweep(Context("ns", "", GATE), []) == 0


def test_the_registry_serves_the_kind():
    from companion.handlers import registry

    handler = registry()["chapter_art"]
    assert handler.family == "chapterArt" and handler.needs == ("texture",)
