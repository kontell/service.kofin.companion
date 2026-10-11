"""The service: wake on Kofin's ping, a finished scan, a settings change or
the timer, and reconcile."""

import threading
from typing import Any, Dict, Optional

import xbmc

from companion import contract, kodi, reconciler
from companion.handlers import registry
from companion.ledger import Ledger
from companion.log import Logger

LOG = Logger(__name__)

# A lost ping costs a timer tick, not a request; a request waiting on a
# scan is retried sooner.
POLL_SECONDS = 300.0
RETRY_SECONDS = 30.0

_SCAN_FINISHED = ("VideoLibrary.OnScanFinished", "AudioLibrary.OnScanFinished")


class Monitor(xbmc.Monitor):
    def __init__(self) -> None:
        super().__init__()
        self.wake = threading.Event()
        self.trigger = "start"
        self.last_ping: Optional[Dict[str, Any]] = None

    def onNotification(self, sender: str, method: str, data: str) -> None:
        if (
            sender == contract.KOFIN_ID
            and method == "Other." + contract.REQUESTS_MESSAGE
        ):
            self.last_ping = contract.decode_ping(data)
            self._wake("ping")
        elif sender == kodi.ADDON_ID and method == "Other." + contract.WAKE_MESSAGE:
            self._wake("settings button")
        elif sender == "xbmc" and method in _SCAN_FINISHED:
            self._wake("scan finished")

    def onSettingsChanged(self) -> None:
        self._wake("settings changed")

    def _wake(self, trigger: str) -> None:
        self.trigger = trigger
        self.wake.set()


def build(handlers: Optional[Dict[str, Any]] = None) -> reconciler.Reconciler:
    # The reconciler reads the ledger at its first run, and again at each
    # run for as long as it cannot be read.
    return reconciler.Reconciler(
        handlers if handlers is not None else registry(),
        Ledger(kodi.translate(contract.LEDGER_PATH)),
        kodi.translate(contract.REQUESTS_PATH),
        kodi.translate(contract.CAPABILITIES_PATH),
        kodi.translate(contract.STATUS_PATH),
    )


def run() -> None:
    LOG.info("--->>> companion %s", kodi.own_version())
    monitor = Monitor()
    worker = build()
    trigger = "start"
    while not monitor.abortRequested():
        # Cleared before the run, not after it: a ping that arrives while
        # the reconcile is running is for a document it may not have read.
        monitor.wake.clear()
        try:
            report = worker.run(trigger)
        except Exception:
            LOG.exception("reconcile failed")
            report = {}
        delay = RETRY_SECONDS if reconciler.waiting(report) else POLL_SECONDS
        waited = 0.0
        while waited < delay and not monitor.wake.is_set():
            if monitor.waitForAbort(1.0):
                LOG.info("---<<< companion")
                return
            waited += 1.0
        trigger = monitor.trigger if monitor.wake.is_set() else "timer"
    LOG.info("---<<< companion")
