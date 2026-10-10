import os
import sys

import pytest

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "lib"))
)

from tests.unit.fakes import FakeAddon, FakeKodi, FakeWindow  # noqa: E402


@pytest.fixture
def profile(tmp_path, monkeypatch):
    """A Kodi profile directory: ``special://profile`` and ``special://database``
    resolve under it, and the schema discovery lists its Database folder."""
    from companion import db, schema

    base = tmp_path / "profile"
    (base / "Database").mkdir(parents=True)
    (base / "addon_data" / "plugin.video.kofin" / "companion").mkdir(parents=True)
    (base / "addon_data" / "service.kofin.companion").mkdir(parents=True)

    def translate(path):
        path = path.replace("special://database/", str(base / "Database") + "/")
        path = path.replace("special://profile/", str(base) + "/")
        return path

    def listdir(path):
        target = translate(path)
        if os.path.isdir(target):
            names = sorted(os.listdir(target))
            dirs = [n for n in names if os.path.isdir(os.path.join(target, n))]
            files = [n for n in names if n not in dirs]
            return dirs, files
        return [], []

    monkeypatch.setattr("xbmcvfs.translatePath", translate)
    monkeypatch.setattr("xbmcvfs.listdir", listdir)
    schema.reset_cache()
    db.reset_overrides()
    yield base
    schema.reset_cache()
    db.reset_overrides()


@pytest.fixture
def kodi(monkeypatch):
    fake = FakeKodi()
    monkeypatch.setattr("xbmc.executeJSONRPC", fake.execute)
    monkeypatch.setattr("xbmc.getCondVisibility", fake.cond_visibility)
    monkeypatch.setattr(
        "xbmcgui.Window", lambda window_id=0: FakeWindow(fake.properties)
    )
    monkeypatch.setattr(
        "xbmcaddon.Addon", lambda addon_id="": FakeAddon(fake, addon_id)
    )
    return fake
