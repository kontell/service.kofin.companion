"""The settings buttons: ask the service to reconcile, show the status."""

import json
from typing import List

import xbmcgui

from companion import contract, kodi


def main(args: List[str]) -> None:
    action = args[0] if args else ""
    if action == "reconcile":
        kodi.notify(contract.WAKE_MESSAGE, {"v": 1})
        xbmcgui.Dialog().notification(
            "Kofin Companion", kodi.localized(30030) or "Reconcile requested"
        )
    elif action == "status":
        report = contract.read_json(kodi.translate(contract.STATUS_PATH))
        text = (
            json.dumps(report, indent=1, sort_keys=True)
            if report is not None
            else (kodi.localized(30031) or "No status yet: the service has not run.")
        )
        xbmcgui.Dialog().textviewer(
            kodi.localized(30032) or "Kofin Companion status", text
        )
