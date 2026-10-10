import pytest

from companion import schema
from tests.unit import kodifixtures


def test_discover_picks_the_newest_file_and_ignores_journals(profile):
    for name in (
        "MyVideos146.db",
        "MyVideos149.db",
        "MyVideos149.db-wal",
        "Textures14.db",
    ):
        (profile / "Database" / name).write_bytes(b"")
    assert schema.discover("video") == ("MyVideos149.db", 149)
    assert schema.discover("texture") == ("Textures14.db", 14)
    with pytest.raises(schema.DatabaseMissing):
        schema.discover("music")


def test_the_gate_serves_the_piers_floor_only(profile):
    for name in ("MyVideos149.db", "MyMusic84.db", "Textures14.db"):
        (profile / "Database" / name).write_bytes(b"")
    assert schema.check("video") == 149
    assert schema.check("music") == 84
    assert schema.check("texture") == 14
    report = schema.gate_report()
    assert all(entry["ok"] for entry in report.values())
    assert schema.gate_refusal(report, ("video", "texture")) is None


def test_an_older_or_newer_version_is_named_in_the_refusal(profile):
    for name in ("MyVideos148.db", "MyMusic84.db"):
        (profile / "Database" / name).write_bytes(b"")
    with pytest.raises(schema.SchemaUnsupported) as caught:
        schema.check("video")
    assert caught.value.version == 148
    report = schema.gate_report()
    assert report["video"] == {
        "version": 148,
        "ok": False,
        "reason": "unsupported",
        "supported": [149],
    }
    assert report["texture"] == {"version": None, "ok": False, "reason": "missing"}
    assert (
        schema.gate_refusal(report, ("video",)) == "gate: MyVideos v148 is not served"
    )
    assert (
        schema.gate_refusal(report, ("music", "texture"))
        == "gate: no Textures database"
    )


def test_database_path_resolves_the_gated_file(profile):
    path = str(profile / "Database" / "MyVideos149.db")
    kodifixtures.create_video_db(path)
    assert schema.database_path("video") == path


def test_every_supported_version_has_a_fixture_and_its_keyed_constants():
    for version in schema.SUPPORTED["video"]:
        assert version in schema.EXTRA_ITEM_TYPE
        assert version in schema.SEASON_HAS_PLOT
    for version in schema.SUPPORTED["texture"]:
        assert version in schema.CHAPTER_ART_WRAPPED
    import os

    for kind, prefix in (
        ("video", "myvideos"),
        ("music", "mymusic"),
        ("texture", "textures"),
    ):
        for version in schema.SUPPORTED[kind]:
            assert os.path.exists(
                os.path.join(kodifixtures.FIXTURES, "%s%d.sql" % (prefix, version))
            )
