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


def test_a_ping_during_a_reconcile_is_not_lost(monkeypatch):
    """The wake event is cleared before the run. Cleared after it, a ping
    for a document the run had already read waited out the whole poll."""
    import threading

    events = []

    class FakeMonitor:
        def __init__(self):
            self.wake = threading.Event()
            self.trigger = "timer"
            self.waits = 0

        def abortRequested(self):
            return len(events) >= 2

        def waitForAbort(self, seconds):
            self.waits += 1
            return self.waits > 5

    monitor = FakeMonitor()

    class Worker:
        def run(self, trigger):
            # The trigger, and how long the loop waited before this run.
            events.append((trigger, monitor.waits))
            if len(events) == 1:
                # Kofin pings while the first reconcile is still running.
                monitor.trigger = "ping"
                monitor.wake.set()
            return {}

    monkeypatch.setattr(runner, "Monitor", lambda: monitor)
    monkeypatch.setattr(runner, "build", lambda: Worker())
    runner.run()
    assert events == [("start", 0), ("ping", 0)]
