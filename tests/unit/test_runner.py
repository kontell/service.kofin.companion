from companion import contract, kodi, runner


def test_the_monitor_wakes_on_the_right_messages():
    monitor = runner.Monitor()
    monitor.onNotification("plugin.video.other", "Other.Companion.Requests", "{}")
    assert not monitor.wake.is_set()
    monitor.onNotification(
        contract.KOFIN_ID, "Other.Companion.Requests", '{"v": 2, "generation": 7}'
    )
    assert monitor.wake.is_set() and monitor.trigger == "ping"
    assert monitor.last_ping == {"v": 2, "generation": 7}
    monitor.wake.clear()
    monitor.onNotification("xbmc", "VideoLibrary.OnScanFinished", "{}")
    assert monitor.wake.is_set() and monitor.trigger == "scan finished"
    monitor.wake.clear()
    monitor.onNotification("xbmc", "VideoLibrary.OnUpdate", "{}")
    assert not monitor.wake.is_set()
    monitor.onNotification(kodi.ADDON_ID, "Other.Companion.Wake", "{}")
    assert monitor.wake.is_set() and monitor.trigger == "settings button"
    monitor.wake.clear()
    monitor.onSettingsChanged()
    assert monitor.wake.is_set() and monitor.trigger == "settings changed"


def test_build_resolves_every_path_in_the_profile(profile, kodi):
    worker = runner.build(handlers={})
    assert worker.requests_path == str(
        profile / "addon_data" / "plugin.video.kofin" / "companion" / "requests.json"
    )
    assert worker.capabilities_path == str(
        profile / "addon_data" / "plugin.video.kofin" / "api-capabilities.json"
    )
    assert worker.status_path == str(
        profile / "addon_data" / "service.kofin.companion" / "status.json"
    )
    assert worker.ledger.path == str(
        profile / "addon_data" / "service.kofin.companion" / "written.json"
    )
