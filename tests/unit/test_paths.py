from companion import paths


def test_version_urls_round_trip_and_ownership_is_by_namespace():
    plain = "plugin://plugin.video.kofin/native/ns1/lib/movies/a/?mode=play&id=a"
    version = plain + "&mediasourceid=s2"
    assert paths.source_of(version) == "s2" and paths.source_of(plain) == ""
    assert paths.without_source(version) == plain
    assert paths.is_version_url(version) and not paths.is_version_url(plain)
    assert paths.owned(plain, "ns1") and not paths.owned(plain, "ns2")
    assert not paths.owned("", "ns1") and not paths.owned(plain, "")
    assert not paths.owned("smb://share/a.mkv", "ns1")
    assert paths.without_source("smb://share/a.mkv") == "smb://share/a.mkv"
